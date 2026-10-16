# Session facets: what to record for each session

You classify **one session at a time** into fixed values. You do not choose fluency levels.
`scripts/score_facets.py` turns the facets of all sessions into levels with fixed rules. Keep
each value to what the digest shows. When the digest does not show something, use the lowest
value (`none`, `false`, `unclear`).

Read `framework.json` (in this folder) for the competency ids and what each job means.

## Input

A session digest (`sessions/<id>.md`). It has a header with counts, then the condensed
transcript:

- `[USER]` is a prompt the engineer typed. `[USER COMMAND]` is a slash command.
- `[USER ANSWERED CLAUDE'S QUESTION]` is the engineer's choice from a question Claude asked.
  Picking an option is a decision, but not a reason: it does not make `alternatives` true and
  does not make Claude's suggestion the engineer's own decomposition.
- `[USER INTERRUPTED]` means the engineer stopped Claude mid-task.
- `!! USER REJECTED` means the engineer refused a tool call.
- `[CLAUDE]` is Claude's text. `->` is a tool call.

Long pasted text (meeting transcripts, documents) is cut. Judge the request around it, not
the pasted content.

## Output

Write one JSON file per session to `facets/<session-id>.json`:

```json
{
  "session": "<session id>",
  "classifier_model": "claude-haiku-5-5",
  "summary": "One sentence: what the engineer used Claude Code for.",
  "jobs": [
    {
      "competency": "documentation",
      "scope": "whole_unit",
      "frame": "spec_or_plan",
      "review": "manual",
      "alternatives": false,
      "outcome": "achieved",
      "evidence": "[USER] asked for the onboarding guide from the agreed outline; corrected two sections"
    }
  ],
  "practices": {
    "caught_errors": 1,
    "redirected_approach": 0,
    "judged_alternatives": false,
    "risk_scaled_checks": false,
    "context": "task_files",
    "decomposition": "brief",
    "cost_or_context_management": false,
    "measured_results": false,
    "configured_guardrails": false,
    "built_reusable_tooling": false
  }
}
```

`classifier_model` is the exact model ID you are running as, from your system prompt. Write
`unknown` if you cannot tell. The score record lists it, because a different model can
classify the same session differently.

## `jobs`: one entry per job competency the session did real work on

List only the Plan, Build and Operate competencies (16 ids). Do not list the agentic
practices here. Leave `jobs` empty for sessions with no job work (for example, a session that
only asked a general question). Most sessions have 1–2 jobs. List a job only if Claude did a
real part of it, not a passing mention.

Map the work by what the engineer was producing:

- A spec, requirements, user stories or acceptance criteria from a business need →
  `requirements`. A framework, policy or content design is `requirements` if it defines
  what something must do. It is `documentation` if it explains or records something.
- Docs, guides, READMEs, reports, written analysis → `documentation`. Rewording or restyling
  an existing framework, policy or rubric is `documentation`. Designing or changing what it
  requires is `requirements`.
- App or site code, scripts → `write-code`. A static site generator config or templates also
  count as code.
- Restructuring existing code without changing behaviour → `refactoring-code`.
- Diagnosing a failure → `debugging-code`.
- Data files and parsing (YAML/JSON/CSV conversion, queries, data loading code) →
  `writing-data-structures`. Designing the shape of the data → `data-modelling`.
- CI/CD, build pipelines, hosting, IaC, release → `deploy-applications`.

Fields:

| Field | Values | Meaning |
| --- | --- | --- |
| `scope` | `question` | Asked for an explanation or an answer, nothing produced |
| | `fragment` | One small piece (a function, a paragraph, a single command) |
| | `scoped_task` | A defined task with several steps or files, but not a whole deliverable |
| | `whole_unit` | A whole deliverable end to end: a feature, a full document, a whole refactor, a full site section |
| `frame` | `none` | Asked with no written frame |
| | `brief` | A few sentences of direction or constraints |
| | `spec_or_plan` | A written spec, plan, outline, acceptance criteria, design doc or plan mode that describes **the output** before the work starts. Source material (transcripts, sheets, documents) is not a spec, even with instructions on how to read it: that is `brief` |
| `review` | `none` | No sign the engineer looked at the output |
| | `ran_it` | Only checked it runs, builds or looks right |
| | `manual` | Read and corrected the output: follow-up corrections, interrupts, rejections |
| | `risk_scaled` | Checked some parts harder than others because they matter more, or asked Claude to verify specific risky claims against a source |
| | `automated` | Tests, linters, type checks, link checks or CI were the check, and the engineer made them the done-condition |
| `alternatives` | `true`/`false` | The engineer compared Claude's approach with another option, or rejected it with a reason ("no, do X because Y") |
| `outcome` | `achieved`, `partial`, `not_achieved`, `unclear` | Did the session deliver what was asked? |
| `evidence` | string | Under 200 characters. Quote or closely paraphrase the digest. Start with `[USER]` or `[CLAUDE]` |

