---
name: doctor
description: "Show what of Longhaul is set up on this machine and what to do next: the plugin, the terminal command, updates, the accounts, the chats running now, the code maps, the features and what it reads of Codex, each marked ✔, ✘ or ⚠ with one next step. A read-only checklist: it changes nothing."
---

The checklist is Longhaul's reading of this machine: its own files, the
settings it reads, the programs on PATH, the chats running now (each
one's folder and account, never what it says), and Codex's chats (how
many, and Codex's version, from `codex --version` or the newest chat,
never what they say). It names accounts, never their emails. The doctor
changes nothing.

Longhaul's command is `../../bin/longhaul`, a path relative to the folder
this SKILL.md is in: run it by its absolute path, that folder's path
followed by `/../../bin/longhaul`, quoted, from whatever folder the shell
is in. Run `../../bin/longhaul doctor` with nothing after it:

```sh
../../bin/longhaul doctor
```

Show the checklist to the user as it is, in a code block, without changing
any line or mark. Then, in a sentence, point to the next step on the line
that counts what is set up. A block of lines to paste is the user's to
paste; do not write it anywhere yourself. The steps are the user's to
take, most of them in a terminal; take none of them yourself.

A step may name an install command; show it, take no install step.
