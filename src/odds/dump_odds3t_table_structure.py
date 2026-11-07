import argparse
import csv
from pathlib import Path
from itertools import permutations
import sys

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.odds.fetch_today_odds3t import parse_odds_table


def build_url(jcd: str, rno: int, hd: str) -> str:
    return f"https://www.boatrace.jp/owpc/pc/race/odds3t?jcd={jcd}&rno={rno}&hd={hd}"


def dump_raw_cells(table, out_csv: Path) -> None:
    rows = table.find_all("tr")
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "row_idx",
                "cell_idx",
                "tag",
                "text",
                "rowspan",
                "colspan",
                "class",
            ]
        )
        for r_idx, tr in enumerate(rows):
            cells = tr.find_all(["th", "td"])
            for c_idx, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                rowspan = int(cell.get("rowspan", 1))
                colspan = int(cell.get("colspan", 1))
                cls = " ".join(cell.get("class", []))
                w.writerow([r_idx, c_idx, cell.name, text, rowspan, colspan, cls])


def expand_grid(table):
    rows = table.find_all("tr")
    # grid[row][col] = dict
    grid = []
    active = {}  # col -> (remaining_rows, payload)
    max_col = 0

    for r_idx, tr in enumerate(rows):
        row = []
        c_ptr = 0

        # place carried rowspans first (as placeholders while seeking next free col)
        def is_occupied(col):
            return any(item["col"] == col for item in row)

        # consume row's real cells
        for cell_idx, cell in enumerate(tr.find_all(["th", "td"])):
            while c_ptr in active or is_occupied(c_ptr):
                c_ptr += 1

            text = cell.get_text(strip=True)
            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))
            cls = " ".join(cell.get("class", []))

            for dc in range(colspan):
                col = c_ptr + dc
                payload = {
                    "row_idx": r_idx,
                    "col": col,
                    "source_row": r_idx,
                    "source_cell_idx": cell_idx,
                    "tag": cell.name,
                    "text": text,
                    "class": cls,
                    "is_rowspan_fill": False,
                    "rowspan_left_after_this_row": max(rowspan - 1, 0),
                }
                row.append(payload)
                if rowspan > 1:
                    active[col] = {
                        "remaining": rowspan - 1,
                        "tag": cell.name,
                        "text": text,
                        "class": cls,
                        "source_row": r_idx,
                        "source_cell_idx": cell_idx,
                    }
            c_ptr += colspan

        # fill remaining active rowspans not overwritten by real cells
        for col, info in list(active.items()):
            if not any(x["col"] == col for x in row):
                row.append(
                    {
                        "row_idx": r_idx,
                        "col": col,
                        "source_row": info["source_row"],
                        "source_cell_idx": info["source_cell_idx"],
                        "tag": info["tag"],
                        "text": info["text"],
                        "class": info["class"],
                        "is_rowspan_fill": True,
                        "rowspan_left_after_this_row": info["remaining"] - 1,
                    }
                )
            info["remaining"] -= 1
            if info["remaining"] <= 0:
                del active[col]

        row = sorted(row, key=lambda x: x["col"])
        max_col = max(max_col, row[-1]["col"] + 1 if row else 0)
        grid.append(row)

    return grid, max_col


def dump_expanded_grid(grid, max_col: int, out_csv: Path) -> None:
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "row_idx",
                "col_idx",
                "text",
                "tag",
                "source_row",
                "source_cell_idx",
                "is_rowspan_fill",
                "class",
            ]
        )
        for row in grid:
            row_idx = row[0]["row_idx"] if row else None
            mapped = {x["col"]: x for x in row}
            for col in range(max_col):
                cell = mapped.get(col)
                if cell is None:
                    w.writerow([row_idx, col, "", "", "", "", "", ""])
                else:
                    w.writerow(
                        [
                            cell["row_idx"],
                            cell["col"],
                            cell["text"],
                            cell["tag"],
                            cell["source_row"],
                            cell["source_cell_idx"],
                            cell["is_rowspan_fill"],
                            cell["class"],
                        ]
                    )


