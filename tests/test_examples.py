import json
from pathlib import Path

from resource_auditor.checks import row_key
from resource_auditor.models import GroundTruth


def test_public_annotations_have_verified_identity_and_distinct_rows():
    root = Path(__file__).resolve().parents[1] / "examples"
    sources = json.loads((root / "public_sources.json").read_text(encoding="utf-8"))
    assert {source["id"] for source in sources} == {
        "barrick-reko-diq",
        "kinross-great-bear",
        "pilbara-jorc",
        "newmont-tanami",
    }
    for source in sources:
        truth = GroundTruth.model_validate_json((root / source["truth"]).read_bytes())
        assert truth.document_id == source["sha256"]
        assert len({row_key(row) for row in truth.rows}) == len(truth.rows)
        assert truth.source["pdf_pages"] == [int(source["pages"])]
        assert truth.source["annotation_method"].startswith("Manual transcription")
        if source.get("automatic_truth"):
            automatic = GroundTruth.model_validate_json(
                (root / source["automatic_truth"]).read_bytes()
            )
            assert automatic.document_id == source["sha256"]
            assert len({row_key(row) for row in automatic.rows}) == len(automatic.rows)
            assert set(truth.source["pdf_pages"]).issubset(automatic.source["pdf_pages"])
            assert all(row.table for row in automatic.rows)
