from dataclasses import replace

import pytest

from resource_auditor.config import RoleConfig, Settings
from resource_auditor.pdf import read_pdf


@pytest.fixture
def settings():
    return Settings(
        RoleConfig("https://a.test/v1", "a", "test"), RoleConfig("https://b.test/v1", "b", "test")
    )


class Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self, **kwargs):
        return self.text

    def extract_tables(self):
        return []

    def close(self):
        pass


class PDF:
    def __init__(self, texts):
        self.pages = [Page(t) for t in texts]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def stub_pdf(monkeypatch, texts):
    monkeypatch.setattr("resource_auditor.pdf.pdfplumber.open", lambda *a, **k: PDF(texts))


def test_chinese_pdf_prioritizes_configured_mineru_and_keeps_original_page_numbers(
    monkeypatch, settings
):
    stub_pdf(
        monkeypatch,
        [
            "NI 43-101 中文资源报告\n这是用于测试中文复杂表格解析的资源报告",
            "Indicated Resources Mt 10",
        ],
    )
    calls = []

    def parse(data, config, selected):
        calls.append(selected)
        return {
            1: "NI 43-101 中文资源报告",
            2: "Mineral Resources Mt\nIndicated 10 1.00 321.5",
        }, "4.0.0"

    monkeypatch.setattr("resource_auditor.pdf.parse_mineru", parse)
    doc = read_pdf(b"%PDF-test", "chinese.pdf", replace(settings, mineru_python="python"), "2")
    assert calls == [[1, 2]]
    assert doc.parser == "mineru:4.0.0"
    assert doc.selected_pages == [2] and "[PDF PAGE 2]" in doc.context
    assert "321.5" in doc.pages[2] and not doc.issues


def test_unconfigured_mineru_fails_closed_for_scanned_pdf(monkeypatch, settings):
    stub_pdf(monkeypatch, ["", ""])
    doc = read_pdf(b"%PDF-test", "scan.pdf", settings, "2")
    assert "scanned_pdf_requires_mineru_or_manual_review" in doc.issues


def test_selected_table_receives_declared_standard_without_expanding_scope(monkeypatch, settings):
    stub_pdf(
        monkeypatch, ["National Instrument 43-101 technical report", "Indicated Resources Mt 10"]
    )
    doc = read_pdf(b"%PDF-test", "unseen.pdf", settings, "2")
    assert doc.selected_pages == [2]
    assert "[STANDARD EVIDENCE ONLY; NOT EXTRACTION SCOPE]" in doc.context
    assert "National Instrument 43-101" in doc.context
    assert "[PDF PAGE 1]" not in doc.context


def test_mineru_failure_can_fallback_only_to_existing_text_layer(monkeypatch, settings):
    stub_pdf(monkeypatch, ["NI 43-101 Mineral Resources Mt\nIndicated 10 1.00 321.5"])
    monkeypatch.setattr(
        "resource_auditor.pdf.parse_mineru",
        lambda *a: (_ for _ in ()).throw(ValueError("mineru_failed")),
    )
    doc = read_pdf(
        b"%PDF-test",
        "text.pdf",
        replace(settings, pdf_parser="mineru", mineru_python="python"),
        "1",
    )
    assert doc.parser == "pdfplumber"
    assert doc.parser_notes == ["mineru_failed; using original PDF text layer"]
    assert not doc.issues


def test_scan_over_page_budget_requires_explicit_scope_before_mineru(monkeypatch, settings):
    stub_pdf(monkeypatch, [""] * 25)
    monkeypatch.setattr(
        "resource_auditor.pdf.parse_mineru", lambda *a: pytest.fail("unbounded OCR")
    )
    doc = read_pdf(b"%PDF-test", "scan.pdf", replace(settings, mineru_python="python"))
    assert any("mineru_page_selection_required" in issue for issue in doc.issues)


def test_missing_mineru_page_is_not_silently_accepted(monkeypatch, settings):
    stub_pdf(monkeypatch, ["", ""])
    monkeypatch.setattr("resource_auditor.pdf.parse_mineru", lambda *a: ({1: "NI 43-101"}, "4.0.0"))
    doc = read_pdf(b"%PDF-test", "scan.pdf", replace(settings, mineru_python="python"), "2")
    assert "mineru_missing_requested_pages" in doc.issues


def test_mixed_pdf_ocr_failure_keeps_scan_as_blocking_issue(monkeypatch, settings):
    text = "NI 43-101 Mineral Resources Tonnes (Mt) Au (g/t) Gold (koz)\nIndicated 10 1.00 321.5"
    stub_pdf(monkeypatch, [text, ""])
    calls = []

    def fail(data, config, selected):
        calls.append(selected)
        raise ValueError("mineru_failed")

    monkeypatch.setattr("resource_auditor.pdf.parse_mineru", fail)
    doc = read_pdf(b"%PDF-test", "mixed.pdf", replace(settings, mineru_python="python"), "1-2")
    assert calls == [[1, 2]]
    assert "unverified_scan_pages: 2" in doc.issues
    assert doc.selected_pages == [1, 2]


def test_mixed_pdf_successful_ocr_preserves_selected_scope(monkeypatch, settings):
    text = "NI 43-101 Mineral Resources Tonnes (Mt) Au (g/t) Gold (koz)\nIndicated 10 1.00 321.5"
    stub_pdf(monkeypatch, [text, ""])
    parsed = {1: text, 2: text.replace("Indicated", "Inferred")}
    monkeypatch.setattr("resource_auditor.pdf.parse_mineru", lambda *a: (parsed, "4.0.11"))
    doc = read_pdf(b"%PDF-test", "mixed.pdf", replace(settings, mineru_python="python"), "1-2")
    assert doc.selected_pages == [1, 2] and not doc.issues
    assert "Inferred" in doc.pages[2] and doc.parser == "mineru:4.0.11"


def test_genuinely_blank_page_is_excluded_with_explicit_note(monkeypatch, settings):
    pdf = PDF(
        ["NI 43-101 Mineral Resources Tonnes (Mt) Au (g/t) Gold (koz)\nIndicated 10 1.00 321.5", ""]
    )
    for page in pdf.pages:
        page.images = []
    monkeypatch.setattr("resource_auditor.pdf.pdfplumber.open", lambda *a, **k: pdf)
    doc = read_pdf(b"%PDF-test", "blank.pdf", settings, "1-2")
    assert doc.selected_pages == [1] and not doc.issues
    assert doc.parser_notes == ["blank_pages_excluded: 2"]
