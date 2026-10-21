# Longhaul

Longhaul shows you where your Claude Code sessions' tokens go, and stops the waste.

It is a Claude Code plugin and a terminal command in one Python file (plus a two-file VS Code launcher), standard library only. The plugin reports a conversation's usage from inside the session, warns about or blocks the tool calls that waste the most context, and puts the model's own checkpoint back after a compaction. Account switching across several subscriptions is an advanced feature, off unless you turn it on.

## See where your tokens go

- **Inside a session:** `/longhaul:inspect` shows the report for this conversation. `/longhaul:inspect <id>` shows another one.
- **In a terminal:** `longhaul inspect [ID|PATH] [--json]`. Give a conversation's id, a short id that only one conversation starts with, or the path of its transcript; with nothing, it reads the latest conversation the status line recorded (without the status line, give the id). `--json` prints one record per number, with its metric, value, unit, source and confidence, so runs can be compared.
- **The status line:** `longhaul statusline`, which you set in your own `~/.claude/settings.json` (see Install), shows the account and how much of its 5-hour and 7-day allowance is used, with the reset times. It also records each reading's context size, cache figures and API-price cost in a ledger that `inspect` reads.
- **The ledger:** `longhaul usage [--days N]` shows it per session and per account, each session named by its `--name` label, or by its project folder and short id, with each account's latest allowance reading first.

The report is read from the conversation's transcript, its subagents' transcripts, the ledger and the guard's record. It holds: requests, main conversation and subagents, per model; tokens by kind, input, cache write, cache read and output, the API's own counts; the context's first, peak and last size, and every compaction; the cache hit ratio from the status line; the five largest tool outputs; calls and output characters per tool; files read again, and searches repeated, with no edit or compaction in between; what the guard warned about or blocked; tool and API errors. Numbers, model names, tool names and file paths only, never message text or tool output. Each heading names where its numbers came from and how sure they are: measured, counted, inferred or estimated.

## Stop wasting tokens

**The guard** runs before every `Read` and every `Bash` call (the hook is described below) and catches three kinds of waste:

1. **Read again unchanged:** a file read in full that is already in the context and has not changed since (same size, modification time and change time).
2. **Whole read of a big file:** a `Read` of a file over 20,000 bytes (`LONGHAUL_GUARD_BIG`) with no `offset`, `limit` or `pages`. Images and PDFs are excepted.
3. **`cat` of a big file:** a bare `cat` of such a file. A `cat` with a pipe, a redirect or a chain is left alone: it is filtered, or it is a write.

In **warn** mode, the default, the call runs and the model gets the advice as context ("longhaul guard (advice; the call went ahead): ..."), naming the part to read instead. With `LONGHAUL_GUARD=block` the call is denied with the same text and "Make the narrower call instead"; the model always has a way through, because a part read is never checked. `LONGHAUL_GUARD=off` does nothing. A subagent is its own context, so its first read of a file is never a repeat of the main conversation's; after a compaction or a clear the guard forgets what was read. `inspect` shows what the guard caught, by rule and in bytes.

**The checkpoint.** Claude Code's own summary at compaction is made when the context is already full. `/longhaul:checkpoint` has the model save its own structured state at a moment you, or it, choose: the goal, the next action, the decisions, what is done, in progress and blocked, the files changed, and how to verify. Longhaul keeps it private under `~/.longhaul/checkpoints/` and puts it back after a compaction or a resume, with its age and which of the named files changed or went since. It is capped at 1,000 characters per text, 20 entries per list and 8,000 bytes in all, and a save that holds anything shaped like a credential is refused.

## Measured

Longhaul claims no saving it has not measured. The guard's block mode is being measured against warn mode and off on one repeatable task. The method is written here so that a stranger can run it again:

