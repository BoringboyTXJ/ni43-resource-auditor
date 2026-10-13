"""Safety regressions from the 9d20777 independent review; no live model or truth."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from resource_auditor.checks import explicit_row_coverage, validate_candidate
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.engine import audit
from resource_auditor.models import Candidate, Critique, ResourceRow
from resource_auditor.pdf import Document


class BlindApproval:
    def __init__(self, candidate):
        self.candidate = candidate

    def complete(self, role, system, payload, schema):
        if role == "critic":
            return Critique(score=10, approved=True, complete=True, issues=[])
        return deepcopy(self.candidate)


@pytest.fixture
def control(tmp_path):
    header = "Tonnes (Mt) Au (g/t) Gold (koz)"
    text = (
        "NI 43-101 Mineral Resources for Example Project\n"
        "Table 1-1, effective December 31, 2024. Cut-off 0.5 g/t Au.\n"
        f"{header}\nIndicated 10 1.00 321.5\nInferred 5 1.00 160.8"
    )
    rows = []
    for category, ore, metal in [("Indicated", "10", "321.5"), ("Inferred", "5", "160.8")]:
        rows.append(
            ResourceRow(
                project="Example Project",
                table="Table 1-1",
                effective_date="2024-12-31",
                basis="total",
                cutoff="0.5 g/t Au",
                category=category,
                commodity="Au",
                ore_mt=float(ore),
                grade=1,
                grade_unit="g/t",
                contained_metal=float(metal) * 1000,
                contained_unit="oz",
                evidence={
                    "page": 1,
                    "row_quote": f"{category} {ore} 1.00 {metal}",
                    "header_quote": header,
                    "ore_raw": ore,
                    "ore_unit": "Mt",
                    "grade_raw": "1.00",
                    "contained_raw": metal,
                    "contained_raw_unit": "koz",
                },
            )
        )
    candidate = Candidate(reporting_standard="NI43-101", rows=rows, unresolved=[])
    document = Document("synthetic", "unseen.pdf", {1: text}, text, [1], True, [])
    settings = Settings(
        RoleConfig("https://a.test", "extractor", "synthetic"),
        RoleConfig("https://b.test", "critic", "synthetic"),
        evolution_log=tmp_path / "evolution.jsonl",
    )
    return candidate, document, settings


@pytest.mark.parametrize("table_count", [0, 2])
def test_omission_abstains_with_zero_or_multiple_native_tables(control, table_count):
    candidate, document, settings = control
    header = [["Category", "Tonnes (Mt)", "Au (g/t)", "Gold (koz)"]]
    document.tables = {1: [header] * table_count}
    assert validate_candidate(candidate, document, False) == []
    candidate.rows.pop(0)
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain" and result.requires_manual_review and not result.rows
    assert len(result.attempts) == 3
    assert all(a.critique.score == 10 for a in result.attempts)
    assert any("missing_explicit_resource_row" in e for e in result.attempts[0].rule_issues)


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "Wrong Project"),
        ("table", "Table 99-99"),
        ("effective_date", "1999-01-01"),
        ("cutoff", "99 g/t Au"),
        ("basis", "Wrong Pit"),
    ],
)
def test_fabricated_identity_abstains_despite_ten_score(control, field, value):
    candidate, document, settings = control
    setattr(candidate.rows[0], field, value)
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain" and result.requires_manual_review and not result.rows
    assert any(f"unverified_identity:{field}" in e for e in result.attempts[0].rule_issues)


@pytest.mark.parametrize("date", ["31 December 2024", "December 31, 2024", "2024-12-31"])
def test_iso_identity_date_accepts_equivalent_source_formats(control, date):
    candidate, document, _ = control
    document.pages[1] = document.pages[1].replace("December 31, 2024", date)
    assert validate_candidate(candidate, document, False) == []


def test_parallel_category_inventory_is_independent_of_table_count(control):
    candidate, document, settings = control
    header = "Location Indicated Inferred Tonnes (Mt) Au (g/t) Gold (koz)"
    document.pages[1] = document.context = (
        "NI 43-101 Example Project Table 1-1 December 31, 2024, cut-off 0.5 g/t Au\n"
        f"{header}\nPit A 10 1.00 321.5 5 1.00 160.8"
    )
    document.tables = {
        1: [
            [
                ["Location", "Indicated", None, None, "Inferred", None, None],
                [
                    "",
                    "Tonnes (Mt)",
                    "Au Grade (g/t)",
                    "Gold (koz)",
                    "Tonnes (Mt)",
                    "Au Grade (g/t)",
                    "Gold (koz)",
                ],
                ["Pit A", "10", "1.00", "321.5", "5", "1.00", "160.8"],
            ]
        ]
        * 2
    }
    # Two distinct source tables with the same numeric rows must be counted separately.
    document.tables[1][1] = deepcopy(document.tables[1][1])
    document.tables[1][1][-1][0] = "Pit B"
    document.pages[1] += "\nPit B 10 1.00 321.5 5 1.00 160.8"
    document.context = document.pages[1]
    for row in candidate.rows:
        row.basis = "Pit A"
        row.evidence.row_quote = "Pit A 10 1.00 321.5 5 1.00 160.8"
        row.evidence.header_quote = header
        row.evidence.category_quote = row.category
    assert audit(document, settings, BlindApproval(candidate)).status == "abstain"
    for row in deepcopy(candidate.rows):
        row.basis = "Pit B"
        row.evidence.row_quote = "Pit B 10 1.00 321.5 5 1.00 160.8"
        candidate.rows.append(row)
    assert audit(document, settings, BlindApproval(candidate)).status == "accepted"


def test_unverifiable_parallel_text_layout_blocks_blind_approval(control):
    candidate, document, settings = control
    document.pages[1] = document.context = (
        "NI 43-101 Example Project Table 1-1 December 31, 2024 cut-off 0.5 g/t Au\n"
        "Indicated Inferred\nTonnes (Mt) Au (g/t) Gold (koz)\nPit 10 1.00 321.5 5 1.00 160.8"
    )
    for row in candidate.rows:
        row.basis = "Pit"
        row.evidence.row_quote = "Pit 10 1.00 321.5 5 1.00 160.8"
        row.evidence.category_quote = row.category
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("scope_completeness_unverified" in e for e in result.attempts[0].rule_issues)


@pytest.mark.parametrize("line", ["Inferred Pit 5 1.00 160.8", "Inferred 5 1.00 160.8 *"])
def test_unparsed_category_numeric_row_is_not_silently_ignored(control, line):
    candidate, document, settings = control
    document.pages[1] = document.context = document.pages[1].replace("Inferred 5 1.00 160.8", line)
    candidate.rows.pop()
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("scope_completeness_unverified" in e for e in result.attempts[0].rule_issues)


def test_borderless_rows_are_inventoried_alongside_native_table(control):
    candidate, document, settings = control
    document.tables = {
        1: [
            [
                ["Category", "Tonnes (Mt)", "Au Grade (g/t)", "Gold (koz)"],
                ["Indicated", "10", "1.00", "321.5"],
            ]
        ]
    }
    assert explicit_row_coverage(candidate, document) == []
    candidate.rows.pop()
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("missing_explicit_resource_row" in e for e in result.attempts[0].rule_issues)


def test_cutoff_cannot_borrow_an_ore_value_from_same_page(control):
    candidate, document, settings = control
    document.pages[1] += "\nAn unrelated Table 2 has a CoG column."
    candidate.rows[0].cutoff = "10 g/t Au"
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("unverified_identity:cutoff" in e for e in result.attempts[0].rule_issues)


def test_total_cannot_drop_explicit_mining_method(control):
    candidate, document, settings = control
    document.pages[1] = document.context = document.pages[1].replace(
        "Indicated 10", "Total UG\nIndicated 10"
    )
    for row in candidate.rows:
        row.basis = "Total underground"
    assert validate_candidate(candidate, document, False) == []
    candidate.rows[0].basis = "total"
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("unverified_identity:basis" in e for e in result.attempts[0].rule_issues)


@pytest.mark.parametrize(
    "line",
    ["Inferred\n5 1.00 160.8 *", "<tr><td>Inferred</td><td>5</td><td>1.00</td><td>160.8</td></tr>"],
)
def test_unsupported_vertical_or_html_rows_block_partial_inventory(control, line):
    candidate, document, settings = control
    document.pages[1] = document.context = document.pages[1].replace("Inferred 5 1.00 160.8", line)
    candidate.rows.pop()
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("scope_completeness_unverified" in e for e in result.attempts[0].rule_issues)


PUBLIC_LAYOUTS = json.loads(
    (Path(__file__).parent / "fixtures/public_resource_layouts.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("source", PUBLIC_LAYOUTS, ids=lambda source: source["id"])
def test_public_layout_controls_and_every_row_omission(control, source):
    _, _, settings = control
    pages = {int(page): text for page, text in source["pages"].items()}
    document = Document(
        source["document_id"],
        "unseen.pdf",  # No filename/company branch may confer approval.
        pages,
        "\n".join(pages[page] for page in source["selected_pages"]),
        source["selected_pages"],
        True,
        [],
        tables={int(page): tables for page, tables in source["tables"].items()},
    )
    candidate = Candidate.model_validate(source["candidate"])
    allow_jorc = source["allow_jorc"]
    assert (
        audit(document, settings, BlindApproval(candidate), allow_jorc=allow_jorc).status
        == "accepted"
    )
    for index in range(len(candidate.rows)):
        missing = deepcopy(candidate)
        missing.rows.pop(index)
        assert any(
            "missing_explicit_resource_row" in error
            for error in validate_candidate(missing, document, allow_jorc)
        ), (source["id"], index)
    for field, value in (
        ("project", "Wrong Project"),
        ("table", "Table 99-99"),
        ("effective_date", "1999-01-01"),
        ("cutoff", "99 g/t"),
        ("basis", "Wrong Pit"),
    ):
        fabricated = deepcopy(candidate)
        setattr(fabricated.rows[0], field, value)
        assert any(
            f"unverified_identity:{field}" in error
            for error in validate_candidate(fabricated, document, allow_jorc)
        ), (source["id"], field)
    candidate.rows.pop(0)
    rejected = audit(document, settings, BlindApproval(candidate), allow_jorc=allow_jorc)
    assert rejected.status == "abstain" and not rejected.rows
    assert all(attempt.critique.score == 10 for attempt in rejected.attempts)
