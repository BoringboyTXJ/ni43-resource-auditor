import calendar
import math
import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher

from .models import Candidate, ResourceRow
from .pdf import NI_STANDARD_PATTERN, Document, normalized, tabular_numbers
from .tables import table_views
from .units import (
    is_number,
    mass_units,
    plain_subscripts,
    same_number,
    table_columns,
    table_unit_check,
    text_unit_check,
)

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


def identity_text(field, value):
    text = normalized(str(value)).casefold()
    if field == "basis":
        text = re.sub(r"\bunderground\b", "ug", text)
        text = re.sub(r"\bopen\s+pit\b", "op", text)
    return text


def row_key(row) -> tuple:
    return tuple(
        identity_text(f, getattr(row, f))
        for f in ("project", "table", "effective_date", "basis", "cutoff", "category", "commodity")
    )


def vertical_category_supported(row, page_text):
    """Conservative fallback for a single-category header above a borderless body."""
    header, quote = normalized(row.evidence.header_quote), normalized(row.evidence.row_quote)
    category = row.category.casefold()
    labels = {c.casefold() for c in re.findall(r"\b(Measured|Indicated|Inferred)\b", header, re.I)}
    if labels != {category} or normalized(row.evidence.category_quote).casefold() != category:
        return False
    # Repeated quotations cannot establish which category block owns the numeric row.
    if page_text.count(header) != 1 or page_text.count(quote) != 1:
        return False
    start, end = page_text.find(header) + len(header), page_text.find(quote)
    if start > end:
        return False
    between = page_text[start:end]
    labels = {c.casefold() for c in re.findall(r"\b(Measured|Indicated|Inferred)\b", between, re.I)}
    if labels - {category}:
        return False
    # A new table or unit header closes the block; never borrow an earlier multiplier.
    return not (mass_units(between) or re.search(r"\bTable\s+\d|g\s*/\s*t|%", between, re.I))


def commodity_labels(text):
    text = plain_subscripts(text)
    return {
        commodity
        for commodity, pattern in (
            ("Au", r"\bAu\b|\bGold\b"),
            ("Cu", r"\bCu\b|\bCopper\b"),
            ("Li2O", r"\bLi2O\b"),
        )
        if re.search(pattern, text, re.I)
    }


def native_inventory(table):
    """Each eligible column group is one source item; never depend on table count."""
    parsed = table_columns(table)
    if parsed is None:
        return [], False
    body, labels, categories, roles = parsed
    commodities = commodity_labels(" ".join(labels))
    if len(commodities) != 1 or not {"ore", "grade", "metal"}.issubset(roles):
        return [], True
    commodity = next(iter(commodities))
    parallel = len(set(categories) - {None}) > 1
    current_category = None
    inventory, uncertain, group_rows = [], False, {}
    for source_row in body:
        label = " ".join(
            cell for cell in source_row if not is_number(cell) and cell not in {"", "-", "–", "—"}
        )
        if not parallel:
            found = {
                c.casefold() for c in re.findall(r"\b(Measured|Indicated|Inferred)\b", label, re.I)
            }
            if found:
                current_category = next(iter(found)) if len(found) == 1 else "combined"
                if (
                    len(found) > 1
                    and found & {"indicated", "inferred"}
                    and not (
                        found == {"measured", "indicated"}
                        and re.search(r"Measured\s*(?:and|&|\+)\s*Indicated", label, re.I)
                    )
                ):
                    uncertain = True
            elif re.search(r"\b(?:sub[- ]?total|total|combined)\b", label, re.I):
                current_category = None
            targets = [current_category] if current_category in {"indicated", "inferred"} else []
        else:
            targets = sorted(set(categories) & {"indicated", "inferred"})
        for category in targets:
            values = {}
            for role in ("ore", "grade", "metal"):
                cells = [
                    source_row[i]
                    for i, kind in enumerate(roles)
                    if i < len(source_row)
                    and kind == role
                    and (not parallel or categories[i] == category)
                    and source_row[i]
                ]
                numeric = [cell for cell in cells if is_number(cell)]
                if len(numeric) == 1:
                    values[role] = numeric[0]
                elif numeric or any(cell not in {"-", "–", "—"} for cell in cells):
                    uncertain = True
            if len(values) == 3:
                if not parallel:
                    group = tuple(
                        source_row[i]
                        for i, role in enumerate(roles)
                        if role is None
                        and i < len(source_row)
                        and source_row[i]
                        and not re.search(
                            r"classification|category|^\s*(?:Measured|Indicated|Inferred)\b",
                            labels[i],
                            re.I,
                        )
                    )
                    conditions = tuple(
                        source_row[i]
                        for i, label in enumerate(labels)
                        if i < len(source_row) and re.search(r"\bcog\b|cut[- ]?off", label, re.I)
                    )
                    key = (group, conditions, category)
                    if group and key in group_rows and group_rows[key] != values:
                        uncertain = True
                    group_rows[key] = values
                basis = (
                    " ".join(
                        source_row[i]
                        for i, role in enumerate(roles)
                        if role is None and i < len(source_row)
                    )
                    if parallel
                    else " ".join(group)
                    if any(re.search(r"\btotal\b", label, re.I) for label in group)
                    else ""
                )
                inventory.append(
                    {"commodity": commodity, "category": category, "raw": values, "basis": basis}
                )
            elif values:
                uncertain = True
    return inventory, uncertain


