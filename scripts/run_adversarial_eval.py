"""Inject an obvious error into a real accepted candidate; use a live critic.

This is a controlled fault test, not an end-to-end model accuracy benchmark.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from resource_auditor.cli import save
from resource_auditor.config import Settings
from resource_auditor.engine import audit
from resource_auditor.evaluation import evaluate, load_truth
from resource_auditor.llm import ModelClient
from resource_auditor.models import AuditResult
from resource_auditor.pdf import read_pdf

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="Public manifest case ID with an accepted model result")
    parser.add_argument(
        "--fault",
        choices=("ore-scale", "coupled-units", "category", "omission"),
        default="ore-scale",
    )
    parser.add_argument("--result-dir", type=Path, default=ROOT / "artifacts/public")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sources = json.loads((ROOT / "examples/public_sources.json").read_text(encoding="utf-8"))
    source = next((s for s in sources if s["id"] == args.case), None)
    if source is None:
        parser.error("Case not found in public manifest")
    original = AuditResult.model_validate_json(
        (args.result_dir / (args.case + ".result.json")).read_bytes()
    )
    if original.status != "accepted":
        parser.error("First obtain a real accepted result; cannot inject a missing candidate")
    candidate = deepcopy(original.attempts[-1].candidate)
    if args.fault == "ore-scale":
        candidate.rows[0].ore_mt *= 1000
        fault = "First row ore_mt multiplied by 1000; original source evidence unchanged"
    elif args.fault == "coupled-units":
        row = next(
            (
                r
                for r in candidate.rows
                if r.evidence.ore_unit == "Mt" and r.evidence.contained_raw_unit == "Mt"
            ),
            None,
        )
        if row is None:
            parser.error("This fault needs a real Mt ore / Mt contained-metal source row")
        row.evidence.ore_unit = row.evidence.contained_raw_unit = "t"
        row.ore_mt /= 1_000_000
        row.contained_metal /= 1_000_000
        fault = "One Mt/Mt row relabelled t/t; both normalized masses divided by a million, arithmetic preserved"
    elif args.fault == "category":
        row = next(
            (
                r
                for r in candidate.rows
                if r.category == "Inferred" and "Inferred" in r.evidence.row_quote
            ),
            None,
        )
        if row is None or not any("Indicated" in r.evidence.row_quote for r in candidate.rows):
            parser.error("This fault needs literal row-oriented Indicated and Inferred categories")
        row.category = "Indicated"
        row.evidence.category_quote = "Indicated"
        fault = "An explicit Inferred row relabelled Indicated using a label from elsewhere on the page; may also duplicate identity"
    else:
        if len(candidate.rows) < 2:
            parser.error("This fault needs multiple eligible rows")
        candidate.rows.pop(0)
        fault = "First eligible resource row omitted; remaining source evidence unchanged"
    settings = Settings.from_env()
    live = ModelClient(settings)

    class FaultClient:
        def complete(self, role, system, payload, schema):
            if role == "extractor":
                return deepcopy(candidate)
            print("  calling live critic", flush=True)
            return live.complete(role, system, payload, schema)

    pdf = ROOT / "data/public" / source["file"]
    doc = read_pdf(pdf.read_bytes(), pdf.name, settings, source["pages"])
    if doc.document_id != original.document_id or doc.document_id != source["sha256"]:
        parser.error("PDF identity changed")
    result = audit(
        doc,
        settings,
        FaultClient(),
        allow_jorc=source["standard"] == "JORC",
        use_few_shots=False,
        log_events=False,
    )
    truth = load_truth((ROOT / "examples" / source["truth"]).read_bytes())
    report = {
        "case": args.case,
        "mode": "controlled_fault_injection_with_live_critic",
        "fault": fault,
        "fault_id": args.fault,
        "extractor_calls": "Replayed corrupted real candidate; no new extractor model calls",
        "models": result.models,
        "critic_output_options": {
            "json_schema": settings.critic.json_schema,
            "enable_thinking": settings.critic.enable_thinking,
        },
        "status": result.status,
        "rounds": len(result.attempts),
        "critic_scores": [a.critique.score if a.critique else None for a in result.attempts],
        "critiques": [a.critique.model_dump() if a.critique else None for a in result.attempts],
        "rule_issues": [a.rule_issues for a in result.attempts],
        "errors": [a.error for a in result.attempts],
        "metrics": evaluate(result, truth),
    }
    output = args.output or ROOT / "artifacts" / f"adversarial-{args.case}-{args.fault}.json"
    save(output, report)
    save(output.with_suffix(".result.json"), result.model_dump())
    print(json.dumps({k: v for k, v in report.items() if k != "metrics"}, ensure_ascii=False))
    if result.status != "abstain" or report["metrics"]["wrong_candidate_rejected"] is not True:
        raise SystemExit("Obvious injected error was not rejected")
    if len(result.attempts) != 3 or any(a.critique is None for a in result.attempts):
        raise SystemExit("Rejection occurred, but three live critic rounds were not verified")


if __name__ == "__main__":
    main()
