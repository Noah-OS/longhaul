# Changelog

All notable changes to Longhaul are recorded here. The format follows Keep a Changelog; the version is the plugin's, from `.claude-plugin/plugin.json`. Each release bumps the version in both `plugin.json` and `marketplace.json` (a test keeps them equal): users on a cached copy get an update only when the string changes.

## 0.2.6 - 2026-10-25

### Fixed

- **The doctor names a terminal chat's own conversation.** For a chat started outside Longhaul it said "start it with `longhaul -c`", which in a folder with two chats could resume the other one. It now reads Claude Code's record of the process (its `sessionId` only) and says `longhaul -r ID`. The same record shows a conversation open in two chats when no `--resume` names it.

### Changed

- **The README says that each account is its own claude.ai sign-in**, so a chat that moves to another account takes that account's connectors and artifacts, not the first one's.

## 0.2.5 - 2026-10-25

Claude picks each subagent's model by how hard its job is, and Longhaul tells you more of what it does.

### Changed

- **Auto Model picks by the task, not by a table.** At each start, compaction, `/clear` and resume, Longhaul gives the main conversation a short guide: Haiku for lookups and small mechanical edits, Sonnet for ordinary implementation, tests and routine reviews, Opus for design, hard debugging, security review and work across many files; Sonnet when unsure. Claude then sets each subagent's model itself. Longhaul reads no task text. Every pick is logged (`models: TYPE picked MODEL (guided)`, or `(its own)` or `(inherited)`). `guide` is the new default mode; the per-type table stays as `longhaul models on`. `longhaul models cap MODEL` keeps every subagent at or below a model. A models trial now compares guided chats against unguided ones, with subagent cost by model and a redo count.
- **`/longhaul:inspect` shows subagents by agent type and model**, with runs, requests, cache reads and the API-price equivalent, so you can see what ran where.

### Fixed

- **The reload line after a compaction, a /clear or a resume too.** A long chat updated under it never starts again, so the line telling it to type `/reload-plugins` now also comes at those moments.
- **An old checkpoint is said to you.** When Longhaul puts back a checkpoint six hours old or more, you are told its age, in the chat and as a pop-up, so a stale one is not followed unseen.
- **The first chat's line counts its quick checks** ("7 of 8 quick checks pass"), so it is not read as the doctor's own count.
- **Auto-compact after a reload.** When the plugin's module had just loaded (a chat's start, or `/reload-plugins`), its first turn only marked where Longhaul's log ended, so a reminder and the save in that same turn were missed and nothing compacted, as in the first live check. Its first turn now also reads the last fifteen minutes of the log; its own `autocompact:` line keeps a replay from compacting twice.

## 0.2.4 - 2026-10-25

The context reminder keeps working through a long conversation and now compacts it, work folders stay on the work account, and the guard shows what it does.

### Added

- **`longhaul doctor`, and `/longhaul:doctor` in any chat.** A read-only checklist of what is set up and what to do next, one line each with ✔, ✘ or ⚠ and the next step: the plugin and the terminal command and their versions, Claude Code's version, updates, the status line, the VS Code panel, accounts (two accounts with the same email are one subscription, so a switch between them never helps), the chats running now and any outside Longhaul or on the wrong account with how to fix each, the code maps, and each feature's switch. The first chat after an install or an update says how much is set up and points to it. The in-chat command list now admits `doctor` alone, as it admits `inspect` and `status` (the owner, 2026-10-25).
- **Word of a newer Longhaul.** Once a day Longhaul asks GitHub for its latest release, sending nothing but the request, in the background so no chat waits for it; a chat's start then says when a newer version is out, with the update steps, or when a newer one is installed than the chat runs (type `/reload-plugins`). `LONGHAUL_UPDATE_CHECK=off` turns the check off. `longhaul update [status|on|off]` shows the versions and turns Claude Code's own auto-update on or off for Longhaul, asking first and keeping a copy of the file.
- **Auto-compact after a reminder's save.** When the context reminder has fired and Claude has saved its checkpoint, Longhaul runs `/compact` once at the end of that turn, so the conversation goes on from a small context and the checkpoint comes back right after. It is logged, shown as a pop-up, and done at most once per reminder and once in ten minutes. `LONGHAUL_AUTO_COMPACT=off` turns it off. It works through Claude Code's plugin modules: a headless run showed it compacting; the terminal and the VS Code panel are checked live.

