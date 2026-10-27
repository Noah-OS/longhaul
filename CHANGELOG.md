# Changelog

All notable changes to Longhaul are recorded here. The format follows Keep a Changelog; the version is the plugin's, from `.claude-plugin/plugin.json`. Each release bumps the version in both `plugin.json` and `marketplace.json` (a test keeps them equal): users on a cached copy get an update only when the string changes.

## 0.3.0 - 2026-10-27

The token layer: where a session's context costs more than its size, a checkpoint that keeps the thread, and an output filter measured before it cuts anything.

### Added

- **The output filter, in shadow.** A `PostToolUse` hook on Bash can hand Claude a shorter output. `LONGHAUL_FILTERS` sets how far it goes:
  - `shadow`, the default: it changes nothing, and logs by size alone what each level would cut;
  - `lossless`: colour codes, progress redraws and repeated lines go, and grep's matches are grouped by file. Nothing is lost;
  - `on`: it also leaves out passed tests, install and build progress, and the middle of a long output. The whole output is saved first under `~/.longhaul/outputs` (private, for 7 days), and one line says where;
  - `off`.

  Files Claude asked to read, diffs, and a format the user chose are never touched. It steps aside when RTK's own hook is installed. After a Bash call with 1,500 characters of output or more, the hook adds about 90 ms on a quiet Mac; shorter output costs a Python start only. `/longhaul:inspect` replays a chat's Bash output through both levels. On the author's two largest chats, `lossless` would cut 0.6% of Bash output and `on` about 1%. After about a fifth of the outputs `on` would have cut, Claude's next calls needed what it left out, so `on` stays off. Its rules are learned from RTK and caveman, and rebuilt in Python.
- **Checkpoint 2.** Four more lists: the user's own words (`owner_said`), the facts found (`facts`), what failed (`tried_failed`), and the files and lines the next step needs (`working_set`).
  - A checkpoint can be 16,000 bytes. About 9,000 characters come back after a compaction, most needed first, with a line naming what stayed in the file.
  - A fact or a working-set entry naming a file that changed since is marked.
  - The save says how many of the last checkpoint's own words and facts are missing, so they are carried over, not lost.
- **`/longhaul:inspect` shows where the context cost more than its size:**
  - the input cost after caching;
  - the cache written again, and why: a tool call or idle time past the cache's lifetime, a message after it, or a changed prefix;
  - large cache writes;
  - files read again after a compaction, by `Read`, or by `cat`, `head`, `tail`, `nl` or `sed` in Bash;
  - the text hooks added;
  - the size of MEMORY.md and the CLAUDE.md files, and the paths they name that are gone;
  - the output filter's replay and log.
- **Advice for a subagent that waits too long.** A subagent's prompt cache lasts 5 minutes. The guard tells a subagent whose Bash call could outlast it (a timeout over 270 s, or a long sleep or wait loop) to run it in the background and check back. This is advice only, in block mode too.

### Changed

- **A checkpoint that uses the new lists is saved as `schema_version` 2.** A Longhaul before 0.3.0, such as another account's older copy, does not put it back, and logs why. A checkpoint with only the old lists is saved as before.
- **Longhaul adds about 400 tokens to every session**, up from about 377: the checkpoint skill's description names the new lists.

### Fixed

- **A failed hook could write a tool's output into the log.** The log kept the error's text, which can hold the hook's input. It now keeps the error's type alone.

## 0.2.9 - 2026-10-27

One install, and the reminder that keeps the thread fires after every compaction.

### Added

