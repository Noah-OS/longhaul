---
name: inspect
description: "Show where this chat's tokens went: requests and tokens by kind and by model, the sub-agents, context growth and the compactions, the biggest tool outputs, searches and repeated reads. Numbers only, each with its source."
---

The report is Longhaul's reading of this chat's own rollout, the file
Codex records the chat in under CODEX_HOME/sessions, and its sub-agents'.
It holds numbers, model names, tool names and file paths only; never
message text or tool output. Each heading names where its numbers came
from and how sure they are: measured, counted, inferred or estimated.

Longhaul's command is `../../bin/longhaul`, a path relative to the folder
this SKILL.md is in: run it by its absolute path, that folder's path
followed by `/../../bin/longhaul`, quoted, from whatever folder the shell
is in. Run it for this chat, whose id Codex sets in each command you run:

```sh
../../bin/longhaul inspect --agent codex "$CODEX_THREAD_ID"
```

Show the report to the user as it is, in a code block, without changing or
rounding any number. If it says no rollout was found, say so and show the
message: the chat may be too new for Codex to have written its file, or
Codex's folder may be elsewhere (CODEX_HOME). If the user named another
chat (its id, the start of one, or its rollout's path), run the same
command with that, quoted, in place of `"$CODEX_THREAD_ID"`, and show that.

The report may name an install command; show it, take no install step.