- **The task.** This repository as it was at the run (commit `92ea697` of the private history; the public history starts afresh, so the hash is for the record), with the untracked `docs/` folder on disk, which the public tree does not carry, `git status --short` empty before each run, and this prompt, verbatim: *"Read the code and explain how Longhaul decides whether to move a conversation to another account at a usage limit: which function decides, the three switching modes and which is the default, what `longhaul pipe` and the VS Code panel do where no one can be asked, and which decision record covers that. Name the functions. Do not change any file."* The code at `v0.1.0` is that tree plus this release's changes. It is read-heavy on a 163 KB module then (187 KB at the release), which is what the guard exists for: a whole `Read` of `longhaul.py` trips "big"; a second whole `Read` of a small file trips "repeat".
- **The quality check.** Five facts, each present in the answer or not: `decide_move()`; ask, auto and never, with ask the default; ask acts as never for `longhaul pipe` and the panel; `--swap` for `longhaul pipe`; the decision record named. A run with fewer facts than the baseline's worst run is a regression. A re-run from this repository replaces the fifth fact, since the decision records are not shipped, with: the mode comes from `--switch`, then `LONGHAUL_SWITCH`, then the default (`switch_mode()`).
- **The configurations.** `LONGHAUL_GUARD=off` (the baseline), `warn` and `block`: five runs each, interleaved (off, warn, block, off, ...) so that the time of day and the provider's cache spread evenly over the three; one model, pinned with `--model opus` (which resolved to `claude-opus-5-5` on the day; Claude Code made its own `claude-haiku-5-5` requests beside it in every run). Nothing else differs.
- **The loop,** run from the checkout, with a scratch folder as `LONGHAUL_HOME` for every run so that `~/.longhaul` is untouched, `PROMPT` set to the prompt above and `MODEL=opus`:

  ```sh
  S=$(mktemp -d)
  for n in 1 2 3 4 5; do for mode in off warn block; do
    LONGHAUL_HOME=$S LONGHAUL_GUARD=$mode claude -p "$PROMPT" --plugin-dir "$PWD" \
      --model "$MODEL" --output-format json \
      --allowedTools "Read,Grep,Glob,Bash(grep:*),Bash(sed:*),Bash(head:*),Bash(cat:*),Bash(wc:*)" \
      > "$S/run-$mode-$n.json"
    sid=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$S/run-$mode-$n.json")
    LONGHAUL_HOME=$S python3 longhaul.py inspect --json "$sid" >> "$S/inspect-$mode.jsonl"
  done; done
  ```

  The Bash allowances are deliberate: the model's workarounds must be allowed, or they cannot be measured.
- **What is measured.** Every number comes from `longhaul inspect --json`, per run: input, cache write, cache read and output tokens (main conversation and subagents), requests, the context's peak and last size, the five largest tool outputs, calls and output characters per tool, repeated reads, what the guard fired on and the bytes involved per rule (in block mode that counts denials), tool errors; plus the result's `duration_ms` and the five-fact score by hand. The headline is the prompt tokens carried by the main conversation, input + cache write + cache read, which is what a smaller context lowers. Median and min–max per mode.
- **The rule for the sentence.** A saving is written here only if block's median prompt tokens carried is below off's, every block run scores all five facts, no block run has more requests or tool errors than off's worst run, and the Bash output characters did not rise by what Read fell. Otherwise this section says "no saving shown", with the numbers.

**The result** (2026-10-20, commit `92ea697`, `--model opus`, resolved to `claude-opus-5-5`, fifteen runs, every run scoring all five facts):

| mode | prompt tokens carried, median (min–max) | requests, median (min–max) | whole reads the guard caught |
|---|---|---|---|
| off | 373,031 (305,079–500,732) | 7 (6–9) | not checked |
| warn | 429,349 (367,897–614,283) | 8 (7–11) | 0 in 5 runs |
| block | 398,455 (372,466–420,245) | 7 (7–8) | 0 in 5 runs |

**No saving shown.** The three medians sit inside the baseline's own range, and the guard fired on nothing: on this task the model read the 163 KB module in parts by itself (nine or ten Read calls per run, of up to 16,000 characters, never the whole file) and read each document once, so none of the three rules applied. What this run shows is that any cost of the hooks in tokens is not visible above the run-to-run spread; it does not show what block mode does when the rules fire. The waste the guard was built from is in real sessions, not in this task: in one owner's 964 transcripts over five weeks, 515 whole-file reads over 20,000 characters, 17.4 million characters in all. A task that provokes them, or a week of real use in warn mode counted by `inspect`, is the next measurement; until it is done block mode stays opt-in and this README claims nothing for it.

## Install

You need Claude Code (`claude`) and `python3` on your PATH. Longhaul is one Python file with no dependencies; nothing is pip-installed. A plugin runs code with your privileges, so read `hooks/hooks.json`, `bin/longhaul`, the two `skills/*/SKILL.md` and `longhaul.py` before you install it. The tests ship with the code.

