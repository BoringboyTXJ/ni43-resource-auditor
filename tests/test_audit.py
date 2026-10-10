import json
from copy import deepcopy

import pytest

from resource_auditor.checks import check_row, quote_hints, validate_candidate
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.engine import audit
from resource_auditor.evaluation import evaluate, load_truth
from resource_auditor.evolution import append_event, few_shots, read_events
from resource_auditor.llm import ModelError
from resource_auditor.models import Candidate, Critique, GroundTruth, ResourceRow
from resource_auditor.pdf import Document


@pytest.fixture
def settings(tmp_path):
    return Settings(
        RoleConfig("https://extractor.test/v1", "strong", "test-key"),
        RoleConfig("https://critic.test/v1", "different", "test-key"),
        evolution_log=tmp_path / "evolution.jsonl",
    )


@pytest.fixture
def document():
    text = "NI 43-101 Mineral Resources\nTonnes (Mt) Au (g/t) Gold (koz)\nIndicated 10 1.00 321.5"
    return Document("synthetic-sha", "synthetic.pdf", {1: text}, text, [1], True, [])


@pytest.fixture
def candidate():
    return Candidate(
        reporting_standard="NI43-101",
        unresolved=[],
        rows=[
            ResourceRow(
                project="Synthetic",
                table="Table 1",
                effective_date="2025-01-01",
                basis="total",
                cutoff="0.5 g/t Au",
                category="Indicated",
                commodity="Au",
                ore_mt=10,
                grade=1,
                grade_unit="g/t",
                contained_metal=321500,
                contained_unit="oz",
                evidence={
                    "page": 1,
                    "row_quote": "Indicated 10 1.00 321.5",
                    "header_quote": "Tonnes (Mt) Au (g/t) Gold (koz)",
                    "ore_raw": "10",
                    "ore_unit": "Mt",
                    "grade_raw": "1.00",
                    "contained_raw": "321.5",
                    "contained_raw_unit": "koz",
                },
            )
        ],
    )


class FakeModels:
    def __init__(self, candidates, critique=None):
        self.candidates = candidates
        self.critique = critique or Critique(score=9, approved=True, complete=True, issues=[])
        self.calls = []
        self.index = 0

    def complete(self, role, system, payload, schema):
        self.calls.append((role, payload))
        if role == "critic":
            return self.critique
        result = self.candidates[min(self.index, len(self.candidates) - 1)]
        self.index += 1
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def test_evidence_and_conversion_are_valid(candidate, document):
    assert check_row(candidate.rows[0], document) == []


def test_reporting_standard_accepts_full_national_instrument_title(candidate, document):
    document.pages[1] = document.pages[1].replace("NI 43-101", "National Instrument 43-101")
    assert validate_candidate(candidate, document, False) == []


def test_header_omission_has_specific_error_and_literal_source_hint(candidate, document):
    row = candidate.rows[0]
    document.pages[1] = (
        "NI 43-101 Resources\nTonnes Grade Gold Classification (Mt) (g/t) (koz)\nIndicated 10 1.00 321.5"
    )
    row.evidence.header_quote = "Tonnes Grade Gold (Mt) (g/t) (koz)"
    assert "header_quote_not_in_source" in check_row(row, document)
    hints = quote_hints(candidate, document)
    assert hints[0]["field"] == "header_quote"
    assert "Classification" in hints[0]["original_window"]
    assert hints[0]["original_window"] in " ".join(document.pages[1].split())


def test_accepted_requires_two_roles_and_independent_approval(settings, candidate, document):
    client = FakeModels([candidate])
    result = audit(document, settings, client)
    assert result.status == "accepted" and not result.requires_manual_review
    assert len(result.rows) == 1
    assert [c[0] for c in client.calls] == ["extractor", "critic"]


def test_nonblocking_notes_are_retained_without_becoming_errors(settings, candidate, document):
    candidate.notes = ["Only gold is reported; copper is outside this table's scope."]
    critique = Critique(
        score=9,
        approved=True,
        complete=True,
        issues=[],
        notes=["The table's reporting constraint is descriptive rather than a numeric cut-off."],
    )
    result = audit(document, settings, FakeModels([candidate], critique))
    assert result.status == "accepted" and len(result.attempts) == 1
    assert result.attempts[0].candidate.notes == candidate.notes
    assert result.attempts[0].critique.notes == critique.notes


