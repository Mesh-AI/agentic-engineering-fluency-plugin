# agentic-engineering-fluency

Scores your Agentic Engineering Fluency from your own Claude Code session transcripts. The
result is a YAML record with a level (L0–L3), confidence, evidence and a next step for each of
the 26 competencies in the framework.

## Use

Ask Claude Code to "score my sessions" or run:

```text
/agentic-engineering-fluency:sessions-to-fluency "Your Name" --days 30
```

## How it works

1. A Python script reads your transcripts in `~/.claude/projects` (or
   `$CLAUDE_CONFIG_DIR/projects`). It writes one condensed digest per session and a set of
   counted metrics.
2. Claude classifies each session into fixed facets, such as scope, how you framed the task
   and how you reviewed the output. Claude does not choose levels.
3. A second script turns the facets and metrics into levels with fixed thresholds. The
   thresholds are written into the record.

The scoring is deterministic after step 2, so two runs on the same facets give the same
levels.

## Requirements

- Claude Code
- Python 3.8 or later (`python3`), standard library only

Nothing to install. On macOS, `python3` comes with the Xcode Command Line Tools, which also
provide `git`. If `python3` asks you to install them, accept, or run `xcode-select --install`.

## Limits

- The maximum level is L3. L4 and L5 need evidence from outside your own sessions.
- L0 means "not seen in your Claude Code sessions", not "cannot do".
- The thresholds are not yet calibrated against hand-scored engineers. Use the result as a
  self-assessment aid, not a performance rating.
- Claude Code deletes transcripts older than `cleanupPeriodDays` (default 30). Raise that
  setting if you want to score a longer period.

## Versions

Each record's `method` block holds two versions, both `MAJOR.MINOR.PATCH`:

- `skill_version`: the plugin version from `.claude-plugin/plugin.json`.
- `framework_version`: the version of `references/framework.json`.

Both follow one rule: would the same sessions get the same levels?

| Bump | When |
| --- | --- |
| MAJOR | The same sessions could get different levels: thresholds, scoring rules, facet definitions, competencies or the level scale change. |
| MINOR | Levels stay the same, but the record changes: new metrics or fields, better evidence or next-step wording. |
| PATCH | The record does not change: docs, refactors, crash fixes. |

Before 1.0.0, the MINOR number takes the place of MAJOR: a change that can move levels goes
from 0.1.x to 0.2.0. Records are comparable when their versions match on MAJOR (0.MINOR before
1.0.0). `compare_runs.py` warns when they do not. When unsure, bump higher. The two versions are
independent: the framework can stay the same while the skill's method changes, and the other
way round.

## Privacy

Your transcripts stay on your machine. The work directory holds condensed transcript text,
which can include anything you pasted into a session. Delete it when you are done. Read the
`evidence` lines in the record before you share it.
