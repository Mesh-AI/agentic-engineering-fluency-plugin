#!/usr/bin/env python3
"""Collect Claude Code session transcripts into digests and metrics for fluency scoring.

Stdlib only. Reads <projects>/<project>/<session>.jsonl (the main sessions; subagent
transcripts are only counted) inside a date window, and writes to --out:

  sessions/<session-id>.md   one compact, readable digest per session
  metrics.json               deterministic per-session and overall metrics
  index.json                 the session list (id, project, start, end, title, digest path)

Usage:
  collect_sessions.py --out DIR [--projects ~/.claude/projects] [--days 30]
                      [--project SUBSTRING ...] [--exclude-session ID ...] [--exclude-latest]
                      [--skip-entrypoint sdk-cli ...] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

MAX_USER = 800      # characters kept per user prompt
MAX_TEXT = 300      # characters kept per assistant text block
MAX_TOOL = 160      # characters kept per tool call summary
MAX_DIGEST = 45000  # characters per session digest before the middle is cut

BASH_CATEGORIES = {
    "test": r"\b(pytest|jest|vitest|mocha|go test|cargo test|mvn test|gradle\w* test|dotnet test|rspec|phpunit|playwright|cypress|npm (run )?test|yarn test|pnpm test)\b",
    "lint_typecheck": r"\b(eslint|tsc|ruff|mypy|pylint|flake8|golangci-lint|prettier --check|astro check|npm run (lint|check|typecheck))\b",
    "build": r"\b(npm run build|yarn build|pnpm build|make\b|mvn |gradle|cargo build|go build|dotnet build)",
    "git": r"(^|&& |; )git ",
    "pr": r"\bgh pr\b",
    "pr_review": r"\bgh pr (view|diff|review|checks)\b|\bgh api .*pulls/\d+/(comments|reviews)",
    "deploy_infra": r"\b(terraform|kubectl|helm|docker|podman|aws |gcloud|az |cdk |pulumi|ansible|flyctl|vercel|netlify|wrangler)\b",
    "data": r"\b(psql|mysql|sqlite3|bq |duckdb|mongosh|redis-cli|dbt )\b",
    "security": r"\b(npm audit|pip-audit|trivy|snyk|semgrep|gitleaks|osv-scanner|bandit|grype|dependabot)\b",
    "observability": r"\b(logs|journalctl|kubectl logs|datadog|grafana|cloudwatch|sentry)\b",
}

FILE_CATEGORIES = [  # first match wins
    ("agent_tooling", r"\.claude/(skills|commands|agents|hooks)/|\.claude/settings(\.local)?\.json$|\.mcp\.json$|plugin\.json$|/hooks/"),
    ("agent_context", r"(^|/)(CLAUDE|AGENTS)\.md$|\.claude/rules/|/memory/"),
    ("tests", r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.\w+$|(^|/)test_\w+\.py$"),
    ("data_schema", r"migration|schema|\.sql$|prisma|\.dbml$|models?/"),
    ("infra_ci", r"\.tf$|\.tfvars$|\.bicep$|Dockerfile|Containerfile|(docker-)?compose\.ya?ml$|\.github/(workflows|actions)/|\.gitlab-ci|"
                 r"\.circleci/|azure-pipelines|bitbucket-pipelines|buildkite|cloudbuild|Jenkinsfile|serverless\.ya?ml$|"
                 r"(^|/)(helm|charts|k8s|kubernetes|manifests|kustomiz\w*|terraform|infra|deploy\w*)/|Chart\.ya?ml$|kustomization\.ya?ml$"),
    ("docs", r"\.(md|mdx|rst|adoc)$|(^|/)docs?/"),
    ("code", r"\.\w+$"),
]

CONTEXT_COMMANDS = {"/clear", "/compact", "/context", "/model", "/cost", "/usage", "/memory", "/init", "/insights", "/resume"}


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def clip(s, n):
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def tool_summary(name, inp):
    if not isinstance(inp, dict):
        return name
    for key in ("command", "file_path", "path", "pattern", "url", "query", "skill", "description", "prompt"):
        if key in inp:
            return f"{name}: {clip(inp[key], MAX_TOOL)}"
    return name


def file_category(path):
    for cat, rx in FILE_CATEGORIES:
        if re.search(rx, path):
            return cat
    return "other"


def user_text(content):
    """Return (kind, text) for a user record: prompt, command, interrupt, meta or None."""
    if isinstance(content, str):
        text = content
    else:
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if not parts:
            return None, None
        text = "\n".join(parts)
    if text.startswith("[Request interrupted"):
        return "interrupt", text
    m = re.search(r"<command-name>(/[\w:.-]+)</command-name>", text)
    if m:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return "command", m.group(1) + (" " + clip(args.group(1), 200) if args and args.group(1).strip() else "")
    if text.startswith(("<local-command", "<system-reminder", "<task-notification", "Caveat:")):
        return None, None
    return "prompt", text


def collect(path: Path):
    s = {
        "id": path.stem, "project": path.parent.name, "title": None, "start": None, "end": None,
        "models": Counter(), "entrypoints": Counter(), "permission_modes": Counter(),
        "user_prompts": 0, "user_answers": 0, "interrupts": 0, "rejections": 0, "classifier_denials": 0, "tool_errors": 0,
        "tools": Counter(), "skills": Counter(), "mcp_servers": Counter(), "commands": Counter(),
        "bash": Counter(), "files": Counter(), "tokens": Counter(), "cwds": Counter(),
    }
    lines = []
    seen_uuids, seen_msgs, seen_prompts = set(), set(), set()
    ask_ids = set()  # AskUserQuestion tool calls, whose results hold the engineer's answers
    s["activity"] = set()  # 5-minute buckets with a record, for parallel-session detection
    for raw in path.open(encoding="utf-8", errors="replace"):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t == "ai-title":
            s["title"] = d.get("aiTitle")
            continue
        if d.get("isSidechain") or t not in ("user", "assistant"):
            continue
        if d.get("uuid") in seen_uuids:
            continue
        seen_uuids.add(d.get("uuid"))
        ts = parse_ts(d.get("timestamp", ""))
        if ts:
            s["activity"].add(int(ts.timestamp() // 300))
            s["start"] = min(s["start"], ts) if s["start"] else ts
            s["end"] = max(s["end"], ts) if s["end"] else ts
        if d.get("cwd"):
            s["cwds"][d["cwd"]] += 1
        if d.get("entrypoint"):
            s["entrypoints"][d["entrypoint"]] += 1
        if d.get("permissionMode"):
            s["permission_modes"][d["permissionMode"]] += 1
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "user":
            if d.get("isMeta"):
                continue
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in ask_ids:
                        answers = re.sub(r"^Your questions have been answered:\s*|\s*You can now continue.*$", "", str(b.get("content")), flags=re.S)
                        s["user_answers"] += 1
                        lines.append(f"\n[USER ANSWERED CLAUDE'S QUESTION] {clip(answers, MAX_USER)}")
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        raw_text = str(b.get("content"))
                        text = clip(raw_text, 200)
                        if "doesn't want to proceed" in text:
                            s["rejections"] += 1
                            said = re.search(r"the user said:\s*(.*?)(?:\n\nNote:|$)", raw_text, re.S)
                            reason = clip(said.group(1), MAX_USER) if said else "(no reason given)"
                            lines.append(f"  !! USER REJECTED tool call. Reason: {reason}")
                        elif "classifier" in text:
                            s["classifier_denials"] += 1
                            lines.append(f"  !! permission classifier denied: {text}")
                        else:
                            s["tool_errors"] += 1
                            lines.append(f"  !! tool error: {clip(text, 140)}")
            kind, text = user_text(content)
            if kind == "prompt":
                if text in seen_prompts:
                    continue
                seen_prompts.add(text)
                s["user_prompts"] += 1
                lines.append(f"\n[USER] {clip(text, MAX_USER)}")
            elif kind == "interrupt":
                s["interrupts"] += 1
                lines.append("\n[USER INTERRUPTED]")
            elif kind == "command":
                s["commands"][text.split()[0]] += 1
                lines.append(f"\n[USER COMMAND] {text}")
        else:
            if msg.get("id") not in seen_msgs:  # one API message is split over several records
                seen_msgs.add(msg.get("id"))
                if msg.get("model") and msg["model"] != "<synthetic>":
                    s["models"][msg["model"]] += 1
                for k, v in (msg.get("usage") or {}).items():
                    if isinstance(v, int):
                        s["tokens"][k] += v
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    lines.append(f"[CLAUDE] {clip(b['text'], MAX_TEXT)}")
                elif b.get("type") == "tool_use":
                    name, inp = b.get("name", "?"), b.get("input") or {}
                    s["tools"][name] += 1
                    if name == "AskUserQuestion":
                        ask_ids.add(b.get("id"))
                    if name.startswith("mcp__"):
                        s["mcp_servers"][name.split("__")[1]] += 1
                    if name == "Skill":
                        s["skills"][str(inp.get("skill"))] += 1
                    if name == "Bash":
                        cmd = str(inp.get("command", ""))
                        for cat, rx in BASH_CATEGORIES.items():
                            if re.search(rx, cmd):
                                s["bash"][cat] += 1
                    if name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                        s["files"][file_category(str(inp.get("file_path", "")))] += 1
                    lines.append(f"  -> {tool_summary(name, inp)}")
    return s, lines


def subagent_count(path: Path):
    d = path.parent / path.stem / "subagents"
    return len(list(d.glob("*.jsonl"))) if d.is_dir() else 0


def digest(s, lines):
    head = [
        f"# Session {s['id']}",
        f"- project: {s['project']}",
        f"- title: {s['title'] or '-'}",
        f"- time: {s['start']:%Y-%m-%d %H:%M} to {s['end']:%Y-%m-%d %H:%M} UTC",
        f"- user prompts: {s['user_prompts']}, answers to Claude's questions: {s['user_answers']}, interrupts: {s['interrupts']}, rejected tool calls: {s['rejections']}, tool errors: {s['tool_errors']}",
        f"- tools: {dict(s['tools'].most_common(12))}",
        f"- files edited by category: {dict(s['files'])}",
        f"- bash categories: {dict(s['bash'])}",
        f"- subagent transcripts: {s['subagents']}, skills: {dict(s['skills'])}, permission modes: {dict(s['permission_modes'])}",
        "",
        "## Transcript (condensed)",
    ]
    body = "\n".join(lines)
    if len(body) > MAX_DIGEST:
        half = MAX_DIGEST // 2
        body = body[:half] + "\n\n[… middle of session cut for length …]\n\n" + body[-half:]
    return "\n".join(head) + "\n" + body + "\n"


def plain(s):
    out = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in s.items() if k != "activity"}
    out["active_minutes"] = len(s["activity"]) * 5
    out["start"], out["end"] = s["start"].isoformat(), s["end"].isoformat()
    out["minutes"] = round((s["end"] - s["start"]).total_seconds() / 60, 1)
    return out


def overall(sessions):
    total = Counter()
    sums = {}
    for s in sessions:
        for key in ("cwds", "tools", "skills", "mcp_servers", "commands", "bash", "files", "tokens", "models", "permission_modes"):
            sums.setdefault(key, Counter()).update(s[key])
        for key in ("user_prompts", "interrupts", "rejections", "classifier_denials", "tool_errors", "subagents"):
            total[key] += s[key]
    n = len(sessions)
    tok = sums["tokens"]
    cache_total = tok["cache_read_input_tokens"] + tok["cache_creation_input_tokens"] + tok["input_tokens"]
    buckets = Counter(b for s in sessions for b in s["activity"])
    parallel = sum(1 for c in buckets.values() if c > 1)
    per = lambda k: round(total[k] / n, 2) if n else 0
    share = lambda pred: round(sum(1 for s in sessions if pred(s)) / n, 2) if n else 0
    return {
        "sessions": n,
        "first": min(s["start"] for s in sessions).isoformat() if n else None,
        "last": max(s["end"] for s in sessions).isoformat() if n else None,
        "totals": dict(total),
        "per_session": {k: per(k) for k in ("user_prompts", "interrupts", "rejections", "tool_errors", "subagents")},
        "share_of_sessions": {
            "with_subagents": share(lambda s: s["subagents"] or s["tools"].get("Agent") or s["tools"].get("Task")),
            "with_plan_mode": share(lambda s: s["tools"].get("ExitPlanMode") or s["tools"].get("EnterPlanMode") or s["permission_modes"].get("plan")),
            "with_task_list": share(lambda s: s["tools"].get("TodoWrite") or s["tools"].get("TaskCreate")),
            "with_skills": share(lambda s: s["skills"]),
            "with_mcp": share(lambda s: s["mcp_servers"]),
            "with_tests_run": share(lambda s: s["bash"].get("test")),
            "with_lint_or_typecheck": share(lambda s: s["bash"].get("lint_typecheck")),
            "with_interrupt_or_rejection": share(lambda s: s["interrupts"] or s["rejections"]),
            "editing_agent_context": share(lambda s: s["files"].get("agent_context")),
            "editing_agent_tooling": share(lambda s: s["files"].get("agent_tooling")),
            "with_context_commands": share(lambda s: any(c in CONTEXT_COMMANDS for c in s["commands"])),
        },
        "active_minutes": len(buckets) * 5,
        "parallel_share_of_active_time": round(parallel / len(buckets), 3) if buckets else 0,
        "cache_read_ratio": round(tok["cache_read_input_tokens"] / cache_total, 3) if cache_total else None,
        "distinct": {
            "models": sorted(sums["models"]),
            "skills": sorted(sums["skills"]),
            "mcp_servers": sorted(sums["mcp_servers"]),
            "commands": dict(sums["commands"].most_common()),
        },
        "sums": {k: dict(v.most_common()) for k, v in sums.items()},
    }


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def hook_count(settings):
    return sum(len(v) for v in (settings.get("hooks") or {}).values() if isinstance(v, list))


def environment(sessions, claude_home: Path):
    """What the engineer has set up, outside the transcripts. Existence only, no file contents."""
    user = read_json(claude_home / "settings.json")
    skills_dir = claude_home / "skills"
    env = {
        "user": {
            "global_claude_md": (claude_home / "CLAUDE.md").is_file(),
            "hooks": hook_count(user),
            "permission_allow_rules": len((user.get("permissions") or {}).get("allow") or []),
            "permission_deny_rules": len((user.get("permissions") or {}).get("deny") or []),
            "own_skills": sorted(p.name for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith(".")) if skills_dir.is_dir() else [],
            "enabled_plugins": len([k for k, v in (user.get("enabledPlugins") or {}).items() if v]),
        },
        "projects": [],
    }
    for cwd in sorted({s["cwds"].most_common(1)[0][0] for s in sessions if s["cwds"]}):
        root = Path(cwd)
        if not root.is_dir():
            continue
        proj = {**read_json(root / ".claude" / "settings.json"), **read_json(root / ".claude" / "settings.local.json")}
        count = lambda sub: len([p for p in (root / ".claude" / sub).glob("*")]) if (root / ".claude" / sub).is_dir() else 0
        env["projects"].append({
            "path": cwd,
            "claude_md": (root / "CLAUDE.md").is_file() or (root / ".claude" / "CLAUDE.md").is_file(),
            "skills": count("skills"), "commands": count("commands"), "agents": count("agents"),
            "hooks": hook_count(proj), "mcp_json": (root / ".mcp.json").is_file(),
        })
    return env


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    ap.add_argument("--projects", type=Path, default=claude_home / "projects",
                    help="transcript folder (default: $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--since", help="window start YYYY-MM-DD (overrides --days)")
    ap.add_argument("--until", help="window end YYYY-MM-DD, inclusive (default: now)")
    ap.add_argument("--project", action="append", default=[], help="only project folders containing this text")
    ap.add_argument("--exclude-session", action="append", default=[])
    ap.add_argument("--exclude-latest", action="store_true", help="skip the most recently modified session (the one running this skill)")
    ap.add_argument("--skip-entrypoint", action="append", default=[],
                    help="skip sessions started from this entrypoint, e.g. sdk-cli for headless `claude -p` runs")
    args = ap.parse_args()

    files = sorted(args.projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
    if args.project:
        files = [f for f in files if any(p in f.parent.name for p in args.project)]
    if args.exclude_latest and files:
        print(f"excluding latest session {files[-1].stem}", file=sys.stderr)
        files = files[:-1]
    day = lambda d: dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc)
    cutoff = day(args.since) if args.since else dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    until = day(args.until) + dt.timedelta(days=1) if args.until else None

    (args.out / "sessions").mkdir(parents=True, exist_ok=True)
    sessions, index = [], []
    skipped = Counter()
    for f in files:
        if f.stem in args.exclude_session:
            continue
        s, lines = collect(f)
        if not s["start"] or s["end"] < cutoff or (until and s["start"] >= until) or s["user_prompts"] == 0:
            continue
        entry = s["entrypoints"].most_common(1)[0][0] if s["entrypoints"] else "unknown"
        if entry in args.skip_entrypoint:
            skipped[entry] += 1
            continue
        s["subagents"] = subagent_count(f)
        rel = f"sessions/{s['id']}.md"
        (args.out / rel).write_text(digest(s, lines), encoding="utf-8")
        sessions.append(s)
        index.append({"id": s["id"], "project": s["project"], "title": s["title"], "start": s["start"].isoformat(),
                      "end": s["end"].isoformat(), "user_prompts": s["user_prompts"], "digest": rel,
                      "digest_chars": (args.out / rel).stat().st_size})

    metrics = {"window_days": None if args.since else args.days, "since": args.since, "until": args.until, "projects_dir": str(args.projects), "overall": overall(sessions),
               "environment": environment(sessions, args.projects.parent),
               "sessions": [plain(s) for s in sessions]}
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=1, default=str), encoding="utf-8")
    (args.out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    if skipped:
        print(f"skipped by entrypoint: {dict(skipped)}", file=sys.stderr)
    print(f"{len(sessions)} sessions -> {args.out} (digests total {sum(i['digest_chars'] for i in index):,} chars)")


if __name__ == "__main__":
    main()