- **A chat on the wrong account is stopped in a folder kept for another one.** A new prompt hook, `longhaul account-check`, refuses each prompt in a folder that `accounts.json` keeps for some accounts (their `repositories`) when the chat runs on any other account, by its `CLAUDE_CONFIG_DIR`. Nothing is sent, and the line says how to fix it: close the chat and open it again, so it starts on the right account. It is logged, and the Longhaul display shows it. This catches a chat whose process began before Longhaul was set up, as two VS Code chats in a work folder did. Whatever the hook cannot read lets the prompt through.
- **The guard shows what it does.** Each read it flags, advised on or refused, is one line in `~/.longhaul/longhaul.log` with the file's path and size, never its content, and the Longhaul display shows a pop-up for this chat's flags. It still advises only Claude by default.

### Changed

- **A code map folder that git doesn't ignore goes into the repository's local exclude.** When Longhaul builds a CodeGraph or Trail-Code map in a repository that doesn't ignore `.codegraph/` or `.trail/`, it adds the folder to `.git/info/exclude`, which is never committed, and logs it. Before, the new folder showed as untracked in `git status`.
- **A trial counts long chats too.** A chat begun before a trial's start now counts from the start onward, only its lines inside the trial, and stays in its half for the whole trial. `longhaul trial status` and `report` say how many began before.

### Fixed

- **The display's context size.** The status entry's "context Nk" added up every request of a turn, so a turn of several requests showed several times the real context. It now shows the live window, as Claude Code's own status line does.
- **The context reminder starts counting again after a compaction.** Before, each threshold was said once per conversation for good, so a conversation reminded at 800,000 tokens and compacted near 968,000 was never reminded again. Now, once the context falls more than one threshold below the last reminder, or under half the threshold (as after a compaction), it is reminded at 200,000, 400,000 and so on of the new context. A drop smaller than that, as when Claude Code clears old tool results, changes nothing.

### Changed

- **`LONGHAUL_ADVISOR_VERSION_TIMEOUT`** sets how long the Advisor's gate waits for `claude --version`: 2 seconds unless set, as before.

## 0.2.3 - 2026-10-24

Longhaul shows itself inside Claude Code, the VS Code panel included.

### Added

- **The Longhaul display.** A small module the plugin loads beside its hooks, `hooks/display.ts`. VS Code's Claude panel shows no status line and no hook message, so until now nothing Longhaul did was visible there.
  - **A status entry:** the account the chat runs on, its 5-hour and weekly allowance when a fresh reading exists, and the context size after each turn, as `personal-1 · 5h 62% · week 71% · context 180k`.
  - **A pop-up for each event:** a checkpoint saved, a checkpoint brought back after `/clear`, the context reminder, a move to another account at a usage limit, and a code index starting to build.
  - **It only reads** `~/.longhaul` (`accounts.json`, `usage/`, and the new lines of `longhaul.log`) and sends nothing anywhere. Any failure shows nothing.

## 0.2.2 - 2026-10-23

The code index builds itself, and subagents' models can be chosen per agent type.

### Added

- **Auto Model for subagents.** `longhaul models TYPE MODEL` keeps a table of agent type to model in `~/.longhaul/config.json`, and Longhaul applies it to every subagent call, in VS Code and the terminal.
  - **It only lowers a model.** The model a call would run on is its own, else its agent definition's, else `CLAUDE_CODE_SUBAGENT_MODEL`, else the main conversation's. The table's model is used when it is cheaper.
  - **It never touches** an Advisor consultation, a forced model, or an unknown one.
  - **Where it applies:** `longhaul models trial`, the default, means only in a models trial's "routed" arm; `on` means everywhere; `off` means nowhere.
- **A trial of the models table.** `longhaul trial start --kind models` runs "routed" against "usual" with the same rule as the checkpoint trial.

### Changed

- **The code index is on by default.** At a new session's start, in a git work tree with no map, Longhaul starts `codegraph init` and `trail init` in the background, where they are installed. Trail's init also writes a hook into the repository's `.claude/settings.json`. `LONGHAUL_INDEX=off` turns this off. Whether the index saves tokens is not yet measured.

## 0.2.1 - 2026-10-23

Setup that does the work for you, and accounts kept to their own folders.

### Added

- **A guided `longhaul add`.** With no arguments, in a terminal, it asks for each part of an account and explains it. Then it shows a summary to confirm, opens the browser sign-in, and asks whether to add another. The one-line form is unchanged.
  - The parts: the name, the email, where its sign-in lives (a new folder is offered when the default already exists), and the folders it may be used in.
  - With two accounts or more, it asks once whether to switch accounts by itself at a usage limit, and keeps the answer in `~/.longhaul/config.json`. The terminal and the VS Code panel both read it. `--switch`, `LONGHAUL_SWITCH` and `vscode.json` still override it.
  - It offers to set up VS Code's Claude panel too.
