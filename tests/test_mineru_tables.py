import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from resource_auditor.checks import (
    check_identity,
    explicit_row_coverage,
    native_inventory,
    validate_candidate,
)
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.engine import audit
from resource_auditor.models import Candidate, Critique, ResourceRow
from resource_auditor.pdf import Document, read_pdf, resource_pages
from resource_auditor.tables import table_views

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/newmont_mineru.json").read_text(encoding="utf-8")
)


def test_actual_mineru_merged_headers_are_selected_without_native_pdf_parser():
    pages = {int(page): text for page, text in FIXTURE["pages"].items()}
    assert resource_pages(pages, FIXTURE["page_count"]) == FIXTURE["selected_pages"]


def test_structured_sensitivity_headers_and_malformed_resource_cells_cannot_be_dropped():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/kinross_mineru.json").read_text(encoding="utf-8")
    )
    pages = {int(page): text for page, text in fixture["pages"].items()}
    assert resource_pages(pages, fixture["page_count"]) == fixture["selected_pages"]


def test_merged_multiple_categories_and_numbers_cannot_claim_a_partial_inventory():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/kinross_mineru.json").read_text(encoding="utf-8")
    )
    table = table_views(fixture["pages"]["155"])[0][0]
    _, uncertain = native_inventory(table)
    assert uncertain, "Merged category/value rows cannot silently disappear from coverage"


def test_oxide_tables_bind_li2o_fields_and_exclude_other_explicit_oxide_columns():
    text = """| Area | Category | Tonnes (Mt) | Li2O (%) | Ta2O5 (ppm) | Fe2O3 (%) | Li2O (Mt) | Ta2O5 (Mlb) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| North | Indicated | 10 | 1.00 | 120 | 0.50 | 0.10 | 3 |
| North | Inferred | 5 | 1.00 | 140 | 0.60 | 0.05 | 2 |"""
    items, uncertain = native_inventory(table_views(text)[0][0])
    assert not uncertain and len(items) == 2
    assert [item["raw"] for item in items] == [
        {"ore": "10", "grade": "1.00", "metal": "0.10"},
        {"ore": "5", "grade": "1.00", "metal": "0.05"},
    ]


def test_conflicting_categories_within_a_merged_location_group_are_unverified():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/kinross_mineru.json").read_text(encoding="utf-8")
    )
    table = table_views(fixture["advanced_pages"]["155"])[0][0]
    _, uncertain = native_inventory(table)
    assert uncertain, "Two different Inferred rows in one method group must not be silently bound"


def test_latex_chemical_headers_do_not_change_numeric_cells_or_original_evidence():
    text = """<table><tr><td>Category</td><td>Tonnes (Mt)</td><td>$Li_{2}O$ (%)</td><td>$Ta_{2}O_{5}$ (ppm)</td><td>$Fe_{2}O_{3}$ (%)</td><td>$Li_{2}O$ (Mt)</td></tr><tr><td>Indicated</td><td>10</td><td>1.00</td><td>120</td><td>0.50</td><td>0.10</td></tr></table>"""
    table = table_views(text)[0][0]
    items, uncertain = native_inventory(table)
    assert not uncertain and items[0]["commodity"] == "Li2O"
    assert items[0]["raw"] == {"ore": "10", "grade": "1.00", "metal": "0.10"}
    assert "$Li_{2}O$ (%)" == table[0][2]


def test_numbers_in_oxide_and_cutoff_labels_do_not_double_count_structured_rows():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/pilbara_mineru.json").read_text(encoding="utf-8")
    )
    pages = {int(k): v for k, v in fixture["pages"].items()}
    doc = Document(
        "x",
        "unknown.pdf",
        pages,
        "\n".join(pages.values()),
        [2, 5],
        False,
        [],
        tables={p: table_views(t)[0] for p, t in pages.items()},
    )
    errors = explicit_row_coverage(
        Candidate(reporting_standard="JORC", rows=[], unresolved=[]), doc
    )
    assert len([e for e in errors if "missing_explicit_resource_row" in e]) == 13
    assert not any("scope_completeness_unverified" in e for e in errors)


def test_explicit_latex_oxide_cutoff_has_the_same_semantic_identity():
    f = json.loads(
        (Path(__file__).parent / "fixtures/pilbara_mineru.json").read_text(encoding="utf-8")
    )
    text = f["pages"]["5"]
    doc = Document("x", "x.pdf", {5: text}, text, [5], True, [])
    row = ResourceRow(
        project="Pilgangoora",
        table="Table 2",
        effective_date="unknown",
        basis="South",
        cutoff="0.2% Li2O",
        category="Indicated",
        commodity="Li2O",
        ore_mt=82,
        grade=1.06,
        grade_unit="%",
        contained_metal=900000,
        contained_unit="t",
        evidence={
            "page": 5,
            "row_quote": "Indicated 82 1.06 0.9",
            "header_quote": "Tonnes (Mt) Li2O (%) Li2O (Mt)",
            "ore_raw": "82",
            "ore_unit": "Mt",
            "grade_raw": "1.06",
            "contained_raw": "0.9",
            "contained_raw_unit": "Mt",
        },
    )
    assert "unverified_identity:cutoff" not in check_identity(row, doc)


def test_explicit_total_location_cannot_be_relabelled_as_a_different_basis():
    f = json.loads(
        (Path(__file__).parent / "fixtures/pilbara_mineru.json").read_text(encoding="utf-8")
    )
    items, uncertain = native_inventory(table_views(f["pages"]["5"])[0][0])
    assert not uncertain
    total_rows = [item for item in items if item["raw"]["ore"] in {"349", "70"}]
    assert len(total_rows) == 2 and all(item["basis"].casefold() == "total" for item in total_rows)


