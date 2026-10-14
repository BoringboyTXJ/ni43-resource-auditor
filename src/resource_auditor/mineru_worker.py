"""Run with a separate MinerU 4.x interpreter; the base app stays lightweight."""

import gc
import json
import os
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path

# Bound CPU memory and nested BLAS workers for whole-document parsing.
# These are official runtime knobs; callers may override them for larger hosts.
for name, value in {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "2",
    "MINERU_PROCESSING_WINDOW_SIZE": "8",
    "MINERU_PDF_RENDER_THREADS": "1",
    "DOCVORTEX_PDF_RENDER_THREADS": "1",
    "MINERU_MODEL_VLM_MAX_CONCURRENCY": "1",
}.items():
    os.environ.setdefault(name, value)


def render_pages(result):
    from mineru.parser import ParseResult

    middle = result.to_dict()
    return {
        page["page_idx"] + 1: ParseResult.from_dict(
            {**middle, "pages": [page], "is_full_document": False}
        ).markdown()
        for page in middle["pages"]
    }


def source_issues(pages, selected):
    from resource_auditor.checks import explicit_row_coverage
    from resource_auditor.models import Candidate
    from resource_auditor.pdf import Document
    from resource_auditor.tables import table_views

    tables = {}
    bad = set()
    for page in selected:
        try:
            tables[page] = table_views(pages.get(page, ""))[0]
        except ValueError:
            bad.add(page)
    document = Document("source-check", "input.pdf", pages, "", selected, True, [], tables=tables)
    issues = explicit_row_coverage(
        Candidate(reporting_standard="unknown", rows=[], unresolved=[]), document
    )
    for issue in issues:
        if "scope_completeness_unverified" in issue:
            bad.add(int(issue.split("]", 1)[0].split("[")[1]))
    return sorted(bad)


def known_rows(pages, page):
    from resource_auditor.checks import native_inventory, number
    from resource_auditor.tables import table_views

    try:
        tables = table_views(pages.get(page, ""))[0]
    except ValueError:
        return Counter()
    return Counter(
        (
            item["commodity"],
            item["category"],
            *(number(item["raw"][role]) for role in ("ore", "grade", "metal")),
        )
        for table in tables
        for item in native_inventory(table)[0]
    )


def refine_pages(source, pages, scope, tier, parse):
    bad = source_issues(pages, scope)
    notes = []
    # More compute only for unclear resource pages. Both passes use MinerU;
    # never split concatenated numbers or rewrite an uncertain rowspan by hand.
    if bad and tier in {"basic", "flash"} and len(bad) <= 24:
        try:
            refined = render_pages(
                parse(
                    source,
                    tier="advanced",
                    ocr_mode="ocr",
                    image_analysis=False,
                    page_range=",".join(map(str, bad)),
                )
            )
            verified = [
                page
                for page in bad
                if page in refined
                and not source_issues(refined, [page])
                and known_rows(pages, page) <= known_rows(refined, page)
            ]
            for page in verified:
                pages[page] = refined[page]
            if verified:
                notes.append("mineru_advanced_reparse: " + ",".join(map(str, verified)))
            remaining = sorted(set(bad) - set(verified))
            if remaining:
                notes.append(
                    "mineru_resource_structure_still_unverified: " + ",".join(map(str, remaining))
                )
        except Exception:
            # No provider body, private path or model input escapes to the API.
            notes.append("mineru_advanced_reparse_failed: resource structure remains unverified")
    return pages, notes


def main():
    from docvortex.document.pdf import PDFDocument
    from mineru.parser import parse
    from mineru.parser.page_range import parse_page_range

    # Reuse the app's source-only checks in the separate parser interpreter.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from resource_auditor.pdf import resource_pages

    installed = version("mineru")
    if installed.split(".")[0] != "4":
        raise ValueError("MinerU 4.x is required")
    source, output, tier, spec = sys.argv[1:]
    # Metadata and all content parsing live inside the same MinerU runtime.
    with PDFDocument(Path(source).read_bytes()) as document:
        count = document.page_count
    if not 1 <= count <= 1000:
        raise ValueError("PDF exceeds the 1000-page parser limit")
    selected = (
        sorted(set(parse_page_range(spec, count)) | set(range(min(10, count))))
        if spec
        else list(range(count))
    )
    page_range = ",".join(str(page + 1) for page in selected) if spec else ""
    result = parse(source, tier=tier, ocr_mode="auto", image_analysis=False, page_range=page_range)
    pages = render_pages(result)
    del result
    gc.collect()
    scope = (
        [page + 1 for page in parse_page_range(spec, count)]
        if spec
        else resource_pages(pages, count)
    )
    pages, notes = refine_pages(source, pages, scope, tier, parse)
    Path(output).write_text(
        json.dumps(
            {"version": installed, "page_count": count, "pages": pages, "notes": notes},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
