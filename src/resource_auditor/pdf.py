import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from .mineru_bridge import parse_mineru, parse_pages
from .tables import table_views

NI_STANDARD_PATTERN = r"(?:\bNI|\bNational\s+Instrument)[\s–—-]*43[\s–—-]*101\b"


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


@dataclass
class Document:
    document_id: str
    filename: str
    pages: dict[int, str]
    context: str
    selected_pages: list[int]
    explicit: bool
    issues: list[str]
    parser: str = "mineru"
    parser_notes: list[str] = field(default_factory=list)
    tables: dict[int, list] = field(default_factory=dict)


def resource_pages(texts: dict[int, str], count: int) -> list[int]:
    candidates = []
    for i, text in texts.items():
        flat = normalized(text)
        try:
            tables, view = table_views(text)
        except ValueError:
            # An unreadable resource table must enter scope and fail closed later.
            if (
                re.search(r"\b(?:Indicated|Inferred)\b", flat, re.I)
                and re.search(r"resource|资源", flat, re.I)
                and re.search(r"tonn|\bMt\b|\bkt\b", flat, re.I)
                and tabular_numbers(flat)
            ):
                candidates.append(i)
            continue
        # Structured headers may include CoG, commodity and unit labels on the
        # category line. Also retain malformed numeric bodies for a blocking review.
        structured_resource = any(
            re.search(
                r"\b(?:Indicated|Inferred)\b", " ".join(cell for row in table for cell in row), re.I
            )
            and re.search(r"tonn|\bMt\b|\bkt\b|矿石量", " ".join(table[0]), re.I)
            and re.search(r"g\s*/\s*t|%|品位", " ".join(table[0]), re.I)
            and any(sum(bool(re.search(r"\d", cell)) for cell in row) >= 3 for row in table[1:])
            for table in tables
        )
        if structured_resource and re.search(r"resource|资源", flat, re.I):
            candidates.append(i)
            continue
        lines = view.splitlines()
        table_category = False
        for j, line in enumerate(lines):
            if not re.search(r"\b(?:Indicated|Inferred)\b|指示|指明|推断|推定", line, re.I):
                continue
            words = re.findall(r"[A-Za-z]{2,}", line)
            category_header = bool(words) and all(
                word.casefold()
                in {
                    "measured",
                    "indicated",
                    "inferred",
                    "location",
                    "total",
                    "category",
                    "classification",
                    "resources",
                    "resource",
                    "mineral",
                    "and",
                }
                for word in words
            )
            numeric_row = tabular_numbers(line) and len(words) <= 8
            nearby = " ".join(lines[max(0, j - 6) : j + 7])
            quantity_header = re.search(r"tonn|\bMt\b|\bkt\b|矿石量|吨", nearby, re.I)
            grade_header = re.search(r"\bg/t\b|%|品位", nearby, re.I)
            if (category_header or numeric_row) and quantity_header and grade_header:
                table_category = True
                break
        if (
            table_category
            and re.search(r"resource|资源", flat, re.I)
            and any(tabular_numbers(line) for line in lines)
            and re.search(r"tonn|\bMt\b|\bkt\b|\bg/t\b|ounces|\boz\b|%|吨|品位", flat, re.I)
        ):
            candidates.append(i)
    return sorted(candidates)


def tabular_numbers(line: str) -> bool:
    # Require data cells, not a caption, TOC entry, date, or a prose mention.
    numbers = re.findall(r"(?<![\w/.-])\d[\d,]*(?:\.\d+)?(?![\w/.-])", line)
    words = re.findall(r"[A-Za-z]{2,}", line)
    return len(numbers) >= 3 and (len(words) <= 5 or bool(re.search(r"\d\s{2,}\d", line)))


def read_pdf(data: bytes, filename: str, settings, pages: str = "") -> Document:
    if len(data) > settings.max_pdf_mb * 1024 * 1024:
        raise ValueError("PDF exceeds configured byte limit")
    if not data.startswith(b"%PDF-"):
        raise ValueError("Upload must be a PDF")
    if settings.pdf_parser not in {"mineru", "auto"}:
        raise ValueError("PDF_PARSER must be mineru (auto is a legacy alias for MinerU)")
    selected = parse_pages(pages, 1000) if pages else []
    issues, parser_notes = [], []
    texts, count, parser = {}, 0, "mineru"
    if len(selected) > settings.max_selected_pages:
        issues.append("page_selection_overflow: select fewer resource table pages explicitly")
    else:
        try:
            texts, version, count, parser_notes = parse_mineru(data, settings, pages)
            parser = "mineru:" + version
            selected = parse_pages(pages, count) if pages else resource_pages(texts, count)
            requested = (
                (set(selected) | set(range(1, min(10, count) + 1)))
                if pages
                else set(range(1, count + 1))
            )
            if not requested.issubset(texts):
                issues.append("mineru_missing_requested_pages")
            if set(texts) - requested and pages:
                issues.append("mineru_unexpected_page_numbers")
        except ValueError as exc:
            # No fallback parser: incomplete MinerU evidence cannot reach the LLM.
            issues.append(str(exc))
    if not selected:
        issues.append("no_resource_table_or_text_layer")
    if len(selected) > settings.max_selected_pages:
        issues.append("page_selection_overflow: select fewer resource table pages explicitly")
        selected = selected[: settings.max_selected_pages]
    standard_evidence = []
    for page in range(1, min(10, count) + 1):
        for pattern in (NI_STANDARD_PATTERN, r"\bJORC\b"):
            if match := re.search(pattern, normalized(texts.get(page, "")), re.I):
                standard_evidence.append(f"PDF page {page}: {match.group(0)}")
    context_parts = (
        ["[STANDARD EVIDENCE ONLY; NOT EXTRACTION SCOPE]\n" + "\n".join(standard_evidence)]
        if standard_evidence
        else []
    )
    source_tables = {}
    for page in selected:
        text = texts.get(page, "")
        if not normalized(text):
            issues.append(f"page[{page}]:empty_mineru_evidence")
        try:
            tables, _ = table_views(text)
        except ValueError:
            issues.append(f"page[{page}]:table_structure_unverified")
            tables = []
        source_tables[page] = tables
        table_view = "\n".join(" | ".join(row) for table in tables for row in table)
        context_parts.append(f"[PDF PAGE {page}]\n{text}\n[TABLE VIEW]\n{table_view}")
    context = "\n\n".join(context_parts)
    # Parsing uncertainty cannot be repaired by a critic approving guessed rows.
    if selected and texts:
        from .checks import explicit_row_coverage
        from .models import Candidate

        source = Document(
            "source-check",
            filename,
            texts,
            context,
            selected,
            bool(pages),
            [],
            tables=source_tables,
        )
        issues.extend(
            issue
            for issue in explicit_row_coverage(
                Candidate(reporting_standard="unknown", rows=[], unresolved=[]), source
            )
            if "scope_completeness_unverified" in issue
        )
    if len(context) > settings.max_context_chars:
        issues.append("context_overflow: select fewer resource table pages explicitly")
        context = context[: settings.max_context_chars]
    return Document(
        hashlib.sha256(data).hexdigest(),
        filename,
        texts,
        context,
        selected,
        bool(pages),
        sorted(set(issues)),
        parser,
        parser_notes,
        source_tables,
    )