**Try it without installing**, from a checkout:

```
claude --plugin-dir /path/to/longhaul
```

**Install the plugin**, in Claude Code:

```
/plugin marketplace add amradel55/longhaul
/plugin install longhaul@longhaul
```

In a session, `/plugin install` opens the plugin's details so you can review it and choose a scope; it does not install outright. The marketplace add needs `git` on your machine; it writes the marketplace to `extraKnownMarketplaces` in your `~/.claude/settings.json` and to Claude Code's own `~/.claude/plugins/` records, and installing records the plugin as enabled there too: Claude Code's own steps, not Longhaul's. The plugin gives you `/longhaul:inspect`, `/longhaul:checkpoint`, the hooks, and `bin/longhaul`, which the skills run by its full path and which runs only `inspect`, `usage`, `status`, `checkpoint save` and `-h` from inside a session and refuses the rest. A plugin cannot set the status line or put `longhaul` on your terminal's PATH, and its folder changes at every update, so the terminal command is a second install. Each release bumps the version in both `plugin.json` and `marketplace.json` (a test keeps them equal): users on a cached copy get an update only when the string changes.

**The terminal command**, for the status line and the advanced feature: keep a checkout of the repository and run, in it,

```
python3 longhaul.py setup
```

It links `~/.local/bin/longhaul` to that checkout, never to the plugin cache (it refuses to). Then point the status line at it in your own `~/.claude/settings.json`, your edit, not Longhaul's:

```json
"statusLine": {"type": "command", "command": "~/.local/bin/longhaul statusline"}
```

**The tests**, from the checkout:

```
S=$(mktemp -d); LONGHAUL_HOME=$S/home LONGHAUL_DEFAULT_CONFIG_DIR=$S/claude LONGHAUL_DEFAULT_GLOBAL_JSON=$S/claude.json LONGHAUL_NO_NOTIFY=1 python3 -m unittest test_longhaul -q
```

The scratch paths keep the tests away from your real `~/.claude` and `~/.longhaul`.

Python 3.10 or later.

## What the hooks do, and how to turn them off

`hooks/hooks.json` registers three hooks. Each runs the plugin's own `longhaul.py` with `python3`, has a 5-second timeout, and shows a status message while it runs.

| Hook | When | What it does |
|---|---|---|
| `PreToolUse` on `Read` and `Bash` | before every `Read` and every `Bash` call | `longhaul guard`. Reads the call from the hook's JSON on stdin: the tool, a `Read`'s file path and whether it is a part, or a `Bash` command's first program and its file arguments, and only when that program is `cat`. Checks each file's size and change times against the session's record and answers with advice (warn), a denial (block), or nothing. Writes `~/.longhaul/guard/<session>.json` (a subagent's record is `<session>+<agent>.json`). The command line itself is never kept. Status message: "Checking this call (longhaul guard)". |
| `SessionStart` on `compact` and `clear` | after a compaction or a clear | `longhaul guard` again: empties the record's list of reads, since nothing read before is in the context now, and keeps its counts. "Forgetting this session's reads (longhaul guard)". |
| `SessionStart` on `compact`, `clear` and `resume` | after a compaction, a clear or a resume | `longhaul checkpoint`: reads `~/.longhaul/checkpoints/<session>.json` if there is one and puts its text back into the context, with its age and which of the files it names changed since. Writes nothing but one log line. "Looking for a checkpoint (longhaul checkpoint)". |

Every hook fails open. Whatever goes wrong, it prints nothing, writes at most a line per record to `~/.longhaul/longhaul.log`, and exits 0. Claude Code lets a call run on any exit but 2 and when a hook overruns its 5 seconds, so a guard never stops a call it did not mean to stop, and a session start is never stopped by its checkpoint. Measured warm on one Mac, the guard takes 87–94 ms per call. Once the plugin is enabled its hooks run in every session of every project; Claude Code has no switch per hook.

Without `python3` on PATH the hooks cannot start; Claude Code then shows its own hook-error notice with the first line of stderr, lets the call run, and the skills' shell steps stop the skill with the shim's message.

