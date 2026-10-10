"""Acceptance regressions reproduced from the independent snapshot review.

Run from the repository root with PYTHONPATH=src:
python -m pytest -q tests/test_review_regressions.py
Controlled model doubles exercise the deterministic safety boundary, not live Qwen quality.
"""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from resource_auditor import pdf as pdf_module
from resource_auditor.checks import check_row, validate_candidate
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.engine import audit
from resource_auditor.evaluation import evaluate, load_truth
from resource_auditor.llm import ModelError
from resource_auditor.models import Candidate, Critique, GroundTruth, ResourceRow
from resource_auditor.pdf import Document, read_pdf
from resource_auditor.units import field_units


@pytest.fixture
def settings(tmp_path):
    return Settings(
        RoleConfig("https://extractor.test/v1", "strong", "test"),
        RoleConfig("https://critic.test/v1", "different", "test"),
        evolution_log=tmp_path / "evolution.jsonl",
    )


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
                cutoff="",
                category="Indicated",
                commodity="Cu",
                ore_mt=100,
                grade=1,
                grade_unit="%",
                contained_metal=1000000,
                contained_unit="t",
                evidence={
                    "page": 1,
                    "row_quote": "Indicated 100 1.00 1.0",
                    "header_quote": "Tonnes (Mt) Cu (%) Copper (Mt)",
                    "ore_raw": "100",
                    "ore_unit": "Mt",
                    "grade_raw": "1.00",
                    "contained_raw": "1.0",
                    "contained_raw_unit": "Mt",
                },
            )
        ],
    )


@pytest.fixture
def document(candidate):
    row = candidate.rows[0]
    text = (
        "NI 43-101 Mineral Resources\n" + row.evidence.header_quote + "\n" + row.evidence.row_quote
    )
    return Document("synthetic", "synthetic.pdf", {1: text}, text, [1], True, [])


class BlindHighScore:
    def __init__(self, candidates):
        self.candidates = candidates
        self.index = 0

    def complete(self, role, system, payload, schema):
        if role == "critic":
            return Critique(score=8, approved=True, complete=True, issues=[])
        value = self.candidates[min(self.index, len(self.candidates) - 1)]
        self.index += 1
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


def test_control_valid_candidate_accepts_at_exactly_eight(settings, candidate, document):
    assert audit(document, settings, BlindHighScore([candidate])).status == "accepted"


def test_control_single_wrong_normalized_value_abstains(settings, candidate, document):
    candidate.rows[0].ore_mt *= 1000
    result = audit(document, settings, BlindHighScore([candidate]))
    assert result.status == "abstain" and result.rows == []


def test_coupled_millionfold_unit_error_must_abstain(settings, candidate, document):
    # Actual Mt values remain in source. Both evidence units are incorrectly changed to t.
    row = candidate.rows[0]
    row.evidence.ore_unit = "t"
    row.evidence.contained_raw_unit = "t"
    row.ore_mt = 0.0001
    row.contained_metal = 1
    result = audit(document, settings, BlindHighScore([candidate]))
    assert result.status == "abstain", (
        f"Accepted millionfold underreporting; rule issues={validate_candidate(candidate, document, False)}"
    )


def test_opposite_row_category_cannot_be_overridden_by_unrelated_label(
    settings, candidate, document
):
    row = candidate.rows[0]
    row.evidence.row_quote = "Inferred 100 1.00 1.0"
    row.evidence.category_quote = "Indicated"
    text = (
        "NI 43-101 Mineral Resources\n"
        + row.evidence.header_quote
        + "\nIndicated 20 1.00 0.2\n"
        + row.evidence.row_quote
    )
    document.pages[1] = document.context = text
    assert audit(document, settings, BlindHighScore([candidate])).status == "abstain"


@pytest.mark.parametrize("qualifier", ["inclusive of", "exclusive of"])
def test_resource_header_reserve_qualifier_is_not_a_reserve_table(
    settings, candidate, document, qualifier
):
    row = candidate.rows[0]
    row.evidence.header_quote = (
        f"Mineral Resources ({qualifier} Mineral Reserves) Tonnes (Mt) Cu (%) Copper (Mt)"
    )
    text = "NI 43-101\n" + row.evidence.header_quote + "\n" + row.evidence.row_quote
    document.pages[1] = document.context = text
    result = audit(document, settings, BlindHighScore([candidate]))
    assert result.status == "accepted", result.reasons