- **`longhaul vscode [on|off]`.** Points VS Code's Claude panel at Longhaul's accounts, or back to its own sign-in. It edits VS Code's user settings itself, as VS Code writes them: comments and trailing commas are kept, one line is added or removed, a copy of the old file is kept, and nothing changes without a yes.
- **`longhaul status` says where each account may be used:** "only in …" or "anywhere". It adds a line for the current folder naming the accounts it may use, or saying that none may.

### Changed

- **A folder an account is limited to is reserved for it.** Only the accounts whose lists name a repository, or a folder above it, are used there; an account allowed anywhere is not. A work account's folders therefore never use personal tokens, and at its limit Longhaul waits for its reset instead of moving the work. Elsewhere, the accounts allowed anywhere are used, as before.
- **The context reminder also has the model tell you, in one line, when it saves the checkpoint.** The VS Code panel does not show the reminder's own message.

## 0.2.0 - 2026-10-23

Measure first, then act: tokens by model, a reminder that keeps sessions short without losing the thread, a trial that tests whether it saves anything on real work, and the Advisor experiment, built and off.

### Added

- **Tokens by model in `inspect`.** Input, cache write, cache read and output per model, for the main conversation and the subagents apart, as `model_tokens` records in `--json`.
- **The context reminder.** When a conversation's context passes 200,000 tokens, and each further 200,000, you see one line suggesting `/longhaul:checkpoint` then `/clear`, and the model is asked to save a checkpoint itself at its next natural stopping point. It is also delivered during unattended work, through the guard's hook on `Read` and `Bash`. It is said once per threshold per session. `LONGHAUL_CHECKPOINT_AT` sets the threshold, and `off` turns it off. This is a new `UserPromptSubmit` hook.
- **The checkpoint comes back after `/clear`.** It comes back once, when it was saved in the same folder within the last 6 hours (`LONGHAUL_CHECKPOINT_CARRY_HOURS`; `0` or `off` turns this off), with a line saying where it came from. The save records the folder and time beside the checkpoint, never its text. The checkpoint skill may write its state file without a permission prompt.
- **The trial.** `longhaul trial start`, `status`, `rate`, `report` and `stop` run a two-week A/B of the checkpoint habit on your own work. Each new session is assigned at random to "usual" (no reminder) or "habit", and a session continued after `/clear` keeps its arm. `report` applies a rule written in advance and says "saving shown" or "no saving shown". `rate` is a blind list for scoring finished tasks 1 to 5.
- **Search and the code index.**
  - `inspect` counts searches (calls, chains, big results) and says whether a CodeGraph or Trail-Code map was seen or used.
  - The guard's startup note names a project's CodeGraph index, and a nudge at 20 searches points at it.
  - With `LONGHAUL_INDEX=on`, Longhaul runs `codegraph init` or `trail init` in the background where the program is installed and the repository has no map. Opt-in, and never installed by Longhaul.
- **Accounts restricted to repositories (advanced).** `longhaul add NAME EMAIL --repo PATH` (or `"repositories"` in `accounts.json`) uses an account only in those repositories. Outside them it is never started, resumed or moved there, and with no account allowed, Longhaul refuses to start, or the VS Code panel refuses the chat.
- **The Advisor experiment, off.** A stronger model, read-only, reviews the work at four decision points through `/longhaul:advise`. It is off unless you turn it on with three steps: a capability probe recorded for your Claude Code version, `longhaul advisor enable` run in your own terminal, and `LONGHAUL_ADVISOR=experiment`. A hook checks every consultation and denies what it cannot check; it enforces caps per task and per session, by advisor model. `inspect` shows the consultations and their tokens. Two hooks on subagent calls, messages and workflows, and a note on each prompt while the experiment is on.

### Changed

- **The code index is looked for only from the session's folder up to its git work tree's top, never in the home folder or above.** CodeGraph's `~/.codegraph` and Trail's `~/.trail` no longer make every project look indexed.
- **An `accounts.json` that cannot be read refuses instead of falling back to one unrestricted account.** `longhaul` and `pipe` exit 2 with the reason, the panel refuses the chat, and `status` shows it. A missing file works as before.
- The hooks now number eight.

### Known limits

