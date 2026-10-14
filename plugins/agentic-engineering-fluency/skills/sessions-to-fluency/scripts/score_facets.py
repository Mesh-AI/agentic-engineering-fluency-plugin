#!/usr/bin/env python3
# Copyright 2026 Rob De Feo and Indicium AI
# SPDX-License-Identifier: MIT
"""Turn per-session facets + metrics into a fluency score record (L0-L3) with fixed rules.

No model judgement happens here: every level comes from the counts and thresholds below.

Usage:
  python3 <skill-dir>/scripts/score_facets.py --work DIR --out record.yaml [--engineer "Name"]

DIR is the collect_sessions.py output, with facets/<session>.json added by the classification
step. Writes record.yaml and record.json (same content; the other scripts read the JSON).
"""
import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

from record_io import PRACTICES_PHASE, competencies, load_framework, skill_version, write_record

# ---- Thresholds. Change these to calibrate; they are reported in the record. ----
T = {
    "routine_sessions": 2,          # L2: at least this many sessions with scoped work and a review step
    "l3_sessions": 2,               # L3: at least this many L3-qualifying sessions ...
    "l3_share": 0.4,                # ... and at least this share of the competency's sessions
    "high_confidence_sessions": 4,  # confidence high at this many sessions, medium at 2+
    "practice_share": 0.4,          # practices: share of sessions needed for a habit
    "tooling_kinds_l3": 2,          # tooling L3: kinds of non-default tooling in use
    "work_session_prompts": 3,      # token economics: sessions with this many prompts count as work
    "long_context": 350_000,        # token economics: requests above this many context tokens are long context
    "waste_flag": 0.10,             # token economics: a waste pattern at this share of input cost drops L3 to L2
    "waste_severe": 0.25,           # ... and at this share drops to L1
}
# Competencies whose L3 descriptor asks the engineer to judge the agent's approach against alternatives.
NEEDS_ALTERNATIVES = {"write-code", "design-architecture", "data-modelling"}
# Competencies whose L3 descriptor is about risk-scaled scrutiny rather than a spec.
NO_SPEC = {"debugging-code", "monitor-incident-response", "maintain-triage-bug-fix-patch", "security-update-libraries-apps"}

ENUMS = {
    "scope": ["question", "fragment", "scoped_task", "whole_unit"],
    "frame": ["none", "brief", "spec_or_plan"],
    "review": ["none", "ran_it", "manual", "risk_scaled", "automated"],
    "outcome": ["achieved", "partial", "not_achieved", "unclear"],
    "context": ["none", "ad_hoc_paste", "task_files", "project_context"],
    "decomposition": ["single_prompt", "brief", "spec_or_plan", "parallel"],
}
TRIVIAL_COMMANDS = {"/clear", "/model", "/copy", "/compact", "/context"}


def rank(kind, value):
    return ENUMS[kind].index(value)


def pct(x):
    return f"{math.floor(100 * x + 0.5)}%"


def needs_spec(cid):
    return cid not in NO_SPEC