def test_selected_scan_page_must_trigger_ocr_or_abstention(
    settings, candidate, document, monkeypatch
):
    class Page:
        def __init__(self, text, scanned=False):
            self.text = text
            self.images = [{"object_type": "image"}] if scanned else []

        def extract_text(self, **kwargs):
            return self.text

        def extract_tables(self):
            return []

        def close(self):
            pass

    class PDF:
        def __init__(self):
            self.pages = [Page(document.context), Page("", scanned=True)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(pdf_module.pdfplumber, "open", lambda *a, **k: PDF())
    calls = []

    def ocr(data, config, selected):
        calls.append(selected)
        raise ValueError("mineru_failed")

    monkeypatch.setattr(pdf_module, "parse_mineru", ocr)
    settings = replace(settings, mineru_python="configured-ocr-runtime")
    doc = read_pdf(b"%PDF-test", "mixed-text-and-scan.pdf", settings, "1-2")
    result = audit(doc, settings, BlindHighScore([candidate]))
    assert calls or result.status == "abstain", (
        f"OCR not called; selected scan page ignored, status={result.status}, issues={doc.issues}"
    )


def test_mapping_preserves_ground_truth_document_identity(settings, candidate, document):
    result = audit(document, settings, BlindHighScore([candidate]))
    raw = json.dumps(
        {
            "document_id": "different-pdf-hash",
            "rows": [candidate.rows[0].model_dump(exclude={"evidence"})],
        }
    ).encode()
    field_names = list(candidate.rows[0].model_dump(exclude={"evidence"}))
    truth = load_truth(raw, {"fields": {name: name for name in field_names}})
    with pytest.raises(ValueError, match="hash"):
        evaluate(result, truth)


def test_known_wrong_candidate_remains_visible_after_later_schema_failures(
    settings, candidate, document
):
    truth = GroundTruth(rows=[candidate.rows[0].model_dump(exclude={"evidence"})])
    candidate.rows[0].ore_mt *= 1000
    result = audit(
        document,
        settings,
        BlindHighScore(
            [
                candidate,
                ModelError("provider_invalid_json_or_schema"),
                ModelError("provider_invalid_json_or_schema"),
            ]
        ),
    )
    report = evaluate(result, truth)
    assert (
        report["wrong_candidate_detected"] is True and report["wrong_candidate_rejected"] is True
    ), report


def test_reserve_table_itself_still_abstains(settings, candidate, document):
    candidate.rows[0].evidence.header_quote = "Mineral Reserves Tonnes (Mt) Cu (%) Copper (Mt)"
    document.pages[1] = candidate.rows[0].evidence.header_quote + "\nIndicated 100 1.00 1.0"
    assert audit(document, settings, BlindHighScore([candidate])).status == "abstain"


def test_category_column_numbers_and_units_cannot_be_borrowed(settings, candidate, document):
    # Both unit pairs appear on the page, but only Mt belongs to the Indicated columns.
    header = "Tonnes (Mt) Cu (%) Copper (Mt) Tonnes (kt) Cu (%) Copper (kt)"
    quote = "Pit 100 1.00 1.0 100 1.00 1.0"
    document.pages[1] = "NI 43-101 Mineral Resources Indicated Inferred\n" + header + "\n" + quote
    document.tables = {
        1: [
            [
                ["Location", "Indicated", None, None, "Inferred", None, None],
                [
                    None,
                    "Tonnes (Mt)",
                    "Grade Cu (%)",
                    "Contained Copper (Mt)",
                    "Tonnes (kt)",
                    "Grade Cu (%)",
                    "Contained Copper (kt)",
                ],
                ["Pit", "100", "1.00", "1.0", "100", "1.00", "1.0"],
            ]
        ]
    }
    ev = candidate.rows[0].evidence
    ev.header_quote, ev.row_quote, ev.category_quote = header, quote, "Indicated"
    assert validate_candidate(candidate, document, False) == []
    ev.ore_unit, ev.contained_raw_unit = "kt", "kt"
    candidate.rows[0].ore_mt, candidate.rows[0].contained_metal = 0.1, 1000
    assert audit(document, settings, BlindHighScore([candidate])).status == "abstain"


def test_repaired_wrong_candidate_is_reported_separately(settings, candidate, document):
    truth = GroundTruth(rows=[candidate.rows[0].model_dump(exclude={"evidence"})])
    bad = deepcopy(candidate)
    bad.rows[0].ore_mt *= 1000
    report = evaluate(audit(document, settings, BlindHighScore([bad, candidate])), truth)
    assert report["wrong_candidate_detected"] is False
    assert report["any_wrong_candidate_detected"] is True
    assert report["wrong_candidate_revised"] is True
    assert report["last_valid_candidate_round"] == 2


def test_explicit_hash_mapping_preserves_standard_metadata(candidate, document):
    raw = json.dumps(
        {
            "sha": document.document_id,
            "source": {"page": 1},
            "rows": [candidate.rows[0].model_dump(exclude={"evidence"})],
        }
    ).encode()
    mapping = {
        "fields": {name: name for name in candidate.rows[0].model_dump(exclude={"evidence"})},
        "document_id_key": "sha",
    }
    truth = load_truth(raw, mapping)
    assert truth.document_id == document.document_id and truth.source == {"page": 1}
    conflicting = json.loads(raw)
    conflicting["document_id"] = "different"
    with pytest.raises(ValueError, match="hash"):
        load_truth(json.dumps(conflicting).encode(), mapping)


def test_automatic_locator_selects_numeric_tables_without_neighbor_prose():
    texts = {
        1: "NI 43-101 Resources introduction and overview",
        2: "Mineral Resources Tonnes (Mt) Au (g/t) Gold (koz)\nIndicated 10 1.00 321.5\nInferred 5 1.00 160.8",
        3: "The Indicated mineral resources total 10 Mt at a grade of 1.0 g/t with 321.5 koz contained gold.",
        4: "Table 1-1 Mineral Resources Summary .................................. 19",
    }
    assert pdf_module.resource_pages(texts, 4) == [2]


@pytest.mark.parametrize("header", ["Tonnes (x 10^6)", "Gold Ounces (* 10^3)"])
def test_unknown_multiplier_never_becomes_unscaled_unit(header):
    assert field_units(header) == set()


def test_explicit_long_multiplier_units_are_preserved():
    assert field_units("Contained Copper, millions of tonnes") == {"Mt"}
    assert field_units("Gold, thousands of troy ounces") == {"koz"}


def test_vertical_merged_category_cannot_be_borrowed_from_previous_group(
    settings, candidate, document
):
    row = candidate.rows[0]
    row.evidence.row_quote = "Lower Pit 100 1.00 1.0"
    row.evidence.category_quote = "Indicated"
    document.tables = {
        1: [
            [
                ["Classification", "Ore Tonnes (Mt)", "Cu Grade (%)", "Contained Copper (Mt)"],
                ["Indicated", "20", "1.00", "0.2"],
                ["Inferred Upper Pit", "30", "1.00", "0.3"],
                ["Lower Pit", "100", "1.00", "1.0"],
            ]
        ]
    }
    text = (
        "NI 43-101 Mineral Resources\n"
        + row.evidence.header_quote
        + "\nIndicated 20 1.00 0.2\nInferred Upper Pit 30 1.00 0.3\n"
        + row.evidence.row_quote
    )
    document.pages[1] = document.context = text
    assert audit(document, settings, BlindHighScore([candidate])).status == "abstain"
    row.category = "Inferred"
    row.evidence.category_quote = "Inferred"
    assert check_row(row, document) == []
    # This fixture validates one merged row; it still omits the other source rows.
    assert any(
        "missing_explicit_resource_row" in issue
        for issue in validate_candidate(candidate, document, False)
    )


def test_disclosure_note_cannot_override_conflicting_column_multiplier(
    settings, candidate, document
):
    document.tables = {
        1: [
            [
                [
                    "Classification",
                    "Ore Tonnes (Mt)",
                    "Cu Grade (%)",
                    "Contained Copper Tonnes (t x 10^3)",
                ],
                ["Indicated", "100", "1.00", "1.0"],
            ]
        ]
    }
    text = document.context + "\nContained metal is reported in millions of tonnes of copper."
    document.pages[1] = document.context = text
    assert audit(document, settings, BlindHighScore([candidate])).status == "abstain"


def test_borderless_vertical_category_is_bound_to_its_own_header(candidate, document):
    row = candidate.rows[0]
    row.evidence.header_quote = "Ore Tonnes (Mt) Cu Grade (%) Copper (Mt) INDICATED"
    row.evidence.row_quote = "Pit 100 1.00 1.0"
    row.evidence.category_quote = "INDICATED"
    document.pages[1] = (
        "NI 43-101 Mineral Resources\n"
        + row.evidence.header_quote
        + "\n"
        + row.evidence.row_quote
        + "\n"
        + "Ore Tonnes (Mt) Cu Grade (%) Copper (Mt) INFERRED\nPit 20 1.00 0.2"
    )
    assert validate_candidate(candidate, document, False) == []
    # The old Indicated header cannot support a row in the later Inferred block.
    row.evidence.row_quote = "Pit 20 1.00 0.2"
    row.evidence.ore_raw, row.evidence.contained_raw = "20", "0.2"
    row.ore_mt, row.contained_metal = 20, 200000
    assert "row[0]:category_column_alignment_unverified" in validate_candidate(
        candidate, document, False
    )


@pytest.mark.parametrize(
    "intervening",
    [
        "Ore Tonnes (kt) Cu Grade (%) Copper (kt)",
        "Table 2: New Mineral Resources",
        "Measured",
        "Indicated Inferred",
    ],
)
def test_borderless_header_cannot_cross_a_new_table_or_unit_block(candidate, document, intervening):
    row = candidate.rows[0]
    row.evidence.header_quote = "Ore Tonnes (Mt) Cu Grade (%) Copper (Mt) INDICATED"
    row.evidence.row_quote = "Pit 100 1.00 1.0"
    row.evidence.category_quote = "INDICATED"
    document.pages[1] = (
        "NI 43-101 Mineral Resources\n"
        + row.evidence.header_quote
        + "\n"
        + intervening
        + "\n"
        + row.evidence.row_quote
        + "\nInferred 20 1.00 0.2"
    )
    assert "row[0]:category_column_alignment_unverified" in validate_candidate(
        candidate, document, False
    )


def test_borderless_repeated_numeric_row_is_ambiguous(candidate, document):
    row = candidate.rows[0]
    row.evidence.header_quote = "Ore Tonnes (Mt) Cu Grade (%) Copper (Mt) INDICATED"
    row.evidence.row_quote = "Pit 100 1.00 1.0"
    row.evidence.category_quote = "INDICATED"
    document.pages[1] = (
        "NI 43-101 Mineral Resources\n"
        + row.evidence.header_quote
        + "\n"
        + row.evidence.row_quote
        + "\n"
        + "Ore Tonnes (Mt) Cu Grade (%) Copper (Mt) INFERRED\n"
        + row.evidence.row_quote
    )
    assert "row[0]:category_column_alignment_unverified" in validate_candidate(
        candidate, document, False
    )


def test_missing_explicit_resource_row_vetoes_blind_approval(settings, candidate, document):
    document.tables = {1: [[["Category", "Tonnes (Mt)", "Cu (%)", "Contained Cu (Mt)"]]]}
    document.pages[1] = document.context + "\nInferred 20 1.00 0.2"
    result = audit(document, settings, BlindHighScore([candidate]))
    assert result.status == "abstain" and result.rows == []
    assert any("missing_explicit_resource_row" in issue for issue in result.attempts[0].rule_issues)
    inferred = deepcopy(candidate.rows[0])
    inferred.category = "Inferred"
    inferred.ore_mt, inferred.contained_metal = 20, 200000
    inferred.evidence.row_quote = "Inferred 20 1.00 0.2"
    inferred.evidence.ore_raw, inferred.evidence.contained_raw = "20", "0.2"
    candidate.rows.append(inferred)
    assert audit(document, settings, BlindHighScore([candidate])).status == "accepted"


def test_explicit_coverage_counts_repeated_source_rows_separately(settings, candidate, document):
    document.tables = {1: [[["Category", "Tonnes (Mt)", "Cu (%)", "Contained Cu (Mt)"]]]}
    document.pages[1] = document.context + "\nIndicated 100 1.00 1.0"
    assert audit(document, settings, BlindHighScore([candidate])).status == "abstain"
    additional = deepcopy(candidate.rows[0])
    additional.basis = "stockpiles"
    candidate.rows.append(additional)
    assert audit(document, settings, BlindHighScore([candidate])).status == "accepted"


def test_explicit_coverage_does_not_turn_prose_or_excluded_categories_into_rows(
    settings, candidate, document
):
    document.tables = {1: [[["Category", "Tonnes (Mt)", "Cu (%)", "Contained Cu (Mt)"]]]}
    document.pages[1] = document.context + (
        "\nIndicated Resources were estimated on 2025 01 01."
        "\nMeasured 30 1.00 0.3\nMeasured and Indicated 130 1.00 1.3"
        "\nInferred - - -\nSub-Total 130 1.00 1.3"
    )
    assert audit(document, settings, BlindHighScore([candidate])).status == "accepted"


def test_basis_method_abbreviations_match_without_merging_locations(settings, candidate, document):
    expected = candidate.rows[0].model_dump(exclude={"evidence"})
    expected["basis"] = "Zone A UG"
    truth = GroundTruth(rows=[expected])
    candidate.rows[0].basis = "Zone A underground"
    result = audit(document, settings, BlindHighScore([candidate]))
    assert evaluate(result, truth)["field_accuracy_all"] == 1
    candidate.rows[0].basis = "Zone B underground"
    assert evaluate(audit(document, settings, BlindHighScore([candidate])), truth)["coverage"] == 0
    candidate.rows[0].basis = "Zone A open pit"
    assert evaluate(audit(document, settings, BlindHighScore([candidate])), truth)["coverage"] == 0


@pytest.mark.parametrize("short,long", [("UG", "underground"), ("OP", "open pit")])
def test_equivalent_basis_spellings_cannot_create_distinct_resource_rows(
    settings, candidate, document, short, long
):
    candidate.rows[0].basis = "Zone A " + short
    duplicate = deepcopy(candidate.rows[0])
    duplicate.basis = "Zone A " + long
    candidate.rows.append(duplicate)
    result = audit(document, settings, BlindHighScore([candidate]))
    assert result.status == "abstain"
    assert "row[1]:duplicate_resource_row" in result.attempts[0].rule_issues
