"""Read the literal structure of MinerU tables; never infer missing cells or values."""

import re
from html.parser import HTMLParser


class TableGrid(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = {}
        self.row = -1
        self.col = 0
        self.in_table = self.in_row = False
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.in_table:
                raise ValueError("nested_table")
            self.in_table = True
        elif tag == "tr":
            if not self.in_table or self.in_row or self.cell is not None:
                raise ValueError("invalid_table_row")
            self.row += 1
            if self.row >= 1000:
                raise ValueError("table_too_large")
            self.col, self.in_row = 0, True
        elif tag in {"td", "th"}:
            if not self.in_row or self.cell is not None:
                raise ValueError("invalid_table_cell")
            attrs = dict(attrs)
            spans = [int(attrs.get(name, "1")) for name in ("rowspan", "colspan")]
            if not all(1 <= value <= 200 for value in spans):
                raise ValueError("invalid_table_span")
            self.cell = (tag, *spans, [])
        elif tag == "br" and self.cell is not None:
            self.cell[-1].append(" ")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell[-1].append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self.cell is None or self.cell[0] != tag:
                raise ValueError("invalid_table_cell_end")
            _, height, width, parts = self.cell
            while (self.row, self.col) in self.cells:
                self.col += 1
            if self.col + width > 200 or self.row + height > 1000:
                raise ValueError("table_too_large")
            value = " ".join("".join(parts).split())
            for row in range(self.row, self.row + height):
                for col in range(self.col, self.col + width):
                    if (row, col) in self.cells:
                        raise ValueError("overlapping_table_span")
                    self.cells[row, col] = value
            if len(self.cells) > 50000:
                raise ValueError("table_too_large")
            self.col += width
            self.cell = None
        elif tag == "tr":
            if not self.in_row or self.cell is not None:
                raise ValueError("invalid_table_row_end")
            self.in_row = False
        elif tag == "table":
            if not self.in_table or self.in_row or self.cell is not None:
                raise ValueError("invalid_table_end")
            self.in_table = False

    def grid(self):
        if self.in_table or self.in_row or self.cell is not None or not self.cells:
            raise ValueError("incomplete_table")
        height = max(row for row, _ in self.cells) + 1
        width = max(col for _, col in self.cells) + 1
        if height != self.row + 1 or len(self.cells) != height * width:
            raise ValueError("nonrectangular_table")
        return [[self.cells[row, col] for col in range(width)] for row in range(height)]


def table_views(text):
    """Return grids and a row view for selection/coverage; original evidence stays untouched."""
    tables = []

    def html_view(match):
        parser = TableGrid()
        parser.feed(match[0])
        parser.close()
        grid = parser.grid()
        tables.append(grid)
        return "\n" + "\n".join(" | ".join(row) for row in grid) + "\n"

    view = re.sub(r"<table\b[^>]*>.*?</table\s*>", html_view, text, flags=re.I | re.S)
    if re.search(r"</?(?:table|tr|td|th)\b", view, re.I):
        raise ValueError("incomplete_or_nested_table")
    # The SDK renders simple tables as Markdown, retaining complex tables as HTML.
    lines = view.splitlines()
    index = 0
    while index < len(lines) - 1:
        if lines[index].strip().startswith("|") and re.fullmatch(
            r"\s*\|(?:\s*:?-+:?\s*\|)+\s*", lines[index + 1]
        ):
            grid = [[cell.strip() for cell in lines[index].strip().strip("|").split("|")]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                grid.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            if len(grid) < 2 or any(len(row) != len(grid[0]) for row in grid):
                raise ValueError("nonrectangular_markdown_table")
            tables.append(grid)
        else:
            index += 1
    return tables, view
