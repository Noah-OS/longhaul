---
name: inspect
description: "Show where this conversation's tokens went, three lines in short first: usage by kind and by model, the subagents by agent type and model, context growth, the cache, the biggest tool outputs, searches (their chains and big results, and whether a code index was seen), repeated reads, each hook's times by event and source, what the guard caught, and the Advisor's consultations when there were any. Numbers only, each with its source."
argument-hint: "[conversation id, or nothing for this one]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect *)
---

The report below is Longhaul's reading of this conversation's own transcript,
its subagents' transcripts, the ledger, the guard's record and the Advisor
experiment's trace. It holds numbers, model names, tool names and file paths
only; never message text or tool output. Each heading names where its numbers
came from and how sure they are: measured, counted, inferred or estimated.

!`${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect "${CLAUDE_SESSION_ID}"`

The report opens with an "In short" block of up to three lines: what the
conversation was, where most of its tokens went, and the one finding worth a
look, which names the section that holds the detail. Lead with the report's
"In short" lines, in your own words: two or three sentences, each number as
the block gives it, never changed or rounded, and with its source (measured,
counted, inferred, or estimated where it carries ≈). If the last line says
nothing stands out, say so. Then offer to show the section it names, or any
other. Do not echo the whole report: show a section, or the whole report in a
code block, when the user asks for it, as it is, without changing or rounding
any number. Never add a saving or a cause the report does not state.

If it says no transcript was found, say so and show the message: the
conversation may be too new for Claude Code to have written its file, or
Longhaul's folder may be elsewhere (LONGHAUL_HOME). If the user gave an
argument ($ARGUMENTS), run
`${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect "$ARGUMENTS"` instead and do the
same with that report.

The report may name an install command; show it, take no install step.
