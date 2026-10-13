import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values


def test_compose_preserves_model_and_parser_settings(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose is required for the deployment configuration check")
    root = Path(__file__).resolve().parents[1]
    template = dotenv_values(root / ".env.example")
    expected = {
        "EXTRACTOR_BASE_URL": "https://extractor.test/v1",
        "EXTRACTOR_MODEL": "extractor",
        "EXTRACTOR_API_KEY": "synthetic-extractor-key",
        "CRITIC_BASE_URL": "https://critic.test/v1",
        "CRITIC_MODEL": "critic",
        "CRITIC_API_KEY": "synthetic-critic-key",
        "EXTRACTOR_JSON_SCHEMA": "true",
        "EXTRACTOR_ENABLE_THINKING": "false",
        "CRITIC_JSON_SCHEMA": "true",
        "CRITIC_ENABLE_THINKING": "true",
        "MAX_PDF_MB": "25",
        "MAX_CONTEXT_CHARS": "80000",
        "MAX_SELECTED_PAGES": "12",
        "PDF_PARSER": "mineru",
        "MINERU_PYTHON": "/opt/mineru/bin/python",
        "MINERU_TIER": "basic",
        "MINERU_TIMEOUT_SECONDS": "450",
    }
    env_file = tmp_path / "synthetic.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in expected.items()), encoding="utf-8")
    # Avoid loading developer credentials through process-level interpolation.
    child_env = {k: v for k, v in os.environ.items() if k not in template}
    result = subprocess.run(
        [docker, "compose", "--env-file", str(env_file), "config", "--format", "json"],
        cwd=root,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    service = json.loads(result.stdout)["services"]["auditor"]
    configured = service["environment"]
    assert set(template) <= set(configured)
    assert {key: configured.get(key) for key in expected} == expected
    assert configured["EVOLUTION_LOG"] == "/app/data/evolution.jsonl"
