import math
import re
from difflib import SequenceMatcher

from .models import Candidate, ResourceRow
from .pdf import NI_STANDARD_PATTERN, Document, normalized

ORE_FACTORS = {"Mt": 1.0, "kt": 0.001, "t": 0.000001}
METAL_FACTORS = {
    "oz": 1.0,
    "koz": 1000.0,
    "Moz": 1000000.0,
    "t": 1.0,
    "kt": 1000.0,
    "Mt": 1000000.0,
}


def number(raw: str) -> float:
    cleaned = raw.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise ValueError("Missing, censored, or ambiguous source number")
    value = float(cleaned)
    if not math.isfinite(value):
        raise ValueError("Non-finite source number")
    return value


def interval(raw: str, factor: float = 1.0) -> tuple[float, float]:
    value = number(raw)
    decimals = len(raw.rsplit(".", 1)[1]) if "." in raw else 0
    half = 0.5 * 10**-decimals
    return max(0, value - half) * factor, (value + half) * factor


def row_key(row) -> tuple:
    return tuple(
        normalized(str(getattr(row, f))).casefold()
        for f in ("project", "table", "effective_date", "basis", "cutoff", "category", "commodity")
    )


def check_row(row: ResourceRow, document: Document) -> list[str]:
    errors = []
    ev = row.evidence
    page_text = normalized(document.pages.get(ev.page, ""))
    quote = normalized(ev.row_quote)
    header = normalized(ev.header_quote)
    if ev.page not in document.selected_pages or quote not in page_text or header not in page_text:
        errors.append("unsupported_quote_or_page")
    if header not in page_text:
        errors.append("header_quote_not_in_source")
    if quote not in page_text:
        errors.append("row_quote_not_in_source")
    category_quote = normalized(ev.category_quote)
    category_supported = re.search(rf"\b{row.category}\b", quote, re.I)
    if category_quote:
        category_supported = category_quote in page_text and re.search(
            rf"\b{row.category}\b", category_quote, re.I
        )
    if not category_supported:
        errors.append("unsupported_category")
    if re.search(
        r"Measured\s*(?:and|&|\+)\s*Indicated|\bM\s*&\s*I\b", quote + " " + category_quote, re.I
    ):
        errors.append("combined_category_is_not_indicated")
    if re.search(r"\breserves?\b", header, re.I):
        errors.append("reserve_table_is_not_a_resource_table")
    expected_grade_unit = "g/t" if row.commodity == "Au" else "%"
    if row.grade_unit != expected_grade_unit:
        errors.append("grade_unit_mismatch")
    unit_patterns = {
        "Mt": r"\bMt\b|million\s+ton",
        "kt": r"\bkt\b|thousand\s+ton|\(000\)|000\s*t",
        "t": r"\bt\b|\btonnes?\b|\btons?\b",
        "oz": r"\boz\b|\bounces?\b",
        "koz": r"\bkoz\b|thousand\s+(?:troy\s+)?ounces?|\(000\)",
        "Moz": r"\bMoz\b|million\s+(?:troy\s+)?ounces?",
    }
    if not re.search(unit_patterns[ev.ore_unit], header, re.I):
        errors.append("ore_unit_not_in_header")
    if not re.search(unit_patterns[ev.contained_raw_unit], header, re.I):
        errors.append("contained_unit_not_in_header")
    if row.grade_unit == "%" and "%" not in header:
        errors.append("grade_unit_not_in_header")
    if row.grade_unit == "g/t" and not re.search(r"g\s*/\s*t", header, re.I):
        errors.append("grade_unit_not_in_header")
    for raw in (ev.ore_raw, ev.grade_raw, ev.contained_raw):
        if not re.search(r"(?<![\d.,])" + re.escape(raw) + r"(?![\d.,])", quote):
            errors.append("source_number_not_in_row_quote")
    try:
        ore = number(ev.ore_raw) * ORE_FACTORS[ev.ore_unit]
        grade = number(ev.grade_raw)
        contained = number(ev.contained_raw) * METAL_FACTORS[ev.contained_raw_unit]
        raw_gold = ev.contained_raw_unit in {"oz", "koz", "Moz"}
        if raw_gold != (row.contained_unit == "oz"):
            errors.append("incompatible_contained_unit_conversion")
        for reported, actual in (
            (row.ore_mt, ore),
            (row.grade, grade),
            (row.contained_metal, contained),
        ):
            if not math.isclose(reported, actual, rel_tol=1e-8, abs_tol=1e-8):
                errors.append("normalized_value_does_not_match_source")
        if row.commodity == "Li2O" and row.contained_unit != "t":
            errors.append("Li2O_requires_oxide_tonnes")
        if row.commodity == "Cu" and row.contained_unit != "t":
            errors.append("Cu_requires_tonnes")
        if row.grade_unit == "%" and row.grade > 100:
            errors.append("percentage_out_of_range")
        # Intervals honor published rounding, including small stockpile rows.
        o_lo, o_hi = interval(ev.ore_raw, ORE_FACTORS[ev.ore_unit])
        g_lo, g_hi = interval(ev.grade_raw)
        divisor = 31.1034768 if row.grade_unit == "g/t" else 100.0
        mass_factor = 1e6
        if row.grade_unit == "g/t" and row.contained_unit == "t":
            divisor = 1e6
        predicted = (o_lo * mass_factor * g_lo / divisor, o_hi * mass_factor * g_hi / divisor)
        m_lo, m_hi = interval(ev.contained_raw, METAL_FACTORS[ev.contained_raw_unit])
        if m_hi < predicted[0] * 0.95 or m_lo > predicted[1] * 1.05:
            errors.append("contained_metal_arithmetic_mismatch")
    except (ValueError, KeyError):
        errors.append("unparseable_source_number_or_unit")
    return sorted(set(errors))


