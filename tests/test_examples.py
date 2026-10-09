import json
from pathlib import Path

from resource_auditor.checks import row_key
from resource_auditor.models import GroundTruth


def test_public_annotations_have_verified_identity_and_distinct_rows():
    root = Path(__file__).resolve().parents[1] / "examples"
    sources = json.loads((root / "public_sources.json").read_text(encoding="utf-8"))
    assert len(sources) == 3
    for source in sources:
        truth = GroundTruth.model_validate_json((root / source["truth"]).read_bytes())
        assert truth.document_id == source["sha256"]
        assert len({row_key(row) for row in truth.rows}) == len(truth.rows)
        assert truth.source["pdf_pages"] == [int(source["pages"])]
        assert truth.source["annotation_method"].startswith("Manual transcription")
