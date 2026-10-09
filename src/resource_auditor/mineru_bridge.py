import json
import subprocess
import tempfile
from pathlib import Path


def parse_mineru(data: bytes, settings, selected: list[int]) -> tuple[dict[int, str], str]:
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
                    ",".join(map(str, selected)),
                ],
                check=True,
                timeout=settings.mineru_timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if output.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("mineru_output_too_large")
            result = json.loads(output.read_text(encoding="utf-8"))
            pages = {int(k): v for k, v in result["pages"].items()}
            if not all(isinstance(v, str) for v in pages.values()):
                raise ValueError("mineru_invalid_page_text")
            if not set(selected).issubset(pages):
                raise ValueError("mineru_missing_requested_pages")
            if set(pages) - set(selected):
                raise ValueError("mineru_unexpected_page_numbers")
            return pages, str(result["version"])
        except subprocess.TimeoutExpired:
            raise ValueError("mineru_timeout") from None
        except (OSError, subprocess.CalledProcessError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError(
                "mineru_failed: check the separate runtime and downloaded models"
            ) from None
