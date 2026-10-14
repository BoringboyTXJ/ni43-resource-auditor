import json
import re
import subprocess
import tempfile
from pathlib import Path


def parse_pages(spec: str, count: int) -> list[int]:
    result = set()
    for item in spec.split(","):
        if not re.fullmatch(r"\s*\d+(?:\s*-\s*\d+)?\s*", item):
            raise ValueError("Pages must look like 5,12-14 (one-based PDF pages)")
        parts = [int(x) for x in item.split("-")]
        start, end = parts[0], parts[-1]
        if not 1 <= start <= end <= count:
            raise ValueError(f"Page range must be within 1..{count}")
        result.update(range(start, end + 1))
    return sorted(result)


def parse_mineru(
    data: bytes, settings, spec: str = ""
) -> tuple[dict[int, str], str, int, list[str]]:
    if not settings.mineru_python:
        raise ValueError("mineru_not_configured")
    with tempfile.TemporaryDirectory(prefix="resource-mineru-") as folder:
        source, output = Path(folder) / "input.pdf", Path(folder) / "parsed.json"
        source.write_bytes(data)
        try:
            subprocess.run(
                [
                    settings.mineru_python,
                    str(Path(__file__).with_name("mineru_worker.py")),
                    str(source),
                    str(output),
                    settings.mineru_tier,
                    spec,
                ],
                check=True,
                timeout=settings.mineru_timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if output.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("mineru_output_too_large")
            result = json.loads(output.read_text(encoding="utf-8"))
            count = result["page_count"]
            if type(count) is not int or not 1 <= count <= 1000:
                raise ValueError("mineru_invalid_page_count_or_page_limit")
            selected = (
                sorted(set(parse_pages(spec, count)) | set(range(1, min(10, count) + 1)))
                if spec
                else list(range(1, count + 1))
            )
            pages = {int(k): v for k, v in result["pages"].items()}
            if not all(isinstance(v, str) for v in pages.values()):
                raise ValueError("mineru_invalid_page_text")
            if not set(selected).issubset(pages):
                raise ValueError("mineru_missing_requested_pages")
            if set(pages) - set(selected):
                raise ValueError("mineru_unexpected_page_numbers")
            installed = result["version"]
            if not isinstance(installed, str) or not re.fullmatch(r"4\.\d+\.\d+", installed):
                raise ValueError("mineru_invalid_version")
            notes = result.get("notes", [])
            if (
                not isinstance(notes, list)
                or len(notes) > 100
                or not all(isinstance(note, str) and len(note) <= 2000 for note in notes)
            ):
                raise ValueError("mineru_invalid_parser_notes")
            return pages, installed, count, notes
        except subprocess.TimeoutExpired:
            raise ValueError("mineru_timeout") from None
        except (OSError, subprocess.CalledProcessError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError(
                "mineru_failed: check the separate runtime and downloaded models"
            ) from None
