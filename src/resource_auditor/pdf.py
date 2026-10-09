import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field

import pdfplumber

from .mineru_bridge import parse_mineru


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


def resource_pages(texts: dict[int, str], count: int) -> list[int]:
    candidates = []
    for i, text in texts.items():
        flat = normalized(text)
        if (
            re.search(r"\b(?:Indicated|Inferred)\b|指示|指明|推断|推定", flat, re.I)
            and re.search(r"resource|资源", flat, re.I)
            and re.search(r"\d", flat)
            and re.search(r"tonn|\bMt\b|\bkt\b|\bg/t\b|ounces|\boz\b|%|吨|品位", flat, re.I)
        ):
            candidates.append(i)
    return sorted(
        {n for i in candidates for n in (i - 1, i, i + 1) if n in texts and 1 <= n <= count}
    )


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
            for i, page in enumerate(pdf.pages, 1):
                if pages and i not in selected and i > 10:
                    continue
                text = page.extract_text(layout=True) or ""
                texts[i] = text
                page.close()
            if not pages:
                selected = resource_pages(texts, len(pdf.pages))
            parser = "pdfplumber"
            parser_notes = []
            sample = " ".join(texts.get(i, "") for i in range(1, 11))
            chinese = len(re.findall(r"[\u4e00-\u9fff]", sample)) >= 20
            no_text = not any(normalized(texts.get(i, "")) for i in selected)
            prefer_mineru = settings.pdf_parser == "mineru" or (
                settings.pdf_parser == "auto" and (chinese or no_text)
            )
            if prefer_mineru:
                if settings.mineru_python:
                    requested = sorted(set(selected) | set(range(1, min(10, len(pdf.pages)) + 1)))
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
                            texts.update(parsed)
                            parser = "mineru:" + version
                            if not pages:
                                selected = resource_pages(texts, len(pdf.pages))
                        except ValueError as exc:
                            if str(exc) == "mineru_missing_requested_pages":
                                issues.append(str(exc))
                            parser_notes.append(str(exc) + "; using original PDF text layer")
                else:
                    parser_notes.append("mineru_not_configured; using original PDF text layer")
            if not selected:
                issues.append("no_resource_table_or_text_layer")
            if len(selected) > settings.max_selected_pages:
                issues.append("page_selection_overflow: select resource table pages explicitly")
                selected = selected[: settings.max_selected_pages]
            context_parts = []
            for i in selected:
                text = texts[i]
                # Original page text remains authoritative; tables are only a second view.
                tables = pdf.pages[i - 1].extract_tables() if parser == "pdfplumber" else []
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
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF cannot be parsed (invalid, encrypted, or unsupported)") from exc
