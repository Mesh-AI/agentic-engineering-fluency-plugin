#!/usr/bin/env python3
"""Compare score records: per-competency levels, median, and agreement.

Usage: python3 <skill-dir>/scripts/compare_runs.py a.yaml b.yaml [c.yaml ...]
"""
import sys

from record_io import read_record


def main():
    paths = sys.argv[1:]
    if len(paths) < 2:
        sys.exit("usage: compare_runs.py a.yaml b.yaml [c.yaml ...]")
    runs = [{s["competency"]: s.get("level") for s in read_record(p)["scores"]} for p in paths]
    show = lambda v: "-" if v is None else str(v)
    exact = near = 0
    print("competency".ljust(34) + "".join(f"r{i + 1}".ljust(4) for i in range(len(runs))) + "median")
    for cid in runs[0]:
        vals = [r.get(cid) for r in runs]
        nums = sorted(v for v in vals if v is not None)
        same = len(set(vals)) == 1
        close = same or (len(nums) == len(vals) and nums[-1] - nums[0] <= 1)
        exact += same
        near += close
        median = nums[(len(nums) - 1) // 2] if nums else "-"
        print(cid.ljust(34) + "".join(show(v).ljust(4) for v in vals) + str(median) + ("" if same else "   <-- differs"))
    n = len(runs[0])
    print(f"\nexact agreement: {exact}/{n} ({round(100 * exact / n)}%)   within one level: {near}/{n} ({round(100 * near / n)}%)")


if __name__ == "__main__":
    main()