- **The guard off:** `LONGHAUL_GUARD=off` in the environment Claude Code starts with. `warn` is the default; `block` denies. The deny text never names the off switch: that is for you, not the model.
- **The checkpoint hook** has no switch of its own. Disabling the plugin turns all three hooks off together: `/plugin`, the Installed tab, select Longhaul, Disable plugin; or `claude plugin disable longhaul@longhaul` in a shell; it applies at `/reload-plugins` or the next session.

## What is written where

Everything Longhaul keeps is under `~/.longhaul` (or the folder `LONGHAUL_HOME` names). The files it writes are readable by you alone (mode 0600), except the empty lock files and the skill's `<session>.state.json`, which the Write tool makes with your default mode and the save removes once it has read it.

| File | Holds | Written by |
|---|---|---|
| `guard/<session>.json`, `guard/<session>+<agent>.json` | the files read in full (path, size, change times, when), and per rule how often the guard fired and how many bytes were involved; never a command line or a file's text | the guard hook |
| `checkpoints/<session>.json` | the model's own summary of the conversation, on purpose: goal, next action, decisions, done, in progress, blocked, changed files, verification; 0600, read by the checkpoint hook alone | `/longhaul:checkpoint`, through `longhaul checkpoint save` |
| `checkpoints/<session>.state.json` | the same state as the model wrote it, before the save checks it; made by the Write tool with your default file mode; removed as soon as the save has read it, whether it saved or refused | the model, through `/longhaul:checkpoint` |
| `ledger.jsonl` (and `.1` once it is moved aside at 10 MB) | one row each time a status-line reading moves: time, session id, account, API-price cost, the 5-hour and 7-day allowance used, the 5-hour reset time, context size, model id, cache hit ratio, misses and re-cached tokens, the API time of the reading and, from the panel, the claude process id | the status line (and the panel wrapper and `longhaul pipe`, which record a chat's usage the same way) |
| `usage/<account>.json` | the latest allowance reading per account | the status line (and the panel wrapper and `longhaul pipe`, which record a chat's usage the same way) |
| `usage/sessions/<session>.json` | a conversation's last row, the path of its transcript, and the account it last ran on, which a resume reads in ask and never | the status line, the panel wrapper and `longhaul pipe`; the supervisor notes the account at the end of each launch (and the panel wrapper for a chat reopened by id) |
| `longhaul.log` (and `.1` at 5 MB) | what Longhaul did and why, one line each: sessions, sizes, reasons; never message text | every command and hook |
| `labels.json` | the name you gave a session with `--name` | the supervisor and `longhaul pipe --name` |
| `accounts.json`, `state.json`, `auth-cache.json`, `locks/<session>.lock` | the advanced feature's accounts (name, folder, email); the recorded limits and the limit stops already acted on; per account folder, the email `claude auth status` last reported and when (kept 120 s, `LONGHAUL_AUTH_TTL`); the supervisor's empty per-conversation lock files | `longhaul add` and `remove`; the supervisor, `longhaul pipe`, the panel wrapper and `longhaul clear`; the panel wrapper and `longhaul pipe` (an entry goes at `setup`, `remove` or a sign-in made in a panel chat); the supervisor |
| `.guard.lock`, `.state.lock`, `.folders.lock`, `.auth.lock`, `.accounts.lock`, `.<file>-move.lock` | nothing: empty files the OS locks so that one writer at a time updates a record | the guard hook; the log and the ledger when they move aside; the advanced feature |

`vscode.json` is yours: Longhaul reads it and never writes it.

Nothing but the checkpoint file, and the skill's state file until the save takes it, holds the model's text. `inspect`, the guard's record, the ledger and the log hold numbers, ids, account, model and tool names, file paths and Longhaul's own lines; never message text, tool output or a credential. (The log names the email an account is signed in as when it is the wrong one.)

**The checkpoint's cost:** the whole of it is put back into the context at every compaction and resume, up to about 2,500 tokens each time (the text is kept under 10,000 characters), however stale it is. Save a new one when the state has moved on, and keep it short.

Longhaul never edits `~/.claude/settings.json` or `~/.claude.json`. Two things touch them, and both are yours: installing the plugin, Claude Code's own step, which records the plugin as enabled in your settings; and the status line, which you point at `longhaul statusline` in your own settings. The advanced feature writes elsewhere, and its section says where. Its one exception is the promotion that section describes: a `settings.json`, `keybindings.json` or `CLAUDE.md` you changed in a chat on another account becomes the shared file, and the one before is kept beside it.

## Advanced, experimental: carry on across your subscriptions (opt-in)

Off unless you turn it on, and experimental: the supervisor's behaviour at a real usage limit, after a real crash and across a real account change has been exercised against a fake Claude and in short live checks, not yet end to end on real sessions (see Limitations). It moves one person's own conversation between that person's own subscriptions, never an account between people; read your subscriptions' terms and decide for yourself.

If you have more than one Claude subscription, `longhaul`, the terminal command, not the plugin, runs `claude` in your terminal with Remote Control on and watches it. At a usage limit it closes Claude, and then:

- `--switch ask`, the default: it asks in that terminal whether to move the conversation to the other account. On `y` it reopens the conversation there with a "carry on" message; with no yes it waits for the reset and carries on where it was.
- `--switch auto`: it moves without asking, and early when an account is nearly full and Claude is idle.
- `--switch never`: it always waits for the reset.

`LONGHAUL_SWITCH` sets the mode without the flag. Where no one can be asked, ask acts as never: `longhaul pipe` (another program driving `claude` over stream-json) moves a session only when the driver passes `--swap` and the mode is `auto` (`--switch auto` before `--`, or `LONGHAUL_SWITCH=auto`); the VS Code panel, through the `longhaul-vscode` process wrapper, moves a chat only with `switch_mid_session` true and `"switch": "auto"` in `~/.longhaul/vscode.json` (or `LONGHAUL_SWITCH=auto` in the environment VS Code starts with). Nothing moves without an explicit auto. A conversation resumed by id (`longhaul -r ID`, a resumed `longhaul pipe` session, a panel chat reopened by id) starts on the account it last ran on in ask and never, unless `--account` names another; with no record of where it ran, or in auto, Longhaul picks. The record is the session's file under `usage/sessions/`, which the status line, the panel wrapper, `longhaul pipe` and the supervisor itself (at the end of each launch) keep, so a terminal session without the status line is remembered too; a panel chat that was new keeps its place once the status line or the relay has recorded it. In `longhaul pipe` and the panel the last account is used only if it is still signed in as its email (and, for pipe, managed); otherwise Longhaul picks as before. If that account is at its limit, `longhaul -r` waits for its reset, or start it with `--account`; the panel, which has no `--account`, sends a notification that the chat stays on the account at its limit. Longhaul also reopens the conversation after a crash, with a countdown that grows each time, and when a Remote Control link stays lost, and gives up after a few.

Setting it up: each account needs its own folder with its own sign-in. `longhaul add NAME EMAIL` makes the folder (`~/.claude-<name>`, lower-cased: `~/.claude-pro` for `Pro`), signs it in once in the browser, and accepts only that email for it from then on. `longhaul setup [NAME]` signs in again any account that is not signed in as its email, and links `~/.local/bin/longhaul` so that `longhaul` works from any folder. `longhaul status` shows the accounts, who each is signed in as, the recorded limits, the links, the Claude Code version check and the switching mode in force. Then `longhaul` for a new conversation, `longhaul -c` to continue the latest, `longhaul -r [ID]` to resume one, `longhaul --account Pro` to start on an account; anything after `--` goes to `claude` on every launch. `longhaul -h` lists the rest.

What the advanced feature writes outside `~/.longhaul` (the plugin writes none of this):

- `~/.claude-<name>/`, one folder per account you add (mode 0700), holding that account's sign-in and its own `.claude.json`. Inside it, links to `~/.claude`'s `projects`, `sessions`, `file-history`, `history.jsonl`, `plans`, `plugins`, `skills`, `agents`, `commands`, `settings.json`, `keybindings.json`, `CLAUDE.md`, `ide` and `todos`, so that every account sees one history, memory and setup. Claude Code keys each account's Keychain entry to its folder.
- The account's own `.claude.json`: at setup, some keys are copied once from `~/.claude.json` (MCP servers, project trust, onboarding done, install method, auto-update, theme; never the sign-in), and before each launch any MCP server `~/.claude.json` has and the account lacks is copied across.
- `~/.local/bin/longhaul`, a link to the checkout's `longhaul.py`.
- If Claude Code saves a linked file as a separate copy under an account, Longhaul puts the link back at the next launch and keeps the copy beside it. For `settings.json`, `keybindings.json` and `CLAUDE.md`, a copy newer than the shared file is your latest edit, made in a chat on that account, and becomes the shared one, with the older kept as `settings.json.before-<account>-<stamp>`. It says so each time.

## Supported platforms and versions

- **macOS**, where Longhaul was built and checked. The supervisor uses macOS pieces: notifications through `osascript`, `caffeinate` to keep the Mac awake while it runs, and the Keychain entry Claude Code keys to each account's folder.
- **Linux:** the suite runs in CI (Ubuntu, Python 3.10 and the latest 3.x); not used in anger.
- **Claude Code.** The supervisor reads files Claude Code writes but does not document (the running process's `sessions/<pid>.json` and the conversation's transcript); it has been checked on 2.1.295 to 2.1.303. On a newer version `longhaul` says it "has not been checked" until it has seen a real limit there: Claude runs as normal, and if Longhaul does not switch accounts at a limit, that is why. The hooks, the shim and the skills were built against 2.1.304.
- **Python 3.10 or later**, the oldest CI runs the suite on; standard library only; nothing is pip-installed. `python3` must be on PATH: the hooks and the plugin's `bin/longhaul` run it by name. Without it the hooks fail open (nothing is checked, nothing is put back) and the shim, which both skills run, stops with one line that says to install it (`xcode-select --install` or `brew install python` on a Mac).
- **Where the plugin loads:** the terminal, the desktop app and VS Code. claude.ai and Cowork do not install a plugin that has a `bin/` folder, and Longhaul has one.

## Limitations

- The guard checks a whole `Read` and a bare `cat`. A `Read` with `offset`, `limit` or `pages` is a part and is never checked, so a `limit` as large as the file gets past block mode; `inspect`'s "Read again" still counts the repeated call. A `cat` with a pipe, a redirect or a chain is left alone, and `head`/`sed` dumps are not covered: counted over 964 transcripts, 14 large-range `sed -n` dumps against 68 big `cat` results and 515 big whole-file Reads, so not worth a rule.
- `inspect`'s "Read again" and "same search again" do not see files changed through Bash (`sed -i`, a `git checkout`, a build), so a read after such a change can be counted as a repeat.
- A relative path in a `cat` is resolved against the working directory the hook is given; whether that follows a `cd` in the Bash tool is not yet checked.
- `/clear` gives the session a new id (seen live), so the checkpoint does not come back after a clear: the hook runs and finds no file. It comes back after a compaction and a resume.
- The checkpoint is put back whole and however old it is, up to about 2,500 tokens each time (the text is kept under 10,000 characters).
- The hooks add 87–94 ms to every `Read` and `Bash` call, measured warm on one Mac.
- The advanced feature: the question at a real limit has been seen only against a fake Claude; Claude Code 2.1.304 is untested at a limit; `ask` asks nobody in `longhaul pipe` or the VS Code panel, so there the conversation stays where it is; a real crash's exit code has not been observed; a resumed conversation's return to its last account has been seen only against the fake Claude; the lock files in `locks/` stay (empty; the OS releases the lock).

## Pairs well with CodeGraph

[CodeGraph](https://github.com/colbymchenry/codegraph) is a local code index (MIT) that builds a symbol and dependency graph of a repository in SQLite under `.codegraph/` and exposes one MCP tool, `codegraph_explore`, and a CLI, `codegraph explore`, so that a question like "how does X decide Y" is answered in one call with the symbols' source and the call paths between them, instead of a grep-then-read loop. Its README reports 88% fewer tool calls and 62% fewer tokens on architecture questions over seven repositories, the median of four runs each: self-reported, not independently replicated. Its README also gives the counterweight: an answer leaves about 80% more retrieval context resident by the end of a session (67k against 18k tokens on one repository), so it moves cost as well as cutting it, and a long session should budget for that. Longhaul's part is to show whether an index would help: `/longhaul:inspect` counts the calls per tool, the searches repeated with nothing changed in between and the files read again, so you can see how search-heavy a session was before you add one. Longhaul never installs CodeGraph and never changes `CLAUDE.md`, `~/.claude/settings.json` or `~/.claude.json` for it; CodeGraph's own installer may write a section into your instructions file, sets up permissions for its tools in Claude Code unless told not to, and asks you about telemetry up front. The link and the decision are yours.

## Licence

MIT. See `LICENSE`.