def build_missing_note(race_id: str, html: str, out_md: Path) -> None:
    # current parser output for comparison (known to be 90)
    parsed = parse_odds_table(html, race_id)
    parsed_set = set(x["trifecta"] for x in parsed)
    all_tri = set("-".join(map(str, p)) for p in permutations([1, 2, 3, 4, 5, 6], 3))
    missing = sorted(all_tri - parsed_set)

    first_counts = {}
    pair_counts = {}
    for tri in parsed_set:
        f, s, t = tri.split("-")
        first_counts[f] = first_counts.get(f, 0) + 1
        pair_counts[(f, s)] = pair_counts.get((f, s), 0) + 1

    with out_md.open("w", encoding="utf-8") as f:
        f.write("# odds3t triangle structure memo\n\n")
        f.write(f"- race_id: `{race_id}`\n")
        f.write(f"- parsed trifecta count (current parser): **{len(parsed_set)}**\n")
        f.write(f"- missing count: **{len(missing)}**\n")
        f.write("- missing trifecta sample (first 30):\n")
        for tri in missing[:30]:
            f.write(f"  - `{tri}`\n")
        f.write("\n")
        f.write("- first-lane counts (should be 20 each at full recovery):\n")
        for key in sorted(first_counts.keys(), key=int):
            f.write(f"  - first={key}: {first_counts[key]}\n")
        f.write("\n")
        f.write("- (first,second) counts (should be 4 each):\n")
        for key in sorted(pair_counts.keys(), key=lambda x: (int(x[0]), int(x[1]))):
            f.write(f"  - {key[0]}-{key[1]}: {pair_counts[key]}\n")
        f.write("\n")
        f.write("## suspected root cause\n\n")
        f.write("- Block row2-4 column mapping is not a simple left-to-right 6-pair map.\n")
        f.write("- Due to rowspan inheritance, effective column placement shifts inside each block.\n")
        f.write("- The missing 30 patterns are concentrated where the shifted columns are interpreted as the wrong first-lane.\n")
        f.write("\n")
        f.write("## rule proposal for 120 reconstruction\n\n")
        f.write("- Use expanded grid (`expanded_grid.csv`) col index as canonical placement.\n")
        f.write("- Header row (row_idx=0) even columns are first-lane anchors.\n")
        f.write("- For each block, row1 provides explicit second and third per first-column.\n")
        f.write("- For block row2-4, map third/odds pairs to first-lane by grid column anchors, not local td order.\n")
        f.write("- After mapping, enforce validation:\n")
        f.write("  - count=120\n")
        f.write("  - unique trifecta\n")
        f.write("  - first,second,third all distinct\n")
        f.write("  - first count=20 each\n")
        f.write("  - (first,second) count=4 each\n")


def main():
    parser = argparse.ArgumentParser(description="Dump odds3t table structure for one race")
    parser.add_argument("--race-id", default="", help="optional race_id label")
    parser.add_argument("--jcd", required=True)
    parser.add_argument("--rno", required=True, type=int)
    parser.add_argument("--hd", required=True, help="YYYYMMDD")
    parser.add_argument("--out-dir", default="reports/odds3t_debug")
    args = parser.parse_args()

    race_id = args.race_id or f"{args.hd}-B??????-{args.rno:02d}"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    url = build_url(args.jcd, args.rno, args.hd)
    html = requests.get(url, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise ValueError("odds table not found")
    table = tables[1]

    dump_raw_cells(table, out_dir / "table_dump.csv")
    grid, max_col = expand_grid(table)
    dump_expanded_grid(grid, max_col, out_dir / "expanded_grid.csv")
    build_missing_note(race_id, html, out_dir / "missing_30_memo.md")

    print(f"saved: {out_dir / 'table_dump.csv'}")
    print(f"saved: {out_dir / 'expanded_grid.csv'}")
    print(f"saved: {out_dir / 'missing_30_memo.md'}")
    print(f"url: {url}")


if __name__ == "__main__":
    main()
