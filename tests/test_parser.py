import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from resource_auditor import mineru_bridge
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.pdf import parse_pages, read_pdf, resource_pages

TEXT = "Mineral Resources Tonnes (Mt) Au (g/t) Gold (koz)\nIndicated 10 1.00 321.5"


@pytest.fixture
def settings():
    return Settings(
        RoleConfig("https://a.test/v1", "a", "test"), RoleConfig("https://b.test/v1", "b", "test")
    )


def stub_mineru(monkeypatch, texts, count=None):
    calls = []

    def parse(data, config, spec):
        calls.append(spec)
        return texts, "4.0.11", count or max(texts), []

    monkeypatch.setattr("resource_auditor.pdf.parse_mineru", parse)
    return calls


@pytest.mark.parametrize("kind", ["english", "chinese", "scan", "mixed"])
def test_every_pdf_uses_mineru_without_language_or_text_layer_routing(monkeypatch, settings, kind):
    calls = stub_mineru(monkeypatch, {1: "National Instrument 43-101 中文资源报告", 2: TEXT})
    doc = read_pdf(b"%PDF-test", kind + ".pdf", settings)
    assert calls == [""]
    assert doc.parser == "mineru:4.0.11" and doc.selected_pages == [2]
    assert not doc.issues and doc.tables[2] == []


def test_unconfigured_mineru_fails_closed_for_all_pdf_types(settings):
    doc = read_pdf(b"%PDF-test", "report.pdf", settings)
    assert "mineru_not_configured" in doc.issues
    assert doc.parser == "mineru" and not doc.pages and not doc.context


def test_selected_table_receives_standard_without_expanding_extraction_scope(monkeypatch, settings):
    calls = stub_mineru(monkeypatch, {1: "National Instrument 43-101 technical report", 2: TEXT})
    doc = read_pdf(b"%PDF-test", "unseen.pdf", settings, "2")
    assert calls == ["2"] and doc.selected_pages == [2]
    assert "[STANDARD EVIDENCE ONLY; NOT EXTRACTION SCOPE]" in doc.context
    assert "National Instrument 43-101" in doc.context and "[PDF PAGE 1]" not in doc.context


def test_mineru_failure_cannot_fallback_to_another_parser(monkeypatch, settings):
    def fail(*args):
        raise ValueError("mineru_failed")

    monkeypatch.setattr("resource_auditor.pdf.parse_mineru", fail)
    doc = read_pdf(b"%PDF-test", "text.pdf", settings, "1")
    assert doc.parser == "mineru" and doc.issues == [
        "mineru_failed",
        "page[1]:empty_mineru_evidence",
    ]
    assert not doc.pages


def test_full_document_discovery_is_not_limited_to_extraction_page_budget(monkeypatch, settings):
    texts = {i: "Non-resource report page" for i in range(1, 205)}
    texts[1], texts[147] = "NI 43-101", TEXT
    calls = stub_mineru(monkeypatch, texts)
    doc = read_pdf(b"%PDF-test", "large.pdf", settings)
    assert calls == [""] and doc.selected_pages == [147] and not doc.issues


def test_explicit_scope_over_budget_is_rejected_before_mineru(monkeypatch, settings):
    monkeypatch.setattr(
        "resource_auditor.pdf.parse_mineru", lambda *a: pytest.fail("unbounded parse")
    )
    doc = read_pdf(b"%PDF-test", "large.pdf", settings, "1-25")
    assert any("page_selection_overflow" in issue for issue in doc.issues)


def test_missing_page_blocks_even_when_another_page_has_valid_resources(monkeypatch, settings):
    stub_mineru(monkeypatch, {1: "NI 43-101", 2: TEXT}, 3)
    doc = read_pdf(b"%PDF-test", "partial.pdf", settings)
    assert "mineru_missing_requested_pages" in doc.issues


def test_empty_explicit_page_is_not_assumed_to_be_genuinely_blank(monkeypatch, settings):
    stub_mineru(monkeypatch, {1: "NI 43-101\n" + TEXT, 2: ""})
    doc = read_pdf(b"%PDF-test", "mixed.pdf", settings, "1-2")
    assert "page[2]:empty_mineru_evidence" in doc.issues and doc.selected_pages == [1, 2]


def test_legacy_auto_is_only_an_alias_and_removed_parser_is_rejected(monkeypatch, settings):
    calls = stub_mineru(monkeypatch, {1: "NI 43-101\n" + TEXT})
    assert read_pdf(
        b"%PDF-test", "report.pdf", replace(settings, pdf_parser="auto")
    ).parser.startswith("mineru:")
    with pytest.raises(ValueError, match="PDF_PARSER"):
        read_pdf(b"%PDF-test", "report.pdf", replace(settings, pdf_parser="pdfplumber"))
    assert calls == [""]


@pytest.mark.parametrize("spec", ["0", "4-2", "1,,2", "abc", "1001", "-1"])
def test_invalid_page_spec_is_rejected_before_invoking_mineru(monkeypatch, settings, spec):
    monkeypatch.setattr(
        "resource_auditor.pdf.parse_mineru", lambda *a: pytest.fail("invalid request")
    )
    with pytest.raises(ValueError):
        read_pdf(b"%PDF-test", "report.pdf", settings, spec)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"page_count": 1001}, "page_count"),
        ({"page_count": True}, "page_count"),
        ({"pages": {"1": "NI 43-101"}}, "missing_requested_pages"),
        ({"pages": {"1": "NI 43-101", "2": TEXT, "3": TEXT}}, "unexpected_page_numbers"),
        ({"pages": {"1": {}, "2": TEXT}}, "invalid_page_text"),
        ({"version": "not-a-version"}, "invalid_version"),
    ],
)
def test_worker_output_contract_rejects_partial_or_invalid_evidence(
    monkeypatch, settings, change, expected
):
    result = {
        "page_count": 2,
        "version": "4.0.11",
        "pages": {"1": "NI 43-101", "2": TEXT},
        **change,
    }

    def run(args, **kwargs):
        Path(args[3]).write_text(json.dumps(result), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mineru_bridge.subprocess, "run", run)
    with pytest.raises(ValueError, match=expected):
        mineru_bridge.parse_mineru(b"%PDF-test", replace(settings, mineru_python="test-runtime"))


def test_worker_explicit_page_contract_preserves_original_numbers(monkeypatch, settings):
    def run(args, **kwargs):
        assert args[-1] == "19"
        pages = {str(i): "NI 43-101" for i in range(1, 11)}
        pages["19"] = TEXT
        Path(args[3]).write_text(
            json.dumps({"page_count": 200, "version": "4.0.11", "pages": pages}), encoding="utf-8"
        )

    monkeypatch.setattr(mineru_bridge.subprocess, "run", run)
    texts, version, count, notes = mineru_bridge.parse_mineru(
        b"%PDF-test", replace(settings, mineru_python="test-runtime"), "19"
    )
    assert set(texts) == set(range(1, 11)) | {19} and version == "4.0.11" and count == 200
    assert parse_pages("19", count) == [19]


def test_newmont_native_table_windows_remain_a_historical_selection_control():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/newmont_selection.json").read_text(encoding="utf-8")
    )
    texts = {int(page): text for page, text in fixture["pages"].items()}
    texts[19] = "Mineral Resources are classified as Indicated and Inferred. See Table 14-5."
    assert resource_pages(texts, fixture["page_count"]) == fixture["expected_pages"]