def load_facets(work, index, job_ids):
    folder = work / "facets"
    facets = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []
    errors = []
    seen = {f.get("session") for f in facets}
    missing = [s["id"] for s in index if s["id"] not in seen]
    if missing:
        errors.append(f"no facets for sessions: {', '.join(missing)}")
    for f in facets:
        for j in f.get("jobs") or []:
            if j.get("competency") not in job_ids:
                errors.append(f"{f.get('session')}: unknown job competency {j.get('competency')}")
            for k in ("scope", "frame", "review", "outcome"):
                if j.get(k) not in ENUMS[k]:
                    errors.append(f"{f.get('session')}/{j.get('competency')}: bad {k} \"{j.get(k)}\"")
        p = f.get("practices") or {}
        for k in ("context", "decomposition"):
            if p.get(k) not in ENUMS[k]:
                errors.append(f"{f.get('session')}: bad practices.{k} \"{p.get(k)}\"")
    if errors:
        sys.exit("\n".join(f"- {e}" for e in errors))
    return facets


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--engineer", default="unknown")
    args = ap.parse_args()

    framework = load_framework()
    comps = competencies(framework)
    job_ids = [c["id"] for c in comps if c["phase"] != PRACTICES_PHASE]
    metrics = json.loads((args.work / "metrics.json").read_text(encoding="utf-8"))
    index = json.loads((args.work / "index.json").read_text(encoding="utf-8"))
    titles = {s["id"]: s.get("title") or s["id"][:8] for s in index}
    facets = load_facets(args.work, index, set(job_ids))

    n = len(facets)
    share = lambda count: count / n if n else 0
    confidence_for = lambda count: "high" if count >= T["high_confidence_sessions"] else "medium" if count >= 2 else "low"
    cite = lambda f, text: f"{titles.get(f['session'], f['session'])} ({f['session'][:8]}): {text}"

    # ---- Job competencies ----
    def is_l2(j):
        return rank("scope", j["scope"]) >= rank("scope", "scoped_task") and rank("review", j["review"]) >= rank("review", "manual")

    def is_l3(cid, j):
        return (j["scope"] == "whole_unit"
                and (not needs_spec(cid) or j["frame"] == "spec_or_plan")
                and j["review"] in ("risk_scaled", "automated")
                and j["outcome"] == "achieved"
                and (cid not in NEEDS_ALTERNATIVES or j.get("alternatives") is True))

    def score_job(cid):
        hits = [(f, j) for f in facets for j in f.get("jobs") or [] if j["competency"] == cid]
        count = len(hits)
        if not count:
            return {"level": 0, "basis": "not_observed", "confidence": "low", "evidence": [],
                    "next_step": "Not seen in Claude Code sessions. Confirm whether this job is part of the role (not_applicable) or is done without Claude Code."}
        l2 = [h for h in hits if is_l2(h[1])]
        l3 = [h for h in hits if is_l3(cid, h[1])]
        level = 1
        if len(l2) >= T["routine_sessions"]:
            level = 2
        if level == 2 and len(l3) >= T["l3_sessions"] and len(l3) / count >= T["l3_share"]:
            level = 3
        best = (l3 + [h for h in l2 if h not in l3] + [h for h in hits if h not in l2])[:3]
        alt = NEEDS_ALTERNATIVES & {cid}
        evidence = [
            f"{count} sessions; {len(l2)} scoped with a review step (L2); {len(l3)} whole-unit, framed, risk-scaled or automated checks, achieved{', alternatives judged' if alt else ''} (L3)",
            *[cite(f, f"{j['scope']}/{j['frame']}/{j['review']}{'/alternatives' if j.get('alternatives') else ''} - {j.get('evidence', '')}") for f, j in best],
        ]
        if level == 1:
            next_step = f"L2 needs {T['routine_sessions']}+ sessions of scoped work with a review step; seen {len(l2)}."
        elif level == 2:
            next_step = (f"L3 needs {T['l3_sessions']}+ sessions (and {pct(T['l3_share'])} of this job's sessions) delivering a whole unit"
                         f"{' from a spec or plan' if needs_spec(cid) else ''} with risk-scaled or automated checks"
                         f"{' and the approach judged against alternatives' if alt else ''}; seen {len(l3)} of {count}.")
        else:
            next_step = "L4 needs this way of working codified into mechanisms the team uses. Needs outside evidence and approval."
        return {"level": level, "basis": "observed", "confidence": confidence_for(count), "evidence": evidence, "next_step": next_step}

    scores = {cid: score_job(cid) for cid in job_ids}
    job_levels = [s["level"] for s in scores.values()]
    job_l3 = [cid for cid in job_ids if scores[cid]["level"] == 3]

    # ---- Agentic practices ----
    def values(k):
        return [(f, (f.get("practices") or {}).get(k)) for f in facets]

    def count_where(k, pred):
        return sum(1 for _, v in values(k) if v is not None and pred(v))

    def example_where(k, pred):
        return [cite(f, f.get("summary", "")) for f, v in values(k) if v is not None and pred(v)][:2]

    M = metrics["overall"]
    S = M["share_of_sessions"]
    env = metrics.get("environment") or {"user": {}, "projects": []}
    user_env, projects = env.get("user") or {}, env.get("projects") or []
    commands = (M.get("distinct") or {}).get("commands") or {}
    all_jobs = [j for f in facets for j in f.get("jobs") or []]
    jobs_n = max(len(all_jobs), 1)
    practice_count = math.ceil(T["practice_share"] * n)
    near = lambda ratio, cut: abs(ratio - cut) < 0.1  # within this distance of a threshold is borderline

    def practice(level_checks, evidence, next_for, borderline=False):
        # level_checks: [(level, passes), ...] from L2 upward; the level is the highest consecutive pass.
        level = 1
        for lv, passes in level_checks:
            if not passes:
                break
            level = lv
        any_positive = len(evidence) > 0
        if borderline:
            evidence = evidence + ["borderline: a ratio is within 10 points of its threshold, so small classification changes can move this level"]
        return {"level": level, "basis": "observed" if any_positive or level > 1 else "not_observed",
                "confidence": "medium" if n >= 8 and any_positive and not borderline else "low",
                "evidence": evidence, "next_step": next_for(level)}

    reviewed = sum(1 for j in all_jobs if rank("review", j["review"]) >= rank("review", "manual"))
    scaled = sum(1 for j in all_jobs if j["review"] in ("risk_scaled", "automated"))
    scores["verification"] = practice(
        [(2, bool(all_jobs) and reviewed / len(all_jobs) >= 0.5), (3, scaled >= 2 and scaled / jobs_n >= T["practice_share"])],
        [f"{reviewed}/{len(all_jobs)} job entries reviewed manually or better; {scaled} with risk-scaled or automated checks",
         f"metrics: tests run in {pct(S['with_tests_run'])} of sessions, lint/typecheck in {pct(S['with_lint_or_typecheck'])}"],
        lambda l: f"L3 needs risk-scaled or automated checks on {pct(T['practice_share'])}+ of job work; seen {scaled}/{len(all_jobs)}." if l < 3 else "L4 needs automated verification others rely on.",
        near(reviewed / jobs_n, 0.5) or near(scaled / jobs_n, T["practice_share"]),
    )

    guardrail_sessions = count_where("configured_guardrails", lambda v: v is True)
    own_hooks = (user_env.get("hooks") or 0) + sum(p.get("hooks") or 0 for p in projects)
    totals = M["totals"]
    scores["guardrails"] = practice(
        [(2, totals["rejections"] + totals["interrupts"] > 0 or S["with_tests_run"] > 0 or S["with_lint_or_typecheck"] > 0),
         (3, guardrail_sessions >= 2 or (guardrail_sessions >= 1 and own_hooks > 0))],
        [f"{totals['rejections']} rejected tool calls, {totals['interrupts']} interrupts, {totals['classifier_denials']} permission denials across {n} sessions",
         f"{guardrail_sessions} sessions configuring guardrails; hooks configured: {own_hooks}; permission modes used: {', '.join((M.get('sums') or {}).get('permission_modes') or {})}",
         *example_where("configured_guardrails", lambda v: v is True)],
        lambda l: "L3 needs guardrails configured for risk (hooks, permission rules, CLAUDE.md rules for known failures) in 2+ sessions." if l < 3 else "L4 needs guardrails others adopt.",
    )

    whole_units = sum(1 for j in all_jobs if j["scope"] == "whole_unit")
    scoped_plus = sum(1 for j in all_jobs if rank("scope", j["scope"]) >= rank("scope", "scoped_task"))
    scores["delegation-trust"] = practice(
        [(2, bool(all_jobs) and scoped_plus / len(all_jobs) >= 0.5), (3, 3 in job_levels and whole_units / jobs_n >= 0.3)],
        [f"{scoped_plus}/{len(all_jobs)} job entries were scoped tasks or whole units; {whole_units} whole units",
         f"job competencies at L3: {', '.join(job_l3) or 'none'}"],
        lambda l: "L3 needs whole units delegated end to end (30%+ of job work) and a job competency at L3." if l < 3 else "L4 needs agents orchestrated under your own guardrails.",
        near(scoped_plus / jobs_n, 0.5),
    )

    caught = count_where("caught_errors", lambda v: v > 0)
    judged = count_where("judged_alternatives", lambda v: v is True)
    scores["critical-evaluation-of-output"] = practice(
        [(2, caught >= 2), (3, judged >= 2 and judged >= math.ceil(0.25 * n))],
        [f"errors caught in {caught}/{n} sessions; approach judged against alternatives in {judged}/{n}",
         *example_where("judged_alternatives", lambda v: v is True)],
        lambda l: f"L3 needs the approach judged against alternatives in 2+ sessions and 25%+ of sessions; seen {judged}/{n}." if l < 3 else "L4 needs subtle errors caught in unfamiliar domains, shown to an approver.",
    )

    brief_plus = count_where("decomposition", lambda v: rank("decomposition", v) >= 1)
    spec_plus = count_where("decomposition", lambda v: rank("decomposition", v) >= 2)
    scores["problem-decomposition"] = practice(
        [(2, brief_plus >= math.ceil(0.5 * n)), (3, spec_plus >= 2 and spec_plus >= practice_count)],
        [f"brief or better in {brief_plus}/{n} sessions; spec, plan or parallel split in {spec_plus}/{n}",
         f"metrics: plan mode in {pct(S['with_plan_mode'])} of sessions, task lists in {pct(S['with_task_list'])}, subagents in {pct(S['with_subagents'])}"],
        lambda l: f"L3 needs spec- or plan-first work in {pct(T['practice_share'])}+ of sessions; seen {spec_plus}/{n}." if l < 3 else "L4 needs parallelisable decomposition with defined interfaces.",
        near(share(brief_plus), 0.5) or near(share(spec_plus), T["practice_share"]),
    )

    task_ctx = count_where("context", lambda v: rank("context", v) >= rank("context", "task_files"))
    proj_ctx = count_where("context", lambda v: v == "project_context")
    scores["context-engineering"] = practice(
        [(2, task_ctx >= math.ceil(0.5 * n)), (3, proj_ctx >= 2 and (proj_ctx >= practice_count or S["editing_agent_context"] >= 0.2))],
        [f"task-level context or better in {task_ctx}/{n} sessions; standing project context in {proj_ctx}/{n}",
         f"metrics: edited CLAUDE.md or memory in {pct(S['editing_agent_context'])} of sessions; projects with CLAUDE.md: {sum(1 for p in projects if p.get('claude_md'))}/{len(projects)}; global CLAUDE.md: {'yes' if user_env.get('global_claude_md') else 'no'}"],
        lambda l: f"L3 needs curated project context (CLAUDE.md, memory, kept specs) used in {pct(T['practice_share'])}+ of sessions; seen {proj_ctx}/{n}." if l < 3 else "L4 needs layered static/dynamic context across complex systems.",
        near(share(task_ctx), 0.5) or near(share(proj_ctx), T["practice_share"]),
    )

    tooling_kinds = {
        "skills": S["with_skills"] >= 0.2,
        "mcp": S["with_mcp"] >= 0.2,
        "subagents": S["with_subagents"] >= 0.2,
        "hooks": own_hooks > 0,
        "parallel_sessions": M["parallel_share_of_active_time"] >= 0.1,
        "plan_or_task_tools": S["with_plan_mode"] + S["with_task_list"] >= 0.2,
    }
    kinds = [k for k, v in tooling_kinds.items() if v]
    scores["tooling"] = practice(
        [(2, n > 0), (3, len(kinds) >= T["tooling_kinds_l3"])],
        [f"non-default tooling in regular use (20%+ of sessions): {', '.join(kinds) or 'none'}",
         f"metrics: skills {pct(S['with_skills'])}, MCP {pct(S['with_mcp'])}, subagents {pct(S['with_subagents'])} of sessions; parallel {pct(M['parallel_share_of_active_time'])} of active time; MCP servers: {', '.join(M['distinct'].get('mcp_servers') or []) or 'none'}"],
        lambda l: f"L3 needs {T['tooling_kinds_l3']}+ kinds of non-default tooling in regular use." if l < 3 else "L4 needs tools you built to fill gaps.",
    )

    built_sessions = count_where("built_reusable_tooling", lambda v: v is True)
    own_skills = user_env.get("own_skills") or []
    own_tooling = len(own_skills) + sum((p.get("commands") or 0) + (p.get("agents") or 0) for p in projects)
    scores["reusability-packaging"] = practice(
        [(2, S["with_skills"] > 0 or built_sessions > 0 or any(c not in TRIVIAL_COMMANDS for c in commands)),
         (3, built_sessions >= 2 or (built_sessions >= 1 and S["editing_agent_tooling"] > 0))],
        [f"{built_sessions}/{n} sessions created or changed reusable tooling; skills invoked in {pct(S['with_skills'])} of sessions: {', '.join(M['distinct'].get('skills') or []) or 'none'}",
         f"environment: {len(own_skills)} user-level skills, {own_tooling} skills/commands/agents across user and project config (authorship not checked)",
         *example_where("built_reusable_tooling", lambda v: v is True)],
        lambda l: "L3 needs your own repeat work codified as skills, commands or scripts in 2+ sessions." if l < 3 else "L4 needs the team to adopt them.",
    )

    measured = count_where("measured_results", lambda v: v is True)
    scores["measurement"] = practice(
        [(2, measured >= 1), (3, measured >= 2)],
        [f"measured or compared results in {measured}/{n} sessions", *example_where("measured_results", lambda v: v is True)] if measured else [],
        lambda l: ("L2 needs a session where you measured your own results with AI, such as time saved or rework." if l < 2 else
                   "L3 needs tracking of your own results with AI (2+ sessions showing it)." if l < 3 else "L4 needs tracking at team level."),
    )

    # Token economics: start at L3 and drop for waste, measured as a share of input cost the way
    # Claude Code's /usage flags long context and cache misses.
    cost_sessions = count_where("cost_or_context_management", lambda v: v is True)
    work = [s for s in metrics.get("sessions") or [] if s.get("user_prompts", 0) >= T["work_session_prompts"]]
    w = len(work)
    # Each request is [context tokens, input cost, cost of an idle cache rebuild]; see collect_sessions.py.
    long_cost = lambda r: r[1] if r[0] > T["long_context"] else 0
    miss_cost = lambda r: r[2]
    total_cost = sum(r[1] for s in work for r in s.get("requests") or []) or 1
    share_of = lambda cost_of: sum(cost_of(r) for s in work for r in s.get("requests") or []) / total_cost

    def worst(cost_of):
        ranked = sorted(((sum(cost_of(r) for r in s.get("requests") or []), s["id"][:8]) for s in work), reverse=True)
        top = [sid for cost, sid in ranked[:3] if cost > 0]
        return f"; most in {', '.join(top)}" if top else ""

    long_share, miss_share = share_of(long_cost), share_of(miss_cost)
    peaks = sorted(s.get("peak_context") or 0 for s in work)
    kt = lambda x: f"{round(x / 1000)}k"
    shares = (miss_share, long_share)
    tips = []
    if miss_share >= T["waste_flag"]:
        tips.append("after a break longer than the cache lifetime (1 hour on a subscription, 5 minutes on an API key), start a new session or resume from a summary instead of continuing a large one")
    if long_share >= T["waste_flag"]:
        tips.append(f"keep context under {kt(T['long_context'])}: /clear between unrelated tasks, /compact at natural breaks, and send verbose work to subagents")
    scores["token-economics"] = practice(
        [(2, w >= T["routine_sessions"] and max(shares) < T["waste_severe"]),
         (3, max(shares) < T["waste_flag"])],
        [f"cache rebuilt after idling past the cache lifetime: {pct(miss_share)} of input cost in {w} work sessions ({T['work_session_prompts']}+ prompts)"
         + worst(miss_cost),
         f"requests over {kt(T['long_context'])} context: {pct(long_share)} of input cost" + worst(long_cost),
         f"peak context per work session: median {kt(peaks[(w - 1) // 2]) if w else '-'}, largest {kt(peaks[-1]) if w else '-'}",
         f"deliberate cost or context management in {cost_sessions}/{n} sessions",
         f"metrics: models used {', '.join(M['distinct'].get('models') or [])}; cache read ratio {M.get('cache_read_ratio')}"],
        lambda l: (f"L2 needs {T['routine_sessions']}+ work sessions to measure; seen {w}." if w < T["routine_sessions"] else
                   f"L2 needs each waste pattern under {pct(T['waste_severe'])} of input cost (L3: under {pct(T['waste_flag'])}): " + "; ".join(tips) + "." if l < 2 else
                   f"L3 needs each waste pattern under {pct(T['waste_flag'])} of input cost: " + "; ".join(tips) + "." if l < 3 else
                   "L4 needs cost controls others adopt."),
        any(abs(x - cut) < 0.03 for x in shares for cut in (T["waste_flag"], T["waste_severe"])),
    )

    if all(l == 0 for l in job_levels):
        for c in comps:
            if c["phase"] == PRACTICES_PHASE:
                scores[c["id"]] = {"status": "not_applicable", "level": None, "confidence": "low", "evidence": [], "next_step": "No job work seen."}

    # ---- Consistency checks (reported, never auto-applied) ----
    lv = lambda cid: scores[cid]["level"] or 0
    checks = [
        f"fired: {', '.join(job_l3)} at L3 with verification L{lv('verification')} and critical evaluation L{lv('critical-evaluation-of-output')}"
        if job_l3 and (lv("verification") < 3 or lv("critical-evaluation-of-output") < 3)
        else "job L3 vs verification/critical evaluation: passed",
        f"fired: delegation & trust L{lv('delegation-trust')} with {len(job_l3)} job L3s"
        if bool(job_l3) != (lv("delegation-trust") == 3)
        else "job L3 vs delegation & trust: passed",
    ]

    # ---- Record ----
    home = str(Path.home())
    window = f"{metrics['since']} to {metrics.get('until') or 'now'}" if metrics.get("since") else f"last {metrics.get('window_days')} days"
    record = {
        "engineer": args.engineer,
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "source": {
            "type": "claude-code-sessions",
            "projects_dir": (metrics.get("projects_dir") or "").replace(home, "~", 1),
            "period": f"{(M.get('first') or '')[:10]} to {(M.get('last') or '')[:10]}",
            "window": window,
            "sessions": n,
            "user_prompts": totals["user_prompts"],
            "active_minutes": M.get("active_minutes"),
        },
        "method": {
            "skill": "sessions-to-fluency",
            "skill_version": skill_version(),
            "framework_version": framework["version"],
            "max_level": 3,
            "note": "Levels computed by fixed rules from per-session facets and transcript metrics. L4-L5 need outside evidence and approval. L0 means not seen in Claude Code sessions.",
            "thresholds": T,
        },
        "scores": [],
        "summary": {},
    }
    for c in comps:
        s = scores[c["id"]]
        entry = {"competency": c["id"], "phase": c["phase"], "level": s["level"], "status": s.get("status", "scored")}
        if "status" not in s:
            entry["basis"] = s["basis"]
        entry.update(confidence=s["confidence"], evidence=s["evidence"], next_step=s["next_step"])
        record["scores"].append(entry)
    for p in framework["phases"]:
        record["summary"][p["id"]] = "; ".join(
            f"{c['name']} L{'-' if scores[c['id']]['level'] is None else scores[c['id']]['level']}" for c in p["competencies"])
    record["summary"]["consistency_checks"] = checks

    write_record(record, args.out)
    print(f"wrote {args.out} and {args.out.with_suffix('.json').name}")
    for p in framework["phases"]:
        print(f"  {p['name']}: {record['summary'][p['id']]}")


if __name__ == "__main__":
    main()
