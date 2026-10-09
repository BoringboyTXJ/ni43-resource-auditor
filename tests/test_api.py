import json

import pytest
from fastapi.testclient import TestClient

from resource_auditor import api
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.models import Attempt, AuditResult, Candidate, Critique, ResourceRow


@pytest.fixture
def result():
    row = ResourceRow(
        project="Example",
        table="Table 1",
        effective_date="unknown",
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
            "header_quote": "Tonnes Mt Cu % Copper Mt",
            "ore_raw": "100",
            "ore_unit": "Mt",
            "grade_raw": "1.00",
            "contained_raw": "1.0",
            "contained_raw_unit": "Mt",
        },
    )
    return AuditResult(
        document_id="sha",
        filename="report.pdf",
        status="accepted",
        requires_manual_review=False,
        selected_pages=[1],
        selection_scope="explicit",
        reasons=[],
        rows=[row],
        attempts=[
            Attempt(
                round=1,
                candidate=Candidate(reporting_standard="NI43-101", rows=[row], unresolved=[]),
                critique=Critique(score=9, approved=True, complete=True, issues=[]),
            )
        ],
        models={"extractor": "a", "critic": "b"},
    )


def test_upload_ui_and_openapi_contract_are_available():
    with TestClient(api.app) as client:
        assert client.get("/").status_code == 200
        schema = client.get("/openapi.json").json()
        assert "/extract" in schema["paths"] and "/evaluate" in schema["paths"]
        assert "GroundTruth" in client.get("/schema").json()["ground_truth"]["title"]


def test_evaluate_does_not_initialize_or_call_models(monkeypatch, result):
    monkeypatch.setattr(
        api.Settings, "from_env", lambda: pytest.fail("evaluation touched model config")
    )
    monkeypatch.setattr(api, "audit", lambda *a, **k: pytest.fail("evaluation called a model"))
    truth = {"rows": [result.rows[0].model_dump(exclude={"evidence"})]}
    with TestClient(api.app) as client:
        response = client.post(
            "/evaluate",
            files={
                "result": ("result.json", result.model_dump_json(), "application/json"),
                "truth": ("truth.json", json.dumps(truth), "application/json"),
            },
        )
    assert response.status_code == 200
    assert response.json()["field_accuracy_all"] == 1


def test_extract_missing_keys_returns_clear_configuration_error(monkeypatch):
    settings = Settings(RoleConfig("", "", ""), RoleConfig("", "", ""))
    monkeypatch.setattr(api.Settings, "from_env", lambda: settings)
    with TestClient(api.app) as client:
        response = client.post(
            "/extract", files={"file": ("report.pdf", b"%PDF-", "application/pdf")}
        )
    assert response.status_code == 503
    assert "API key" in response.json()["detail"]


def test_invalid_pdf_and_invalid_truth_are_rejected(monkeypatch, tmp_path):
    settings = Settings(
        RoleConfig("https://a.test/v1", "a", "test"),
        RoleConfig("https://b.test/v1", "b", "test"),
        evolution_log=tmp_path / "log",
    )
    monkeypatch.setattr(api.Settings, "from_env", lambda: settings)
    with TestClient(api.app) as client:
        assert client.post("/extract", files={"file": ("fake.pdf", b"not pdf")}).status_code == 422
        assert (
            client.post(
                "/evaluate", files={"result": ("a.json", "bad"), "truth": ("b.json", "bad")}
            ).status_code
            == 422
        )


def test_request_concurrency_limit_is_enforced(monkeypatch, tmp_path):
    settings = Settings(
        RoleConfig("https://a.test/v1", "a", "test"),
        RoleConfig("https://b.test/v1", "b", "test"),
        evolution_log=tmp_path / "log",
    )
    monkeypatch.setattr(api.Settings, "from_env", lambda: settings)
    api._slots.acquire()
    api._slots.acquire()
    try:
        with TestClient(api.app) as client:
            response = client.post("/extract", files={"file": ("a.pdf", b"%PDF-")})
        assert response.status_code == 429
    finally:
        api._slots.release()
        api._slots.release()
