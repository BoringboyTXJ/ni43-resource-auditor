"""Real two-model benchmark. Only independently scored public development data."""

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--automatic",
        action="store_true",
        help="Upload whole PDFs; score only if an independent reference covers selected pages",
    )
    parser.add_argument("--case", action="append", help="Public manifest case ID (repeatable)")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.validate()
    reports = []
    sources = json.loads((ROOT / "examples/public_sources.json").read_text(encoding="utf-8"))
    if args.case and not set(args.case).issubset({s["id"] for s in sources}):
        parser.error("Unknown manifest case")
    output = args.output_dir or ROOT / "artifacts" / ("automatic" if args.automatic else "public")
    for source in sources:
        if args.case and source["id"] not in args.case:
            continue
        started = time.monotonic()
        scope = "automatic" if args.automatic else source["pages"]
        print(f"CASE {source['id']} PDF pages {scope}", flush=True)
        path = ROOT / "data/public" / source["file"]
        doc = read_pdf(
            path.read_bytes(), path.name, settings, "" if args.automatic else source["pages"]
        )
        print(f"  selected={doc.selected_pages}; parser issues={doc.issues}", flush=True)
        if doc.document_id != source["sha256"]:
            raise ValueError("Source changed; re-check annotation before evaluation")
        result = audit(
            doc,
            settings,
            ProgressClient(settings),
            allow_jorc=source["standard"] == "JORC",
            use_few_shots=False,
        )
        save(output / (source["id"] + ".result.json"), result.model_dump())
        # Ground truth is loaded only after extraction and independent review are finished.
        metrics = None
        truth_scope = "Not scored: reference covers explicit pages only"
        truth_file = source.get("automatic_truth") if args.automatic else source["truth"]
        if truth_file:
            truth = load_truth((ROOT / "examples" / truth_file).read_bytes())
            if not args.automatic or truth.source.get("pdf_pages") == result.selected_pages:
                metrics = evaluate(result, truth)
                save(output / (source["id"] + ".metrics.json"), metrics)
                truth_scope = truth.source["pdf_pages"]
            else:
                truth_scope = "Not scored: automatic pages differ from annotated scope"
        reports.append(
            {
                "id": source["id"],
                "models": result.models,
                "reasoning_effort": {
                    role: getattr(settings, role).reasoning_effort
                    for role in ("extractor", "critic")
                },
                "output_options": {
                    role: {
                        "json_schema": getattr(settings, role).json_schema,
                        "enable_thinking": getattr(settings, role).enable_thinking,
                    }
                    for role in ("extractor", "critic")
                },
                "parser": result.parser,
                "selected_pages": result.selected_pages,
                "selection_scope": result.selection_scope,
                "status": result.status,
                "rounds": len(result.attempts),
                "accepted_rows": len(result.rows),
                "critic_scores": [
                    a.critique.score if a.critique else None for a in result.attempts
                ],
                "rule_issues": [a.rule_issues for a in result.attempts],
                "errors": [a.error for a in result.attempts],
                "reasons": result.reasons,
                "seconds": round(time.monotonic() - started, 1),
                "metrics": {k: v for k, v in metrics.items() if k != "details"}
                if metrics
                else None,
                "truth_scope": truth_scope,
            }
        )
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
        save(output / "summary.json", reports)


if __name__ == "__main__":
    main()
