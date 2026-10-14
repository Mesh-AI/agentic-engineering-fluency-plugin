#!/usr/bin/env python3
# Copyright 2026 Rob De Feo and Indicium AI
# SPDX-License-Identifier: MIT
"""Validate a sessions-to-fluency score record against references/framework.json.

Usage: python3 <skill-dir>/scripts/check_record.py <record.yaml|record.json>
"""
import re
import sys

from record_io import PRACTICES_PHASE, competencies, load_framework, read_record


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: check_record.py <record.yaml|record.json>")
    expected = competencies(load_framework())
    by_id = {c["id"]: c for c in expected}
    record = read_record(sys.argv[1])
    scores = record.get("scores") or []
    errors = []

    ids = [s.get("competency") for s in scores]
    if ids != [c["id"] for c in expected]:
        errors.append(f"scores must list all {len(expected)} competencies in framework order")

    for s in scores:
        want = by_id.get(s.get("competency"))
        if not want:
            continue
        where, level, basis = s["competency"], s.get("level"), s.get("basis")
        if s.get("phase") != want["phase"]:
            errors.append(f"{where}: phase should be {want['phase']}")
        if s.get("status") not in ("scored", "not_applicable", "insufficient_evidence"):
            errors.append(f"{where}: bad status {s.get('status')}")
        if s.get("confidence") not in ("high", "medium", "low") and s.get("status") != "not_applicable":
            errors.append(f"{where}: bad confidence")
        if s.get("status") == "scored":
            if isinstance(level, bool) or level not in (0, 1, 2, 3):
                errors.append(f"{where}: level must be 0-3, got {level}")
                continue
            if basis not in ("observed", "not_observed"):
                errors.append(f"{where}: basis must be observed or not_observed")
            if level == 0 and want["phase"] == PRACTICES_PHASE:
                errors.append(f"{where}: practices have no L0")
            positive = [e for e in s.get("evidence") or [] if not re.match(r"\((negative|gap)\)", e, re.I)]
            if level >= 1 and basis == "observed" and not positive:
                errors.append(f"{where}: L{level} needs positive evidence")
            if level == 0 and basis != "not_observed":
                errors.append(f"{where}: L0 must be basis not_observed")
            if basis == "not_observed" and s.get("confidence") != "low":
                errors.append(f"{where}: not_observed must be low confidence")
        elif level is not None:
            errors.append(f"{where}: level must be null when status is {s.get('status')}")

    if errors:
        sys.exit("\n".join(f"- {e}" for e in errors))
    print(f"ok: {sys.argv[1]} ({len(ids)} competencies)")


if __name__ == "__main__":
    main()
