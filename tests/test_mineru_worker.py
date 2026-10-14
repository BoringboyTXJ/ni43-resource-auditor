import json
from pathlib import Path

from resource_auditor import mineru_worker as worker

FIXTURES = Path(__file__).parent / "fixtures"


def test_actual_unclear_oxide_pages_receive_vision_reparse_without_another_pdf_parser(monkeypatch):
    f = json.loads((FIXTURES / "pilbara_mineru.json").read_text(encoding="utf-8"))
    before = {p: f["basic_pages"][str(p)] for p in [2, 5]}
    refined = {int(p): t for p, t in f["pages"].items()}
    calls = []

    def parse(source, **kwargs):
        calls.append(kwargs)
        return refined

    monkeypatch.setattr(worker, "render_pages", lambda result: result)
    pages, notes = worker.refine_pages("test.pdf", before, [2, 5], "basic", parse)
    assert calls == [
        {"tier": "advanced", "ocr_mode": "ocr", "image_analysis": False, "page_range": "2,5"}
    ]
    assert pages == refined and notes == ["mineru_advanced_reparse: 2,5"]


def test_real_conflicting_mining_method_span_is_never_adopted(monkeypatch):
    f = json.loads((FIXTURES / "kinross_mineru.json").read_text(encoding="utf-8"))
    before = {155: f["pages"]["155"]}
    refined = {155: f["advanced_pages"]["155"]}
    monkeypatch.setattr(worker, "render_pages", lambda result: result)
    pages, notes = worker.refine_pages(
        "test.pdf", before.copy(), [155], "basic", lambda *a, **k: refined
    )
    assert pages == before
    assert notes == ["mineru_resource_structure_still_unverified: 155"]


def test_vision_failure_keeps_original_uncertainty_and_sanitizes_details(monkeypatch):
    f = json.loads((FIXTURES / "kinross_mineru.json").read_text(encoding="utf-8"))
    before = {155: f["pages"]["155"]}

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_SECRET_SENTINEL")

    pages, notes = worker.refine_pages("test.pdf", before.copy(), [155], "basic", fail)
    assert pages == before and notes == [
        "mineru_advanced_reparse_failed: resource structure remains unverified"
    ]
    assert "PRIVATE_PROVIDER_SECRET_SENTINEL" not in str(notes)


def test_verified_rows_keep_multiplicity_during_vision_reparse():
    source = """| Location | Category | Tonnes (Mt) | Au (g/t) | Gold (koz) |
| --- | --- | --- | --- | --- |
| A | Indicated | 10 | 1.00 | 321.5 |
| B | Indicated | 10 | 1.00 | 321.5 |"""
    partial = source.rsplit("\n", 1)[0]
    assert sum(worker.known_rows({1: source}, 1).values()) == 2
    assert not worker.known_rows({1: source}, 1) <= worker.known_rows({1: partial}, 1)
