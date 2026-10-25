---
name: inspect
description: "Show where this conversation's tokens went: usage by kind and by model, the subagents by agent type and model, context growth, the cache, the biggest tool outputs, searches (their chains and big results, and whether a code index was seen), repeated reads, what the guard caught, and the Advisor's consultations when there were any. Numbers only, each with its source."
argument-hint: "[conversation id, or nothing for this one]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect *)
---

The report below is Longhaul's reading of this conversation's own transcript,
its subagents' transcripts, the ledger, the guard's record and the Advisor
experiment's trace. It holds numbers, model names, tool names and file paths
only; never message text or tool output. Each heading names where its numbers
came from and how sure they are: measured, counted, inferred or estimated.

!`${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect "${CLAUDE_SESSION_ID}"`

Show the report to the user as it is, in a code block, without changing or
rounding any number. If it says no transcript was found, say so and show
the message: the conversation may be too new for Claude Code to have
written its file, or Longhaul's folder may be elsewhere (LONGHAUL_HOME).
If the user gave an argument ($ARGUMENTS), run
`${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect "$ARGUMENTS"` instead and show
that.

The report may name an install command; show it, take no install step.
