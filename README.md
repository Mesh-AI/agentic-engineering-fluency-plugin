# Agentic Engineering Fluency plugins

A Claude Code plugin marketplace with tools that assess Agentic Engineering Fluency.

| Plugin | What it does |
| --- | --- |
| [`agentic-engineering-fluency`](plugins/agentic-engineering-fluency) | Scores your fluency (L0–L3) from your own Claude Code session transcripts. |

Running an assessment uses tokens. Classification runs on Haiku by default to keep the cost low.

## Install

```text
/plugin marketplace add mesh-ai/agentic-engineering-fluency-plugin
/plugin install agentic-engineering-fluency@agentic-engineering-fluency
```

To try it from a local clone:

```text
/plugin marketplace add ./agentic-engineering-fluency-plugin
```

## Check the manifests

```bash
claude plugin validate .
```
