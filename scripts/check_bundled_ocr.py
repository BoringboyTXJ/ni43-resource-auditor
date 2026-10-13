"""Offline smoke test for the Docker-bundled OCR runtime, without any model API."""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pdfplumber
from PIL import Image, ImageDraw, ImageFont

from resource_auditor.config import RoleConfig, Settings
from resource_auditor.pdf import read_pdf


def main():
    font_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    )
    font = ImageFont.truetype(font_path, 38)
    image = Image.new("RGB", (1800, 1300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((100, 90), "NI 43-101 矿产资源报告", font=font, fill="black")
    draw.text((100, 170), "中文扫描表：控制样本，仅用于检验解析器", font=font, fill="black")
    draw.text((100, 250), "Mineral Resources - Gold", font=font, fill="black")
    rows = [
        ["Category", "Ore (Mt)", "Au (g/t)", "Gold (koz)"],
        ["Indicated", "10.0", "1.00", "321.5"],
        ["Inferred", "5.0", "1.00", "160.8"],
    ]
    columns = [100, 620, 960, 1260, 1700]
    for i, row in enumerate(rows):
        y = 390 + i * 120
        for j, value in enumerate(row):
            draw.text((columns[j] + 15, y + 30), value, font=font, fill="black")
    for x in columns:
        draw.line((x, 390, x, 750), fill="black", width=3)
    for y in [390, 510, 630, 750]:
        draw.line((100, y, 1700, y), fill="black", width=3)
    with tempfile.TemporaryDirectory(prefix="auditor-ocr-check-") as directory:
        source = Path(directory) / "chinese-scan.pdf"
        image.save(source, "PDF", resolution=150.0)
        data = source.read_bytes()
        with pdfplumber.open(source) as pdf:
            assert not pdf.pages[0].extract_text(), "Fixture must have no text layer"
        settings = Settings(
            RoleConfig("https://extractor.test/v1", "extractor", "synthetic"),
            RoleConfig("https://critic.test/v1", "critic", "synthetic"),
            mineru_python=os.environ["MINERU_PYTHON"],
            mineru_tier="basic",
            mineru_timeout=600,
        )
        document = read_pdf(data, source.name, settings)
        assert document.parser == "mineru:4.0.11", (document.parser, document.parser_notes)
        assert not document.issues, document.issues
        assert document.selected_pages == [1], document.selected_pages
        text = document.pages[1]
        for expected in ["矿产资源报告", "Indicated", "Inferred", "321.5", "160.8"]:
            assert expected in text, (expected, text)
        # Simple tables render as Markdown in the official 4.x SDK.
        assert re.search(r"\|\s*Indicated\s*\|\s*10\.0\s*\|\s*1\.00\s*\|\s*321\.5\s*\|", text), text
        assert re.search(r"\|\s*Inferred\s*\|\s*5\.0\s*\|\s*1\.00\s*\|\s*160\.8\s*\|", text), text
        print(
            json.dumps(
                {
                    "parser": document.parser,
                    "tier": "basic",
                    "pages": document.selected_pages,
                    "chinese_text": True,
                    "resource_table": True,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
