import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from resource_auditor import api
from resource_auditor.evaluation import evaluate, load_truth
from resource_auditor.models import AuditResult, ResourceRow


def mixed_input():
    raw = {
        "metadata": {"sha256": "mixed-report-sha"},
        "items": [
            {
                "class": "Indicated",
                "metal": "Au",
                "tonnage_mt": 10,
                "grade": 1,
                "contained": 321500,
            },
            {"class": "Inferred", "metal": "Cu", "tonnage_mt": 10, "grade": 1, "contained": 100000},
        ],
    }
    mapping = {
        "rows_key": "items",
        "document_id_key": "metadata.sha256",
        "fields": {
            "category": "class",
            "commodity": "metal",
            "ore_mt": "tonnage_mt",
            "grade": "grade",
            "contained_metal": "contained",
        },
        "unit_defaults_by_commodity": {
            "Au": {"grade_unit": "g/t", "contained_unit": "oz"},
            "Cu": {"grade_unit": "%", "contained_unit": "t"},
        },
    }
    return raw, mapping


def mixed_result():
    rows = []
    for category, commodity, metal, grade_unit, contained_unit in (
        ("Indicated", "Au", 321500, "g/t", "oz"),
        ("Inferred", "Cu", 100000, "%", "t"),
    ):
        rows.append(
            ResourceRow(
                project="Mixed",
                table="Table 1",
                effective_date="unknown",
                basis="total",
                cutoff="",
                category=category,
                commodity=commodity,
                ore_mt=10,
                grade=1,
                grade_unit=grade_unit,
                contained_metal=metal,
                contained_unit=contained_unit,
                evidence={
                    "page": 1,
                    "row_quote": f"{category} 10 1 {metal}",
                    "header_quote": "Tonnes Grade Metal",
                    "ore_raw": "10",
                    "ore_unit": "Mt",
                    "grade_raw": "1",
                    "contained_raw": str(metal),
                    "contained_raw_unit": contained_unit,
                },
            )
        )
    return AuditResult(
        document_id="mixed-report-sha",
        filename="mixed.pdf",
        status="accepted",
        requires_manual_review=False,
        selected_pages=[1],
        selection_scope="explicit",
        reasons=[],
        rows=rows,
        attempts=[],
        models={"extractor": "a", "critic": "b"},
    )


def test_mixed_commodity_unit_defaults_score_through_api_without_models(monkeypatch):
    raw, mapping = mixed_input()
    mapping["defaults"] = {"grade_unit": "%", "contained_unit": "t"}
    monkeypatch.setattr(
        api.Settings, "from_env", lambda: pytest.fail("GT mapping touched model configuration")
    )
    with TestClient(api.app) as client:
        response = client.post(
            "/evaluate",
            files={
                "result": ("result.json", mixed_result().model_dump_json(), "application/json"),
                "truth": ("truth.json", json.dumps(raw), "application/json"),
            },
            data={"mapping": json.dumps(mapping)},
        )
    assert response.status_code == 200
    assert response.json()["correct_fields"] == 6
    assert response.json()["coverage"] == 1


def test_explicit_units_override_commodity_and_global_defaults_without_conversion():
    raw, mapping = mixed_input()
    raw["items"][0].update({"grade_unit": "%", "contained_unit": "t"})
    raw["items"][1].update({"grade_unit": "%", "contained_unit": "t"})
    mapping["fields"].update({"grade_unit": "grade_unit", "contained_unit": "contained_unit"})
    mapping["defaults"] = {"grade_unit": "g/t", "contained_unit": "oz"}
    truth = load_truth(json.dumps(raw).encode(), mapping)
    assert truth.rows[0].grade_unit == "%" and truth.rows[0].contained_unit == "t"
    assert truth.rows[0].contained_metal == 321500
    assert evaluate(mixed_result(), truth)["correct_fields"] == 4


def test_commodity_defaults_preserve_hash_and_do_not_guess_missing_units():
    raw, mapping = mixed_input()
    truth = load_truth(json.dumps(raw).encode(), mapping)
    assert truth.document_id == "mixed-report-sha"
    wrong = mixed_result().model_copy(update={"document_id": "other-pdf"})
    with pytest.raises(ValueError, match="hash"):
        evaluate(wrong, truth)
    del mapping["unit_defaults_by_commodity"]["Cu"]
    with pytest.raises(ValueError):
        load_truth(json.dumps(raw).encode(), mapping)


@pytest.mark.parametrize(
    "invalid", [[], {"Au": []}, {"Au": {"ore_mt": 999}}, {"Unknown": {"grade_unit": "%"}}]
)
def test_unit_defaults_cannot_inject_numeric_answers_or_unknown_configuration(invalid):
    raw, mapping = mixed_input()
    mapping["unit_defaults_by_commodity"] = deepcopy(invalid)
    with pytest.raises(ValueError, match="unit_defaults_by_commodity"):
        load_truth(json.dumps(raw).encode(), mapping)
