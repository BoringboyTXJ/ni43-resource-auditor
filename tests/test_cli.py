import hashlib
import json
import sys
from types import SimpleNamespace

from resource_auditor import cli
from resource_auditor.config import RoleConfig, Settings
from resource_auditor.evolution import append_event


def test_replay_preserves_scope_and_jorc_opt_in_without_changing_log(tmp_path, monkeypatch):
    pdf = tmp_path / "unseen.pdf"
    pdf.write_bytes(b"not sent to real parser")
    doc_id = hashlib.sha256(pdf.read_bytes()).hexdigest()
    log = tmp_path / "evolution.jsonl"
    append_event(
        log,
        {
            "kind": "failure",
            "document_id": doc_id,
            "selection_scope": "explicit",
            "selected_pages": [19],
            "allow_jorc": True,
        },
    )
    original = log.read_bytes()
    settings = Settings(
        RoleConfig("https://a.test/v1", "a", "test"),
        RoleConfig("https://b.test/v1", "b", "test"),
        evolution_log=log,
    )
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    parsed, audited = [], []

    def parse(data, name, config, pages):
        parsed.append(pages)
        return SimpleNamespace(document_id=doc_id)

    def audit(document, config, **options):
        audited.append(options)
        return SimpleNamespace(model_dump=lambda: {"status": "abstain"})

    monkeypatch.setattr(cli, "read_pdf", parse)
    monkeypatch.setattr(cli, "audit", audit)
    output = tmp_path / "replay.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resource-auditor",
            "replay",
            "--pdf-dir",
            str(tmp_path),
            "--log",
            str(log),
            "--output",
            str(output),
        ],
    )
    cli.main()
    assert parsed == ["19"]
    assert audited == [
        {"allow_jorc": True, "use_few_shots": False, "log_events": False},
        {"allow_jorc": True, "use_few_shots": True, "log_events": False},
    ]
    assert log.read_bytes() == original
    assert json.loads(output.read_text())["cases"][0]["few_shot_count"] == 0
