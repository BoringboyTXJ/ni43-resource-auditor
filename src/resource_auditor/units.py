"""Bind unit multipliers to resource columns, never to arbitrary page words."""

import re

from .pdf import normalized

MASS_PATTERN = re.compile(
    r"millions?\s+(?:of\s+)?(?:troy\s+)?ounces?|thousands?\s+(?:of\s+)?(?:troy\s+)?ounces?|"
    r"millions?\s+(?:of\s+)?tonnes?|thousands?\s+(?:of\s+)?tonnes?|"
    r"(?<![\w/])(?:Moz|koz|oz|Mt|kt|t)(?!\w)|\(000\)",
    re.I,
)


def mass_units(text):
    units = []
    for match in MASS_PATTERN.finditer(text):
        token = match.group().casefold()
        if token.startswith("million"):
            unit = "Moz" if "ounce" in token else "Mt"
        elif token.startswith("thousand"):
            unit = "koz" if "ounce" in token else "kt"
        else:
            unit = {
                "moz": "Moz",
                "koz": "koz",
                "oz": "oz",
                "mt": "Mt",
                "kt": "kt",
                "t": "t",
                "(000)": "000",
            }[token]
        units.append((match.start(), unit))
    return units


def field_units(text):
    # Unknown multiplier notation must not fall through to unscaled tonnes/ounces.
    if re.search(r"(?:[×x*]\s*10|10\s*\^|\b(?:million|thousand)s?\b)", text, re.I) and not any(
        unit in {"Mt", "kt", "Moz", "koz"} for _, unit in mass_units(text)
    ):
        return set()
    found = {unit for _, unit in mass_units(text) if unit != "000"}
    if found:
        return found
    if "000" in {unit for _, unit in mass_units(text)}:
        if re.search(r"tonn", text, re.I):
            return {"kt"}
        if re.search(r"ounces?", text, re.I):
            return {"koz"}
        return set()
    if re.search(r"\btonnes?\b|\btons?\b", text, re.I):
        return {"t"}
    if re.search(r"\bounces?\b", text, re.I):
        return {"oz"}
    return set()


def text_unit_check(row, page_text):
    header = normalized(row.evidence.header_quote)
    # Compact unit-only quotations still need the adjacent original column labels.
    if "000" in header and not re.search(r"tonn|ounces?", header, re.I):
        start = page_text.find(header)
        if start >= 0:
            header = page_text[max(0, start - 160) : start] + header
    tokens = mass_units(header)
    values = [unit for _, unit in tokens]
    if len(values) == 1:
        grade = re.search(r"g\s*/\s*t|%", header, re.I)
        if (
            grade
            and tokens[0][0] > grade.start()
            and re.search(r"tonn", header[: grade.start()], re.I)
        ):
            values.insert(0, "t")
        elif (
            grade
            and tokens[0][0] < grade.start()
            and re.search(r"ounces?", header[grade.end() :], re.I)
        ):
            values.append("oz")
    if not values and re.search(r"tonn", header, re.I) and re.search(r"ounces?", header, re.I):
        values = ["t", "oz"]
    pairs = []
    if len(values) % 2 == 0:
        for ore, metal in zip(values[::2], values[1::2], strict=True):
            if ore == "000" and re.search(r"tonn", header, re.I):
                ore = "kt"
            if metal == "000" and re.search(r"ounces?", header, re.I):
                metal = "koz"
            pairs.append((ore, metal))
    expected = (row.evidence.ore_unit, row.evidence.contained_raw_unit)
    return [] if expected in pairs else ["source_column_units_unverified_or_conflicting"]


