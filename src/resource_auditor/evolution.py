import json
import threading
from datetime import UTC, datetime
from pathlib import Path

_lock = threading.Lock()


def append_event(path: Path, event: dict):
    # ponytail: single-process append lock; use a database before multi-worker deployment.
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(UTC).isoformat(), **event}
    with _lock, path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Evolution log exceeds the 20 MB replay limit")
    records = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError()
                records.append(item)
            except ValueError:
                raise ValueError(f"Invalid evolution JSONL at line {line_no}") from None
    return records


def few_shots(path: Path, exclude_document: str, limit: int = 3) -> list[dict]:
    examples = []
    seen = set()
    for event in reversed(read_events(path)):
        doc_id = event.get("document_id")
        if (
            event.get("kind") != "resolution"
            or doc_id == exclude_document
            or doc_id in seen
            or not event.get("after")
            or not event.get("verified_by_rules_and_critic")
        ):
            continue
        example = {k: event[k] for k in ("before", "feedback", "after") if k in event}
        if len(json.dumps(example)) <= 12000:
            examples.append(example)
            seen.add(doc_id)
        if len(examples) >= limit:
            break
    return list(reversed(examples))
