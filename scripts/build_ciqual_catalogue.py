"""Build the checked-in, minimal catalogue from the official Ciqual 2025 exports.

Usage: python scripts/build_ciqual_catalogue.py table.xlsx alim.xml
Download URLs, checksums and attribution: ../data/README.md.
No network requests or credentials are involved in this command.
"""

import hashlib
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def build(xlsx: Path, names_xml: Path) -> list[dict[str, object]]:
    if hashlib.md5(xlsx.read_bytes()).hexdigest() != "a953e8c473d8cee24a32f89a48fbd54d":
        raise ValueError("Unexpected Ciqual workbook. Review the new source before importing.")
    if hashlib.md5(names_xml.read_bytes()).hexdigest() != "8e1171d63cee4b6010cfce25dd29243d":
        raise ValueError("Unexpected Ciqual food names export.")
    names = {
        (row.findtext("alim_code") or "").strip(): (
            (row.findtext("alim_nom_fr") or "").strip(),
            (row.findtext("alim_nom_eng") or "").strip(),
        )
        for row in ET.parse(names_xml).getroot()
    }
    with ZipFile(xlsx) as archive:
        strings = [
            "".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
        rows = ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(".//m:row", NS)
    foods: list[dict[str, object]] = []
    columns = {"K": "calories_kcal", "O": "protein_g", "Q": "carbohydrates_g", "R": "fat_g"}
    for row in rows[1:]:
        values: dict[str, str] = {}
        for cell in row:
            value = cell.findtext("m:v", "", NS)
            column = re.sub(r"\d", "", cell.attrib["r"])
            values[column] = strings[int(value)] if cell.get("t") == "s" else value
        code = values.get("G", "").strip()
        if code not in names:
            raise ValueError(f"Unknown food code: {code}")
        nutrients: dict[str, float | None] = {}
        notes: dict[str, str] = {}
        for column, nutrient in columns.items():
            raw = values.get(column, "").strip()
            try:
                number = float(raw.replace(",", "."))
                if not math.isfinite(number) or number < 0:
                    raise ValueError("Invalid nutrient")
                nutrients[nutrient] = number
            except ValueError:
                nutrients[nutrient] = None
                if raw and raw != "-":
                    notes[nutrient] = raw
        foods.append(
            {
                "id": f"ciqual:{code}",
                "name_fr": names[code][0],
                "name_en": names[code][1],
                "nutrients": nutrients,
                "nutrient_notes": notes,
            }
        )
    if len(foods) != 3484 or len({row["id"] for row in foods}) != 3484:
        raise ValueError("Unexpected food count or duplicate IDs")
    return sorted(foods, key=lambda row: str(row["id"]))


if __name__ == "__main__":
    data = build(Path(sys.argv[1]), Path(sys.argv[2]))
    target = Path(__file__).resolve().parents[1] / "data" / "ciqual-2025.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"Built {len(data)} source-attributed foods.")
