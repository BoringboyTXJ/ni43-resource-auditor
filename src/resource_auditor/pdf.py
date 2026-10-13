import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field

import pdfplumber

from .mineru_bridge import parse_mineru

NI_STANDARD_PATTERN = r"(?:\bNI|\bNational\s+Instrument)[\s–—-]*43[\s–—-]*101\b"


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


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


@dataclass
class Document:
    document_id: str
    filename: str
    pages: dict[int, str]
    context: str
    selected_pages: list[int]
    explicit: bool
    issues: list[str]
    parser: str = "pdfplumber"
    parser_notes: list[str] = field(default_factory=list)
    tables: dict[int, list] = field(default_factory=dict)


def resource_pages(texts: dict[int, str], count: int) -> list[int]:
    candidates = []
    for i, text in texts.items():
        flat = normalized(text)
        lines = text.splitlines()
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
    issues = []
    try:
        with pdfplumber.open(io.BytesIO(data), unicode_norm="NFKC") as pdf:
            if len(pdf.pages) > 1000:
                raise ValueError("PDF exceeds the 1000-page parser limit")
            selected = parse_pages(pages, len(pdf.pages)) if pages else []
            texts = {}
            opaque_pages = []
            blank_pages = []
            for i, page in enumerate(pdf.pages, 1):
                if pages and i not in selected and i > 10:
                    continue
                text = page.extract_text(layout=True) or ""
                texts[i] = text
                flat = normalized(text)
                has_graphics = any(
                    getattr(page, name, []) for name in ("images", "curves", "lines", "rects")
                )
                if len(flat) < 80 and (has_graphics or (not flat and not hasattr(page, "images"))):
                    opaque_pages.append(i)
                elif not flat and not has_graphics:
                    blank_pages.append(i)
                page.close()
            if not pages:
                selected = sorted(set(resource_pages(texts, len(pdf.pages))) | set(opaque_pages))
            parser = "pdfplumber"
            parser_notes = []
            mineru_pages = set()
            sample = " ".join(texts.get(i, "") for i in range(1, 11))
            chinese = len(re.findall(r"[\u4e00-\u9fff]", sample)) >= 20
            no_text = not any(normalized(texts.get(i, "")) for i in selected)
            needs_ocr = set(selected) & set(opaque_pages)
            prefer_mineru = settings.pdf_parser == "mineru" or (
                settings.pdf_parser == "auto" and (chinese or no_text or needs_ocr)
            )
            if prefer_mineru:
                if settings.mineru_python:
                    requested = sorted(set(selected) | set(range(1, min(10, len(pdf.pages)) + 1)))
                    mixed_ocr = settings.pdf_parser != "mineru" and not chinese and not no_text
                    if mixed_ocr:
                        requested = sorted(set(requested) & set(opaque_pages))
                    if not selected and len(pdf.pages) <= settings.max_selected_pages:
                        requested = list(range(1, len(pdf.pages) + 1))
                    if (not selected and len(pdf.pages) > settings.max_selected_pages) or len(
                        selected
                    ) > settings.max_selected_pages:
                        issues.append(
                            "mineru_page_selection_required: select resource table pages explicitly"
                        )
                    else:
                        try:
                            parsed, version = parse_mineru(data, settings, requested)
                            if not set(requested).issubset(parsed):
                                raise ValueError("mineru_missing_requested_pages")
                            texts.update({i: parsed[i] for i in requested})
                            mineru_pages = set(requested)
                            parser = ("pdfplumber+mineru:" if mixed_ocr else "mineru:") + version
                            if not pages:
                                selected = resource_pages(texts, len(pdf.pages))
                            needs_ocr = {
                                i for i in needs_ocr if len(normalized(texts.get(i, ""))) < 80
                            }
                        except ValueError as exc:
                            if str(exc) == "mineru_missing_requested_pages":
                                issues.append(str(exc))
                            parser_notes.append(str(exc) + "; using original PDF text layer")
                else:
                    parser_notes.append("mineru_not_configured; using original PDF text layer")
            if needs_ocr:
                issues.append("unverified_scan_pages: " + ",".join(map(str, sorted(needs_ocr))))
            blank_pages = [i for i in blank_pages if not normalized(texts.get(i, ""))]
            if selected and any(i in blank_pages for i in selected):
                parser_notes.append(
                    "blank_pages_excluded: "
                    + ",".join(str(i) for i in selected if i in blank_pages)
                )
                selected = [i for i in selected if i not in blank_pages]
            if not selected:
                issues.append("no_resource_table_or_text_layer")
            if len(selected) > settings.max_selected_pages:
                issues.append("page_selection_overflow: select resource table pages explicitly")
                selected = selected[: settings.max_selected_pages]
            standard_evidence = []
            for i in range(1, min(10, len(pdf.pages)) + 1):
                for pattern in (NI_STANDARD_PATTERN, r"\bJORC\b"):
                    if match := re.search(pattern, normalized(texts.get(i, "")), re.I):
                        standard_evidence.append(f"PDF page {i}: {match.group(0)}")
            context_parts = (
                ["[STANDARD EVIDENCE ONLY; NOT EXTRACTION SCOPE]\n" + "\n".join(standard_evidence)]
                if standard_evidence
                else []
            )
            source_tables = {}
            for i in selected:
                text = texts[i]
                # Original page text remains authoritative; tables are only a second view.
                tables = pdf.pages[i - 1].extract_tables() if i not in mineru_pages else []
                source_tables[i] = tables
                table_view = "\n".join(
                    " | ".join(c or "" for c in row) for table in tables for row in table
                )
                context_parts.append(f"[PDF PAGE {i}]\n{text}\n[TABLE VIEW]\n{table_view}")
                pdf.pages[i - 1].close()
            context = "\n\n".join(context_parts)
            if len(context) > settings.max_context_chars:
                issues.append("context_overflow: select fewer resource table pages explicitly")
                context = context[: settings.max_context_chars]
            if selected and not any(normalized(texts[i]) for i in selected):
                issues.append("scanned_pdf_requires_mineru_or_manual_review")
            return Document(
                hashlib.sha256(data).hexdigest(),
                filename,
                texts,
                context,
                selected,
                bool(pages),
                issues,
                parser,
                parser_notes,
                source_tables,
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF cannot be parsed (invalid, encrypted, or unsupported)") from exc
