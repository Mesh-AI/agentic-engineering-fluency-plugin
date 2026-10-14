#!/usr/bin/env python3
# Copyright 2026 Rob De Feo and Indicium AI
# SPDX-License-Identifier: MIT
"""Compare score records: per-competency levels, median, and agreement.

Usage: python3 <skill-dir>/scripts/compare_runs.py a.yaml b.yaml [c.yaml ...]
"""
import sys

from record_io import compatible_series, read_record


def main():
    paths = sys.argv[1:]
    if len(paths) < 2:
        sys.exit("usage: compare_runs.py a.yaml b.yaml [c.yaml ...]")
    records = [read_record(p) for p in paths]
    for key in ("skill_version", "framework_version"):
        versions = [(r.get("method") or {}).get(key) for r in records]
        if len({compatible_series(v) for v in versions}) > 1:
            listed = ", ".join(f"r{i + 1} {v or 'none'}" for i, v in enumerate(versions))
            print(f"warning: {key} differs in a breaking way ({listed}); levels may not be comparable\n")
    runs = [{s["competency"]: s.get("level") for s in r["scores"]} for r in records]
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