def text_inventory(text, native_rows=()):
    """Literal rows and unambiguous vertical category blocks, including MinerU Markdown."""
    try:
        _, text = table_views(text)
        uncertain = False
    except ValueError:
        uncertain = True
    inventory = []
    raw_lines = text.splitlines()
    lines = [normalized(line.replace("|", " ")) for line in raw_lines]
    value = r"(?:\d[\d,]*(?:\.\d+)?|[-–—])"
    explicit = (
        rf"(?:[A-Za-z][A-Za-z ()/–—-]*\s+)?(Indicated|Inferred)\s+({value}(?:\s+{value}){{2,}})"
    )
    vertical = rf"(?:[A-Za-z][A-Za-z ()/–—-]*\s+)?({value}(?:\s+{value}){{2,}})"
    current = None
    covered = Counter(native_rows)
    for index, line in enumerate(lines):
        if not line:
            continue
        labels = {
            c.casefold() for c in re.findall(r"\b(Measured|Indicated|Inferred)\b", line, re.I)
        }
        cells = re.findall(r"(?<![\w/.-])\d[\d,]*(?:\.\d+)?(?![\w/.-])", line)
        numeric_key = tuple(number(cell) for cell in cells)
        if "|" in raw_lines[index]:
            # Cell labels can contain cut-offs and chemical subscripts. They
            # aren't data cells and must not defeat the native coverage counter.
            numeric_key = tuple(
                number(normalized(cell))
                for cell in raw_lines[index].split("|")
                if is_number(normalized(cell))
            )
        if covered[numeric_key]:
            covered[numeric_key] -= 1
            if labels:
                current = next(iter(labels)) if len(labels) == 1 else None
            elif re.search(r"\b(?:sub[- ]?total|total|combined)\b", line, re.I):
                current = None
            continue
        if (len(labels) > 1 and re.search(r"Measured\s*(?:and|&|\+)\s*Indicated", line, re.I)) or (
            not labels and re.match(r"^(?:sub[- ]?total|total)\b", line, re.I)
        ):
            current = None
            continue
        match = re.fullmatch(explicit, line, re.I)
        if match and len(labels) == 1:
            category, numeric = match[1].casefold(), match[2]
            current = category
        else:
            if labels:
                current = next(iter(labels)) if len(labels) == 1 else None
            elif re.search(r"\bTable\s+\d|\b(?:sub[- ]?total|total)\b", line, re.I):
                current = None
            if labels:
                if labels & {"indicated", "inferred"} and tabular_numbers(line):
                    uncertain = True
                continue
            match = re.fullmatch(vertical, line, re.I)
            if not match:
                if current in {"indicated", "inferred"} and tabular_numbers(line):
                    uncertain = True
                continue
            category, numeric = current, match[1]
        if sum(is_number(cell) for cell in numeric.split()) < 3:
            continue
        # Never turn a multi-category column body into a guessed row category.
        if category not in {"indicated", "inferred"}:
            if category != "measured" and re.search(r"\bIndicated\b|\bInferred\b", text, re.I):
                uncertain = True
            continue
        previous = lines[:index]
        first_header = next(
            (
                i
                for i in reversed(range(len(previous)))
                if re.search(r"tonn|\bore\b", previous[i], re.I)
            ),
            0,
        )
        nearby = " ".join(previous[first_header:])
        commodities = commodity_labels(nearby)
        if (
            len(commodities) != 1
            or not re.search(r"tonn|\bMt\b|\bkt\b", nearby, re.I)
            or not re.search(r"g\s*/\s*t|%", nearby, re.I)
        ):
            uncertain = True
            continue
        inventory.append(
            {"commodity": next(iter(commodities)), "category": category, "quote": line}
        )
    return inventory, uncertain


