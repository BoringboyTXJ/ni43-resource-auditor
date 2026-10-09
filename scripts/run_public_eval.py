"""Real two-model benchmark. Only independently scored public development data."""

import json
import time
from pathlib import Path

from resource_auditor.cli import save
from resource_auditor.config import Settings
from resource_auditor.engine import audit
from resource_auditor.evaluation import evaluate, load_truth
from resource_auditor.llm import ModelClient
from resource_auditor.pdf import read_pdf

ROOT = Path(__file__).resolve().parents[1]


class ProgressClient(ModelClient):
    def complete(self, role, system, payload, schema):
        started = time.monotonic()
        print(f"  calling {role}: {getattr(self.settings, role).model}", flush=True)
        result = super().complete(role, system, payload, schema)
        print(f"  {role} returned valid schema in {time.monotonic() - started:.1f}s", flush=True)
        return result


def main():
    settings = Settings.from_env()
    settings.validate()
    reports = []
    sources = json.loads((ROOT / "examples/public_sources.json").read_text(encoding="utf-8"))
    for source in sources:
        started = time.monotonic()
        print(f"CASE {source['id']} PDF pages {source['pages']}", flush=True)
        path = ROOT / "data/public" / source["file"]
        doc = read_pdf(path.read_bytes(), path.name, settings, source["pages"])
        if doc.document_id != source["sha256"]:
            raise ValueError("Source changed; re-check annotation before evaluation")
        result = audit(
            doc,
            settings,
            ProgressClient(settings),
            allow_jorc=source["standard"] == "JORC",
            use_few_shots=False,
        )
        save(ROOT / "artifacts/public" / (source["id"] + ".result.json"), result.model_dump())
        # Ground truth is loaded only after extraction and independent review are finished.
        truth = load_truth((ROOT / "examples" / source["truth"]).read_bytes())
        metrics = evaluate(result, truth)
        save(ROOT / "artifacts/public" / (source["id"] + ".metrics.json"), metrics)
        reports.append(
            {
                "id": source["id"],
                "models": result.models,
                "parser": result.parser,
                "status": result.status,
                "rounds": len(result.attempts),
                "seconds": round(time.monotonic() - started, 1),
                "metrics": {k: v for k, v in metrics.items() if k != "details"},
            }
        )
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
        save(ROOT / "artifacts/public/summary.json", reports)


if __name__ == "__main__":
    main()
