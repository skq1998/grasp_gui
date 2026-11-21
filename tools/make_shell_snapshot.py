#!/usr/bin/env python3
"""从 NIST ASD 全表生成发行版所需的紧凑离线快照。"""

import argparse
import base64
import csv
from datetime import date
import gzip
import io
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener

SOURCE = "https://physics.nist.gov/cgi-bin/ASD/ie.pl"
QUERY = {"units": 1, "format": 2, "at_num_out": "on", "sp_name_out": "on", "ion_charge_out": "on",
         "shells_out": "on", "level_out": "on", "e_out": 0, "unc_out": "on", "biblio": "on"}
CITATION = ("Kramida, A., Ralchenko, Yu., Reader, J., and NIST ASD Team (2024), "
            "NIST Atomic Spectra Database (v5.12), https://doi.org/10.18434/T4W30F")
MAX_DOWNLOAD = 4 * 1024 * 1024
MAX_SNAPSHOT = 200_000
ISOTOPES = {"D", "T"}
OUTPUT = Path(__file__).resolve().parents[1] / "grasp_shell_data.py"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def value(text):
    text = (text or "").strip()
    return text[2:-1] if text.startswith('="') and text.endswith('"') else text


def snapshot(raw, download_date):
    """保留 NIST 输出的分量、项和电离能标注；选择元素通用行。"""
    table = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    required = ["At. num", "Sp. Name", "Ion Charge", "Ground Shells (a)", "Ground Level",
                "Prefix", "Ionization Energy (b) (eV)", "Suffix", "Uncertainty (c) (eV)", "References"]
    if not set(required) <= set(table.fieldnames or ()):
        raise ValueError("NIST ASD did not return the expected ground-state CSV columns")
    rows = {}
    for row in table:
        z, spectrum, charge, shells, term, prefix, energy, suffix, uncertainty, refs = (
            value(row[k]) for k in required)
        if not z.isdigit():
            continue
        symbol = spectrum.split()[0]
        if symbol in ISOTOPES or symbol[0].isdigit():
            continue
        key = int(z), int(charge)
        if key in rows:
            raise ValueError(f"duplicate atomic number and charge: {key}")
        rows[key] = [shells, term, prefix + energy + suffix if energy else "", uncertainty, refs]
    if len(rows) < 6000:
        raise ValueError(f"incomplete NIST ASD query: {len(rows)} ions")
    payload = dict(source=SOURCE + "?" + urlencode(QUERY), retrieved=download_date, citation=CITATION,
                   rows=[[z, charge, *rows[z, charge]] for z, charge in sorted(rows)])
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)
    # Python 3.10 和 3.12 写相同的 gzip OS 字节。
    compressed = compressed[:9] + b"\xff" + compressed[10:]
    encoded = base64.b85encode(compressed)
    lines = [repr(encoded[i:i + 96]) for i in range(0, len(encoded), 96)]
    source = ('"""NIST ASD 离线快照，由 tools/make_shell_snapshot.py 生成。"""\n'
              f"# Download date: {download_date}\n# {CITATION}\n\nPAYLOAD = (\n"
              + "".join("    " + line + "\n" for line in lines) + ")\n")
    if len(source.encode()) > MAX_SNAPSHOT:
        raise ValueError(f"NIST ASD packaged snapshot exceeds {MAX_SNAPSHOT} bytes")
    return source, len(rows), len(compressed)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, help="Reuse a downloaded NIST ASD CSV without using the network")
    cli.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Date when the data were downloaded")
    cli.add_argument("--output", type=Path, default=OUTPUT)
    args = cli.parse_args()
    if args.input:
        raw = args.input.read_bytes()
    else:
        with build_opener(NoRedirect()).open(SOURCE + "?" + urlencode(QUERY), timeout=45) as response:
            raw = response.read(MAX_DOWNLOAD + 1)
    if len(raw) > MAX_DOWNLOAD:
        cli.error("NIST ASD table exceeds the download size limit")
    source, count, compressed = snapshot(raw, args.date.isoformat())
    args.output.write_text(source, encoding="utf-8", newline="\n")
    print(f"{len(source.encode())} bytes in {args.output.name}, {compressed} gzip bytes, {count} ions")


if __name__ == "__main__":
    main()
