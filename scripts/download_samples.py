"""Fetch public reports with bounded size and pinned SHA-256; no API keys."""

import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sources = json.loads((ROOT / "examples/public_sources.json").read_text(encoding="utf-8"))
    target = ROOT / "data/public"
    target.mkdir(parents=True, exist_ok=True)
    for item in sources:
        path = target / item["file"]
        if path.exists():
            data = path.read_bytes()
        else:
            request = urllib.request.Request(
                item["url"], headers={"User-Agent": "ResourceAuditor/0.1 public-report-research"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(40 * 1024 * 1024 + 1)
        if len(data) > 40 * 1024 * 1024 or not data.startswith(b"%PDF-"):
            raise ValueError(f"{item['id']}: not a PDF within 40 MB")
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(
                f"{item['id']}: SHA-256 changed; verify source before updating annotations"
            )
        if not path.exists():
            path.write_bytes(data)
        print(f"{item['id']}: verified {len(data)} bytes, PDF pages {item['pages']}")


if __name__ == "__main__":
    main()
