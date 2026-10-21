---
name: checkpoint
description: "Save this conversation's state as a checkpoint: the goal, the next action, the decisions, what is done, in progress and blocked, the files changed and how to verify. Longhaul puts it back after a compaction or a resume, with its age and what changed since. Use it when the state is worth keeping: before a compaction, at the end of a step, before stopping. The user can ask for it too."
argument-hint: "[a note to fold into the state]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul checkpoint *)
---

Write this conversation's state as one JSON object with these eight keys
to the file named below, then save it with the command below. `goal` and
`next_action` are texts and required; the other six are lists of texts
and may be empty.

```json
{
  "goal": "what this conversation is for, in a sentence or two",
  "next_action": "the one thing to do first when work resumes",
  "decisions": ["a choice made, and why"],
  "done": ["a step finished"],
  "in_progress": ["a step started and not finished, and where it stands"],
  "blocked": ["what waits, and on whom or what"],
  "changed_files": ["a/path/relative/to/this/folder.py"],
  "verification": ["the command that shows the work is right"]
}
```

Rules:
- Facts the next turn needs, not a narrative. Name files and commands
  rather than copying their contents.
- Nothing secret: no tokens, keys or passwords. The save refuses a value
  shaped like one.
- Caps: 1,000 characters per text, 20 entries per list, 8,000 bytes in
  all. Over a cap the save refuses and names the field; shorten it and
  save again.
- One line per text; no tabs. A newline or a tab in a text comes back
  as `?`.
- `changed_files` are absolute paths, or relative to the folder the
  command runs in (the Bash tool's current directory, which a `cd`
  changes). When the checkpoint comes back, Longhaul says which of them
  changed or went since it was written.
- Only the main conversation saves a checkpoint. A subagent must not run
  this; it reports its state to its caller instead.

Write the JSON with the Write tool, as the whole content of one file, at
this path (Longhaul's own folder, keyed to this session):

!`echo "${LONGHAUL_HOME:-$HOME/.longhaul}/checkpoints/${CLAUDE_SESSION_ID}.state.json"`

Then save it from that file. No session id is needed: the save takes this
session's from the Bash tool's environment. In a print-mode run (`claude
-p`) the Write tool is denied, so no checkpoint is saved there.

```sh
${CLAUDE_PLUGIN_ROOT}/bin/longhaul checkpoint save --file <that path>
```

Repeat the command's one line to the user as it is; do not show the JSON.
The state file is removed once the save has read it, saved or refused. If
it refuses, it names the field: write the file again with that fixed, and
save again. The checkpoint is private (0600)
under Longhaul's own folder, keyed to this session, and is read only by
Longhaul's SessionStart hook, which puts the state back after a
compaction or a resume, with its age and what changed since. If the user
added a note ($ARGUMENTS), fold it into the state where it belongs.
