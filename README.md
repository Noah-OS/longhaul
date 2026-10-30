<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/longhaul-banner-dark.svg">
    <img src=".github/assets/longhaul-banner-light.svg" alt="Longhaul. Stay in context. Go the distance. A Claude Code plugin: see where the tokens go, catch the reads that flood the context, pick up where you left off after a compaction.">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Noah-OS/longhaul/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/Noah-OS/longhaul/actions/workflows/tests.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/Noah-OS/longhaul/releases"><img alt="latest release" src="https://img.shields.io/github/v/release/Noah-OS/longhaul?label=release&color=F5B544"></a>
  <a href="https://github.com/Noah-OS/longhaul/wiki/Installation"><img alt="Python 3.10 or later, standard library only" src="https://img.shields.io/badge/python-3.10%2B%20%7C%20stdlib%20only-3776AB?logo=python&logoColor=white"></a>
  <a href="LICENSE"><img alt="MIT licence" src="https://img.shields.io/badge/licence-MIT-2DD4BF"></a>
</p>

<p align="center">
  <a href="#1-install"><b>Install</b></a> &nbsp;·&nbsp;
  <a href="#3-day-to-day"><b>Day to day</b></a> &nbsp;·&nbsp;
  <a href="#4-more-than-one-account"><b>Accounts</b></a> &nbsp;·&nbsp;
  <a href="#6-codex"><b>Codex</b></a> &nbsp;·&nbsp;
  <a href="#troubleshooting"><b>Troubleshooting</b></a> &nbsp;·&nbsp;
  <a href="https://github.com/Noah-OS/longhaul/wiki"><b>Wiki</b></a>
</p>

**Longhaul** keeps the thread in long Claude Code sessions: Claude saves where it is, Longhaul compacts, and the work picks up after a compaction, a resume or `/clear`. It shows where a conversation's tokens went, and flags the tool calls that put a big file into the context whole or a file the context already holds. It is a Claude Code plugin and a terminal command in one Python file, standard library only, and what it keeps stays on your machine.