def explicit_row_coverage(candidate, document):
    """Source-only inventory. Unsupported selected resource layouts are blocking, not silent."""
    errors = []
    for page in document.selected_pages:
        inventory, uncertain, native_rows = [], False, []
        for table in document.tables.get(page, []):
            items, unverified = native_inventory(table)
            inventory.extend(items)
            uncertain |= unverified
            parsed = table_columns(table)
            if items and parsed:
                native_rows.extend(
                    tuple(number(cell) for cell in line if is_number(cell))
                    for line in parsed[0]
                    if sum(is_number(cell) for cell in line) >= 3
                )
        if inventory:
            # Account for gridless tables on the same page, without double-counting
            # numeric lines already covered by native cells (including repeated rows).
            extra, unverified = text_inventory(document.pages.get(page, ""), native_rows)
            inventory.extend(extra)
            uncertain |= unverified
        else:
            inventory, uncertain = text_inventory(document.pages.get(page, ""))
        if uncertain or not inventory:
            errors.append(f"page[{page}]:scope_completeness_unverified")
        used = set()
        for item in inventory:
            found = None
            for i, row in enumerate(candidate.rows):
                if (
                    i in used
                    or row.evidence.page != page
                    or row.commodity != item["commodity"]
                    or row.category.casefold() != item["category"]
                ):
                    continue
                if "raw" in item:
                    raw = {
                        "ore": row.evidence.ore_raw,
                        "grade": row.evidence.grade_raw,
                        "metal": row.evidence.contained_raw,
                    }
                    supported = all(
                        same_number(raw[field], value) for field, value in item["raw"].items()
                    )
                    if item.get("basis"):
                        expected_basis = identity_text("basis", item["basis"])
                        actual_basis = identity_text("basis", row.basis)
                        supported &= actual_basis == expected_basis or (
                            actual_basis == "total"
                            and bool(re.search(r"\btotal\b", expected_basis))
                        )
                else:
                    quote = normalized(row.evidence.row_quote.replace("|", " "))
                    supported = quote in item["quote"] or item["quote"] in quote
                if supported:
                    found = i
                    break
            if found is None:
                errors.append(
                    f"page[{page}]:missing_explicit_resource_row:{item['commodity']}:{item['category']}:{item.get('quote', item.get('raw'))}"
                )
            else:
                used.add(found)
    return errors


def identity_source_text(text):
    return (
        normalized(plain_subscripts(text))
        .casefold()
        .translate(str.maketrans({"–": "-", "—": "-", "−": "-"}))
    )


def source_dates(text):
    months = {
        name.casefold(): number
        for number in range(1, 13)
        for name in (calendar.month_name[number], calendar.month_abbr[number])
    }
    month = "(?:" + "|".join(months) + ")"
    dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text))
    for pattern in (
        rf"\b({month})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(\d{{4}})\b",
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month})[,]?\s+(\d{{4}})\b",
    ):
        for first, second, year in re.findall(pattern, text, re.I):
            name, day = (first, second) if first.casefold() in months else (second, first)
            try:
                dates.add(date(int(year), months[name.casefold()], int(day)).isoformat())
            except ValueError:
                pass
    return dates


