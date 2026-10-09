"""Run with a separate MinerU 4.x interpreter; the base app stays lightweight."""

import json
import sys
from importlib.metadata import version
from pathlib import Path


def main():
    from mineru.parser import ParseResult, parse

    installed = version("mineru")
    if installed.split(".")[0] != "4":
        raise ValueError("MinerU 4.x is required")
    source, output, tier, page_range = sys.argv[1:]
    result = parse(source, tier=tier, ocr_mode="auto", image_analysis=False, page_range=page_range)
    middle = result.to_dict()
    pages = {}
    for page in middle["pages"]:
        # Render one original page at a time; never renumber a selected subset.
        scoped = {**middle, "pages": [page], "is_full_document": False}
        pages[str(page["page_idx"] + 1)] = ParseResult.from_dict(scoped).markdown()
    Path(output).write_text(
        json.dumps({"version": installed, "pages": pages}, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