This page is the guide: install it, check it, use it day to day, and set up the parts you want. The detail is in the [wiki](https://github.com/Noah-OS/longhaul/wiki).

> [!IMPORTANT]
> A plugin runs code with your privileges, and Longhaul's hooks run in every session once it is enabled. Read [what the hooks do](https://github.com/Noah-OS/longhaul/wiki/Hooks) before you install it.

## 1. Install

You need Claude Code and `python3` on your PATH: Python 3.10 or later, nothing pip-installed. Longhaul is built and checked on macOS, and its tests pass on Linux in CI.

**In Claude Code in a terminal:**

```
/plugin marketplace add Noah-OS/longhaul
/plugin install longhaul@longhaul
```

**In the VS Code panel:** type `/plugins`, add `Noah-OS/longhaul` in the **Marketplaces** tab, then install Longhaul from the **Plugins** tab with **Install for you**. Or open this link in VS Code, which takes you to the same choice:

```
vscode://anthropic.claude-code/install-plugin?plugin=longhaul&marketplace=Noah-OS/longhaul
```

The plugin loads in the next session, or after `/reload-plugins` in a running one.

## 2. Check it

In any chat:

```
/longhaul:doctor
```

It lists what is set up, one line each with ✔, ✘ or ⚠ and the next step: the plugin, the terminal command, Claude Code, updates, the status line, the VS Code panel, your accounts, the chats running now, the code maps, the hooks and their measured time, the features, and Codex if you have it. It only reads.

**The terminal command** (optional, but most of this guide uses it). Installing the plugin does not create it. `/longhaul:doctor` gives the exact line to run under *Terminal command*, such as:

```
python3 ~/.claude/plugins/cache/longhaul/longhaul/<version>/longhaul.py setup
```

It writes `~/.local/bin/longhaul`, a short launcher that runs the newest Longhaul Claude Code has installed, so it keeps working after each update. Then `longhaul doctor` works in a terminal too. If the shell says `longhaul: command not found`, see [Troubleshooting](#troubleshooting).

**The status line** (optional): `longhaul statusline install` shows the account and its 5-hour and 7-day allowance, with the reset times, under Claude Code's prompt, and keeps each reading for `/longhaul:inspect` and `longhaul usage`. It backs up your `settings.json`, asks before replacing a status line you already have, and checks the command runs; `longhaul statusline uninstall` puts the old one back.

## 3. Day to day

Once the plugin is loaded, most of it works by itself.

**What runs by itself**

| | What it does |
|---|---|
| **[Context guard](https://github.com/Noah-OS/longhaul/wiki/Context-Guard)**<br><sub>On, advice only</sub> | Tells Claude when a whole `Read` or bare `cat` would put a file over 20,000 bytes into the context, or a file it already read and that has not changed since. Advice by default (`LONGHAUL_GUARD=block` denies instead). It also tells a subagent whose Bash call could outlast its 5-minute prompt cache to run it in the background and check back. |
| **The context reminder and the compaction** | At 200,000 tokens of context Claude is asked to save a checkpoint at its next stopping point; once it has, Longhaul compacts the chat at the end of that turn (`LONGHAUL_AUTO_COMPACT=off` turns that off), and the checkpoint comes back. A subagent whose own context passes 150,000 tokens is told to finish its step and report what is done and what is left (`LONGHAUL_SUBAGENT_AT` sets the size; `off` turns it off). |
| **Auto Model**<br><sub>On, a guide</sub> | At each start Claude gets a short guide for choosing each subagent's model by how hard its job is, and every pick is logged. `longhaul models off` turns it off; `longhaul models cap sonnet` lowers a call above Sonnet. Its effect on cost and quality is not measured yet. |
| **Output filter**<br><sub>Shadow: it only logs</sub> | After each `Bash` call it measures what it could cut from a long output, and changes nothing until a week of it is measured (`LONGHAUL_FILTERS=lossless` or `on` to use it; `off` turns it off). |
| **[Code index](https://github.com/Noah-OS/longhaul/wiki/Code-Index)**<br><sub>On</sub> | Where CodeGraph or Trail-Code is installed and the repository has no map, builds one in the background, and tells Claude when an index exists (`LONGHAUL_INDEX=off` turns it off). Longhaul installs nothing. |
| **Display**<br><sub>In Claude Code and the VS Code panel</sub> | A status entry with the account, its allowance and the context size, and a pop-up when a checkpoint is saved or brought back, the reminder fires, the account changes at a limit, a turn stops at a limit or on a sign-in or billing error, or a newer Longhaul is out. |

**What you type in a chat**

| Skill | When to use it |
|---|---|
| `/longhaul:checkpoint` | Before a compaction, at the end of a step, or before you stop. Claude writes the goal, the next action, your own words, the facts it found, the decisions and why, what failed and the files it changed; Longhaul puts it back after a compaction, a resume, or a `/clear` in the same folder within 6 hours. |
| `/longhaul:inspect` | To see where a conversation's tokens went: requests and tokens by kind and model, subagents, context growth and compactions, what the cache cost, the largest tool outputs, searches, repeated reads, what the guard caught, and each hook's measured time. Every number says where it came from. |
| `/longhaul:doctor` | To see what is set up and what to do next. |
| `/longhaul:manage` | To set Longhaul up without a terminal: see [From the VS Code chat](#5-from-the-vs-code-chat). |

**Keeping a long session on track:** let the reminder ask for the checkpoint, or type `/longhaul:checkpoint` yourself, then `/compact` or `/clear`; the work picks up from the checkpoint. For Claude Code's own compactions, `/longhaul:doctor` prints a "# Compact instructions" block to paste into your CLAUDE.md; Longhaul never writes it.

## 4. More than one account

Longhaul can run your chats across several of your own Claude subscriptions. Each account is its own claude.ai sign-in, in its own folder. This is advanced and experimental; it is off until you add a second account.

**Add an account** (in a terminal). It opens the browser sign-in at once; sign in with that same email:

```
longhaul add work you@example.com
```

`longhaul status` lists the accounts and whether each is signed in. `longhaul setup work --relogin` signs one in again; `longhaul remove work` takes it off the list (its folder and sign-in stay).

**Use them in the VS Code panel:** `longhaul vscode on`, then reload VS Code (Cmd+Shift+P, then *Developer: Reload Window*). `longhaul vscode off` puts the panel back on its own sign-in.

**At a usage limit**, the switching mode decides (`longhaul switch` shows it; `longhaul switch ask` sets it):

| Mode | At a limit |
|---|---|
| `ask` (the default) | In a terminal `longhaul`, it asks before moving the chat to another account; in the VS Code panel the chat stays where it is. It never moves a chat early. |
| `auto` | Moves the chat to an account with room, and early, while the chat is idle, when one is nearly full. |
| `never` | Waits for the reset. |

**Keep a folder for one account:** `longhaul add work you@example.com --repo ~/work` uses that account only in `~/work` and the repositories inside it, and keeps the folder for it: a chat there on another account is refused before anything is sent. When the account kept for a folder is at its limit, `longhaul` in a terminal asks whether to run the chat on another one; `longhaul allow <chat id>` lets one refused chat's prompts through on its account for 30 days.

**From a terminal**, `longhaul` starts a chat (`-c` continues the latest, `-r <id>` resumes one) that it watches and moves at a limit. A chat moved to another account takes that account's connectors and artifacts, not the first one's. [Account switching](https://github.com/Noah-OS/longhaul/wiki/Account-Switching) has the rest.

## 5. From the VS Code chat

In a chat, ask in your own words, for example "add my work account", "turn on the panel" or "set switching to ask". `/longhaul:manage` shows the exact command and what it changes, and runs it only after your yes in the chat. From a chat it can:

- add, sign in again or remove an account (the sign-in opens in your browser; never type a password or code into the chat);
- set the switching mode and the subagent models;
- turn the VS Code panel's use of your accounts on or off;
- install or remove the status line, and turn Claude Code's auto-update for Longhaul on or off;
- forget the recorded limits, allow a refused chat (`longhaul allow`), and uninstall (with `--purge` only after it says that this deletes `~/.longhaul`).

What stays in a terminal: starting a chat with `longhaul`, the trials, and the Advisor's probe record.

## 6. Codex

Longhaul works in OpenAI's Codex too. In a terminal:

```
codex plugin marketplace add Noah-OS/longhaul
codex plugin add longhaul@longhaul
```

Then start `codex`, open `/hooks` and trust Longhaul's hooks (newer Codex versions ask again after each update; `longhaul doctor` says how many are trusted). In Codex you get the checkpoint (`$longhaul:checkpoint`, put back after a compaction, a resume or a clear), the context reminder, the guard, the output filter in shadow, and a log of each sub-agent's model. `longhaul inspect --agent codex`, `longhaul usage` and `longhaul doctor` read Codex's chats and settings from the terminal.

Codex does not get the status line, the display's pop-ups, Longhaul's own compaction (Codex compacts by itself, at 90% of the model's window), the Advisor, or lowering a sub-agent's model. A checkpoint save writes `~/.longhaul`, outside the project, so Codex's sandbox asks once per save; `longhaul doctor` gives the line to paste into Codex's `config.toml` to let it through (Longhaul never writes it). Codex compresses a chat after a week: where Python's standard library has no `compression.zstd`, those are counted, not read.

## 7. Settings

The switches are environment variables, read at each hook and command; `longhaul status` names any set to a value it can't use (that one keeps its default).

| Setting | Default | What it changes |
|---|---|---|
| `LONGHAUL_GUARD` | `warn` | `block` denies the reads it flags; `off` does nothing |
| `LONGHAUL_CHECKPOINT_AT` | `200000` | tokens of context at which Claude is asked to save; `off` |
| `LONGHAUL_AUTO_COMPACT` | on | `off`: no compaction after a save |
| `LONGHAUL_SUBAGENT_AT` | `150000` | a subagent's own context at which it is told to report back; `off` |
| `LONGHAUL_FILTERS` | `shadow` | `lossless`, `on` or `off` |
| `LONGHAUL_INDEX` | on | `off`: no code index built |
| `LONGHAUL_SWITCH` | `ask` | `auto` or `never`; overrides `longhaul switch` |
| `LONGHAUL_UPDATE_CHECK` | on | `off`: no daily release check |

`longhaul models [guide|on|off|cap MODEL]` sets Auto Model. The full list of commands: `longhaul -h`, or [Commands](https://github.com/Noah-OS/longhaul/wiki/Commands).

## 8. Update and remove

Longhaul asks GitHub once a day, in the background, whether a newer release is out, and says so when a session starts; the request sends nothing but Longhaul's version number, and `LONGHAUL_UPDATE_CHECK=off` turns it off. To update: `claude plugin marketplace update longhaul`, then `claude plugin update longhaul@longhaul` (and `longhaul-advisor@longhaul` where it is installed), then `/reload-plugins` in a running chat. Claude Code's own auto-update is off by default for a marketplace like Longhaul's: `longhaul update on` turns it on, after asking (or `/plugin` > Marketplaces > Enable auto-update), and `longhaul update` shows the versions.

To remove it: `longhaul uninstall --dry-run` lists what Longhaul put outside the plugin (its status line, the VS Code panel's line, the auto-update entry, the terminal command), and `longhaul uninstall` removes them after a yes, putting back what was there before; `--purge` also removes `~/.longhaul`. Then `claude plugin uninstall longhaul@longhaul`.

## Troubleshooting

- **`longhaul: command not found`.** The command is not set up yet: run the line `/longhaul:doctor` gives under *Terminal command* (step 2). If it is still not found, `~/.local/bin` is not on your PATH: add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc` (or `~/.bashrc`) and open a new terminal.
- **A chat says a folder "is kept for" another account.** Open it again on that account (`longhaul -r <id>`), or allow it on its own: `longhaul allow <id>` in a terminal, or ask Claude in another chat to run it.
- **The VS Code panel ignores your accounts.** Run `longhaul vscode on`, then reload VS Code.
- **A chat open twice**, in a terminal and in the panel, writes the same conversation from two places: close one. Longhaul says so when it notices.
- **Anything else:** `/longhaul:doctor`, then the [issue tracker](https://github.com/Noah-OS/longhaul/issues).

## Know before you use it

- **The hooks run in every session** of every project once the plugin is enabled, and add 87–94 ms to every `Read` and `Bash` call (measured warm on one Mac); `longhaul doctor` and `/longhaul:inspect` show each hook's measured time. They let the call run whatever goes wrong, except the Advisor's check of a consultation. [Hooks](https://github.com/Noah-OS/longhaul/wiki/Hooks)
- **It adds about 509 tokens to every session's context**, its four skills' descriptions (estimated with `claude plugin details`). The Advisor's own plugin, `longhaul-advisor`, adds about 291 more where it is installed.
- **A checkpoint is not a backup.** It is the model's own summary, at most 16,000 bytes, of which about 9,000 characters come back, most needed first. [Checkpoints](https://github.com/Noah-OS/longhaul/wiki/Checkpoints)
- **Local only.** Longhaul keeps its records under `~/.longhaul`: numbers, ids and file paths, never message text or tool output, except the checkpoint, which is the model's own summary on purpose, and, with `LONGHAUL_FILTERS=on`, the whole output of a command it shortened, kept for 7 days so Claude can read the rest. Of Codex it reads only the chats under `sessions/` (numbers, names and the paths of files read, never what was said) and the few settings `longhaul doctor` names in `config.toml`, never `auth.json`. It has no telemetry. [Privacy and security](https://github.com/Noah-OS/longhaul/wiki/Privacy-and-Security)
- **[Advisor](https://github.com/Noah-OS/longhaul/wiki/Advisor-Experiment)**, an experiment in its own plugin (`longhaul-advisor`), is off: a stronger model would review the work at four decision points.
- Everything not covered or not yet checked: [Limitations](https://github.com/Noah-OS/longhaul/wiki/Limitations).

## Measured

> [!NOTE]
> **So far, no saving shown.** On a task where the guard had nothing to catch, block mode saved nothing; on a task where it denied reads the task needed, it cost 3.5 times the baseline's prompt tokens and four times its requests. `warn` stays the default until a week of real use is counted. The method and every number: [Measurements](https://github.com/Noah-OS/longhaul/wiki/Measurements).

Longhaul was built from patterns in real use: in one person's 964 transcripts over five weeks, 515 whole-file reads over 20,000 characters, 17.4 million characters in all. Every number it reports names where it came from and how sure it is, and no saving is claimed that has not been measured.

## Documentation

The [wiki](https://github.com/Noah-OS/longhaul/wiki) holds the detail:

- **Using it:** [Installation](https://github.com/Noah-OS/longhaul/wiki/Installation) · [Usage report](https://github.com/Noah-OS/longhaul/wiki/Usage-Report) · [Context guard](https://github.com/Noah-OS/longhaul/wiki/Context-Guard) · [Checkpoints](https://github.com/Noah-OS/longhaul/wiki/Checkpoints) · [Commands](https://github.com/Noah-OS/longhaul/wiki/Commands)
- **How it works:** [Architecture](https://github.com/Noah-OS/longhaul/wiki/Architecture) · [Hooks](https://github.com/Noah-OS/longhaul/wiki/Hooks) · [Code index](https://github.com/Noah-OS/longhaul/wiki/Code-Index) · [Advisor experiment](https://github.com/Noah-OS/longhaul/wiki/Advisor-Experiment) · [Account switching](https://github.com/Noah-OS/longhaul/wiki/Account-Switching)
- **What to trust:** [Measurements](https://github.com/Noah-OS/longhaul/wiki/Measurements) · [Privacy and security](https://github.com/Noah-OS/longhaul/wiki/Privacy-and-Security) · [Limitations](https://github.com/Noah-OS/longhaul/wiki/Limitations) · [Roadmap](https://github.com/Noah-OS/longhaul/wiki/Roadmap)

## Releases

Every release's notes: [Releases](https://github.com/Noah-OS/longhaul/releases) · [changelog](CHANGELOG.md). What is planned next: [Roadmap](https://github.com/Noah-OS/longhaul/wiki/Roadmap#releases).

## Development

The tests ship with the code. From a checkout:

```
S=$(mktemp -d); LONGHAUL_HOME=$S/home LONGHAUL_DEFAULT_CONFIG_DIR=$S/claude LONGHAUL_DEFAULT_GLOBAL_JSON=$S/claude.json LONGHAUL_NO_NOTIFY=1 python3 -m unittest test_longhaul -q
```

The scratch paths keep the tests away from your real `~/.claude` and `~/.longhaul`, and the suite runs on two fakes of Claude Code, so no `claude` is needed. [CI](.github/workflows/tests.yml) runs that command on every push and pull request, on macOS and Linux, on Python 3.10 and the latest 3.x, and checks both plugins with `claude plugin validate --strict` (`.` and `plugins/longhaul-advisor`). To work on Longhaul itself, a `git clone` and `python3 longhaul.py setup` link the terminal command to your checkout.

## Licence

MIT. See [LICENSE](LICENSE).
