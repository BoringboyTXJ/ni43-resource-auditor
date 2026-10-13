import json

from .checks import identity_text, row_key
from .models import AuditResult, GroundTruth

FIELDS = ("ore_mt", "grade", "contained_metal")


def within_tolerance(predicted: float, expected: float, tolerance: float = 0.05) -> bool:
    # Zero has no relative interval: only a near-zero prediction is correct.
    boundary = max(abs(expected) * tolerance, 1e-9)
    return abs(predicted - expected) <= boundary + 1e-12 * max(1, abs(expected))


def load_truth(raw: bytes, mapping: dict | None = None) -> GroundTruth:
    data = json.loads(raw)
    if mapping:
        # Explicit mappings only: do not guess unknown assessor field names or units.
        source_rows = data if isinstance(data, list) else data[mapping.get("rows_key", "rows")]
        field_map = mapping["fields"]

        def value(row, path):
            for part in path.split("."):
                row = row[part]
            return row

        metadata = (
            {key: data[key] for key in ("schema_version", "document_id", "source") if key in data}
            if isinstance(data, dict)
            else {}
        )
        if path := mapping.get("document_id_key"):
            mapped_id = value(data, path)
            if metadata.get("document_id") and mapped_id != metadata["document_id"]:
                raise ValueError("Conflicting ground truth document hash mappings")
            metadata["document_id"] = mapped_id
        data = {
            **metadata,
            "rows": [
                {target: value(row, path) for target, path in field_map.items()}
                for row in source_rows
            ],
        }
        defaults = mapping.get("defaults", {})
        unit_defaults = mapping.get("unit_defaults_by_commodity", {})
        if not isinstance(unit_defaults, dict) or any(
            commodity not in {"Au", "Cu", "Li2O"}
            or not isinstance(units, dict)
            or set(units) - {"grade_unit", "contained_unit"}
            for commodity, units in unit_defaults.items()
        ):
            raise ValueError(
                "unit_defaults_by_commodity must map supported commodities to unit fields only"
            )
        data["rows"] = [
            {
                **defaults,
                **unit_defaults.get(row.get("commodity", defaults.get("commodity")), {}),
                **row,
            }
            for row in data["rows"]
        ]
    return GroundTruth.model_validate(data)


def evaluate(result: AuditResult, truth: GroundTruth, tolerance: float = 0.05) -> dict:
    if not 0 <= tolerance <= 0.05:
        raise ValueError("Evaluation tolerance must be 0..0.05")
    if truth.document_id and truth.document_id != result.document_id:
        raise ValueError("Ground truth document hash does not match the PDF result")
    expected = {row_key(row): row for row in truth.rows}
    if len(expected) != len(truth.rows):
        raise ValueError("Duplicate keys in ground truth; specify date/table/basis/cutoff")
    predictions = result.rows if result.status == "accepted" else []
    seen = set()
    matched = 0
    correct = 0
    extras = 0
    details = []

    def matching_target(row):
        matches = []
        for key, target in expected.items():
            if any(
                identity_text(f, getattr(row, f)) != identity_text(f, getattr(target, f))
                for f in ("category", "commodity")
            ):
                continue
            if any(
                getattr(target, f)
                and identity_text(f, getattr(row, f)) != identity_text(f, getattr(target, f))
                for f in ("project", "table", "effective_date", "basis", "cutoff")
            ):
                continue
            matches.append((key, target))
        if len(matches) > 1:
            raise ValueError("Ambiguous ground truth identity: add project/table/date/basis/cutoff")
        return matches[0] if matches else (None, None)

    for row in predictions:
        key, target = matching_target(row)
        if target is None or key in seen:
            extras += 1
            details.append({"key": list(row_key(row)), "error": "unexpected_or_duplicate_row"})
            continue
        seen.add(key)
        matched += 1
        checks = {
            f: within_tolerance(getattr(row, f), getattr(target, f), tolerance) for f in FIELDS
        }
        checks["grade"] &= row.grade_unit == target.grade_unit
        checks["contained_metal"] &= row.contained_unit == target.contained_unit
        correct += sum(checks.values())
        details.append({"key": list(key), "fields": checks})
    total_fields = len(truth.rows) * len(FIELDS) + extras * len(FIELDS)
    accepted_fields = len(predictions) * len(FIELDS)

    def candidate_is_wrong(last_rows):
        candidate_wrong = len(last_rows) != len(truth.rows)
        candidate_seen = set()
        for row in last_rows:
            key, target = matching_target(row)
            if target is None or key in candidate_seen:
                candidate_wrong = True
                continue
            candidate_seen.add(key)
            candidate_wrong |= (
                row.grade_unit != target.grade_unit
                or row.contained_unit != target.contained_unit
                or not all(
                    within_tolerance(getattr(row, f), getattr(target, f), tolerance) for f in FIELDS
                )
            )
        return candidate_wrong

    valid_attempts = [attempt for attempt in result.attempts if attempt.candidate is not None]
    candidate_wrong = (
        candidate_is_wrong(valid_attempts[-1].candidate.rows) if valid_attempts else False
    )
    any_wrong = any(candidate_is_wrong(attempt.candidate.rows) for attempt in valid_attempts)
    return {
        "status": result.status,
        "tolerance": tolerance,
        "field_accuracy_all": correct / total_fields,
        "accepted_field_accuracy": correct / accepted_fields if accepted_fields else None,
        "coverage": matched / len(truth.rows),
        "correct_fields": correct,
        "total_fields": total_fields,
        "missing_rows": len(truth.rows) - matched,
        "unexpected_rows": extras,
        "wrong_candidate_detected": candidate_wrong,
        "wrong_candidate_rejected": result.status == "abstain" if candidate_wrong else None,
        "valid_candidate_attempts": len(valid_attempts),
        "last_valid_candidate_round": valid_attempts[-1].round if valid_attempts else None,
        "any_wrong_candidate_detected": any_wrong,
        "wrong_candidate_revised": result.status == "accepted"
        and any_wrong
        and not candidate_wrong,
        "details": details,
        "note": "Ground truth is used only here, never in model prompts or evolution few-shots.",
    }
