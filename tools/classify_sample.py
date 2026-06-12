"""Classify a captured ft8mon output file and print a breakdown (dev tool).

Usage:
    python tools/classify_sample.py [path]

Defaults to docs/samples/ft8mon_output.txt. Prints counts by message kind and
dumps every REJECT line so the accept/reject split can be eyeballed against
real data.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watchersattherim.monitor.parser import Kind, classify, parse_line  # noqa: E402

DEFAULT = Path(__file__).resolve().parent.parent / "docs/samples/ft8mon_output.txt"


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    lines = path.read_text().splitlines()

    decodes = 0
    kinds: Counter[str] = Counter()
    rejects: list[str] = []
    cq_with_grid = 0
    std_with_grid = 0
    std_with_report = 0

    for line in lines:
        d = parse_line(line)
        if d is None:
            continue
        decodes += 1
        m = classify(d.message)
        kinds[m.kind.value] += 1
        if m.kind is Kind.REJECT:
            rejects.append(f"{m.reject_reason:16} | {d.message}")
        if m.kind is Kind.CQ and m.grid:
            cq_with_grid += 1
        if m.kind is Kind.STANDARD and m.grid:
            std_with_grid += 1
        if m.kind is Kind.STANDARD and m.report_db is not None:
            std_with_report += 1

    print(f"file: {path}")
    print(f"lines: {len(lines)}   decode lines: {decodes}")
    print("\nby kind:")
    for kind, n in kinds.most_common():
        print(f"  {kind:10} {n:5}  ({n / decodes:5.1%})")
    print("\ndetail:")
    print(f"  CQ with grid (direct-capable):        {cq_with_grid}")
    print(f"  STANDARD with grid (direct-capable):  {std_with_grid}")
    print(f"  STANDARD with report (indirect-able): {std_with_report}")

    if rejects:
        print(f"\nrejects ({len(rejects)}):")
        for r in rejects:
            print(f"  {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