- **The terminal command without a git clone.** `longhaul setup`, run from the installed plugin, writes a small launcher at `~/.local/bin/longhaul` that runs the newest Longhaul Claude Code has installed (the `installPath` in each Claude config folder's `installed_plugins.json`), so it survives plugin updates. If the plugin is gone it says so in one line: reinstall it. `longhaul doctor` prints the exact line to run. A git clone with `python3 longhaul.py setup` still links the command to the checkout, for working on Longhaul itself.
- **`longhaul statusline install` and `uninstall`.** Run in a terminal: it backs up `settings.json` beside itself, asks before replacing a status line you already have, test-runs the command and puts everything back if it fails. `uninstall` puts the old status line back. Longhaul never runs either on its own; `longhaul doctor` names `statusline install` as the step when the status line is not Longhaul's.
- **`longhaul uninstall`, with `--dry-run`.** Lists what Longhaul put outside the plugin (the launcher, the status line, the VS Code wrapper, the auto-update flag) and, without `--dry-run`, asks and removes them; `~/.longhaul` stays unless `--purge`. It then says what stays and how to remove it: the plugin, the code index's lines in `.git/info/exclude`, Trail's hook.
- **A log line for each compaction.** A `PreCompact` hook writes one line to `~/.longhaul/longhaul.log`: whether you, Claude Code or Longhaul compacted, and how old the checkpoint was. It never blocks a compaction. `longhaul doctor` prints a "# Compact instructions" block to paste into your CLAUDE.md, so Claude Code's own summary keeps the thread too; Longhaul never writes CLAUDE.md.
- **Why the context reminder fired or not.** Each prompt adds one `reminder check:` line to the same log: the context size read, the threshold, and fired or the reason it did not. Numbers and codes only.

### Changed

- **The Advisor is its own plugin, `longhaul-advisor`,** in the same marketplace: `/longhaul-advisor:advise` and the `longhaul-advisor:advisor` agent. Longhaul alone now adds about 377 tokens to every session, down from about 653; `longhaul-advisor` adds about 291 more where it is installed, and installs Longhaul with it. The experiment stays off under its own rules. A 0.2.8 session's `longhaul:advisor` is still checked as the Advisor.

### Fixed

- **The context reminder went quiet after a compaction.** It is said once per 200k crossing, and the crossing already said survived a compaction; it was forgotten only when a prompt-time reading fell below 100k, which in a long autonomous turn never came. So the 200k crossing after a compaction was skipped and the context grew to 400k before the next save and compaction. It is now forgotten at each compaction.

## 0.2.8 - 2026-10-27

Longhaul hears Claude Code's own word when a turn stops or a chat ends.

### Added

- **Claude Code's own signals that a turn stopped.** Two new hooks: StopFailure, which Claude Code runs when a turn ends on an API error, and its Notification for a wait at a claude.ai usage limit (2.1.234 and later). For the main conversation each logs one line and keeps the error's type or the wait's moment, and the time, in `~/.longhaul/stops.json`; never the error's text or the notification's message. Where you have to act, the display shows a pop-up: a rate or usage limit, a failed sign-in, a billing error, an account on hold, a limit that reset while the computer slept (press Enter to carry on), or Claude Code no longer waiting. `/longhaul:inspect` counts them under Failures. The terminal `longhaul` also takes a `rate_limit` stop for a limit stop when the account's own status-line reading shows it at its limit, with that reading's reset time and kind, beside the transcript fields it read before, which Claude Code does not document; with the account not at its limit it is a passing rate limit, logged and left to Claude Code. When Claude Code says it stopped waiting at a usage limit without carrying on, the terminal `longhaul` acts at once on a limit it already has, instead of waiting up to 30 seconds while Claude Code's status still says busy; with no limit seen yet, that word is a limit stop only when the account's reading shows it at its limit, as for `rate_limit`. Claude Code starts no wait of its own in a Remote Control session, which a terminal `longhaul` chat is by default, so there that word comes only after a wait you picked from `/rate-limit-options`, or after repeated hits. The VS Code panel and `longhaul pipe` read Claude Code's stream as before, and the other types, a wait that started or a limit that reset, decide nothing.
- **A normal end told from a crash by Claude Code's word.** A SessionEnd hook keeps why each session ended (`clear`, `resume`, `logout`, `prompt_input_exit` or `other`) and when, nothing else. The terminal `longhaul` no longer reopens a chat that exited with an error code after Claude Code recorded that you closed it at its prompt or signed out; any other non-zero exit is still a crash. The hook keeps within Claude Code's 1.5-second budget for SessionEnd.

### Changed

- **The plugin's description leads with keeping the thread.** `/plugin` showed "See where your Claude Code sessions' tokens go, and stop the waste; keeps long sessions running across your own subscriptions, opt-in." It now shows "Keep the thread in long Claude Code sessions: Claude saves where it is, Longhaul compacts, and the work picks up after a compaction, a resume or /clear. Shows where the tokens go.", and the README opens the same way. Moving a chat to another of your own subscriptions is unchanged and still opt-in; the README's table and the wiki describe it.
- **The model guide names Fable, narrowly, and a step up after two failures.** Claude was told `haiku`, `sonnet` or `opus` for each subagent. The guide now adds `fable` only for a decision that is hard to undo, the review before a release, or a problem Opus has failed at twice, at most one Fable run per task and with the reason said; and when a run's work fails its tests twice, the next run starts one model higher, not again on the same one. Longhaul still never raises a subagent's model itself, and a cap (`longhaul models cap opus`) still lowers a Fable call. In a models trial only the guided half gets the guide.
- **Checked on Claude Code 2.1.310.** A live test at a real weekly limit on 2.1.310 saw the limit stop, the question, and a yes carry the chat on on the other account; Claude Code's StopFailure `rate_limit` fired in the same second as the transcript's line, and no quota notification did. `longhaul` no longer says 2.1.304 to 2.1.310 are unchecked.

### Fixed

- **The README said the code index is off until you turn it on.** It has been on by default since 0.2.2, where CodeGraph or Trail-Code is installed; `LONGHAUL_INDEX=off` turns it off.
- **`--account` naming an account at its limit says so.** It started the chat on another account with no word of why; it now says the account is at its limit until its reset and that it is picking another.

## 0.2.7 - 2026-10-26

Long subagent runs are told to wrap up.

### Added

- **A reminder for long subagent runs.** Subagents were most of the cost on the owner's real work, and the costly runs were long ones: each request re-reads the subagent's whole, growing context (one run made 355 requests at 466k tokens each). When a subagent's own context passes `LONGHAUL_SUBAGENT_AT` (150,000 tokens unless set; `off` turns it off), Longhaul tells that subagent, at its next file read or shell command, to finish the step it is on and report back what is done, what is left and what the next agent needs, so the main conversation can hand the rest to a fresh one. Once per multiple per run, and the count starts again after the subagent's own compaction. Logged with the agent type and size; `longhaul doctor` shows the setting and `/longhaul:inspect` how many subagents were told.

## 0.2.6 - 2026-10-25

### Fixed

- **A VS Code tab no longer opens a chat a terminal is running.** Longhaul refused to resume in a terminal a chat open in the VS Code panel, but not the other way round: a tab reopened a chat a terminal `longhaul` still ran, and both wrote to it. The panel now refuses too, says where the chat is open and to close it there first. Both checks read Claude Code's records of its processes, in every account's folder.
- **The reminder also asks Claude to save before it ends its turn.** A reminder late in a long, unattended turn was saved only at the next prompt, hours later, so auto-compact waited as long. The line still keeps saves out of the middle of an edit or a test run.
- **A model pick is "guided" only where the guide was given.** In a models trial's unguided half, a call that named its model was logged `(guided)`, so the trial would have credited the guide; it is now `(its call)`.
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