@pytest.mark.parametrize("blocking", ["rules", "unresolved", "critic"])
def test_notes_never_override_any_blocking_gate(settings, candidate, document, blocking):
    candidate.notes = ["Nonblocking explanation"]
    critique = Critique(score=10, approved=True, complete=True, issues=[], notes=["Explanation"])
    if blocking == "rules":
        candidate.rows[0].ore_mt *= 1000
    elif blocking == "unresolved":
        candidate.unresolved = ["Contained value cannot be assigned to a column"]
    else:
        critique.issues = ["Wrong resource category"]
    result = audit(document, settings, FakeModels([candidate], critique))
    assert result.status == "abstain" and not result.rows
    assert len(result.attempts) == 3


def test_parser_fallback_is_logged_even_when_original_text_passes(settings, candidate, document):
    document.parser_notes = ["mineru_failed; using original PDF text layer"]
    result = audit(document, settings, FakeModels([candidate]))
    assert result.status == "accepted"
    event = read_events(settings.evolution_log)[0]
    assert event["kind"] == "degradation" and event["round"] == 0
    assert event["reasons"] == document.parser_notes


@pytest.mark.parametrize(
    "field,value",
    [("ore_mt", 10000), ("grade", 1000), ("contained_metal", 321500000), ("grade_unit", "%")],
)
def test_obviously_wrong_fields_abstain_even_with_blind_high_score(
    settings, candidate, document, field, value
):
    setattr(candidate.rows[0], field, value)
    result = audit(document, settings, FakeModels([candidate]))
    assert result.status == "abstain" and result.requires_manual_review and result.rows == []
    assert len(result.attempts) == 3
    assert len(read_events(settings.evolution_log)) == 3


@pytest.mark.parametrize(
    "score,approved,complete,issues",
    [
        (7, True, True, []),
        (9, False, True, []),
        (9, True, False, []),
        (9, True, True, ["wrong header"]),
    ],
)
def test_score_is_only_one_gate(settings, candidate, document, score, approved, complete, issues):
    critique = Critique(score=score, approved=approved, complete=complete, issues=issues)
    result = audit(document, settings, FakeModels([candidate], critique))
    assert result.status == "abstain" and len(result.attempts) == 3


def test_combined_category_and_fabricated_quote_fail(candidate, document):
    candidate.rows[0].evidence.row_quote = "Measured & Indicated 10 1.00 321.5"
    issues = check_row(candidate.rows[0], document)
    assert "combined_category_is_not_indicated" in issues
    assert "unsupported_quote_or_page" in issues


def test_revision_resolution_becomes_cross_document_example(settings, candidate, document):
    bad = deepcopy(candidate)
    bad.rows[0].ore_mt = 10000
    client = FakeModels([bad, candidate])
    result = audit(document, settings, client)
    assert result.status == "accepted" and len(result.attempts) == 2
    assert client.calls[2][1]["feedback"]
    assert few_shots(settings.evolution_log, document.document_id) == []
    assert len(few_shots(settings.evolution_log, "different-hash")) == 1
    assert read_events(settings.evolution_log)[-1]["human_verified"] is False


def test_truncated_context_abstains_before_spending_on_models(settings, document, candidate):
    document.issues = ["context_overflow"]
    client = FakeModels([candidate])
    result = audit(document, settings, client)
    assert result.status == "abstain" and not client.calls


def test_provider_failure_is_abstention_not_success(settings, document):
    client = FakeModels([ModelError("provider_http_401")])
    result = audit(document, settings, client)
    assert result.status == "abstain" and len(result.attempts) == 1
    assert read_events(settings.evolution_log)[0]["error"] == "provider_http_401"


def test_schema_failure_followed_by_success_is_a_logged_resolution(settings, candidate, document):
    client = FakeModels([ModelError("provider_invalid_json_or_schema"), candidate])
    result = audit(document, settings, client)
    assert result.status == "accepted" and len(result.attempts) == 2
    events = read_events(settings.evolution_log)
    assert [event["kind"] for event in events] == ["failure", "resolution"]
    assert events[-1]["before"] is None
    assert few_shots(settings.evolution_log, "different-document")


def truth_for(candidate):
    row = candidate.rows[0].model_dump(exclude={"evidence"})
    return GroundTruth.model_validate({"rows": [row]})


