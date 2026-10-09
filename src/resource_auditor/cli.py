import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .engine import audit
from .evaluation import evaluate, load_truth
from .evolution import few_shots, read_events
from .models import AuditResult
from .pdf import read_pdf


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Mineral resource adversarial auditor")
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="Extract and independently critique a PDF")
    extract.add_argument("pdf", type=Path)
    extract.add_argument("--pages", default="", help="One-based PDF pages, e.g. 12-14,20")
    extract.add_argument("--output", type=Path, default=Path("artifacts/result.json"))
    extract.add_argument("--allow-jorc", action="store_true")
    extract.add_argument("--no-few-shots", action="store_true")
    score = sub.add_parser("evaluate", help="Offline evaluation; no model API calls")
    score.add_argument("result", type=Path)
    score.add_argument("truth", type=Path)
    score.add_argument("--mapping", type=Path)
    score.add_argument("--output", type=Path, default=Path("artifacts/evaluation.json"))
    replay = sub.add_parser("replay", help="Compare baseline and cross-document few-shot replay")
    replay.add_argument("--pdf-dir", type=Path, required=True)
    replay.add_argument("--log", type=Path)
    replay.add_argument("--pages", default="")
    replay.add_argument("--allow-jorc", action="store_true")
    replay.add_argument("--limit", type=int, default=3)
    replay.add_argument("--output", type=Path, default=Path("artifacts/replay.json"))
    args = parser.parse_args()
    try:
        if args.command == "evaluate":
            result = AuditResult.model_validate_json(args.result.read_bytes())
            mapping = json.loads(args.mapping.read_text(encoding="utf-8")) if args.mapping else None
            report = evaluate(result, load_truth(args.truth.read_bytes(), mapping))
            save(args.output, report)
            print(
                json.dumps({k: v for k, v in report.items() if k != "details"}, ensure_ascii=False)
            )
            return
        settings = Settings.from_env()
        settings.validate()
        if args.command == "extract":
            document = read_pdf(args.pdf.read_bytes(), args.pdf.name, settings, args.pages)
            result = audit(
                document, settings, allow_jorc=args.allow_jorc, use_few_shots=not args.no_few_shots
            )
            save(args.output, result.model_dump())
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "rows": len(result.rows),
                        "rounds": len(result.attempts),
                        "output": str(args.output),
                    }
                )
            )
            return
        if not 1 <= args.limit <= 20:
            raise ValueError("Replay limit must be 1..20")
        if args.log:
            settings = replace(settings, evolution_log=args.log)
        events = read_events(args.log or settings.evolution_log)
        failures = {e["document_id"]: e for e in events if e.get("kind") == "failure"}
        report = []
        available = set()
        for path in sorted(args.pdf_dir.glob("*.pdf")):
            data = path.read_bytes()
            doc_id = hashlib.sha256(data).hexdigest()
            if doc_id not in failures:
                continue
            available.add(doc_id)
            if len(report) >= args.limit:
                continue
            event = failures[doc_id]
            scope = args.pages
            if not scope and event.get("selection_scope") == "explicit":
                scope = ",".join(map(str, event["selected_pages"]))
            allow_jorc = args.allow_jorc or event.get("allow_jorc", False) is True
            doc = read_pdf(data, path.name, settings, scope)
            baseline = audit(
                doc, settings, allow_jorc=allow_jorc, use_few_shots=False, log_events=False
            )
            # Replay is read-only: baseline output cannot train the augmented run.
            improved = audit(
                doc, settings, allow_jorc=allow_jorc, use_few_shots=True, log_events=False
            )
            report.append(
                {
                    "filename": path.name,
                    "baseline": baseline.model_dump(),
                    "with_few_shots": improved.model_dump(),
                    "few_shot_count": len(few_shots(settings.evolution_log, doc_id)),
                }
            )
        save(
            args.output,
            {
                "cases": report,
                "missing_document_ids": sorted(set(failures) - available),
                "skipped_by_limit": len(available) - len(report),
                "note": "Acceptance is not accuracy. Evaluate both outputs with independently verified truth.",
            },
        )
        print(json.dumps({"replayed_cases": len(report), "output": str(args.output)}))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
