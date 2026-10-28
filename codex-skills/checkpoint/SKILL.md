---
name: checkpoint
description: "Save this chat's state as a checkpoint: the goal, the next action, what the user said, the facts found, the decisions and why, what failed, the files the next step needs, what is done, in progress and blocked, and how to verify. Longhaul puts it back after a compaction, a resume or a clear, with its age and what changed since. Use it when the state is worth keeping: before a compaction, at the end of a step, before stopping. The user can ask for it too."
---

Write this chat's state as one JSON object with these twelve keys, then
save it with the one shell command below, the JSON on its stdin. `goal`
and `next_action` are texts and required; the other ten are lists of
texts and may be empty.

```json
{
  "goal": "what this chat is for, in a sentence or two",
  "next_action": "the one thing to do first when work resumes",
  "owner_said": ["2026-10-27: an instruction, preference or correction of the user's, near their own words"],
  "facts": ["a fact from tool output that would cost work to find again (a root cause, src/app.py:120, an id, a commit, a value, a test count), and where it came from"],
  "decisions": ["a choice made, because of why"],
  "tried_failed": ["an approach that failed, and why"],
  "working_set": ["a file or symbol the next step needs, with its lines: src/app.py 120-180"],
  "done": ["a step finished"],
  "in_progress": ["a step started and not finished, and where it stands"],
  "blocked": ["what waits, and on whom or what"],
  "changed_files": ["a/path/relative/to/this/folder.py"],
  "verification": ["the command that shows the work is right"]
}
```

Rules:
- The next you starts from this and nothing else. Fill it: aim for 5,000
  to 9,000 characters, with exact ids, `file:line` locations and numbers.
- Start from the last checkpoint, the one that came back after the last
  compaction, if one did. Keep each `owner_said` and `facts` entry that
  still holds, word for word; drop only what no longer holds. When a fact
  changed, write the new one ending with `(replaces: <the old one, short>)`
  and leave the old one out. The save says how many of the last
  checkpoint's entries are missing.
- Each decision gives its why on the same line.
- Facts the next turn needs, not a narrative. Name files and commands
  rather than copying their contents.
- Nothing secret: no tokens, keys or passwords. The save refuses a value
  shaped like one.
- Caps: 1,000 characters per text, 20 entries per list, 16,000 bytes in
  all. Over a cap the save refuses and names the field; shorten it and
  save again. What comes back is in the order above, up to about 9,000
  characters, with one line naming what stayed in the file.
- One line per text; no tabs. A newline or a tab in a text comes back
  as `?`.
- `changed_files` are absolute paths, or relative to the folder the
  command runs in (the shell's working directory). When the checkpoint
  comes back, Longhaul says which of them changed or went since it was
  written, and marks a `facts` or `working_set` entry that names a file
  changed since.
- Only the main chat saves a checkpoint. A sub-agent must not run this;
  it reports its state to its caller instead.

Longhaul's command is `../../bin/longhaul`, a path relative to the folder
this SKILL.md is in: run it by its absolute path, that folder's path
followed by `/../../bin/longhaul`, quoted, from whatever folder the shell
is in. Save with it, the JSON in place of the middle line:

```sh
../../bin/longhaul checkpoint save --agent codex <<'LONGHAUL_STATE'
{"goal": "...", "next_action": "...", ...}
LONGHAUL_STATE
```

The quotes around the first `LONGHAUL_STATE` keep the shell from changing
anything in the JSON, and no file is written first. No chat id is needed:
the save takes this chat's from CODEX_THREAD_ID, which Codex sets in each
command you run. The save writes one file under Longhaul's own folder in
the home folder (`~/.longhaul/checkpoints/`, or LONGHAUL_HOME's), outside
the workspace: if the sandbox refuses the write, run the same command
again with escalated permissions, saying it saves Longhaul's checkpoint.

Repeat the command's one line to the user as it is; do not show the JSON.
If it refuses, it names the field: fix that and run the command again.
The checkpoint is private (0600) under Longhaul's own folder, keyed to
this chat, and is read only by Longhaul's SessionStart hook, which puts
the state back after a compaction, a resume or a clear, with its age and
what changed since. If the user added a note when asking for the save,
fold it into the state where it belongs.