## `practices`: across the whole session

| Field | Values | Meaning |
| --- | --- | --- |
| `caught_errors` | integer | Times the engineer caught a mistake in Claude's output (wrong fact, bug, missed requirement) |
| `redirected_approach` | integer | Times the engineer changed Claude's direction ("not like that", interrupts, rejected tool calls with a new instruction) |
| `judged_alternatives` | bool | Same as `alternatives`, at session level |
| `risk_scaled_checks` | bool | Same as `review: risk_scaled`, at session level |
| `context` | `none` | No context given beyond the prompt |
| | `ad_hoc_paste` | Pasted content into the prompt |
| | `task_files` | Pointed Claude at files, folders, URLs or tools for this task |
| | `project_context` | Relied on or maintained standing project context: CLAUDE.md, memory, specs or docs kept for the agent, or asked Claude to save context for later sessions |
| `decomposition` | `single_prompt` | One request, no breakdown |
| | `brief` | Gave a short brief, or broke the work into a few steps as it went |
| | `spec_or_plan` | Planned first (plan mode, written plan, task list, acceptance criteria), then executed |
| | `parallel` | Split the work into independent parts run by parallel subagents or sessions, with defined outputs |
| `cost_or_context_management` | bool | Deliberate action on tokens or context: `/clear` or `/compact` between tasks, choosing a model for cost or speed, keeping context small on purpose |
| `measured_results` | bool | The engineer tracked or measured outcomes (time saved, quality metrics, comparisons across runs) |
| `configured_guardrails` | bool | The engineer set up or changed controls on the agent: hooks, permission rules, CLAUDE.md rules to prevent a known failure, CI checks for agent output |
| `built_reusable_tooling` | bool | The engineer created or changed agent tooling for reuse: a skill, slash command, subagent definition, hook, output style or prompt template. A script counts only if a `[USER]` line says it is for repeated use in the engineer's own workflow. A tool that is the job's deliverable (a measurement harness, an app feature) is job work, not this |

## Rules

- **Score the engineer, not Claude.** Practices record what the engineer asked for or did.
  If Claude chose on its own to run parallel subagents, write memory files, or run tests,
  that does not count for `decomposition`, `context`, `review` or any practice. It counts
  only when a `[USER]` line asked for it, or the engineer set it up (CLAUDE.md, a skill, a
  hook that makes Claude do it).
- **Slash commands alone are not practices.** `/clear`, `/compact` and `/model` are already
  counted in `metrics.json`. Set `cost_or_context_management` only if the engineer states a
  cost, speed or context reason, or deliberately manages context in a way the metrics do
  not show (for example, "start a fresh session with only the spec").
- **`measured_results`** means the engineer measured their own outcomes with AI (time saved,
  rework, quality over time). Building a tool that measures something else, such as token
  cost of a configuration, is job work, not this practice.
- **Interrupts**: an interrupt followed by a re-prompt counts as one `redirected_approach`.
  On its own it is not a `review` of output, because no output was checked.
- **`caught_errors` vs `redirected_approach`**: count each engineer correction once. It is
  `caught_errors` if Claude's output was wrong (wrong fact, bug, missed stated requirement).
  It is `redirected_approach` if the output was acceptable but the engineer wanted a
  different direction or added a new requirement. A rejected tool call shows its reason as
  `Reason:`. Use the reason to decide.
- **`alternatives` is true** only if the engineer gives a reason for preferring one approach
  over another, or asks for options and picks one with a reason. Choosing from Claude's own
  menu with no reason is false.
- **Questions**: a question about the engineer's own system or work is a job with `scope:
  question`. A general how-to question with no work product has no jobs.
- **Prototypes and visual pages**: an HTML page that explains or presents something is
  `documentation`. A UI prototype or mock-up of an app is `write-code`. A data shape shown
  in a page is `data-modelling` only if the engineer is designing that shape.
- **Running the engineer's own skill**: if a short prompt runs a skill or command the engineer
  wrote, set `frame: spec_or_plan` (the skill is the written frame) and
  `built_reusable_tooling: false` (it was built earlier, not in this session).
- **Reverted work**: if the engineer reverts the result at the end, set `outcome: not_achieved`.
- Classify what happened, not what was planned or suggested.
- A session about AI fluency or skills is still classified by the work done. Writing a
  scoring rubric is `documentation` or `requirements`, not evidence of high fluency.
- Do not guess the engineer's intent from Claude's text. The `[USER]` lines show what the
  engineer did. `[CLAUDE]` lines and tool calls show what the agent did.
- Use the header counts (interrupts, rejected tool calls, files by category, bash categories)
  as facts.
