---
name: doctor
description: "Show what of Longhaul is set up on this machine and what to do next: the plugin, the terminal command, Claude Code's version, updates, the status line, the VS Code panel, the accounts, the chats running now, the code maps and the features, each marked ✔, ✘ or ⚠ with one next step. A read-only checklist: it changes nothing."
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul doctor)
---

The checklist below is Longhaul's reading of this machine: its own files,
Claude Code's and VS Code's settings, the programs on PATH, and the chats
running now (each one's folder and account, never what it says). It names
accounts, never their emails. The doctor changes nothing.

!`${CLAUDE_PLUGIN_ROOT}/bin/longhaul doctor`

Show the checklist to the user as it is, in a code block, without changing
any line or mark. Then, in a sentence, point to the next step on the line
that counts what is set up. The "# Compact instructions" block after it is
the user's to paste into their CLAUDE.md; do not write it there yourself.
The steps are the user's to take, most of them in a terminal; take none of
them yourself.

A step may name an install command; show it, take no install step.