def table_columns(table):
    """Shared native column map for unit binding and source completeness checks."""
    cells = [[normalized(str(cell or "")) for cell in line] for line in table]
    first = next((i for i, line in enumerate(cells) if sum(is_number(c) for c in line) >= 3), None)
    if first is None:
        return None
    headers, body = cells[:first], cells[first:]
    width = max(map(len, cells))
    labels = [" ".join(line[col] for line in headers if col < len(line)) for col in range(width)]
    categories = [None] * width
    for line in headers:
        current = None
        for col, cell in enumerate(line):
            if re.fullmatch(r"Measured\s*(?:and|&|\+)\s*Indicated", cell, re.I):
                current = "combined"
            elif re.fullmatch(r"Measured|Indicated|Inferred", cell, re.I):
                current = cell.casefold()
            if current:
                categories[col] = current
    roles, current = [], None
    for label in labels:
        if re.search(r"contained|ounces?", label, re.I):
            current = "metal"
        elif re.search(r"tonn|\bore\b", label, re.I):
            current = "ore"
        elif re.search(r"\bgrade\b|%|(?<![a-z])g\s*/\s*t", label, re.I):
            current = "grade"
        elif re.search(r"\b(?:Gold|Copper)\b", label, re.I):
            current = "metal"
        roles.append(current)
    return body, labels, categories, roles


def table_unit_check(row, tables, page_text):
    """Use extractor-independent cells when a full resource table is available."""
    usable = False
    for table in tables:
        parsed = table_columns(table)
        if parsed is None:
            continue
        body, labels, categories, roles = parsed
        if not {"ore", "grade", "metal"}.issubset(roles):
            continue
        usable = True
        parallel_categories = len(set(categories) - {None}) > 1
        body_categories = set()
        category = row.category.casefold()
        for source_row in body:
            row_categories = {
                c.casefold()
                for c in re.findall(r"\b(Indicated|Inferred)\b", " ".join(source_row), re.I)
            }
            if row_categories and not parallel_categories:
                body_categories = row_categories
            if (row_categories and row_categories != {category}) or (
                body_categories and body_categories != {category}
            ):
                continue
            columns = {
                role: [
                    i
                    for i, kind in enumerate(roles)
                    if kind == role
                    and (
                        (body_categories and not parallel_categories)
                        or categories[i] == category
                        or not categories[i]
                    )
                ]
                for role in ("ore", "grade", "metal")
            }
            raw = {
                "ore": row.evidence.ore_raw,
                "grade": row.evidence.grade_raw,
                "metal": row.evidence.contained_raw,
            }
            if not all(
                any(
                    i < len(source_row) and same_number(source_row[i], raw[role])
                    for i in columns[role]
                )
                for role in raw
            ):
                continue
            field_headers = {role: " ".join(labels[i] for i in columns[role]) for role in raw}
            ore_units = field_units(field_headers["ore"])
            metal_units = field_units(field_headers["metal"])
            # A field-specific disclosure note can clarify a malformed printed unit.
            if (
                not metal_units
                and not mass_units(field_headers["metal"])
                and not re.search(r"[×x*]\s*10|10\s*\^", field_headers["metal"], re.I)
            ):
                note = re.search(r"Contained\s+metal\s+is\s+reported\s+in[^.]*", page_text, re.I)
                if (
                    note
                    and row.commodity == "Au"
                    and re.search(r"million\s+troy\s+ounces\s+of\s+gold", note.group(), re.I)
                ):
                    metal_units = {"Moz"}
                elif (
                    note
                    and row.commodity == "Cu"
                    and re.search(r"millions?\s+of\s+tonnes\s+of\s+copper", note.group(), re.I)
                ):
                    metal_units = {"Mt"}
            grade_ok = (
                "%" in field_headers["grade"]
                if row.grade_unit == "%"
                else bool(re.search(r"g\s*/\s*t", field_headers["grade"], re.I))
            )
            if (
                ore_units == {row.evidence.ore_unit}
                and metal_units == {row.evidence.contained_raw_unit}
                and grade_ok
            ):
                return []
    return ["source_column_alignment_or_units_conflict"] if usable else None


def is_number(text):
    return bool(re.fullmatch(r"\d[\d,]*(?:\.\d+)?", text))


def same_number(left, right):
    return (
        is_number(left)
        and is_number(right)
        and float(left.replace(",", "")) == float(right.replace(",", ""))
    )