- **No token saving has been shown yet.** The guard's two controlled runs showed none; block mode cost 3.5 times the baseline on a task that needs whole files. The trial exists to measure the checkpoint habit on real work. Its cost figure uses per-model prices fitted from Claude Code's own estimates.
- **The reminder** reaches the model on your prompts and on `Read` and `Bash` calls only.
- **The checkpoint carry** uses the folder the save ran in, and its write permission is not yet seen in a live run.
- **The Advisor:**
  - it has been probed on Claude Code 2.1.307 only, and the VS Code extension's bundled 2.1.304 is refused;
  - in the benchmark's automatic runs the executor never consulted on its own, and the note meant to change that is not yet retested;
  - if its check cannot run at all, the call goes ahead, and the advisor's own refusal is the model following its instructions;
  - an executor that sets out to get around it could.
- **The advanced account switching** is still unproven at a real limit.

## 0.1.0 - 2026-10-21

The first release: a Claude Code plugin and a terminal command, in one Python file (plus a two-file VS Code launcher) with no dependencies, that show where a session's tokens go and stop the waste. Account switching ships opt-in.

### Added

- **The usage report.** `longhaul inspect [ID|PATH] [--json]` reads one conversation's transcript and its subagents' and reports requests per model; tokens by kind (input, cache write, cache read, output); context growth and compactions; the cache; the largest tool outputs; calls and output per tool; files read again and searches repeated with nothing changed in between; what the guard caught; tool and API errors. Numbers only, each heading named with its source and how sure it is. `--json` gives one record per number. `/longhaul:inspect` shows the report inside the session.
- **The status line and the ledger.** `longhaul statusline`, set in your own settings, shows the account and its 5-hour and 7-day allowance with the reset times, and records each reading's context, cache and API-price cost in a ledger that `inspect` reads. `longhaul usage` shows it per session and per account, a session named by its `--name` label or by its project folder and short id, never by a word of the conversation. `python3 longhaul.py setup` links `~/.local/bin/longhaul` for the status line, with or without a second subscription on the list.
- **The guard.** A `PreToolUse` hook on `Read` and `Bash` that catches a file read in full again unchanged, a whole read of a file over 20,000 bytes, and a bare `cat` of one. Warn mode, the default, lets the call run and tells the model; `LONGHAUL_GUARD=block` denies it and names the narrower call; `off` does nothing. It fails open: on any error, and after 5 seconds, the call runs. A `SessionStart` hook forgets the session's reads after a compaction or a clear.
- **The checkpoint.** `/longhaul:checkpoint` saves the model's own structured state (goal, next action, decisions, done, in progress, blocked, changed files, verification) through `longhaul checkpoint save`; a `SessionStart` hook puts it back after a compaction or a resume, with its age and which of the named files changed since. The file is private (0600), capped, and a save that holds anything shaped like a credential is refused.
- **The plugin.** `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` (the repository is its own marketplace), `hooks/hooks.json`, the two skills, and `bin/longhaul`, a shim that runs only `inspect`, `usage`, `status`, `checkpoint save` and `-h` from inside a session. Try it with `claude --plugin-dir`, or install it from the repository's own marketplace as `longhaul@longhaul`.
- **The supervisor (advanced, opt-in).** `longhaul` runs `claude` in your terminal with Remote Control on and watches it. At a usage limit it asks before moving the conversation to another of your subscriptions (`--switch ask`, the default), moves without asking (`auto`), or waits for the reset (`never`). Where no one can be asked, `longhaul pipe` and the VS Code panel, ask acts as never, and a move needs `auto` and the front end's own consent (`--swap`, `switch_mid_session`). A conversation resumed by id starts on the account it last ran on in ask and never, unless `--account` names another (a panel chat too, when it was reopened by id; a panel chat that was new keeps its place once the status line or the relay has recorded it). It reopens the conversation after a crash or a lost Remote Control link, and stops after a few. `longhaul add`, `setup`, `remove`, `status` and `clear` manage the accounts.
- Every numeric `LONGHAUL_*` setting survives a typo: the default is used and `longhaul status` names the setting ignored.
- The tests ship with the code, with a GitHub Actions workflow that runs them on macOS and Linux, on Python 3.10 and the latest 3.x, and runs `claude plugin validate --strict` without failing the build.

### Known limits

The guard checks a whole `Read` and a bare `cat` only: a `Read` with a large `limit` is a part and gets past block mode, and `head`/`sed` dumps are not covered. `inspect`'s "Read again" does not see files changed through Bash. `/clear` gives the session a new id, so the checkpoint does not come back after a clear; it comes back after a compaction and a resume, whole and however old, up to about 2,500 tokens each time (the text is kept under 10,000 characters). The hooks add 87–94 ms to every `Read` and `Bash` call. In the advanced feature, the question at a real limit has been seen only against a fake Claude, Claude Code 2.1.304 is untested at a limit, a real crash's exit code has not been observed, and the lock files in `locks/` stay (empty; the OS releases the lock).