def test_evaluation_coverage_prevents_all_abstain_from_looking_perfect(
    settings, candidate, document
):
    bad = deepcopy(candidate)
    bad.rows[0].ore_mt *= 1000
    result = audit(document, settings, FakeModels([bad]))
    report = evaluate(result, truth_for(candidate))
    assert report["field_accuracy_all"] == 0 and report["coverage"] == 0
    assert report["accepted_field_accuracy"] is None
    assert report["wrong_candidate_rejected"] is True


def test_evaluation_tolerance_missing_rows_and_units(settings, candidate, document):
    result = audit(document, settings, FakeModels([candidate]))
    truth = truth_for(candidate)
    result.rows[0].grade = 1.04
    assert evaluate(result, truth)["field_accuracy_all"] == 1
    result.rows[0].grade = 1.06
    assert evaluate(result, truth)["field_accuracy_all"] == pytest.approx(2 / 3)
    result.rows[0].grade_unit = "%"
    assert evaluate(result, truth)["accepted_field_accuracy"] == pytest.approx(2 / 3)


def test_five_percent_boundary_is_inclusive(settings, candidate, document):
    result = audit(document, settings, FakeModels([candidate]))
    truth = truth_for(candidate)
    for value in (0.95, 1.05):
        result.rows[0].grade = value
        assert evaluate(result, truth)["field_accuracy_all"] == 1
    result.rows[0].grade = 1.0501
    assert evaluate(result, truth)["field_accuracy_all"] == pytest.approx(2 / 3)


def test_category_column_quote_and_rounded_stockpile(candidate, document):
    row = candidate.rows[0]
    document.pages[1] = (
        "NI 43-101 Mineral Resources\nIndicated\nTonnes (Mt) Au (g/t) Gold (koz)\nExample 10 1.00 321.5"
    )
    row.evidence.row_quote = "Example 10 1.00 321.5"
    row.evidence.category_quote = "Indicated"
    assert check_row(row, document) == []
    row.evidence.category_quote = "Inferred"
    assert "unsupported_category" in check_row(row, document)
    row.commodity, row.grade_unit, row.contained_unit = "Li2O", "%", "t"
    row.ore_mt, row.grade, row.contained_metal = 7, 0.93, 100000
    row.evidence.ore_raw, row.evidence.grade_raw, row.evidence.contained_raw = "7", "0.93", "0.1"
    row.evidence.contained_raw_unit = "Mt"
    row.evidence.category_quote = ""
    row.evidence.row_quote = "Indicated 7 0.93 0.1"
    row.evidence.header_quote = "Tonnes (Mt) Li2O (%) Li2O (Mt)"
    document.pages[1] = row.evidence.header_quote + "\n" + row.evidence.row_quote
    assert check_row(row, document) == []


def test_sparse_truth_identity_and_explicit_field_mapping(settings, candidate, document):
    result = audit(document, settings, FakeModels([candidate]))
    raw = json.dumps(
        {"items": [{"class": "Indicated", "tonnage": 10, "assay": 1, "gold": 321500}]}
    ).encode()
    mapping = {
        "rows_key": "items",
        "fields": {
            "category": "class",
            "ore_mt": "tonnage",
            "grade": "assay",
            "contained_metal": "gold",
        },
        "defaults": {"commodity": "Au", "grade_unit": "g/t", "contained_unit": "oz"},
    }
    truth = load_truth(raw, mapping)
    assert evaluate(result, truth)["field_accuracy_all"] == 1


def test_truth_is_not_logged_or_sent_to_any_model(settings, candidate, document):
    client = FakeModels([candidate])
    result = audit(document, settings, client)
    truth = truth_for(candidate)
    truth.source["private_marker"] = "HIDDEN_ASSESSOR_ANSWER"
    evaluate(result, truth)
    assert "HIDDEN_ASSESSOR_ANSWER" not in json.dumps(client.calls)
    assert not settings.evolution_log.exists()


def test_malformed_evolution_is_explicit_failure(tmp_path):
    log = tmp_path / "broken.jsonl"
    log.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        few_shots(log, "other")


def test_unverified_failures_never_become_examples(tmp_path):
    log = tmp_path / "log.jsonl"
    append_event(log, {"kind": "failure", "document_id": "other", "after": {"fake": True}})
    assert few_shots(log, "current") == []