def quote_hints(candidate: Candidate, document: Document) -> list[dict]:
    """Offer original text for revision; never repair or accept an unsupported quote."""
    hints = []
    for i, row in enumerate(candidate.rows):
        if row.evidence.page not in document.selected_pages:
            continue
        text = normalized(document.pages.get(row.evidence.page, ""))
        for field in ("header_quote", "row_quote"):
            quote = normalized(getattr(row.evidence, field))
            if quote in text:
                continue
            match = SequenceMatcher(None, quote, text, autojunk=False).find_longest_match()
            if match.size < 12:
                continue
            start = max(0, match.b - match.a - 40)
            end = min(len(text), start + min(len(quote) + 120, 900))
            hints.append(
                {
                    "row": i,
                    "field": field,
                    "page": row.evidence.page,
                    "original_window": text[start:end],
                }
            )
    return hints


def validate_candidate(candidate: Candidate, document: Document, allow_jorc: bool) -> list[str]:
    errors = list(document.issues)
    if not candidate.rows:
        errors.append("no_resource_rows")
    if candidate.unresolved:
        errors.append("extractor_reports_unresolved_fields")
    if candidate.reporting_standard == "unknown":
        errors.append("unknown_reporting_standard")
    if candidate.reporting_standard == "JORC" and not allow_jorc:
        errors.append("JORC_requires_explicit_opt_in")
    opening = normalized(" ".join(document.pages.get(i, "") for i in range(1, 11)))
    if candidate.reporting_standard == "NI43-101" and not re.search(
        NI_STANDARD_PATTERN, opening, re.I
    ):
        errors.append("reporting_standard_not_supported_by_opening_pages")
    if candidate.reporting_standard == "JORC" and not re.search(r"\bJORC\b", opening, re.I):
        errors.append("reporting_standard_not_supported_by_opening_pages")
    keys = set()
    for i, row in enumerate(candidate.rows):
        key = row_key(row)
        if key in keys:
            errors.append(f"row[{i}]:duplicate_resource_row")
        keys.add(key)
        errors.extend(f"row[{i}]:{issue}" for issue in check_row(row, document))
    return errors
