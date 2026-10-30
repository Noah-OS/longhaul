---
name: manage
description: "Run Longhaul's setup from this chat: add, sign in or remove an account, switching, subagent models, the VS Code panel, the status line, auto-update, clearing limits, uninstalling. Each change runs only after the user's yes."
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul status) Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul switch) Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul models) Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul update status) Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul uninstall --dry-run)
---

Longhaul's setup actions, which once needed a terminal, run from this chat
on the user's word: each line below is one action and its exact command.
Run each by that full path, `${CLAUDE_PLUGIN_ROOT}/bin/longhaul`; the
plugin's shim runs these forms and refuses any other. What the user asked
for: $ARGUMENTS (or the conversation before).

Ask first, always. For every command that changes something, show the user
the exact command, say in plain words what it changes, ask, and run it only
after their yes in this chat: one yes, one command. Claude Code's own
permission prompt is not enough: the VS Code panel can run in a mode that
approves Bash by itself. The lines marked "reads" change nothing and need
no yes. Show what a command prints as it is.

Before `${CLAUDE_PLUGIN_ROOT}/bin/longhaul uninstall --yes --purge`, say
first that it deletes ~/.longhaul: the checkpoints, the usage ledger and
the accounts list, for good.

Accounts:
- See them, their sign-ins and limits, and the switching mode (reads): `${CLAUDE_PLUGIN_ROOT}/bin/longhaul status`
- Add one: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul add NAME EMAIL`, with `--repo PATH` for each repository it alone may be used in (and those inside it) and `--dir DIR` for its folder (default ~/.claude-<name>). It writes Longhaul's accounts list, then signs the account in.
- Sign one in again: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul setup NAME --relogin`; `${CLAUDE_PLUGIN_ROOT}/bin/longhaul setup NAME` signs in one that is not.
- Take one off the list: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul remove NAME`; its folder and sign-in stay.

For add and setup, tell the user first that the sign-in opens in the
browser and that they sign in there with that account's email: Longhaul
accepts no other. A browser signed in to claude.ai as someone else: copy
the link into a private window. Never ask for a password, a code or a token
in the chat. If no browser opens, give the user the same line to type in a
terminal, `longhaul` in place of the full path.

Settings:
- Switching at a usage limit (reads): `${CLAUDE_PLUGIN_ROOT}/bin/longhaul switch`. Set it: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul switch MODE`, MODE ask (ask in the terminal first; the panel stays put), auto (move by itself) or never (wait for the reset).
- Subagent models (reads): `${CLAUDE_PLUGIN_ROOT}/bin/longhaul models`. Set them: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul models MODE` (guide, on, off or trial), `${CLAUDE_PLUGIN_ROOT}/bin/longhaul models cap MODEL` (haiku, sonnet, opus or fable; off for none), `${CLAUDE_PLUGIN_ROOT}/bin/longhaul models TYPE MODEL` (one agent type's row).
- The VS Code panel on Longhaul's accounts: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul vscode on --yes` (it replaces any other program the panel was set to run); back to its own sign-in: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul vscode off`. Each edits VS Code's settings, a copy kept. After either, tell the user to reload VS Code: Cmd+Shift+P, then Developer: Reload Window.
- The status line: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul statusline install --yes` sets Claude Code's to Longhaul's, replacing one of the user's own (say so; a copy is kept); `${CLAUDE_PLUGIN_ROOT}/bin/longhaul statusline uninstall` puts the one before back.
- Claude Code's auto-update for Longhaul (reads): `${CLAUDE_PLUGIN_ROOT}/bin/longhaul update status`. Set it: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul update on --yes` or `${CLAUDE_PLUGIN_ROOT}/bin/longhaul update off --yes`.
- Forget the recorded usage limits: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul clear`.

Uninstall:
- What it would remove (reads): `${CLAUDE_PLUGIN_ROOT}/bin/longhaul uninstall --dry-run`; with ~/.longhaul too: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul uninstall --dry-run --purge`. Show it before asking.
- Remove what Longhaul set up outside the plugin (its status line, VS Code's line, auto-update, the terminal command): `${CLAUDE_PLUGIN_ROOT}/bin/longhaul uninstall --yes`; ~/.longhaul too: `${CLAUDE_PLUGIN_ROOT}/bin/longhaul uninstall --yes --purge`. The output says how to remove the plugin itself.

Terminal only: give the user the line to type there. The trials
(`longhaul trial ...`: a chat must not know or set its own arm),
`longhaul advisor record-probe` (half of the Advisor's gate, the owner's
alone), and starting a chat (`longhaul`, `-c`, `-r`: in VS Code the panel
already runs the chat on the right account).

If the shim refuses a command, show its message and do not try another
form of it.
