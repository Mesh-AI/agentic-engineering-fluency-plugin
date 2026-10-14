---
name: sessions-to-fluency
argument-hint: "[\"engineer name\"] [--days 30] [--projects ~/.claude/projects] [--project substring] [-o out.yaml]"
description: >-
  Score Agentic Engineering Fluency (L0–L3) directly from Claude Code session transcripts
  (~/.claude/projects/*/*.jsonl). A script collects metrics and condensed digests, the model
  classifies each session into fixed facets, and a second script turns the facets into levels
  with fixed, reported thresholds. Writes a score record YAML with evidence and next steps for
  each of the 26 competencies. Use when the user wants to "score my sessions", "assess my AI
  fluency", "assess fluency from transcripts" or "how fluent am I with Claude Code".
---

# Session transcripts → fluency score record

Score an engineer's fluency from their own Claude Code sessions. The model never chooses a
level. It records fixed facts about each session, and `score_facets.py` turns the facts into
levels with thresholds that are written into the record.

The pipeline:

1. `collect_sessions.py` (deterministic): reads transcripts and writes one digest per session,
   `metrics.json` (tool use, interrupts, rejections, tests run, files edited by type, tokens,
   models, parallel sessions, setup such as hooks, skills and CLAUDE.md) and `index.json`.
2. Classification (model): one facets file per session, using `references/facets.md`.
3. `score_facets.py` (deterministic): facets + metrics → levels, evidence, next steps and
   consistency checks.

## Files in this skill

`<skill-dir>` is the folder that holds this SKILL.md (Claude Code shows it as the skill's base
directory). Use it in every path below, so the skill runs from any working directory.

- `references/framework.json`: the competencies, phases and level descriptors. A copy of the
  framework at the version in its `version` field (`MAJOR.MINOR.PATCH`). Replace it when the
  framework changes.
- `references/facets.md`: what to record for each session.
- `scripts/`: `collect_sessions.py`, `score_facets.py`, `check_record.py`, `compare_runs.py`
  and their shared `record_io.py`. Python 3.8+, standard library only.

## Requirements

- `python3` (3.8 or later) on `PATH`. Nothing to install. On macOS it comes with the Xcode
  Command Line Tools, which also provide `git`.
- Claude Code transcripts on this machine. Claude Code deletes transcripts older than
  `cleanupPeriodDays` (default 30) in `settings.json`. A window longer than that finds only
  what is left.

## Fixed rules

- **Maximum level is 3.** L4 and L5 need team or org evidence and approval.
- **L0 means "not seen in Claude Code sessions".** It is marked `basis: not_observed`.
- **The model classifies, the script scores.** Do not edit levels in the output. To change a
  result, fix the facets or change the thresholds at the top of `score_facets.py`, and say so.
- **Transcripts stay local.** Digests and facets go in the work directory. Do not copy
  transcript content anywhere else. The record quotes at most short evidence lines.
- **A self-assessment aid, not a rating.** The thresholds are fixed so results are
  repeatable, but they are not yet calibrated against hand-scored engineers. Say this when
  you report the results. Do not present the levels as a performance judgement.

## Procedure

1. **Inputs.** Engineer name (optional, default `unknown`). Window (default `--days 30`).
   Transcript folder (default `~/.claude/projects`, or `$CLAUDE_CONFIG_DIR/projects` when
   that is set). Optional `--project` filters. `<scratch>` is the session's scratchpad
   directory if it has one, otherwise `${TMPDIR:-/tmp}/sessions-to-fluency`. Output path
   (default `<scratch>/sessions-fluency.yaml`). Work directory: `<scratch>/sessions-work/`.
2. **Collect.**
   ```bash
   python3 <skill-dir>/scripts/collect_sessions.py --out <work> --days 30 --exclude-latest
   ```
   `--exclude-latest` leaves out the session running this skill. Add `--skip-entrypoint sdk-cli`
   to leave out headless `claude -p` runs (automated evals, scripts, and headless runs of this
   skill). Keep them only if the engineer really works through `claude -p`. For another
   profile, pass `--projects <that CLAUDE_CONFIG_DIR>/projects`. For a fixed window, pass `--since`
   and `--until` (`YYYY-MM-DD`, both inclusive) in place of `--days`. If fewer than 8 sessions
   are found, tell the user and suggest a longer window before going on.
3. **Classify.** Read `references/facets.md`. Write `<work>/facets/<session-id>.json` for every
   session in `<work>/index.json`.
   - Up to 8 sessions or about 60,000 digest characters: classify them yourself, one digest at
     a time.
   - More than that: split the sessions into batches of about 60,000 characters
     (`digest_chars` in `index.json`). Give each batch to a subagent with the Agent tool, all in
     one message so they run in parallel. Each prompt gives the absolute paths of
     `<skill-dir>/references/facets.md`, `<skill-dir>/references/framework.json`, the digests and
     the facets folder. It
     also says: classify each session independently, and do not read other facets files.
4. **Score.**
   ```bash
   python3 <skill-dir>/scripts/score_facets.py --work <work> --out <out> --engineer "<name>"
   ```
   It writes `<out>` (YAML, for people) and a `.json` file beside it with the same content
   (for the other scripts).
   If it reports facet errors (bad values, missing sessions), fix those facets and run it
   again.
5. **Check.**
   ```bash
   python3 <skill-dir>/scripts/check_record.py <out>
   ```
6. **Report back.** Give a table of phase, competency, level and confidence. Add the main
   metrics (sessions, active time, share with subagents, skills, tests run) and any consistency
   checks that fired. Say where the record and the work directory are. Remind the user that
   the work directory holds condensed transcript text, which can include secrets pasted into
   sessions, and that they can delete it when they are done. Before they share the record,
   they should read its `evidence` lines.

## Checking consistency and comparing records

To check that classification is stable, run step 3 twice into separate work directories,
score both, and compare. The same script compares any records in this format, for example
the same engineer over two windows:

```bash
python3 <skill-dir>/scripts/compare_runs.py <record-a.yaml> <record-b.yaml> [...]
```

It prints each competency's level per record, the median, and the share of exact and
within-one agreement. It warns when the records' `skill_version` or `framework_version` differ in a
breaking way, because their levels may not be comparable.