@pytest.fixture
def control(tmp_path):
    pages = {int(page): text for page, text in FIXTURE["pages"].items()}
    document = Document(
        FIXTURE["document_id"],
        "unknown.pdf",
        pages,
        "\n".join(pages.values()),
        FIXTURE["selected_pages"],
        False,
        [],
        parser=FIXTURE["parser"],
        tables={page: table_views(pages[page])[0] for page in FIXTURE["selected_pages"]},
    )
    # Positive values transcribed from the visible public table, never an evaluator input.
    values = [
        ("Tanami UG", "3,800", "4.73", "570", "6,300", "5.67", "1,140"),
        ("Tanami OP", "4,200", "2.21", "300", "3,900", "1.88", "230"),
        ("Total UG + OP", "8,000", "3.40", "870", "10,200", "4.22", "1,370"),
    ]
    rows = []
    for page, table in ((22, "Table 1-3"), (147, "Table 14-5")):
        header = re.search(r"<tr>.*?</tr><tr>.*?</tr>", pages[page])[0]
        for basis, *numbers in values:
            quote = re.search(r"<tr><td>" + re.escape(basis) + r"</td>.*?</tr>", pages[page])[0]
            for category, raw in (("Indicated", numbers[:3]), ("Inferred", numbers[3:])):
                ore, grade, metal = raw
                rows.append(
                    ResourceRow(
                        project="Tanami Operations",
                        table=table,
                        effective_date="2018-12-31",
                        basis=basis,
                        cutoff="",
                        category=category,
                        commodity="Au",
                        ore_mt=float(ore.replace(",", "")) / 1000,
                        grade=float(grade),
                        grade_unit="g/t",
                        contained_metal=float(metal.replace(",", "")) * 1000,
                        contained_unit="oz",
                        evidence={
                            "page": page,
                            "row_quote": quote,
                            "header_quote": header,
                            "category_quote": category + " Resource",
                            "ore_raw": ore,
                            "ore_unit": "kt",
                            "grade_raw": grade,
                            "contained_raw": metal,
                            "contained_raw_unit": "koz",
                        },
                    )
                )
    candidate = Candidate(reporting_standard="NI43-101", rows=rows, unresolved=[])
    settings = Settings(
        RoleConfig("https://extractor.test/v1", "extractor", "synthetic"),
        RoleConfig("https://critic.test/v1", "critic", "synthetic"),
        evolution_log=tmp_path / "evolution.jsonl",
    )
    return candidate, document, settings


class BlindApproval:
    def __init__(self, candidate):
        self.candidate = candidate

    def complete(self, role, *args):
        return (
            self.candidate
            if role == "extractor"
            else Critique(
                score=10,
                approved=True,
                complete=True,
                issues=[],
            )
        )


def test_actual_merged_table_positive_control_still_accepts_all_12_rows(control):
    candidate, document, settings = control
    assert validate_candidate(candidate, document, False) == []
    assert audit(document, settings, BlindApproval(candidate)).status == "accepted"


@pytest.mark.parametrize("index", range(12))
def test_each_merged_category_row_omission_abstains_despite_critic_10(control, index):
    candidate, document, settings = control
    candidate.rows.pop(index)
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain"
    assert any("missing_explicit_resource_row" in issue for issue in result.attempts[0].rule_issues)


@pytest.mark.parametrize("mistake", ["category", "million_scale", "measured", "combined", "total"])
def test_merged_column_binding_and_multiplier_rules_cannot_be_bypassed(control, mistake):
    candidate, document, settings = control
    row = candidate.rows[0]
    if mistake == "category":
        row.category, row.evidence.category_quote = "Inferred", "Inferred Resource"
    elif mistake == "million_scale":
        row.evidence.ore_unit, row.evidence.contained_raw_unit = "t", "oz"
        row.ore_mt /= 1e6
        row.contained_metal /= 1e6
    elif mistake in {"measured", "combined"}:
        ore, grade, metal = (
            ("400", "3.15", "40") if mistake == "measured" else ("4,200", "4.57", "610")
        )
        row.evidence.ore_raw, row.evidence.grade_raw, row.evidence.contained_raw = ore, grade, metal
        row.ore_mt, row.grade, row.contained_metal = (
            float(ore.replace(",", "")) / 1000,
            float(grade),
            float(metal) * 1000,
        )
    else:
        candidate.rows[4].basis = "total"
    result = audit(document, settings, BlindApproval(candidate))
    assert result.status == "abstain" and result.attempts[0].rule_issues


@pytest.mark.parametrize(
    "html",
    [
        '<table><tr><td colspan="0">Indicated</td></tr></table>',
        '<table><tr><td rowspan="-1">Indicated</td></tr></table>',
        '<table><tr><td rowspan="2">A</td><td>B</td></tr></table>',
        "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>",
        "<table><tr><td><table><tr><td>A</td></tr></table></td></tr></table>",
        "<tr><td>Indicated</td></tr>",
    ],
)
def test_invalid_table_structure_is_blocking_not_guessed(control, monkeypatch, html):
    _, _, settings = control
    with pytest.raises(ValueError):
        table_views(html)
    monkeypatch.setattr(
        "resource_auditor.pdf.parse_mineru", lambda *a: ({1: html}, "4.0.11", 1, [])
    )
    doc = read_pdf(b"%PDF-test", "unknown.pdf", replace(settings, mineru_python="test"), "1")
    assert "page[1]:table_structure_unverified" in doc.issues
