import json
import re
from pathlib import Path

import camelot

ROOT = Path(__file__).parent
PDF = max(ROOT.glob("stammdaten*.pdf"), key=lambda path: path.stat().st_mtime)
WKN = re.compile(r"^[A-Z0-9]{6}$")
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")

tables = camelot.read_pdf(str(PDF), pages="all", flavor="stream")
stocks = []

for table in tables:
    for row in table.df.values.tolist():
        if len(row) < 4:
            continue
        wkn, isin, name, shortcode = (value.strip() for value in row[:4])
        if WKN.fullmatch(wkn) and ISIN.fullmatch(isin):
            stocks.append([wkn, isin, name, shortcode])

with open(ROOT / "isins.json", "w") as f:
    json.dump(stocks, f)

print("Converted {0} stocks from {1}".format(len(stocks), PDF.name))