def check_identity(row, document):
    """Weak source provenance, independent of the critic; no truth or filename evidence."""
    errors = []
    page_text = identity_source_text(document.pages.get(row.evidence.page, ""))
    visible = identity_source_text(
        " ".join(document.pages.get(page, "") for page in document.selected_pages)
    )
    source = identity_source_text(" ".join(document.pages.values()))

    def contains(value, text):
        return bool(value and re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text))

    project_parts = identity_source_text(row.project).split(" - ")
    if not contains(project_parts[0], source) or any(
        not contains(part, page_text) for part in project_parts[1:]
    ):
        errors.append("unverified_identity:project")
    table = identity_source_text(row.table)
    table_id = re.fullmatch(r"table\s*(\d+(?:[-.]\d+)*[a-z]?)", table)
    source_ids = re.findall(r"\btable\s*(\d+(?:\s*[-.]\s*\d+)*[a-z]?)(?!\w|[.-]\s*\d)", page_text)
    table_supported = (
        table_id[1] in {re.sub(r"\s+", "", value) for value in source_ids}
        if table_id
        else contains(table, page_text)
    )
    if not table_supported:
        errors.append("unverified_identity:table")
    if row.effective_date.casefold() not in {"", "unknown", "not reported"}:
        try:
            effective = date.fromisoformat(row.effective_date).isoformat()
        except ValueError:
            effective = None
        if effective not in source_dates(visible):
            errors.append("unverified_identity:effective_date")
    basis = identity_text("basis", identity_source_text(row.basis))
    if basis == "total":
        quote = normalized(row.evidence.row_quote)
        try:
            _, basis_view = table_views(document.pages.get(row.evidence.page, ""))
        except ValueError:
            basis_view = document.pages.get(row.evidence.page, "")
        total_labels = [
            identity_text("basis", identity_source_text(line))
            for line in basis_view.splitlines()
            if re.match(r"^total\b", normalized(line), re.I)
        ]
        only_qualified = any(
            re.match(r"^total\s+(?:ug|op)\b", label) for label in total_labels
        ) and not any(
            re.match(r"^total(?:$|\s+(?:indicated|inferred|\d)\b)", label) for label in total_labels
        )
        if only_qualified or not (
            re.match(r"^(?:\|\s*)?(?:Indicated|Inferred)\b", quote, re.I)
            or re.search(r"\btotal\b", page_text, re.I)
        ):
            errors.append("unverified_identity:basis")
    else:
        basis_source = identity_text(
            "basis",
            page_text
            + " "
            + " ".join(
                normalized(str(cell or ""))
                for table in document.tables.get(row.evidence.page, [])
                for line in table
                for cell in line
            ),
        )
        parts = basis.rsplit(" ", 1)
        supported = contains(basis, basis_source)
        if len(parts) == 2 and parts[1] in {"op", "ug"}:
            supported |= contains(parts[0], basis_source) and contains(parts[1], basis_source)
        if not supported:
            errors.append("unverified_identity:basis")
    cutoff = identity_source_text(row.cutoff)
    if cutoff not in {"", "unknown", "not reported", "not specified"}:
        numbers = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", cutoff)
        if numbers:
            lines = document.pages.get(row.evidence.page, "").splitlines()
            cutoff_source = identity_source_text(
                " ".join(
                    " ".join(lines[max(0, index - 1) : index + 2])
                    for index, line in enumerate(lines)
                    if re.search(r"cut[- ]?offs?|\bcut-\s*$", line, re.I)
                )
            )
            supported = all(
                re.search(r"(?<![\d.])" + re.escape(value) + r"(?![\d.])", cutoff_source)
                for value in numbers
            )
            # Sensitivity tables report the cut-off in their first CoG column.
            if (
                not supported
                and len(numbers) == 1
                and re.search(r"\bcog\b", row.evidence.header_quote, re.I)
            ):
                quote_numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", row.evidence.row_quote)
                supported = bool(quote_numbers and same_number(numbers[0], quote_numbers[0]))
            for unit in ("g/t", "%", "li2o", "au", "cu"):
                if unit in cutoff and unit not in page_text:
                    supported = False
        else:
            supported = contains(cutoff, page_text)
        if not supported:
            errors.append("unverified_identity:cutoff")
    return errors


def check_row(row: ResourceRow, document: Document) -> list[str]:
    errors = check_identity(row, document)
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
    row_categories = set(re.findall(r"\b(Indicated|Inferred)\b", quote, re.I))
    if row_categories and {c.casefold() for c in row_categories} != {row.category.casefold()}:
        errors.append("row_category_conflicts_with_output")
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
    table_subject = re.sub(
        r"\b(?:inclusive\s+of|exclusive\s+of|including|excluding)\s+(?:mineral\s+)?reserves?\b",
        "",
        header,
        flags=re.I,
    )
    if re.search(r"\breserves?\b", table_subject, re.I):
        errors.append("reserve_table_is_not_a_resource_table")
    expected_grade_unit = "g/t" if row.commodity == "Au" else "%"
    if row.grade_unit != expected_grade_unit:
        errors.append("grade_unit_mismatch")
    bound_units = table_unit_check(row, document.tables.get(ev.page, []), page_text)
    errors.extend(text_unit_check(row, page_text) if bound_units is None else bound_units)
    if (
        bound_units is None
        and not row_categories
        and re.search(r"\bIndicated\b.*\bInferred\b|\bInferred\b.*\bIndicated\b", page_text, re.I)
        and not vertical_category_supported(row, page_text)
    ):
        errors.append("category_column_alignment_unverified")
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
    errors.extend(explicit_row_coverage(candidate, document))
    return errors
