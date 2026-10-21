#!/usr/bin/env python3
"""longhaul - see where a Claude Code session's tokens go, and stop the waste:
`inspect` reports a conversation's usage, `guard` warns about or blocks the
tool calls that waste the most context, `checkpoint` puts the model's own
state back after a compaction, `statusline` shows the allowance. Carrying
one conversation across your own subscriptions is the advanced feature
below, off unless you turn it on.

Why this exists: with two Claude subscriptions (say Max and Pro), when
one hit its session or weekly limit the work stopped until the reset, often
while you were away, following it from the phone over Remote Control.
longhaul runs `claude` in this terminal with Remote Control on and watches it:

- a limit stop: longhaul closes Claude, asks in this terminal whether to move
  the conversation to the other account, and on a yes reopens it there
  with a "carry on" message. With no yes it waits for the reset and
  carries on where it was. `--switch auto` moves without asking (and
  waits for the first reset when every account is at its limit);
  `--switch never` always waits. Before Sprint 1 longhaul always moved without
  asking: that is now `--switch auto` (or LONGHAUL_SWITCH=auto). `longhaul pipe` and
  the VS Code panel cannot ask, so there "ask" acts as "never": they move
  only in auto (an unattended move is never made without an explicit yes);
- Remote Control lost while Claude sits idle: longhaul reopens the conversation,
  which connects it again (Claude Code reconnects short drops itself; this
  is for the ones it gives up on, after a VPN change or a long outage). A
  link lost again and again is left to Claude after a few reopens;
- Claude closed normally (/exit): longhaul stops too. Claude crashed: longhaul reopens
  the conversation after a countdown that grows with each crash, and stops
  after a few.

How it sees what Claude is doing. Claude Code documents no API for any of
it, so longhaul reads the files Claude Code itself writes (checked on 2.1.295 to
2.1.303; if a later version moves them, longhaul stops switching but Claude runs
as normal):

- <config>/sessions/<pid>.json: the running process's sessionId, its
  status (idle, busy, waiting) and bridgeSessionId, set while Remote
  Control is connected;
- <config>/projects/*/<sessionId>.jsonl: the conversation. A limit stop is
  an assistant line with isApiErrorMessage true, error "rate_limit" and
  quotaLimits {status: rejected, resetsAt: epoch seconds, rateLimitType}.

Each account is a config directory with its own claude.ai sign-in. Remote
Control refuses the long-lived `claude setup-token` token, so each account
needs a full sign-in, and Claude Code keys the macOS Keychain entry to the
config directory. Each account has its own folder (~/.claude-max,
~/.claude-pro) holding only its sign-in and .claude.json, and links
everything else (history, memory, settings, CLAUDE.md) into ~/.claude so
every account sees one conversation. None uses ~/.claude itself: the VS
Code panel signs in there, and on 2026-10-15 a sign-in in VS Code moved
longhaul's first account to the second one. `longhaul setup` builds the folders.

Usage:
  longhaul                      new conversation (Remote Control on)
  longhaul -c | longhaul -r [ID]      continue the latest one | resume one (given
                          its ID, it starts where it last ran in ask and never)
  longhaul -r 557ce21f          a short ID works when only one conversation matches
  longhaul --name api           the session's name on the phone (default: the folder)
  longhaul --account Pro        start on that account
  longhaul --no-rc              without Remote Control
  longhaul --first FILE         the file's text is the first message, sent on the
                          first launch only (never after a move or a reopen)
  longhaul --switch MODE        ask (the default): ask here before moving the
                          conversation to another account; auto: move at a
                          limit, and early when one is nearly full; never:
                          wait for the reset (or set LONGHAUL_SWITCH)
  longhaul -- --model opus      anything after -- goes to claude on every launch
  longhaul add NAME EMAIL       a new account: its own folder, one browser sign-in
  longhaul remove NAME          off the list (its folder and sign-in are kept)
  longhaul setup [NAME]         sign each account in to its own folder (a browser
                          sign-in); only the emails in accounts.json count
  longhaul status               accounts, sign-ins, limits, links
  longhaul usage [--days N]     each account's allowance; work per task and account
  longhaul inspect [ID|PATH] [--json]
                          where one conversation's usage went (numbers only;
                          default: the latest conversation in the ledger);
                          --json gives one record per number, with its source
  longhaul statusline           the status line command (set in ~/.claude/settings.json)
  longhaul guard                the plugin's PreToolUse hook: warns about a file read
                          again unchanged, or a whole read or cat of a big one;
                          LONGHAUL_GUARD=block denies instead, =off does nothing
  longhaul checkpoint save [ID] [--file PATH]
                          the model's own state for conversation ID (default: the
                          one this runs in), JSON on stdin or in the file, kept
                          private under LONGHAUL_HOME/checkpoints; 8,000 bytes in
                          all. The /longhaul:checkpoint skill writes the file
                          there as <ID>.state.json, removed once read, saved or
                          refused
  longhaul checkpoint           the plugin's SessionStart hook: puts that state back
                          after a compaction or a resume
  longhaul clear                forget the recorded limits
  longhaul pipe [--name L] [--account A] [--swap] [--switch MODE] [--no-git-sign-in] -- ARGS
                          another program drives claude over stream-json. At
                          a limit the session moves only with --swap and
                          --switch auto (or LONGHAUL_SWITCH=auto); otherwise it
                          stays on its account. longhaul's own lines in the
                          stream: moved, all_limited, stays (see pipe()).
                          --sw is ambiguous now: write --swap in full
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import fcntl
import heapq
import json
import math
import os
import re
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import uuid
from pathlib import Path

HOME = Path.home()
LONGHAUL_HOME = Path(os.environ.get("LONGHAUL_HOME", HOME / ".longhaul"))
# Where the default account's files are; tests point it at a temp folder.
DEFAULT_CONFIG = Path(os.environ.get("LONGHAUL_DEFAULT_CONFIG_DIR", HOME / ".claude"))
DEFAULT_GLOBAL_JSON = Path(os.environ.get("LONGHAUL_DEFAULT_GLOBAL_JSON", HOME / ".claude.json"))
CLAUDE = os.environ.get("LONGHAUL_CLAUDE_BIN", "claude")
# The settings a setting() call left at its default: it runs at import, before
# log() exists, so they are named later, by settings_note(): `longhaul status`
# prints it, run and pipe log it at their start. The hooks print nothing.
IGNORED_SETTINGS = []


def setting(name: str, default, kind, minimum=0):
    """`name` (LONGHAUL_...) as an int or a float, or its default when it is
    unset, not a finite number, or under minimum. Read at import, so a typo
    must not raise: it would stop every command, the hooks included. Before,
    int() or float() of seventeen of them did."""
    raw = os.environ.get(name)
    if not raw:
        return kind(default)
    try:
        value = kind(raw)
        if math.isfinite(value) and value >= minimum:
            return value
    except (ValueError, OverflowError):
        pass
    IGNORED_SETTINGS.append(name)
    return kind(default)


def settings_note() -> str | None:
    """The settings left at their default, by name (never by value), or
    None when every one was good."""
    if not IGNORED_SETTINGS:
        return None
    return f"Ignored (not a number, or under its minimum; the default is in force): {', '.join(IGNORED_SETTINGS)}"


POLL = setting("LONGHAUL_POLL_SECONDS", 2, float, 0.01)  # under 10 ms the loop would spin
# Claude Code retries short Remote Control drops itself; longhaul steps in only
# after the link has been gone this long and Claude is idle.
RC_GRACE = setting("LONGHAUL_RC_GRACE_SECONDS", 90, float)
REOPEN_DELAY = setting("LONGHAUL_REOPEN_DELAY", 10, int)
# After a crash longhaul reopens at most this many times within the window, each
# wait three times the last (reopen_delay), then stops and says so. Before,
# a conversation that crashed on every start was reopened every 12 s for as
# long as the Mac stayed awake.
MAX_REOPENS = setting("LONGHAUL_MAX_REOPENS", 5, int)
REOPEN_WINDOW = setting("LONGHAUL_REOPEN_WINDOW", 1800, float)
# A Remote Control link that keeps dropping is reopened at most this many
# times within REOPEN_WINDOW; then longhaul leaves Claude running without it and
# says so. Before, a flaky link was reopened all night.
MAX_RECONNECTS = setting("LONGHAUL_MAX_RECONNECTS", 5, int)
# How long a limit stop may go unacted on while the status file still says
# busy: it can lag, and Claude Code 2.1.234+ may sit waiting for the reset
# itself (autoContinueAtUsageLimit), so longhaul moves on after this.
LIMIT_BUSY_GRACE = setting("LONGHAUL_LIMIT_BUSY_GRACE", 30, float)
# The Claude Code versions whose internal files longhaul has been seen to read
# correctly: 2.1.295 and 2.1.300 by hand, 2.1.301 to 2.1.303 from limit
# switches in real use. Outside this range longhaul still runs Claude, and says
# that switching may not work (the files it reads are undocumented).
TESTED_VERSIONS = ((2, 1, 295), (2, 1, 303))
# Tests end the loop after this many launches; 0 runs until you stop it.
MAX_RUNS = setting("LONGHAUL_MAX_RUNS", 0, int)
# A limit's reset time plus this margin before longhaul tries that account again.
RESET_MARGIN = setting("LONGHAUL_RESET_MARGIN", 60, float)
# Past this size longhaul.log and ledger.jsonl move to <name>.1, replacing the one
# there, and a new one starts. The ledger grew 355 KB in five days and
# nothing ever trimmed it.
LOG_MAX = setting("LONGHAUL_LOG_MAX_BYTES", 5 * 2**20, int)
LEDGER_MAX = setting("LONGHAUL_LEDGER_MAX_BYTES", 10 * 2**20, int)

# Linked from the second account's folder into ~/.claude, so both accounts
# share one history, memory and setup. Not the sign-in, the daemon, caches.
SHARED = [
    "projects", "sessions", "file-history", "history.jsonl", "plans", "plugins",
    "skills", "agents", "commands", "settings.json", "keybindings.json",
    "CLAUDE.md", "ide", "todos",
]
# Copied once from ~/.claude.json at setup (MCP servers, project trust,
# onboarding done). Never oauthAccount or userID: those are the sign-in.
COPIED_KEYS = [
    "mcpServers", "projects", "hasCompletedOnboarding", "lastOnboardingVersion",
    "installMethod", "autoUpdates", "theme",
]
# Set when longhaul is started from a terminal a Claude session opened; they
# describe that session, and a child claude would take itself for it.
SESSION_ENV = [
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_EXECPATH", "CLAUDE_AGENT_SDK_VERSION",
]
# These outrank the claude.ai sign-in, so with any of them set every
# account would bill the same key and Remote Control would refuse it.
AUTH_ENV = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]
# `longhaul pipe --no-git-sign-in`: a session another program drives holds no
# GitHub or git sign-in, so it cannot push anywhere. env_for strips only the
# Anthropic and Claude variables, so a GITHUB_TOKEN or an SSH agent in your
# shell would reach the session, and GIT_CONFIG_COUNT/KEY/VALUE or
# GIT_CONFIG_PARAMETERS could hand its git an http.extraheader with a token.
# GIT_CONFIG, GIT_CONFIG_SYSTEM, GIT_DIR, GIT_WORK_TREE and GIT_COMMON_DIR
# point git at another configuration, or at another repository than the
# session's own clone. Any name holding GITHUB goes too, not only those
# starting with it: HOMEBREW_GITHUB_API_TOKEN once reached claude that way.
# no_git_env strips these whatever it is given and says what it sets in
# their place.
GIT_SIGN_IN_ENV = ["SSH_AUTH_SOCK", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND",
                   "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG", "GIT_CONFIG_SYSTEM",
                   "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"]
GIT_SIGN_IN_PREFIXES = ("GH_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")

CARRY_ON = (
    "The {old} account hit its {kind} limit, so longhaul reopened this conversation "
    "on the {new} account. Carry on from where you stopped. Background agents "
    "started before the switch have stopped; resume any that were still working."
)
CARRY_ON_SAME = (
    "The {old} account hit its {kind} limit, so longhaul waited for it to reset and "
    "reopened this conversation on it. Carry on from where you stopped. Background "
    "agents started before the stop have stopped; resume any that were still working."
)
CARRY_ON_AFTER_WAIT = (
    "The {old} account hit its {kind} limit while every account was at its limit, "
    "so longhaul waited for the {new} account to reset and reopened this conversation. "
    "Carry on from where you stopped. Background agents started before the stop "
    "have stopped; resume any that were still working."
)
# A swap starts the new claude with the driver's own initialize, and will
# not swap without one; a driver that sends plain user lines sends none. So
# longhaul pipe makes this one, the panel's minimal shape. start_claude gives it a
# request id of longhaul's own and keeps its answer from the driver, as it does
# the panel's.
PIPE_INITIALIZE = {"type": "control_request", "request_id": "", "request": {"subtype": "initialize"}}
KIND = {"five_hour": "session", "seven_day": "weekly"}


# ---------------------------------------------------------------- files

def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def dict_at(entry, key) -> dict:
    """entry[key] when both are objects, else {}: files Claude Code or a
    person wrote can hold null, a list or junk where an object belongs."""
    found = entry.get(key) if isinstance(entry, dict) else None
    return found if isinstance(found, dict) else {}


def files_of(paths) -> list[Path]:
    """The hits that are files. Path.glob() lists a link to nowhere on
    Python 3.12 and later and passes it over before (with a wildcard last
    part, on every version); is_file() follows the link, so a dead one is
    left out everywhere and a transcript reached through a live one kept."""
    return [p for p in paths if p.is_file()]


def write_json(path: Path, data) -> None:
    """Write whole or not at all. The temporary file is unique per write:
    on 2026-10-16 two sessions' status lines saved Max.json at the same
    moment through one shared name, and one found it already moved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append(path: Path, text: str, cap: int):
    """Add `text` at the end of `path`, a file only you can read: the log and
    the ledger name your accounts, sessions and spending, and anyone on the
    Mac could read them. Past `cap` bytes the file becomes <name>.1 first;
    the move is made under a lock and checked again inside it, so two
    writers never move it twice. Returns why it could not be moved, if it
    could not: the text is written all the same, to the growing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    problem = None
    try:
        if cap and path.stat().st_size >= cap:
            with locked(f"{path.name}-move"):
                if path.stat().st_size >= cap:
                    moved = path.with_name(path.name + ".1")
                    os.replace(path, moved)
                    os.chmod(moved, 0o600)  # it may have been made before longhaul kept it private
    except FileNotFoundError:
        pass  # not made yet, or another writer has just moved it
    except OSError as e:
        problem = e
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        if os.fstat(fd).st_mode & 0o077:
            # Made before longhaul kept it private. A disk that refuses must not
            # cost the line: the old code wrote it.
            with contextlib.suppress(OSError):
                os.fchmod(fd, 0o600)
        f.write(text)
    return problem


def log(msg: str) -> None:
    """Never raises: it runs in the panel wrapper's fail-safe path, where a
    log that cannot be written must not stop the panel getting its Claude."""
    try:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # A log that cannot be moved aside grows on; there is nowhere to say so.
        append(LONGHAUL_HOME / "longhaul.log", f"{stamp} {msg}\n", LOG_MAX)
    except OSError:
        pass


@contextlib.contextmanager
def locked(name: str):
    """One writer at a time across processes: terminal longhaul windows, panel
    relays and panel launches all update the same few files."""
    LONGHAUL_HOME.mkdir(parents=True, exist_ok=True)
    with open(LONGHAUL_HOME / f".{name}.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# Set when longhaul runs as the VS Code extension's process wrapper: its stdout
# is then the extension's conversation with Claude, and one stray line of
# text there would break the panel. Everything said goes to the log only.
QUIET = False


def show(msg: str) -> None:
    """In this terminal only; say() also logs it."""
    if not QUIET:
        print(f"\033[36mlonghaul\033[0m {msg}", flush=True)


def say(msg: str) -> None:
    show(msg)
    log(msg)


def notify(msg: str) -> None:
    """A Mac notification, for when you are not looking at this terminal.
    Never raises or hangs: it runs inside the watch loop too, where a hung
    osascript would stop limit switching with it."""
    if os.environ.get("LONGHAUL_NO_NOTIFY") or not shutil.which("osascript"):
        return
    text = msg.replace("\\", "").replace('"', "'")
    try:
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "Claude"'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"notification not shown: {e!r}")


def clock(epoch: float) -> str:
    when = dt.datetime.fromtimestamp(epoch)
    fmt = "%H:%M" if when.date() == dt.date.today() else "%a %H:%M"
    return when.strftime(fmt)


def ago_text(seconds: float) -> str:
    """How long ago, in the largest unit that fits: 5 s, 3 min, 2 h, 1
    day, 2 days. A clock set back is 0 s ago."""
    ago = max(0, int(seconds))
    if ago < 60:
        return f"{ago} s ago"
    if ago < 3600:
        return f"{ago // 60} min ago"
    if ago < 86400:
        return f"{ago // 3600} h ago"
    return f"{ago // 86400} day{'s' if ago >= 2 * 86400 else ''} ago"


# ------------------------------------------------------------- accounts

def load_accounts() -> list[dict]:
    accounts = read_json(LONGHAUL_HOME / "accounts.json")
    return accounts or [{"name": "Max", "config_dir": None}]


def read_accounts_file():
    """accounts.json for the commands that change it: (the accounts, None);
    ([], None) when there is no file, which is no accounts, as an empty
    list is; or (None, why) when there is a file that is not a list of
    accounts with names (not JSON, not UTF-8, a half-written file, a lone
    object). read_json() gives None for both, so setup took a broken file
    for "no accounts", said Ready and exited 0, and add would have written
    a one-account list over it (review, Sprint 5)."""
    try:
        found = json.loads((LONGHAUL_HOME / "accounts.json").read_text())
    except FileNotFoundError:
        return [], None
    except OSError as e:
        return None, e.strerror or repr(e)
    except UnicodeDecodeError:
        return None, "not UTF-8 text"
    except json.JSONDecodeError as e:
        return None, f"not JSON ({e.msg} at line {e.lineno} column {e.colno})"
    if not (isinstance(found, list)
            and all(isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"] for a in found)):
        return None, "not a list of accounts, each with a name"
    return found, None


def config_of(acct: dict) -> Path:
    d = acct.get("config_dir")
    return Path(os.path.expanduser(d)) if d else DEFAULT_CONFIG


def env_for(acct: dict) -> dict:
    env = os.environ.copy()
    for k in SESSION_ENV + AUTH_ENV:
        env.pop(k, None)
    if acct.get("config_dir"):
        env["CLAUDE_CONFIG_DIR"] = str(config_of(acct))
    else:
        # Setting CLAUDE_CONFIG_DIR even to ~/.claude moves .claude.json and
        # the Keychain entry, which would sign the default account out.
        env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def no_git_env(acct: dict) -> dict:
    """`longhaul pipe --no-git-sign-in`'s environment: run's (no API key, none of
    an outer Claude session's variables) with no GitHub or git sign-in in it
    (GIT_SIGN_IN_ENV says why). It also makes git safe by itself, not only
    through what the driver passes:
    - GIT_CONFIG_NOSYSTEM=1 always. Both gits on a Mac read a system
      file naming credential.helper=osxkeychain, and Apple's /usr/bin/git
      reads Xcode's file even when GIT_CONFIG_SYSTEM names another, so only
      this drops it;
    - GIT_CONFIG_GLOBAL is the driver's file, or an empty one when it named
      none, so your ~/.gitconfig and its helper are never read;
    - GIT_TERMINAL_PROMPT=0: git never waits on a password prompt that no
      one would see."""
    env = env_for(acct)
    for k in list(env):
        if k in GIT_SIGN_IN_ENV or k.startswith(GIT_SIGN_IN_PREFIXES) or "GITHUB" in k.upper():
            del env[k]
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def update_state(change) -> dict:
    """load, change, write state.json under its lock: without it a relay
    and a terminal longhaul recording limits at once could each wipe the other's."""
    with locked("state"):
        state = load_state()
        change(state)
        write_json(LONGHAUL_HOME / "state.json", state)
    return state


def load_state() -> dict:
    """Never raises: run() reads it outside any try, so a state.json a crash
    or a hand left as a list, a string or junk is an empty state."""
    state = read_json(LONGHAUL_HOME / "state.json")
    state = state if isinstance(state, dict) else {}
    state["limited_until"] = dict_at(state, "limited_until")
    return state


# Above this share of a 5-hour or weekly allowance an account is "nearly
# full": picked only when no other has room, and a session on it moves
# while idle.
NEAR_FULL = setting("LONGHAUL_NEAR_FULL", 95, float)
IDLE_MOVE = setting("LONGHAUL_IDLE_MOVE_SECONDS", 120, float)


def windows(reading, now: float):
    """(used %, resets_at) for each allowance window still open."""
    for key in ("five_hour", "seven_day"):
        part = (reading or {}).get(key) or {}
        resets, used = part.get("resets_at"), part.get("used_percentage")
        if resets and resets > now and used is not None:
            yield used, resets


def near_full(reading, now: float) -> bool:
    return any(used >= NEAR_FULL for used, _ in windows(reading, now))


def expires(reading, now: float) -> float:
    """When the soonest unused allowance on this account is lost.

    Spend what expires first, not a guess at what a task will cost: an
    open 5-hour window or a weekly
    allowance that resets with room left is lost; an account with no open
    window loses nothing by waiting, since its clock starts at first use."""
    return min((resets for used, resets in windows(reading, now) if used < 100), default=float("inf"))


def readings(accounts: list[dict]) -> dict:
    return {a["name"]: read_json(USAGE_DIR / f"{a['name']}.json") for a in accounts}


def pick(accounts: list[dict], state: dict, start: int, now: float | None = None, skip=(),
         seen: dict | None = None):
    """The account to launch on.

    Among accounts not at a limit, those not nearly full come first, and
    among them the one whose unused allowance expires soonest; ties (and no
    readings at all) go in order from `start`. Returns (index, None), or
    (index, until) when every account is at a limit: the one that resets
    first, and when. Accounts in `skip` (signed in to the wrong email) are
    never picked; (None, None) when none is left.
    """
    now = time.time() if now is None else now
    seen = seen or {}
    limited = state.get("limited_until", {})
    n = len(accounts)
    usable = [i for i in range(n) if accounts[i]["name"] not in skip]
    if not usable:
        return None, None
    free = [i for i in usable if limited.get(accounts[i]["name"], 0) <= now]
    if free:
        roomy = [i for i in free if not near_full(seen.get(accounts[i]["name"]), now)]
        i = min(roomy or free, key=lambda j: (expires(seen.get(accounts[j]["name"]), now), (j - start) % n))
        return i, None
    i = min(usable, key=lambda j: limited.get(accounts[j]["name"], 0))
    return i, limited[accounts[i]["name"]]


def last_account(sid: str | None, accounts: list[dict]) -> str | None:
    """The account a conversation last ran on, or None. It is the "account"
    in the session's record under USAGE_DIR/sessions (the status line and
    the panel relay write it through record_usage(), the supervisor at each
    launch end and the panel wrapper for a chat reopened by id through
    note_session_account()), when that names an account in accounts.json.
    None for no record, junk, "main" (a session outside longhaul: what
    account_for_config calls ~/.claude), an account since removed, or an
    id that is not a plain one (GUARD_ID): a "/" or ".." in a --resume
    argument must not become a path (review, Sprint 5). In ask and never a
    conversation resumed by id starts there (decision 6 of the owner's
    record on switching without a terminal, not shipped)."""
    if not isinstance(sid, str) or not re.fullmatch(GUARD_ID, sid):
        return None
    found = read_json(USAGE_DIR / "sessions" / f"{sid}.json")
    name = found.get("account") if isinstance(found, dict) else None
    return name if name and any(a.get("name") == name for a in accounts) else None


def note_session_account(sid: str, name: str) -> None:
    """The supervisor's own note of where a conversation ran, at each launch
    end: a terminal session whose user set no status line has no record
    otherwise. Merged into the session's record, never written over it,
    and never a reason to stop: a note that cannot be kept is logged, as
    is an id that is not a plain one (GUARD_ID), which is never a path."""
    if not isinstance(sid, str) or not re.fullmatch(GUARD_ID, sid):
        log(f"could not note where {shown(str(sid), 60)} ran on {name}: not a plain session id")
        return
    path = USAGE_DIR / "sessions" / f"{sid}.json"
    found = read_json(path)
    row = found if isinstance(found, dict) else {}
    if row.get("account") == name:
        return
    try:
        write_json(path, {**row, "account": name})
    except OSError as e:
        log(f"could not note that {sid[:8]} ran on {name}: {e!r}")


def resumed_index(resumed: str | None, accounts: list[dict], mode: str, who: str, binary: str,
                  unmanaged=()) -> int | None:
    """The index of the account a session resumed by id last ran on, for
    the panel wrapper and longhaul pipe (`who` names which in the log). In
    ask and never a session starts where it last ran, limited or not: there
    it stays at a limit anyway (decision 6 of the owner's record on
    switching without a terminal, not shipped). None when the caller picks
    instead: in auto, for a new session, with no record, or when the record
    names an account not signed in as expected, or one in `unmanaged`
    (pipe: no email, or ~/.claude). Each None but auto's and a new
    session's has its line in the log: a session is never moved from where
    it ran without a word (review, Sprint 5)."""
    if not resumed or mode == "auto":
        return None
    last = last_account(resumed, accounts)
    if not last:
        log(f"{who}: no record of where {resumed[:8]} last ran; picking an account")
        return None
    idx = [a["name"] for a in accounts].index(last)
    if last in unmanaged:
        why = "not managed"
    elif not signed_in_cached(accounts[idx], binary):
        why = "not signed in as expected"
    else:
        log(f"{who}: {resumed[:8]} last ran on {last}; with switching set to {mode} it starts there")
        return idx
    log(f"{who}: {resumed[:8]} last ran on {last}, which is {why}; picking an account")
    return None


def signed_in_as_expected(acct: dict) -> bool:
    """Only the emails in accounts.json are allowed. A `/login` in a
    terminal can change which account a folder is signed in to, and longhaul
    must not carry the work on to an account you did not choose."""
    want = acct.get("email")
    if not want:
        return True
    status = auth_status(acct)
    if status.get("unknown"):
        log(f"{acct['name']}: could not check who is signed in ({status.get('why')}); trusting it for this launch")
        return True
    got = status.get("email")
    if (got or "").lower() == want.lower():
        return True
    msg = f"{acct['name']} should be {want} but is signed in as {got or 'nobody'}; longhaul will not use it."
    say(msg)
    notify(msg)
    return False


# ------------------------------------------------------------ watching

def parse_ts(value) -> float:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def limit_stop(line: dict, since: float):
    """The reset time and kind if this transcript line is a limit stop on the
    main conversation written after `since`, else None.

    A 429 without quotaLimits {status: rejected} is a passing overload that
    Claude Code retries itself; switching accounts for it would be wrong.
    """
    if line.get("type") != "assistant" or line.get("isSidechain"):
        return None
    if not line.get("isApiErrorMessage") or line.get("error") != "rate_limit":
        return None
    # No grace before `since`: on 2026-10-15 a 5 s grace let the first
    # account's limit line, written a second before the switch, count as
    # the second account's, which marked a free account as limited.
    if parse_ts(line.get("timestamp")) < since:
        return None
    quota = line.get("quotaLimits") or (line.get("apiErrorParams") or {}).get("rate_limit_info") or {}
    if quota.get("status") != "rejected":
        return None
    resets = quota.get("resetsAt") or (time.time() + 3600)
    kind = quota.get("rateLimitType") or ""
    # "type" is the raw rateLimitType: longhaul pipe's `moved` line carries it for
    # the driver to word itself; "kind" is the word longhaul's own messages use.
    return {"resets_at": float(resets), "kind": KIND.get(kind, kind.replace("_", " ") or "usage"),
            "uuid": line.get("uuid"), "type": kind}


def transcript_size(sid) -> dict:
    """Where the conversation ends before a launch. The new process reads on
    from there, so nothing written under the previous account (above all
    its limit stop) is taken for the new one's."""
    if not sid:
        return {}
    return {p: p.stat().st_size for p in files_of((DEFAULT_CONFIG / "projects").glob(f"*/{sid}.jsonl"))}


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def online() -> bool:
    if os.environ.get("LONGHAUL_SKIP_NET_CHECK"):
        return True
    try:
        socket.create_connection(("api.anthropic.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


class Watch:
    """What one launched claude process is doing, read from its files."""

    def __init__(self, acct: dict, pid: int, started: float, rc: bool,
                 offsets: dict | None = None, handled=(), label: str | None = None):
        self.cfg = config_of(acct)
        self.acct_name = acct["name"]
        self.label = label
        self.status_since = None
        self.pid = pid
        self.started = started
        self.rc = rc
        self.sid = None
        self.status = None
        self.limit = None
        self.limit_seen_at = None
        # Shared folders resolve to the same files under either account;
        # key the offsets by the real path so a link and its target agree.
        self.offsets: dict[Path, int] = {p.resolve(): n for p, n in (offsets or {}).items()}
        self.handled = set(handled)
        self.paths: dict[str, Path | None] = {}
        self.rc_seen = False
        self.rc_lost_at = None

    def step(self) -> None:
        s = read_json(self.cfg / "sessions" / f"{self.pid}.json")
        if s:
            sid = s.get("sessionId") or self.sid
            if sid != self.sid and sid and self.label:  # `longhaul usage` shows "docs", not an ID
                save_label(sid, self.label)
            self.sid = sid
            self.status = s.get("status")
            if s.get("statusUpdatedAt"):
                self.status_since = s["statusUpdatedAt"] / 1000
            if self.rc:
                self._remote_control(s)
        if self.sid:
            self._read_transcript()

    def _remote_control(self, s: dict) -> None:
        if s.get("bridgeSessionId"):
            self.rc_seen, self.rc_lost_at = True, None
        elif self.rc_seen and self.rc_lost_at is None:
            self.rc_lost_at = time.time()

    def _transcript(self):
        found = self.paths.get(self.sid)
        if found and found.exists():
            return found
        hits = files_of((self.cfg / "projects").glob(f"*/{self.sid}.jsonl"))
        self.paths[self.sid] = hits[0].resolve() if hits else None
        return self.paths[self.sid]

    def _read_transcript(self) -> None:
        path = self._transcript()
        if not path:
            return
        off = self.offsets.get(path, 0)
        if path.stat().st_size < off:  # rewritten: read it again
            off = 0
        with path.open("rb") as f:
            f.seek(off)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return
        self.offsets[path] = off + end + 1
        for raw in data[:end].splitlines():
            if b'"rate_limit"' not in raw:  # the first read can be many MB
                continue
            try:
                hit = limit_stop(json.loads(raw), self.started)
            except ValueError:
                continue
            if hit and hit.get("uuid") in self.handled:
                continue
            if hit and not self.limit:
                self.limit, self.limit_seen_at = hit, time.time()

    def opened_elsewhere(self) -> bool:
        """Another live claude has this conversation: you opened it
        somewhere else, and Remote Control followed it there on purpose."""
        for f in (self.cfg / "sessions").glob("*.json"):
            s = read_json(f)
            if s and s.get("sessionId") == self.sid and s.get("pid") != self.pid and pid_alive(s.get("pid")):
                return True
        return False

    def agents_busy(self, window: float = 120) -> bool:
        path = self._transcript()
        if not path:
            return False
        sub = path.with_suffix("") / "subagents"
        now = time.time()
        return any(now - p.stat().st_mtime < window for p in sub.glob("*.jsonl"))

    def should_switch(self) -> bool:
        # After a limit stop Claude goes idle; give it LIMIT_BUSY_GRACE at
        # most in case the status file lags, since nothing more can run on
        # this account.
        return bool(self.limit) and (self.status != "busy"
                                     or time.time() - self.limit_seen_at > LIMIT_BUSY_GRACE)

    def should_move(self, other_has_room) -> bool:
        """This account is nearly full and Claude is idle: move now rather
        than at the limit. Idle for IDLE_MOVE first, so a session is not
        moved while you are writing the next message from the phone;
        never while background agents work, since closing Claude stops them."""
        if self.status != "idle" or not self.status_since:
            return False
        if time.time() - self.status_since < IDLE_MOVE:
            return False
        if not near_full(read_json(USAGE_DIR / f"{self.acct_name}.json"), time.time()):
            return False
        return not self.agents_busy() and other_has_room()

    def should_reconnect(self) -> bool:
        if not (self.rc_lost_at and time.time() - self.rc_lost_at >= RC_GRACE):
            return False
        if self.status != "idle" or self.agents_busy():
            return False
        if self.opened_elsewhere():
            log(f"Remote Control left {self.sid} for another window; leaving it there")
            self.rc_seen, self.rc_lost_at = False, None
            return False
        return online()


# ------------------------------------------------------------- terminal

def restore_terminal() -> None:
    """Claude draws full screen; when it is stopped from outside, put the
    terminal back so longhaul's own lines are readable."""
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\033[?1049l\033[?25h\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?2004l\033[<u\r\n")
    sys.stdout.flush()
    subprocess.run(["stty", "sane"], stdin=sys.stdin, stderr=subprocess.DEVNULL)


def countdown(seconds: int, text: str) -> bool:
    """True to reopen: on Enter or when the time runs out. False on q or
    Ctrl+C. Other keys are ignored (an arrow key starts with ESC, which
    once stopped longhaul) and cost no time (each once used up a second of the
    wait); keys typed while Claude was dying are thrown away first; and a
    terminal that goes away, for reading or writing, still waits the time
    out. As in ask_to_move, the terminal is switched without waiting for
    its output to be shown, which hung with output paused (Sprint 2, T2)."""
    try:
        fd = sys.stdin.fileno() if sys.stdin is not None and sys.stdin.isatty() else None
    except ValueError:  # a closed stdin
        fd = None
    if fd is None:
        time.sleep(seconds)
        return True
    ends = time.time() + seconds

    def wait_out(why: str) -> bool:
        log(f"the countdown's terminal is gone ({why}); waiting the {seconds}s out without it")
        time.sleep(max(0.0, ends - time.time()))
        return True

    try:
        old = termios.tcgetattr(fd)
    except termios.error as e:
        return wait_out(repr(e))
    try:
        termios.tcflush(fd, termios.TCIFLUSH)
        tty.setcbreak(fd, termios.TCSANOW)
        shown = None
        while time.time() < ends:
            left = int(ends - time.time()) + 1
            if left != shown:
                sys.stdout.write(f"\r\033[K\033[36mlonghaul\033[0m {text} in {left}s (Enter: now, q: stop) ")
                sys.stdout.flush()
                shown = left
            ready, _, _ = select.select([fd], [], [], min(1.0, max(0.0, ends - time.time())))
            if not ready:
                continue
            try:
                key = os.read(fd, 1)
            except OSError as e:
                return wait_out(repr(e))
            if not key:
                return wait_out("end of input")
            if key in (b"q", b"Q", b"\x03"):
                print()
                return False
            if key in (b"\r", b"\n"):
                break
        print()
        return True
    except KeyboardInterrupt:
        print()
        return False
    except (termios.error, OSError) as e:  # the terminal went away under longhaul
        return wait_out(repr(e))
    finally:
        with contextlib.suppress(termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, old)


# How longhaul may move a conversation to another account (decision D3, Phase 0
# report §9; "ask" is the default, D6). `longhaul pipe` and `longhaul wrap` show no
# terminal to ask in, so there "ask" keeps the conversation where it is, as
# "never" does; a move needs "auto" and the front end's own consent (--swap,
# switch_mid_session). The owner's decision record on switching without a
# terminal says why (not shipped).
SWITCH_MODES = {
    "ask": "at a limit longhaul asks in its terminal before it moves a conversation to another account",
    "auto": "longhaul moves a conversation to another account at a limit, and early when one is nearly full",
    "never": "longhaul waits for the reset and never moves a conversation to another account",
}


def decide_move(mode: str, consent: bool, can_ask: bool) -> str:
    """The one rule for every front end (decision 1 of the owner's record on
    switching without a terminal, not shipped): a conversation moves to another account
    only when the user's mode allows it and the front end consented (the
    person at the terminal; --swap from a driver; switch_mid_session for
    the panel). "move": go. "ask": ask the person first. "stay": wait for
    the reset where it is. Written once, so run() and the relay cannot
    drift apart, as they did in Sprint 2's review."""
    if not consent or mode == "never":
        return "stay"
    if mode == "auto":
        return "move"
    return "ask" if can_ask else "stay"


def switch_mode(flag: str | None = None) -> str:
    """--switch, then LONGHAUL_SWITCH, then "ask". ValueError for anything else."""
    mode = flag or os.environ.get("LONGHAUL_SWITCH") or "ask"
    if mode not in SWITCH_MODES:
        raise ValueError(f"LONGHAUL_SWITCH is {mode!r}, which is not a switching mode. "
                         "Set it to ask, auto or never, or unset it; longhaul has not started Claude.")
    return mode


def ask_to_move(here: str, kind: str, resets_at: float, there: str) -> str:
    """At a limit in "ask" mode: "yes" only for a y typed in this terminal
    once longhaul has asked; "stop" for q or Ctrl+C; "no" for n or Enter, no
    answer by the reset, no terminal, or a reset already past. Other keys
    are ignored: an arrow key, or a terminal's focus report, starts with
    ESC, and once stopped longhaul (review, Sprint 1). Keys typed while Claude
    ran are thrown away first, so a stray y never moves the conversation."""
    if resets_at <= time.time():  # no one could answer in time, so nothing is asked
        return "no"
    question = (f"{here} hit its {kind} limit (resets {clock(resets_at)}). "
                f"Move this conversation to {there} now?")
    then = f"longhaul waits and carries on with {here} at {clock(resets_at)}"
    try:
        fd = sys.stdin.fileno() if sys.stdin is not None and sys.stdin.isatty() else None
    except ValueError:  # a closed stdin
        fd = None
    if fd is None:
        say(f"{question} There is no terminal to answer in, so {then}. "
            "To move without asking, use --switch auto or LONGHAUL_SWITCH=auto.")
        return "no"
    try:
        notify(f"{here} hit its {kind} limit. Answer in longhaul's terminal: move to {there}? "
               f"With no answer {then}. To move without asking, run longhaul --switch auto.")
        old = termios.tcgetattr(fd)
    except KeyboardInterrupt:
        print()
        return "stop"
    except termios.error as e:
        log(f"asked to move to {there}: the terminal cannot be read ({e!r})")
        say(f"{question} The terminal cannot be read, so {then}.")
        return "no"
    try:
        # TCSANOW, not the default that first waits for the terminal to show
        # what it was sent: with its output paused (Ctrl+S, a stuck pane)
        # that wait never ended, and longhaul hung at the limit (Sprint 1).
        termios.tcflush(fd, termios.TCIFLUSH)
        tty.setcbreak(fd, termios.TCSANOW)
        sys.stdout.write(f"\033[36mlonghaul\033[0m {question} (y: move to {there}, n: wait for {here}, q: stop). "
                         f"With no answer {then}. ")
        sys.stdout.flush()
        while time.time() < resets_at:
            ready, _, _ = select.select([fd], [], [], min(1.0, max(0.0, resets_at - time.time())))
            if not ready:
                continue
            try:
                key = os.read(fd, 1)
            except OSError as e:
                key, why = b"", repr(e)
            else:
                why = "end of input"
            if not key:  # the terminal went away: a closed one reads as empty for ever
                print()
                log(f"asked to move to {there}: no answer, the terminal is gone ({why})")
                return "no"
            if key in (b"y", b"Y"):
                print("y")
                log(f"asked to move to {there}: yes")
                return "yes"
            if key in (b"q", b"Q", b"\x03"):
                print()
                log(f"asked to move to {there}: stop")
                return "stop"
            if key in (b"n", b"N", b"\r", b"\n"):
                print("n")
                log(f"asked to move to {there}: no")
                return "no"
        print()
        say(f"No answer by the reset, so longhaul carries on with {here}.")
        notify(f"No answer by the reset, so longhaul carries on with {here}.")
        return "no"
    except KeyboardInterrupt:
        print()
        log(f"asked to move to {there}: stop (Ctrl+C)")
        return "stop"
    except termios.error as e:  # the terminal went away while longhaul set it up
        print()
        log(f"asked to move to {there}: no answer, the terminal is gone ({e!r})")
        return "no"
    finally:
        with contextlib.suppress(termios.error):  # a terminal that went away cannot be set back
            termios.tcsetattr(fd, termios.TCSANOW, old)


def wait_until(until: float, what: str) -> bool:
    say(f"{what}. Waiting until {clock(until)}, then carrying on (Ctrl+C stops).")
    notify(f"{what}. Carrying on at {clock(until)}.")
    try:
        while time.time() < until:
            time.sleep(min(30, max(0.1, until - time.time())))
        return True
    except KeyboardInterrupt:
        print()
        return False


# ------------------------------------------------------------------ run

def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def supervise(proc: subprocess.Popen, w: Watch, other_has_room=lambda: False,
              may_reconnect=lambda: True, claim=lambda sid: True) -> str:
    while proc.poll() is None:
        try:
            proc.wait(timeout=POLL)
            break
        except subprocess.TimeoutExpired:
            pass
        try:
            w.step()
            # Locked as soon as Claude names it, not when it closes: only
            # then does `longhaul -c` (or the picker, or /clear) know which it is.
            if w.sid and not claim(w.sid):
                stop(proc)
                return "held"
            if w.should_switch():
                stop(proc)
                return "limit"
            if w.should_reconnect():
                if may_reconnect():
                    stop(proc)
                    return "reconnect"
                # Reopening has not kept the link: leave it to Claude for
                # this launch, and forget the loss, or it is asked at every poll.
                w.rc, w.rc_seen, w.rc_lost_at = False, False, None
            if w.should_move(other_has_room):
                stop(proc)
                return "move"
        except Exception as e:  # watching must never take the session down
            log(f"watch error: {e!r}")
    try:  # a limit written in the last moments before Claude closed
        w.step()
    except Exception:
        pass
    return "limit" if w.limit else "exit"


# Shared files edited by hand or by /config, where the newer copy is
# your latest change; history.jsonl and the folders are not promoted.
PROMOTED = ("settings.json", "keybindings.json", "CLAUDE.md")


def relink(acct: dict) -> None:
    """Claude Code may save a shared file by replacing it, which turns the
    link into a separate copy that the other account no longer sees. Put
    the link back and keep the older copy beside it, named per process so
    two launches in one second cannot overwrite each other's. A settings
    copy newer than the shared file is the latest edit (made in a chat on
    this account) and becomes the shared one."""
    cfg = config_of(acct)
    if not acct.get("config_dir") or cfg == DEFAULT_CONFIG:
        return
    for item in SHARED:
        src, dst = DEFAULT_CONFIG / item, cfg / item
        if not src.exists() or dst.is_symlink():
            continue
        stamp = f"{dt.datetime.now():%Y%m%d%H%M%S}-{os.getpid()}"
        try:
            if item in PROMOTED and dst.is_file() and src.is_file() and dst.stat().st_mtime > src.stat().st_mtime:
                before = src.with_name(f"{item}.before-{acct['name'].lower()}-{stamp}")
                os.replace(src, before)
                os.replace(dst, src)
                say(f"{acct['name']}: {item} was changed under this account; that change is now shared "
                    f"and the one before is kept as {before.name}")
            elif dst.exists():
                aside = dst.with_name(f"{item}.{acct['name'].lower()}-{stamp}")
                dst.rename(aside)
                say(f"{acct['name']}: {item} had become a separate copy; linked it again and kept the copy as {aside.name}")
            dst.symlink_to(src)
        except FileExistsError:
            if not (dst.is_symlink() and dst.resolve() == src.resolve()):
                raise
        except FileNotFoundError:
            continue  # another launch was tidying the same file


def prepare(acct: dict, chat: bool = True) -> None:
    """The account's folder before a launch: links back in place and, for
    a chat, MCP servers copied across. One process at a time; a failure
    is logged and the launch goes on (the folder only gets tidier)."""
    try:
        with locked("folders"):
            relink(acct)
            if chat:
                sync_mcp(acct)
    except Exception as e:
        log(f"{acct['name']}: could not tidy its folder: {e!r}")


def sync_mcp(acct: dict) -> None:
    """`claude mcp add` writes to ~/.claude.json, which the second account
    does not share (it holds the sign-in): user scope at its top level,
    local scope (the default) under projects[<folder>]. Copy both across
    before each launch, so a connection added once (Linear, CodeGraph) is
    there on both accounts. ~/.claude.json is the source; servers only the
    second account has are kept. A project the account has never opened
    gets ~/.claude.json's whole entry for it, as `longhaul setup` gives it."""
    if not acct.get("config_dir") or config_of(acct) == DEFAULT_CONFIG:
        return
    path = config_of(acct) / ".claude.json"
    src, dst = read_json(DEFAULT_GLOBAL_JSON), read_json(path)
    for name, data in ((DEFAULT_GLOBAL_JSON, src), (path, dst)):
        if not isinstance(data, dict):
            # Missing is nothing to share, or not signed in yet (`longhaul
            # setup` copies them); there but unreadable is worth a line.
            if name.exists():
                log(f"{acct['name']}: MCP servers not shared: {name} could not be read as an object")
            return
    if not mcp_missing(src, dst):
        return
    # Claude Code writes this file often; read it again right before the
    # write so as little as possible of a change made meanwhile is lost.
    # Half written, it is left alone: writing back the copy read a moment
    # ago would undo what Claude saved since.
    dst = read_json(path)
    if not isinstance(dst, dict):
        log(f"{acct['name']}: MCP servers not shared this time: {path} changed as longhaul read it")
        return
    changed = mcp_missing(src, dst)
    for folder, name in changed:
        here, there = dst, src
        if folder is not None:
            if not isinstance(dst.get("projects"), dict):
                dst["projects"] = {}
            if not isinstance(dst["projects"].get(folder), dict):
                dst["projects"][folder] = src["projects"][folder]
            here, there = dst["projects"][folder], src["projects"][folder]
        if not isinstance(here.get("mcpServers"), dict):
            here["mcpServers"] = {}
        here["mcpServers"][name] = there["mcpServers"][name]
    write_json(path, dst)
    path.chmod(0o600)
    say(f"{acct['name']}: shared MCP servers "
        + ", ".join(name if folder is None else f"{name} (in {folder})" for folder, name in changed))


def mcp_missing(src: dict, dst: dict) -> list:
    """(project folder, or None for user scope; server name) for each MCP
    server in ~/.claude.json that this account lacks or has otherwise."""
    out = [(None, k) for k, v in sorted(dict_at(src, "mcpServers").items())
           if dict_at(dst, "mcpServers").get(k) != v]
    theirs = dict_at(dst, "projects")
    for folder, entry in sorted(dict_at(src, "projects").items()):
        out += [(folder, k) for k, v in sorted(dict_at(entry, "mcpServers").items())
                if dict_at(theirs.get(folder), "mcpServers").get(k) != v]
    return out


def reopen_delay(crashes: int, base: int) -> int:
    """Seconds to wait before reopening after the n-th crash within
    REOPEN_WINDOW: base, then three times as long each time, never more
    than 5 minutes."""
    return min(base * 3 ** (crashes - 1), 300) if base else 0


def parse_version(text: str):
    """(2, 1, 304) from `claude --version`'s "2.1.304 (Claude Code)"."""
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def claude_version():
    """The installed Claude Code's version, or None if it cannot be read.
    Never raises: an unreadable version must not stop Claude starting."""
    try:
        out = subprocess.run([CLAUDE, "--version"], capture_output=True, text=True, timeout=15)
        return parse_version(out.stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def version_note(version) -> str | None:
    """What to say about this version, or None when it has been checked."""
    low, high = TESTED_VERSIONS
    shown = lambda v: ".".join(map(str, v))
    if version is None:
        return "could not read Claude Code's version, so longhaul cannot tell whether switching works on it"
    if low <= version <= high:
        return None
    return (f"Claude Code {shown(version)} has not been checked with longhaul (checked: {shown(low)} to "
            f"{shown(high)}). Claude runs as normal; if longhaul does not switch accounts at a limit, "
            "this is why.")


# hold() could not make a lock at all; the session runs unguarded.
NO_LOCK = object()


def hold(sid: str):
    """An exclusive lock on one conversation for as long as this longhaul looks
    after it; None when another longhaul holds it; NO_LOCK when no lock can be
    made. Two longhaul on one conversation both append to its transcript and both
    switch at a limit. The OS lets go of the lock when longhaul ends, however it
    ends, so none goes stale (the empty files stay in locks/).

    Never raises: a home folder on a network drive or a read-only disk
    once stopped Claude starting at all (review, Phase 1). That case is
    logged and runs without the guard."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sid or ""):
        log(f"no lock for conversation id {sid!r}: not a plain id")
        return NO_LOCK
    try:
        path = LONGHAUL_HOME / "locks" / f"{sid}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        f = path.open("a")
    except OSError as e:
        log(f"no lock for {sid}: {e!r}")
        return NO_LOCK
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    except OSError as e:
        f.close()
        log(f"no lock for {sid}: {e!r}")
        return NO_LOCK
    return f


def exit_code(code, default: int) -> int:
    """A process's return code as longhaul's own exit code: a signal comes back
    negative, which sys.exit would turn into 247 or 245."""
    return code if code and code > 0 else default


HELD_ELSEWHERE = ("another longhaul is already looking after this conversation. "
                  "Use that one, or stop it first.")


def full_session_id(prefix: str) -> str:
    """`longhaul -r 557ce21f`: you read short IDs off a list; claude wants
    the whole one (a part opens its picker instead). Expanded only when
    exactly one conversation starts with it."""
    if not prefix or len(prefix) >= 36:
        return prefix
    hits = {p.stem for p in files_of((DEFAULT_CONFIG / "projects").glob(f"*/{prefix}*.jsonl"))}
    return hits.pop() if len(hits) == 1 else prefix


def first_message(path: str) -> str | None:
    """The text `--first FILE` names, read once before the first launch.

    A session started for a task gets the task as its first message. Put
    after `--` it went to claude on every launch, so each account move or
    reopen gave the session its task again from the start. Read
    here, it rides the first launch only, in the slot the carry-on message
    takes later. None, said in this terminal, when the file cannot be read
    or holds no text: a session started with no task would sit waiting."""
    try:
        text = Path(path).read_text().strip()
    except (OSError, ValueError) as e:
        say(f"could not read the first message from {path}: {getattr(e, 'strerror', None) or e}")
        return None
    if not text:
        say(f"the first message in {path} is empty, so longhaul did not start Claude")
        return None
    return text


def run(opts) -> int:
    try:
        mode = switch_mode(getattr(opts, "switch", None))
    except ValueError as e:
        say(str(e))
        return 2
    accounts = load_accounts()
    names = [a["name"] for a in accounts]
    if opts.account and opts.account not in names:
        say(f"no account called {opts.account}; the accounts are {', '.join(names)}")
        return 2
    idx = names.index(opts.account) if opts.account else 0
    resume, cont, prompt = full_session_id(opts.resume), opts.cont, None
    if opts.first is not None:
        # Goes with the first launch only: after every launch the loop
        # below sets `prompt` again, to the carry-on message or to nothing.
        # `is not None`, not truthiness: `--first ""` (an empty path a
        # launcher quoted) must be refused, not read as "no first message".
        prompt = first_message(opts.first)
        if prompt is None:
            return 2
    label = opts.name or Path.cwd().name
    if resume:
        # Two windows on one conversation both write to it, and Remote
        # Control jumps between them; moving a session from the VS Code
        # panel to longhaul means closing it there first.
        for f in (DEFAULT_CONFIG / "sessions").glob("*.json"):
            s = read_json(f)
            if s and s.get("sessionId") == resume and pid_alive(s.get("pid")):
                where = "the VS Code panel" if "vscode" in str(s.get("entrypoint")) else "another window"
                say(f"This conversation is still open in {where} (process {s.get('pid')}). "
                    "Close it there first, then run this again.")
                return 1

    lock, locked_sid = None, None

    def guard(sid: str, tell=say) -> bool:
        """Lock `sid` before letting go of the conversation locked so far,
        so the id can change (/clear) without a gap. False when another longhaul
        holds it. While Claude runs, `tell` keeps what to say for later."""
        nonlocal lock, locked_sid
        if lock is NO_LOCK or sid == locked_sid:
            return True
        new = hold(sid)
        if new is None:
            tell(HELD_ELSEWHERE)
            return False
        if new is NO_LOCK:
            tell("longhaul cannot guard this conversation against a second longhaul (see longhaul.log); carrying on.")
        if lock is not None:
            lock.close()
        lock, locked_sid = new, sid
        return True

    # Locked as typed; a short id that matched nothing makes Claude open
    # its picker, and guard() then moves the lock to the conversation
    # Claude names. Meanwhile the check above refuses a live claude on it.
    if resume and not guard(resume):
        return 1
    version = claude_version()
    log(f"claude {'.'.join(map(str, version)) if version else 'version unknown'}")
    if settings_note():
        log(settings_note())
    note = version_note(version)
    if note:
        say(note)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))
    if shutil.which("caffeinate") and not os.environ.get("LONGHAUL_NO_CAFFEINATE"):
        # Keeps the Mac from sleeping while longhaul runs (plugged in, lid open).
        subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())])

    runs = 0
    crashes: list[float] = []  # when each recent crash happened
    reconnects: list[float] = []  # when each recent reconnect happened
    untold: list[str] = []  # shown here once Claude has closed: it draws over this terminal

    def later(msg: str) -> None:
        """Say `msg` while Claude runs: logged now, shown when it closes."""
        log(msg)
        untold.append(msg)

    def may_reconnect() -> bool:
        """False once Remote Control has been lost MAX_RECONNECTS times
        within REOPEN_WINDOW: reopening has not kept it, and each reopen
        stops Claude. Claude carries on; you hear by notification now."""
        now = time.time()
        reconnects[:] = [t for t in reconnects if now - t < REOPEN_WINDOW]
        if len(reconnects) < MAX_RECONNECTS:
            return True
        msg = (f"Remote Control was lost {len(reconnects) + 1} times in {REOPEN_WINDOW / 60:g} minutes, "
               "so longhaul no longer reopens the conversation for it. Claude carries on, and Remote "
               "Control may come back by itself.")
        log("state: running -> running without Remote Control")
        later(msg)
        notify(msg)
        return False

    forced = opts.account  # `--account` decides the first launch only
    # In "ask" and "never" the conversation reopens on the account it was on,
    # whatever pick() would prefer: only a yes at a limit moves it. Before,
    # a reopen after a crash could land on another account unasked. A
    # conversation resumed by id starts where it last ran for the same
    # reason (D3d); --account chooses instead, and "auto" leaves it to pick().
    stay: str | None = None
    if resume and mode != "auto" and not forced:
        stay = last_account(resume, accounts)
        if stay:
            say(f"{resume[:8]} last ran on {stay}; with switching set to {mode} it starts there "
                "(use --account to choose, --switch auto to let longhaul pick)")
        else:
            # Said here, not only logged: a short id several transcripts match
            # is not expanded and has no record, and the conversation then
            # opened wherever pick() chose with no word of why (review, Sprint 5).
            say(f"no record of where {resume[:8]} last ran; picking an account")
    while True:
        state = load_state()
        seen = readings(accounts)
        # Checked before every launch, not once: on 2026-10-15 a sign-in in
        # VS Code moved ~/.claude to the second account while longhaul waited for
        # a reset, and a check made only at start would have carried on as
        # "Max" on the wrong account.
        wrong: set[str] = set()
        chosen = False
        if stay:
            # Within the margin too: a question unanswered until the reset
            # second otherwise reopened before the limit had lifted (review).
            resets = state["limited_until"].get(stay, 0)
            if resets + RESET_MARGIN > time.time():
                if not wait_until(resets + RESET_MARGIN,
                                  f"{stay} is at its limit, and with switching set to {mode} "
                                  "the conversation stays on it (or start it with --account NAME)"):
                    return 0
                continue
            if not signed_in_as_expected(accounts[names.index(stay)]):
                say(f"{stay} is not signed in as expected, and with switching set to {mode} longhaul does not move "
                    "the conversation by itself. Run `longhaul status`, then `longhaul setup`.")
                return 1
            idx, until, chosen = names.index(stay), None, True
        elif forced:
            i, forced = names.index(forced), None
            if state["limited_until"].get(names[i], 0) <= time.time() and signed_in_as_expected(accounts[i]):
                idx, until, chosen = i, None, True
        while not chosen:
            idx, until = pick(accounts, state, idx, skip=wrong, seen=seen)
            if idx is None or until or signed_in_as_expected(accounts[idx]):
                break
            wrong.add(accounts[idx]["name"])
        if idx is None:
            say("No account is signed in as expected. Run `longhaul status`, then `longhaul setup`.")
            return 1
        if until:
            if not wait_until(until + RESET_MARGIN, "Every account is at its limit"):
                return 0
            continue
        acct = accounts[idx]
        prepare(acct)

        cmd = [CLAUDE]
        if resume:
            cmd += ["--resume", resume]
        elif resume == "":
            cmd += ["--resume"]
        elif cont:
            cmd += ["--continue"]
        if opts.rc:
            # On the phone two sessions from one folder would both read
            # "myproject · Max"; --name tells them apart.
            cmd += ["--remote-control", f"{label} · {acct['name']}"]
        if os.environ.get("TERM_PROGRAM") == "vscode" and "--ide" not in opts.extra:
            cmd += ["--ide"]  # in VS Code's terminal: diffs open in VS Code
        cmd += opts.extra
        if prompt:
            cmd.append(prompt)

        log(f"launch {acct['name']}: {' '.join(cmd[1:-1] if prompt else cmd[1:])}")
        marks = transcript_size(resume)
        started = time.time()
        # A Python-level handler, unlike SIG_IGN, resets to the default in
        # the child at exec: Ctrl+C reaches Claude, not longhaul.
        signal.signal(signal.SIGINT, lambda *_: None)
        proc = subprocess.Popen(cmd, env=env_for(acct))
        w = Watch(acct, proc.pid, started, opts.rc, offsets=marks,
                  handled=state.get("handled_limits", []), label=opts.name)

        def other_has_room(here=acct["name"], skip=frozenset(wrong)) -> bool:
            st, now = load_state(), time.time()
            fresh = readings(accounts)
            return any(a["name"] != here and a["name"] not in skip
                       and st["limited_until"].get(a["name"], 0) <= now
                       and not near_full(fresh.get(a["name"]), now) for a in accounts)

        try:
            # The early move to an account with room happens in "auto" only.
            why = supervise(proc, w, other_has_room if mode == "auto" else (lambda: False), may_reconnect,
                            claim=lambda sid: guard(sid, later))
        finally:
            if proc.poll() is None:
                stop(proc)
            signal.signal(signal.SIGINT, signal.default_int_handler)
        restore_terminal()
        for msg in untold:
            show(msg)
        untold.clear()
        runs += 1
        if why == "exit":
            # 0 is how Claude Code ends on /exit and on Ctrl+C twice
            # (2.1.304); anything else, a signal included, is a crash.
            why = "completed" if proc.returncode == 0 else "crashed"
        log(f"{acct['name']} ended: {why} (session {w.sid}, exit {proc.returncode})")
        log(f"state: running -> {why}")
        if w.sid:  # where it ran, for a later `longhaul -r` in ask or never
            note_session_account(w.sid, acct["name"])
        if why == "held":  # it stopped a Claude it had started, maybe with no one watching
            notify(HELD_ELSEWHERE)
            return 1
        if w.sid and not guard(w.sid):
            return 1

        if w.sid:
            resume, cont = w.sid, False
        elif why in ("completed", "crashed") and time.time() - started < 10:
            say("Claude closed straight away before a conversation began. Run `longhaul status` to check the sign-ins.")
            return exit_code(proc.returncode, 1)
        if MAX_RUNS and runs >= MAX_RUNS:
            return exit_code(proc.returncode, 0)
        if mode != "auto":
            stay = acct["name"]

        if why == "limit":
            def mark(state, name=acct["name"], limit=w.limit):
                state["limited_until"][name] = limit["resets_at"]
                if limit.get("uuid"):  # never act on the same stop twice
                    state["handled_limits"] = (state.get("handled_limits", []) + [limit["uuid"]])[-50:]
            state = update_state(mark)
            nxt, wait = pick(accounts, state, (idx + 1) % len(accounts), skip=wrong,
                             seen=readings(accounts))
            new = accounts[nxt]["name"]
            msg = f"{acct['name']} hit its {w.limit['kind']} limit (resets {clock(w.limit['resets_at'])})."
            # The person at this terminal is the front end, so consent is
            # given. With every account at its limit there is nowhere to
            # move, so nothing is asked. An account signed in as someone
            # else is never offered: a yes to it used to end longhaul (review,
            # Sprint 1); signed_in_as_expected says why.
            verdict = decide_move(mode, consent=True, can_ask=not wait and signed_in_as_expected(accounts[nxt]))
            if verdict == "ask":
                answer = ask_to_move(acct["name"], w.limit["kind"], w.limit["resets_at"], new)
            else:
                answer = "yes" if verdict == "move" else "no"
            if answer == "stop":
                again = f" `longhaul -r {resume}` opens it again." if resume else ""
                say(f"Stopped at your request. The conversation is saved.{again}")
                return 0
            if answer == "yes":  # always, in "auto"
                prompt = (CARRY_ON_AFTER_WAIT if wait else CARRY_ON).format(
                    old=acct["name"], kind=w.limit["kind"], new=new)
                if mode == "ask":  # the question has said the rest
                    msg = f"Moving to {new}: reopening this conversation there with a carry-on message."
                elif not wait:
                    msg += f" Carrying on with {new}."
                idx, stay = nxt, (new if mode != "auto" else None)
                say(msg)
                notify(msg)
            else:  # "ask" without a yes, or "never": the reset, on this account
                prompt = (CARRY_ON_AFTER_WAIT if wait else CARRY_ON_SAME).format(
                    old=acct["name"], kind=w.limit["kind"], new=acct["name"])
                log(msg)  # the wait at the top of the loop says it, with the time
        elif why == "move":
            # No carry-on message: Claude was idle, so there is nothing to
            # carry on, and your next message goes to the new account.
            prompt = None
            used = max((u for u, _ in windows(readings(accounts).get(acct["name"]), time.time())), default=0)
            nxt, _ = pick(accounts, load_state(), (idx + 1) % len(accounts), skip=wrong,
                          seen=readings(accounts))
            msg = (f"{acct['name']} is at {used:g}% of its allowance, so while {label} was idle "
                   f"longhaul moved it to {accounts[nxt]['name']}.")
            say(msg)
            notify(msg)
            idx = nxt
        elif why == "reconnect":
            prompt = None
            reconnects.append(time.time())
            say("Remote Control was lost, so longhaul reopened the conversation to connect it again.")
            notify("Remote Control was lost; reopened the conversation to connect it again.")
        elif why == "completed":
            again = f" `longhaul -r {w.sid}` opens it again." if w.sid else ""
            say(f"Claude closed normally, so longhaul stops here.{again}")
            return 0
        else:
            prompt = None
            now = time.time()
            crashes = [t for t in crashes if now - t < REOPEN_WINDOW] + [now]
            if len(crashes) > MAX_REOPENS:
                log("state: crashed -> failed")
                msg = (f"Claude crashed {len(crashes)} times in {REOPEN_WINDOW / 60:g} minutes "
                       f"(last exit {proc.returncode}), so longhaul stopped reopening it. "
                       f"Check `longhaul status` and {LONGHAUL_HOME / 'longhaul.log'}.")
                say(msg)
                notify(msg)
                return exit_code(proc.returncode, 1)
            if not countdown(reopen_delay(len(crashes), REOPEN_DELAY),
                             f"Claude crashed (exit {proc.returncode}). Reopening this conversation"):
                return exit_code(proc.returncode, 0)


# ---------------------------------------------------------------- usage
#
# Claude Code sends the status line a JSON reading after every reply
# (proved live on 2.1.300): rate_limits {five_hour, seven_day: {
# used_percentage, resets_at}}, session_id, and cost.total_cost_usd, the
# API-price equivalent of this process's work so far. The status line is
# the only live source: ~/.claude.json's cachedUsageUtilization is filled
# only when /usage is opened. `longhaul statusline` keeps the latest reading per
# account (step 3 picks accounts from it) and a ledger row whenever a
# number moves; `longhaul usage` adds the ledger up per task and account. Rows
# also keep the documented context_window, prompt_cache (main conversation
# only), model.id and cost.total_api_duration_ms
# (code.claude.com/docs/en/statusline, checked 2026-10-20 on 2.1.304).

USAGE_DIR = LONGHAUL_HOME / "usage"
LEDGER = LONGHAUL_HOME / "ledger.jsonl"
LABELS = LONGHAUL_HOME / "labels.json"


def account_for_config(config_dir, accounts: list[dict]) -> str:
    """The account a claude process runs as, from its CLAUDE_CONFIG_DIR.
    None is ~/.claude: the one account in `accounts` with no folder of its
    own, when there is exactly one ({"name": "Max", "config_dir": null}, a
    subscription that keeps the default folder); else "main", whatever
    the VS Code panel is signed in to. Before, every status-line reading
    from such an account was "main", written over the supervisor's note
    of the account, and a resume lost where it ran (review, Sprint 5)."""
    if not config_dir:
        on_default = [a["name"] for a in accounts if not a.get("config_dir")]
        return on_default[0] if len(on_default) == 1 else "main"
    path = Path(os.path.expanduser(config_dir)).resolve()
    for a in accounts:
        if a.get("config_dir") and config_of(a).resolve() == path:
            return a["name"]
    return path.name


def save_label(sid: str, label: str) -> None:
    labels = read_json(LABELS) or {}
    if labels.get(sid) != label:
        labels[sid] = label
        write_json(LABELS, labels)


def gauge(name: str, part: dict) -> str:
    pct = part.get("used_percentage")
    if pct is None:
        return ""
    color = "32" if pct < 70 else "33" if pct < 90 else "31"
    when = f" (resets {clock(part['resets_at'])})" if part.get("resets_at") else ""
    return f" · {name} \033[{color}m{pct}%\033[0m{when}"


def record_usage(data: dict, config_dir, now: float | None = None) -> str:
    """Keep this reading and return the status line text."""
    now = time.time() if now is None else now
    # The file's accounts, not load_accounts()'s stand-in for a missing file,
    # which would name every reading from ~/.claude after it: with no
    # accounts.json a session there is "main".
    acct = account_for_config(config_dir, read_json(LONGHAUL_HOME / "accounts.json") or [])
    limits = data.get("rate_limits") or {}
    sid = data.get("session_id")
    if limits:
        write_json(USAGE_DIR / f"{acct}.json", {"at": now, "session": sid, **limits})
    else:  # the first update after a launch comes before any reply
        stored = read_json(USAGE_DIR / f"{acct}.json") or {}
        limits = {k: v for k, v in stored.items() if k in ("five_hour", "seven_day")
                  and (v.get("resets_at") or 0) > now}
    five, week = limits.get("five_hour") or {}, limits.get("seven_day") or {}

    # Only a plain id (GUARD_ID) names a record: anything else would be a
    # path. Not logged: the status line runs at every update.
    if isinstance(sid, str) and re.fullmatch(GUARD_ID, sid):
        cost, ctx, cache = dict_at(data, "cost"), dict_at(data, "context_window"), dict_at(data, "prompt_cache")
        row = {"at": round(now, 1), "session": sid, "account": acct,
               "cost": round(float(cost.get("total_cost_usd") or 0), 4),
               "five": five.get("used_percentage"), "five_resets": five.get("resets_at"),
               "week": week.get("used_percentage")}
        # Absent or null early in a session, after /compact, before 2.1.251
        # (prompt_cache) and from the panel relay: left out, never saved as null.
        row.update({k: v for k, v in (
            ("ctx", ctx.get("used_percentage")), ("ctx_tokens", ctx.get("total_input_tokens")),
            ("ctx_size", ctx.get("context_window_size")), ("model", dict_at(data, "model").get("id")),
            ("cache_hit", cache.get("hit_ratio")), ("cache_misses", cache.get("misses")),
            ("recache", cache.get("miss_recache_tokens")), ("api_ms", cost.get("total_api_duration_ms")),
        ) if v is not None})
        if data.get("proc"):  # the panel relay knows which claude this was
            row["proc"] = data["proc"]
        last_path = USAGE_DIR / "sessions" / f"{sid}.json"
        last = read_json(last_path) or {}
        # ctx too: a /compact shrinks the context but leaves the cost alone.
        if any(row.get(k) != last.get(k) for k in ("account", "cost", "five", "week", "proc", "ctx", "model")):
            problem = append(LEDGER, json.dumps(row) + "\n", LEDGER_MAX)  # one short line: an atomic append
            if problem:
                log(f"ledger.jsonl reached {LEDGER_MAX} bytes but was not moved aside cleanly: {problem!r}")
            # Once per session here, not on every ledger row. Kept when a reading
            # has none, as the panel relay's never do (review, Phase 2).
            path = data.get("transcript_path")
            path = path if isinstance(path, str) else last.get("transcript")
            write_json(last_path, {**row, "transcript": path} if isinstance(path, str) else row)
    return f"\033[1m{acct}\033[0m{gauge('5h', five)}{gauge('week', week)}"


def statusline(_opts) -> int:
    """Claude Code's status line command. Must print one line and never
    fail: an error here would blank the line in every session."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
        print(record_usage(data, os.environ.get("CLAUDE_CONFIG_DIR")))
    except Exception as e:
        log(f"statusline error: {e!r}")
        print("longhaul")
    return 0


def summarize(rows: list[dict]) -> dict:
    """Per (session, account): the API-price cost of the work done there,
    and its share of that account's 5-hour percentage.

    Cost is cumulative per claude process and starts again at 0 when longhaul
    reopens a conversation, so a drop means a new process. A rise in an
    account's 5-hour percentage is put to the session whose reading showed
    it: exact with one session per account, close with two at once."""
    out: dict = {}
    last_cost: dict = {}
    last_five: dict = {}
    for r in sorted(rows, key=lambda r: r["at"]):
        key = (r["session"], r["account"])
        item = out.setdefault(key, {"cost": 0.0, "five": 0, "first": r["at"], "last": r["at"]})
        item["last"] = r["at"]
        # A process's cost starts at 0. The relay names its process, so a
        # chat back on an account it used before counts in full even when
        # the new process's first cost is above the old one's last.
        proc_key = (r["session"], r["account"], r.get("proc"))
        prev = last_cost.get(proc_key)
        item["cost"] += r["cost"] - prev if prev is not None and r["cost"] >= prev else r["cost"]
        last_cost[proc_key] = r["cost"]
        if r.get("five") is not None:
            before = last_five.get(r["account"])
            if before and before[1] == r.get("five_resets") and r["five"] > before[0]:
                item["five"] += r["five"] - before[0]
            last_five[r["account"]] = (r["five"], r.get("five_resets"))
    return out


def ledger_rows(since: float = 0) -> list[dict]:
    rows = []
    for path in (LEDGER.with_name(LEDGER.name + ".1"), LEDGER):  # the part moved aside, then the rest
        try:
            text = path.read_text()
        except FileNotFoundError:
            continue  # none yet, or being moved aside this moment
        for raw in text.splitlines():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            at = r.get("at", 0) if isinstance(r, dict) else None
            if as_number(at) is not None and at >= since:
                rows.append(r)
    return rows


def transcript_hits(sid) -> list[Path]:
    """Where a conversation's transcript is: the file its session record
    names (where its status line said it was), when that still exists;
    else every <project>/<sid>.jsonl under the projects folder, in order.
    Nothing for anything but a plain id (GUARD_ID): a ledger row or a
    label a hand wrote must not become a path."""
    if not isinstance(sid, str) or not re.fullmatch(GUARD_ID, sid):
        return []
    last = read_json(USAGE_DIR / "sessions" / f"{sid}.json")
    recorded = last.get("transcript") if isinstance(last, dict) else None
    if isinstance(recorded, str) and Path(recorded).is_file():
        return [Path(recorded)]
    return sorted(files_of((DEFAULT_CONFIG / "projects").glob(f"*/{sid}.jsonl")))


def task_name(sid: str, labels: dict) -> str:
    """The name given with --name; else the transcript's project folder (as
    Claude Code names it under projects/) and the short id. Never a word of
    the conversation: longhaul shows no message text. Each part through
    shown(): a label came out as it was, escape bytes and all (review,
    Sprint 5)."""
    if sid in labels:
        return shown(labels[sid])
    hits = transcript_hits(sid)
    if hits:
        return f"{shown(hits[0].parent.name)} {shown(sid[:8])}"
    return shown(sid[:8])


def usage(opts) -> int:
    now = time.time()
    print("Allowance (latest reading per account)")
    for name in [a["name"] for a in load_accounts()] + ["main"]:
        u = read_json(USAGE_DIR / f"{name}.json")
        if not u:
            continue
        parts = []
        for key, label in (("five_hour", "5h"), ("seven_day", "week")):
            part = u.get(key) or {}
            if part.get("resets_at") and part["resets_at"] <= now:
                parts.append(f"{label} reset since")
            elif part.get("used_percentage") is not None:
                parts.append(f"{label} {part['used_percentage']}% (resets {clock(part['resets_at'])})")
        ago = int((now - u.get("at", now)) / 60)
        print(f"  {name:<5} {'   '.join(parts)}   [read {ago} min ago]")

    totals = summarize(ledger_rows(now - opts.days * 86400))
    if not totals:
        print(f"\nNo work recorded in the last {opts.days:g} day(s).")
        return 0
    labels = read_json(LABELS) or {}
    print(f"\nWork in the last {opts.days:g} day(s), per task and account "
          "(cost = what it would cost at API prices: a measure of size, not money spent)")
    print(f"  {'task':<42} {'account':<7} {'cost':>8} {'5h share':>9}   when")
    for (sid, acct), t in sorted(totals.items(), key=lambda kv: kv[1]["first"]):
        span = f"{clock(t['first'])}–{dt.datetime.fromtimestamp(t['last']):%H:%M}"
        share = f"≈{t['five']}%" if t["five"] else "-"
        cost = f"${t['cost']:.2f}"
        print(f"  {task_name(sid, labels):<42} {acct:<7} {cost:>8} {share:>9}   {span}")
    by_acct: dict = {}
    for (_, acct), t in totals.items():
        by_acct[acct] = by_acct.get(acct, 0) + t["cost"]
    print("  " + "   ".join(f"{a}: ${c:.2f}" for a, c in sorted(by_acct.items())))
    return 0


# -------------------------------------------------------------- inspect
#
# `longhaul inspect [ID|PATH]`: where one conversation's usage went, from its
# transcript and its subagents' (<transcript minus .jsonl>/subagents/
# agent-*.jsonl). The format is undocumented. Read from real transcripts
# on 2.1.304, structure only:
# - a response is one assistant line per content block, all with one
#   message.id, and the last line of an id holds its usage (a subagent's
#   early lines carry a partial output_tokens);
# - tool calls are tool_use blocks in assistant lines and their outputs
#   tool_result blocks in user lines. api-request-blob lines repeat both,
#   so only those two line types are read;
# - an API error is an assistant line with isApiErrorMessage, model
#   <synthetic> and no usage; a compaction is a system line, subtype
#   compact_boundary, with compactMetadata {trigger, preTokens, postTokens}.
# Numbers only: no message text or tool output, and of a tool's input
# only a file path or the program a command runs.

EDITS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SEARCHES = ("Grep", "Glob")
SEARCH_PROGRAMS = ("grep", "rg", "ag", "find", "fd", "ls", "git")  # a Bash search, by program: git log counts as grep
SEARCH_BIG = 5_000  # characters: a search result over this is big (the count: 8% of them carry 38% of search output)
SEARCH_HEAVY = 20  # searches in a conversation before the report advises a code index
CODEGRAPH_INSTALL = {"link": "github.com/colbymchenry/codegraph",  # as its README gave them on 2026-10-21; ours repeats
                     "sh": "curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh",
                     "npm": "npm i -g @colbymchenry/codegraph"}
TOKEN_KINDS = (("input", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
               ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens"))


def as_int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def as_number(value):
    """A number JSON can hold, else None (json.loads takes NaN)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def program(command) -> str:
    """The program a shell command runs and nothing else of it: the rest
    can hold a token. `FOO=1 /usr/bin/git log` is git. Words are split as
    the shell splits them, so a quoted value never passes for the program
    (review, Phase 2: `API_KEY="abc def" git` gave "def"); a first line
    the shell could not split, or a first word that does not look like a
    program's name, is "?"."""
    try:
        words = shlex.split(str(command).split("\n", 1)[0])
    except ValueError:
        return "?"
    for word in words:
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", word):
            continue
        name = word.rsplit("/", 1)[-1]
        return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,29}", name) else "?"
    return "?"


def shown(text, limit: int = 300) -> str:
    """Text from a transcript, safe to print: no control characters, which
    could move or retitle the terminal, and not too long."""
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", str(text))
    return text if len(text) <= limit else text[:limit - 1] + "…"


def home_path(path) -> str:
    if not isinstance(path, str):
        return ""
    home = str(HOME)
    return shown("~" + path[len(home):] if path == home or path.startswith(home + "/") else path)


def tool_target(name: str, args: dict) -> str:
    """What a tool call worked on, as far as that is safe to show."""
    if name == "Bash":
        return program(args.get("command", ""))
    if name == "Read" or name in EDITS:
        return home_path(args.get("file_path") or args.get("notebook_path"))
    if name in SEARCHES:
        return home_path(args.get("path"))
    return ""


def output_size(content) -> tuple[int, int]:
    """The characters of text a tool's output put into the context, and
    how many images. An image costs tokens too, but has no characters."""
    if isinstance(content, str):
        return len(content), 0
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    return (sum(len(b["text"]) for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)),
            sum(b.get("type") == "image" for b in blocks))


def codegraph_index_at(cwd) -> bool:
    """Whether a CodeGraph index covers this folder: a .codegraph/ folder
    at the absolute path cwd or in one of its parents, which is where
    CodeGraph itself looks (the nearest index at or above the path; a
    session opened in a subfolder of an indexed repository has one).
    Anything but an absolute path is no index."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return False
    return any(os.path.isdir(os.path.join(folder, ".codegraph")) for folder in (cwd, *map(str, Path(cwd).parents)))


def search_stats(events, calls, results, cwd) -> dict:
    """The search numbers (Sprint 6, G3), by the count's definition: a
    search is a Grep or Glob call, or a Bash whose program is one of
    SEARCH_PROGRAMS (`git grep` cannot be told from `git log` by the
    program alone, so every git counts, and the heading says so); a chain
    is a run of consecutive searches in one context, replayed in time
    order as the repeats are; a big result is over SEARCH_BIG characters.
    A code index is seen as a tool name holding `codegraph`, or as a
    `.codegraph/` folder at or above the transcript's first line's cwd;
    with no absolute cwd that is None (unknown), not 0, and its record is
    left out."""
    chains, open_run = [], collections.Counter()  # the closed runs; the run under way, per context
    searches, chars, big, big_chars = 0, 0, 0, 0
    for _, ctx, what, item in heapq.merge(*events, key=lambda e: e[0]):
        if what != "call":
            continue
        name, tid = item[1], item[3]
        if name not in SEARCHES and not (name == "Bash" and calls[tid][1] in SEARCH_PROGRAMS):
            if open_run[ctx]:
                chains.append(open_run.pop(ctx))
            continue
        open_run[ctx] += 1
        size = results.get(tid, (0,))[0]
        searches, chars = searches + 1, chars + size
        if size > SEARCH_BIG:
            big, big_chars = big + 1, big_chars + size
    chains.extend(n for n in open_run.values() if n)
    return {"search_calls": searches, "search_share": searches / len(calls) if calls else 0.0, "search_chars": chars,
            "search_chain_max": max(chains, default=0),
            "search_chain_mean": sum(chains) / len(chains) if chains else 0.0,
            "search_big": big, "search_big_share": big_chars / chars if chars else 0.0,
            "codegraph_calls": sum("codegraph" in name for name, _ in calls.values()),
            "codegraph_index": int(codegraph_index_at(cwd)) if isinstance(cwd, str) and os.path.isabs(cwd) else None}


def search_advice(s: dict) -> str:
    """The one line `inspect` adds for a search-heavy conversation in which
    no index was seen: its numbers, what an index does, and CodeGraph's
    install as its README gives it, with what the install does to the
    project. Longhaul never installs it."""
    return (f"Search-heavy: {s['search_calls']:,} searches ({s['search_share']:.0%} of calls), the longest chain "
            f"{s['search_chain_max']}, {s['search_big']:,} result{'' if s['search_big'] == 1 else 's'} over {SEARCH_BIG:,} characters "
            f"({s['search_big_share']:.0%} of search output). A code index answers symbol and dependency questions "
            f"in one call. CodeGraph ({CODEGRAPH_INSTALL['link']}, MIT): install with `{CODEGRAPH_INSTALL['sh']}` or "
            f"`{CODEGRAPH_INSTALL['npm']}`, then `codegraph init` in the project; it writes `.codegraph/` there, may "
            "add a section to your instructions file, and asks about telemetry. Longhaul never installs it; this line "
            "is all it does.")


def inspect_transcript(path: Path) -> dict:
    """The numbers `longhaul inspect` shows for the conversation in `path`."""
    files = [path] + sorted(path.with_suffix("").glob("subagents/agent-*.jsonl"))
    responses: dict = {}  # message id -> (scope, model, usage), from the id's last line with usage
    calls: dict = {}      # tool_use id -> (tool, what it worked on)
    results: dict = {}    # tool_use id -> (characters, images, is_error, scope)
    events = []           # per file, in its order: (time, file, "call" or "compact", (scope, tool, input, id))
    compactions, api_errors, unreadable, lines, cwd = [], collections.Counter(), 0, 0, None
    tool_calls, tool_chars = collections.Counter(), collections.Counter()  # (scope, tool) -> calls, output characters
    for n, file in enumerate(files):
        events.append([])
        when = 0.0  # a line with no time keeps its place after the one before
        with file.open(errors="replace") as f:
            for raw in f:
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                except (ValueError, RecursionError):
                    line = None
                if not isinstance(line, dict):
                    unreadable += 1
                    continue
                kind, msg = line.get("type"), dict_at(line, "message")
                if kind not in ("assistant", "user", "system"):
                    continue
                lines += 1
                if cwd is None and not n and isinstance(line.get("cwd"), str):
                    cwd = line["cwd"]  # the main file's first, looked under for an index, never shown
                when = parse_ts(line.get("timestamp")) or when
                scope = "subagents" if n or line.get("isSidechain") else "main"
                blocks = [b for b in msg.get("content") or [] if isinstance(b, dict)] \
                    if isinstance(msg.get("content"), list) else []
                if kind == "system" and line.get("subtype") == "compact_boundary":
                    meta = dict_at(line, "compactMetadata")
                    compactions.append({"trigger": meta.get("trigger") if meta.get("trigger") in ("auto", "manual")
                                        else "?", "before": as_int(meta.get("preTokens")),
                                        "after": as_int(meta.get("postTokens")), "scope": scope})
                    events[n].append((when, n, "compact", None))
                elif kind == "assistant" and line.get("isApiErrorMessage"):
                    error = line.get("error")
                    api_errors[error if isinstance(error, str) and re.fullmatch(r"[a-z_]{1,40}", error)
                               else "other"] += 1
                elif kind == "assistant":
                    key, model, usage = msg.get("id") or line.get("uuid"), msg.get("model"), dict_at(msg, "usage")
                    # <synthetic> lines are Claude Code's own (18 on this Mac), not API requests.
                    if isinstance(key, str) and model != "<synthetic>" and (usage or key not in responses):
                        responses[key] = (scope, shown(model, 80) if isinstance(model, str) else None, usage)
                    for b in blocks:
                        tid = b.get("id")
                        if b.get("type") != "tool_use" or not isinstance(tid, str) or tid in calls:
                            continue
                        name, args = shown(b.get("name") or "?", 80), dict_at(b, "input")
                        calls[tid] = (name, tool_target(name, args))
                        tool_calls[(scope, name)] += 1
                        events[n].append((when, n, "call", (scope, name, args, tid)))
                elif kind == "user":
                    for b in blocks:
                        tid = b.get("tool_use_id")
                        if b.get("type") == "tool_result" and isinstance(tid, str):
                            results[tid] = (*output_size(b.get("content")), b.get("is_error") is True, scope)

    # The same read or search again, with the same input, in the same
    # context, when nothing since could have changed its answer: no edit
    # of that file in any context (subagents edit what the main
    # conversation reads), no edit at all for a search, no compaction. A
    # call whose output was an error, or never came, is neither repeated
    # nor a repeat (review, Phase 2). Contexts are replayed in time order.
    repeats = []  # (scope, tool, what, tool_use id)
    seen: dict = collections.defaultdict(dict)  # context -> {whole input: the file read, None for a search}
    for _, ctx, what, item in heapq.merge(*events, key=lambda e: e[0]):
        if what == "compact":
            seen[ctx] = {}  # what was read is no longer in the context
            continue
        scope, name, args, tid = item
        if name in EDITS:
            changed = args.get("file_path") or args.get("notebook_path")
            for c in seen:
                seen[c] = {k: p for k, p in seen[c].items() if p is not None and p != changed}
        elif (name == "Read" or name in SEARCHES) and results.get(tid, (0, 0, True))[2] is False:
            same = json.dumps([name, args], sort_keys=True, default=str)
            if same in seen[ctx]:
                repeats.append((scope, name, calls[tid][1], tid))
            seen[ctx][same] = args.get("file_path") if name == "Read" else None

    requests = {"main": 0, "subagents": 0}
    tokens = {s: {k: 0 for k, _ in TOKEN_KINDS} for s in requests}
    models, context = collections.Counter(), []
    for scope, model, usage in responses.values():
        requests[scope] += 1
        if isinstance(model, str) and model != "<synthetic>":
            models[model] += 1
        for k, field in TOKEN_KINDS:
            tokens[scope][k] += as_int(usage.get(field))
        size = sum(as_int(usage.get(field)) for k, field in TOKEN_KINDS if k != "output")
        if scope == "main" and size:
            context.append(size)

    rereads, searches = {}, collections.Counter()
    for scope, name, what, tid in repeats:
        if name == "Read":
            item = rereads.setdefault((scope, what), {"path": what, "repeats": 0, "chars": 0, "images": 0,
                                                      "scope": scope})
            item["repeats"] += 1
            chars, images = results.get(tid, (0, 0))[:2]
            item["chars"] += chars
            item["images"] += images
        else:
            searches[(scope, name, what)] += 1

    # Output characters per tool, beside the calls (Sprint 4, W2): a denied
    # Read that a Bash took over shows as moved, not saved.
    for tid, (chars, _, _, scope) in results.items():
        tool_chars[(scope, calls.get(tid, ("?",))[0])] += chars
    by_tool = sorted(set(tool_calls) | set(tool_chars), key=lambda k: (-tool_chars[k], -tool_calls[k], k))

    # The session's last ledger row with cache numbers: a reopened
    # conversation's first readings have none (review, Phase 2).
    cache, fields = None, (("hit", "cache_hit"), ("misses", "cache_misses"), ("recache", "recache"))
    for row in ledger_rows():  # oldest first
        if row.get("session") == path.stem and any(as_number(row.get(f)) is not None for _, f in fields):
            cache = {**{k: as_number(row.get(f)) for k, f in fields}, "at": as_number(row.get("at"))}
    return {
        "session": path.stem, "path": str(path), "subagents": len(files) - 1, "lines": lines,
        "requests": requests, "models": dict(models.most_common()), "tokens": tokens,
        "context": {"first": context[0], "peak": max(context), "last": context[-1]} if context else None,
        "compactions": compactions, "cache": cache,
        "outputs": [{"tool": calls.get(tid, ("?", ""))[0], "what": calls.get(tid, ("?", ""))[1],
                     "chars": chars, "scope": scope}
                    for tid, (chars, _, _, scope) in sorted(results.items(), key=lambda kv: -kv[1][0])[:5]],
        "by_tool": [{"tool": k[1], "calls": tool_calls[k], "chars": tool_chars[k], "scope": k[0]} for k in by_tool],
        "rereads": sorted(rereads.values(), key=lambda x: (-x["chars"], -x["repeats"], x["path"])),
        "searches": [{"tool": name, "where": what, "repeats": c, "scope": scope}
                     for (scope, name, what), c in sorted(searches.items(), key=lambda kv: (-kv[1], kv[0]))],
        "search": search_stats(events, calls, results, cwd),
        "tool_errors": dict(collections.Counter(calls.get(tid, ("?",))[0]
                                                for tid, (_, _, error, _) in results.items() if error).most_common()),
        "api_errors": dict(api_errors.most_common()), "unreadable": unreadable, "guard": guard_counts(path.stem),
    }


def find_transcript(target: str | None) -> tuple[Path | None, str]:
    """The transcript to read, and a note: why there is none, or which of
    several it is. A path (holding a / or ending in .jsonl) is read as
    given. An ID, or a short one only one conversation starts with, is
    looked for where its status line said its transcript was, then in the
    projects folder every account shares. With neither: the conversation
    the ledger heard from last."""
    plain = r"[A-Za-z0-9_-]{1,100}"
    if target and ("/" in target or target.endswith(".jsonl")):
        path = Path(target).expanduser()
        return (path, "") if path.is_file() else (None, f"there is no file {target}.")
    if target:
        if not re.fullmatch(plain, target):
            return None, f"{target!r} is neither a conversation ID nor a transcript's path."
        known = ({p.stem for p in files_of((DEFAULT_CONFIG / "projects").glob(f"*/{target}*.jsonl"))}
                 | {p.stem for p in (USAGE_DIR / "sessions").glob(f"{target}*.json")})
        if target not in known and len(known) > 1:
            return None, f"{len(known)} conversations start with {target}; give more of the ID."
        sid = target if target in known or not known else known.pop()
    else:
        rows = ledger_rows()
        if not rows:
            return None, "the ledger names no conversation yet. Give an ID or a transcript's path."
        sid = max(rows, key=lambda r: as_number(r.get("at")) or 0).get("session")
        if not isinstance(sid, str) or not re.fullmatch(plain, sid):
            return None, f"the ledger's latest row names no plain conversation ID ({shown(sid, 80)})."
    hits = transcript_hits(sid)
    if not hits:
        return None, (f"no transcript found for conversation {sid}. Give its ID, a short ID only one "
                      "conversation starts with, or its transcript's path.")
    if len(hits) > 1:
        return hits[0], (f"conversation {sid} has {len(hits)} transcripts; reading "
                         f"{home_path(str(hits[0]))}. Give a path to read another.")
    return hits[0], ""


def inspect_lines(r: dict) -> list[str]:
    """The report as text. Each heading names where its numbers came from."""
    def where(item):
        return "  (a subagent)" if item["scope"] != "main" else ""

    files = f", and {r['subagents']} subagent transcript{'s' * (r['subagents'] != 1)}" if r["subagents"] else ""
    out = [f"Conversation {r['session']} ({home_path(r['path'])}{files})",
           "Requests [transcript, counted]",
           f"  main {r['requests']['main']}, subagents {r['requests']['subagents']}"]
    if r["models"]:
        out.append("  " + ", ".join(f"{m} {c}" for m, c in r["models"].items()))

    out.append("Tokens [transcript, measured: the API's own counts]")
    out.append("  Of the requests whose response is in the transcript. Claude Code also makes requests it")
    out.append("  does not write there, so its own totals can be higher.")
    out.append(f"  {'':<10}{'input':>12}{'cache write':>14}{'cache read':>14}{'output':>12}")
    total = {k: r["tokens"]["main"][k] + r["tokens"]["subagents"][k] for k in r["tokens"]["main"]}
    for name, t in (("main", r["tokens"]["main"]), ("subagents", r["tokens"]["subagents"]), ("total", total)):
        out.append(f"  {name:<10}{t['input']:>12,}{t['cache_write']:>14,}{t['cache_read']:>14,}{t['output']:>12,}")

    out.append("Context, main conversation [transcript, measured: input, cache write and cache read per request]")
    c = r["context"]
    out.append(f"  first {c['first']:,}, peak {c['peak']:,}, last {c['last']:,} tokens" if c else "  no request yet")
    for k in r["compactions"]:
        out.append(f"  compacted ({k['trigger']}): {k['before']:,} -> {k['after']:,} tokens{where(k)}")

    ch = r["cache"]
    if not ch:
        out.append("Cache, main conversation [status line: no reading of it yet]")
    else:
        when = f", last reading {clock(ch['at'])}" if ch["at"] else ""
        out.append(f"Cache, main conversation [status line, measured{when}]")
        parts = [f"{ch['hit']:.0%} of input read from the cache" if ch["hit"] is not None else "",
                 f"{ch['misses']:g} misses" if ch["misses"] is not None else "",
                 f"{ch['recache']:,} tokens cached again by them" if ch["recache"] is not None else ""]
        out.append("  " + ", ".join(p for p in parts if p))

    def section(head, rows):
        out.append(head)
        out.extend(rows or ["  none"])

    def times(n):
        return f"{n} more time{'s' * (n != 1)}"

    section("Largest tool outputs [transcript: characters counted; ≈ tokens estimated as characters / 4]",
            [f"  {o['chars']:>10,} chars {'≈' + format(o['chars'] // 4, ','):>9} tokens  {o['tool']} {o['what']}"
             .rstrip() + where(o) for o in r["outputs"]])
    section("Tool calls [transcript, counted]",
            [f"  {t['tool']}: {t['calls']} call{'s' * (t['calls'] != 1)}, {t['chars']:,} chars{where(t)}"
             for t in r["by_tool"]])
    s = r["search"]
    seen = {1: "a .codegraph/ index at or above the cwd", 0: "no .codegraph/ index at or above the cwd",
            None: "no cwd in the transcript to look for an index"}[s["codegraph_index"]]
    # The advice only when no index was seen: a folder at or above the cwd, or the tool called, means it is there to use.
    advise = s["search_calls"] >= SEARCH_HEAVY and not s["codegraph_index"] and not s["codegraph_calls"]
    section(f"Search [transcript, counted: {', '.join(SEARCHES)}, and Bash {', '.join(SEARCH_PROGRAMS)}]",
            [f"  {s['search_calls']:,} search{'es' * (s['search_calls'] != 1)}, {s['search_share']:.0%} of calls, "
             f"{s['search_chars']:,} chars",
             f"  longest chain {s['search_chain_max']}, mean {s['search_chain_mean']:.1f}",
             f"  {s['search_big']:,} result{'s' * (s['search_big'] != 1)} over {SEARCH_BIG:,} chars, "
             f"{s['search_big_share']:.0%} of search output",
             f"  CodeGraph: {s['codegraph_calls']} call{'s' * (s['codegraph_calls'] != 1)}, {seen}"]
            + (["  " + search_advice(s)] if advise else []))
    section("Read again, with no edit or compaction in between [transcript, inferred: "
            "files changed through Bash are not seen]",
            [f"  {x['path']}: {times(x['repeats'])}, {x['chars']:,} chars"
             + (f" and {x['images']} image{'s' * (x['images'] != 1)}" if x["images"] else "") + where(x)
             for x in r["rereads"]])
    section("The same search again, with no edit or compaction in between [transcript, inferred: "
            "files changed through Bash are not seen]",
            [f"  {s['tool']}{' in ' + s['where'] if s['where'] else ''}: {times(s['repeats'])}{where(s)}"
             for s in r["searches"]])

    g = r["guard"]
    if g is None:
        out.append("Guard [longhaul guard: no record: the hook was off, or saw no whole Read, cat or search]")
    else:
        section("Guard [longhaul guard, counted: calls it warned about or blocked]",
                [f"  {label}: {g[k]} ({g['bytes'][k]:,} bytes)" for k, label in
                 (("repeat", "read again unchanged"), ("big", "whole read of a big file"), ("cat", "cat of a big file"))
                 if g[k]])

    out.append("Failures [transcript, counted]")
    out.append("  tool errors: " + (", ".join(f"{t} {c}" for t, c in r["tool_errors"].items()) or "none"))
    out.append("  API errors: " + (", ".join(f"{e} {c}" for e, c in r["api_errors"].items()) or "none"))
    if r["unreadable"]:
        out.append(f"  {r['unreadable']} transcript line{'s' * (r['unreadable'] != 1)} could not be read "
                   "and are not counted")
    return out


def inspect_records(r: dict) -> list[dict]:
    """The same numbers as flat records, for comparing runs (handover §8):
    one number each, with its session, what it is of (`item`: a model,
    tool, file or error kind, else None), where it came from and how sure
    it is. No totals: a total next to main and subagents would be counted
    again by anyone adding a metric up."""
    out = []

    def add(metric, value, unit, scope, source, confidence, item=None):
        out.append({"session": r["session"], "metric": metric, "scope": scope, "item": item,
                    "value": value, "unit": unit, "source": source, "confidence": confidence})

    add("subagent_transcripts", r["subagents"], "files", "subagents", "transcript", "counted")
    for scope, n in r["requests"].items():
        add("requests", n, "requests", scope, "transcript", "counted")
    for model, n in r["models"].items():
        add("model_requests", n, "requests", "all", "transcript", "counted", model)
    for scope, kinds in r["tokens"].items():
        for kind, n in kinds.items():
            add(f"{kind}_tokens", n, "tokens", scope, "transcript", "measured")
    for k, n in (r["context"] or {}).items():
        add(f"context_{k}", n, "tokens", "main", "transcript", "measured")
    for i, c in enumerate(r["compactions"], 1):
        add("compaction_before", c["before"], "tokens", c["scope"], "transcript", "measured", f"{i} {c['trigger']}")
        add("compaction_after", c["after"], "tokens", c["scope"], "transcript", "measured", f"{i} {c['trigger']}")
    for key, metric, unit in (("hit", "cache_hit_ratio", "ratio"), ("misses", "cache_misses", "requests"),
                              ("recache", "cache_recache_tokens", "tokens")):
        if r["cache"] and r["cache"][key] is not None:
            add(metric, r["cache"][key], unit, "main", "statusline", "measured")
    for o in r["outputs"]:
        item = f"{o['tool']} {o['what']}".strip()
        add("tool_output_chars", o["chars"], "chars", o["scope"], "transcript", "counted", item)
        add("tool_output_tokens", o["chars"] // 4, "tokens", o["scope"], "estimate", "estimated", item)
    for t in r["by_tool"]:
        add("tool_calls", t["calls"], "calls", t["scope"], "transcript", "counted", t["tool"])
        add("tool_chars", t["chars"], "chars", t["scope"], "transcript", "counted", t["tool"])
    units = {"search_calls": "calls", "search_share": "ratio", "search_chars": "chars", "search_chain_max": "searches",
             "search_chain_mean": "searches", "search_big": "results", "search_big_share": "ratio",
             "codegraph_calls": "calls", "codegraph_index": "indexes"}
    for metric, value in r["search"].items():
        if value is not None:  # codegraph_index, when the transcript carries no cwd to look under
            add(metric, value, units[metric], "all", "transcript", "counted")
    for x in r["rereads"]:
        add("repeated_reads", x["repeats"], "reads", x["scope"], "transcript", "inferred", x["path"])
        add("repeated_read_chars", x["chars"], "chars", x["scope"], "transcript", "inferred", x["path"])
        if x["images"]:
            add("repeated_read_images", x["images"], "images", x["scope"], "transcript", "inferred", x["path"])
    for s in r["searches"]:
        add("repeated_searches", s["repeats"], "searches", s["scope"], "transcript", "inferred",
            f"{s['tool']} {s['where']}".strip())
    for tool, n in r["tool_errors"].items():
        add("tool_errors", n, "errors", "all", "transcript", "counted", tool)
    for kind, n in r["api_errors"].items():
        add("api_errors", n, "errors", "all", "transcript", "counted", kind)
    for k in GUARD_RULES if r["guard"] else ():
        add("guard_fired", r["guard"][k], "calls", "all", "guard", "counted", k)
        add("guard_bytes", r["guard"]["bytes"][k], "bytes", "all", "guard", "counted", k)
    add("transcript_lines", r["lines"], "lines", "all", "transcript", "counted")
    add("unreadable_lines", r["unreadable"], "lines", "all", "transcript", "counted")
    return out


def inspect(opts) -> int:
    path, note = find_transcript(opts.target)
    if note:
        print(f"longhaul inspect: {note}", file=sys.stderr)
    if not path:
        return 1
    try:
        report = inspect_transcript(path)
    except OSError as e:  # the main transcript or one of its subagents'
        print(f"longhaul inspect: could not read {e.filename or path}: {e.strerror or e}", file=sys.stderr)
        return 1
    if not report["lines"]:  # an all-zero report would look like a real one
        print(f"longhaul inspect: no transcript lines in {path}: it is empty, or not a Claude Code transcript.",
              file=sys.stderr)
        return 1
    if opts.json:  # one record per line, so runs can be appended to one file
        print("\n".join(json.dumps(r) for r in inspect_records(report)))
    else:
        print("\n".join(inspect_lines(report)))
    return 0


# ---------------------------------------------------------------- guard
#
# `longhaul guard`: the plugin's hook (hooks/hooks.json), run before every
# Read and Bash call, on SessionStart after a compaction or a clear, and
# at a session's start. It reads the hook's JSON on stdin and answers
# with JSON, or with nothing (code.claude.com/docs/en/hooks, read
# 2026-10-20). In warn mode, the default, it adds context and the call
# runs; in block mode (LONGHAUL_GUARD=block) it denies with the same
# text; off, it does nothing. Whatever goes wrong it prints nothing and
# exits 0: the engine lets a call run on any exit but 2, and a guard must
# never stop a call it did not mean to stop. Its record holds numbers
# only: file paths, sizes, times and counts; never a command line or a
# file's text. A subagent is its own context (the hook input names it),
# so its first read of a file is never a repeat of the main
# conversation's. After a compaction or a clear nothing read before is in
# the context, so the record forgets its reads and keeps its counts.
# In a project with a CodeGraph index (.codegraph/) the guard also names
# the index to the model, at startup and once a context's Bash searches
# (SEARCH_PROGRAMS, inspect's definition) reach GUARD_SEARCHES (Sprint 6,
# G3: the installed index's tool was called 0 times in five weeks of
# sessions, 87% of whose searches ran inside subagents); it is context,
# never a denial, the record keeps the count and never the pattern, and
# without the index it says nothing of it.


GUARD_BIG = setting("LONGHAUL_GUARD_BIG", 20_000, int, 1)  # bytes: a whole read above this is waste
GUARD_RULES = ("repeat", "big", "cat")
GUARD_ID = r"[A-Za-z0-9_-]{1,100}"  # a session's or an agent's id; "+" joins them in a record's name
GUARD_KEEP = 500  # reads kept per record; the oldest go first
GUARD_NO_PART = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".pdf"}  # offset and limit are no advice
GUARD_CODEGRAPH = ("This project has a CodeGraph index (.codegraph/). For a symbol or dependency question (who calls X, "
                   "what does Y depend on, where is Z defined), `codegraph explore \"<question>\"` or the "
                   "codegraph_explore tool answers in one call with the source and the call paths; keep grep and rg "
                   "for plain text.")
GUARD_SEARCHES = 20  # a context's searches before the index is named to it, once


def guard_mode() -> str:
    mode = os.environ.get("LONGHAUL_GUARD") or "warn"
    return mode if mode in ("warn", "block", "off") else "warn"


def guard_record_path(data: dict):
    """LONGHAUL_HOME/guard/<session>[+<agent>].json, or None for an input
    that names no plain session (nothing is recorded, nothing is said).
    No id can hold "+", so a subagent's record is never a session's."""
    sid, agent = data.get("session_id"), data.get("agent_id")
    if not isinstance(sid, str) or not re.fullmatch(GUARD_ID, sid):
        return None
    if agent is not None:
        if not isinstance(agent, str) or not re.fullmatch(GUARD_ID, agent):
            return None
        sid += f"+{agent}"
    return LONGHAUL_HOME / "guard" / f"{sid}.json"


def guard_targets(data: dict) -> list:
    """(how, file, whole?) for each file a Read or a bare Bash `cat` would
    put into the context. Of a Bash command only its first program and
    that program's file arguments are looked at; the command itself is
    never kept. A first line with a shell operator is left alone: a piped
    cat is filtered, a redirected one is a write."""
    tool, args = data.get("tool_name"), dict_at(data, "tool_input")
    if tool == "Read":
        path = args.get("file_path")
        if not isinstance(path, str) or not os.path.isabs(path):
            return []
        part = any(args.get(k) for k in ("offset", "limit", "pages"))
        return [("Read", Path(path), not part)]
    if tool == "Bash" and isinstance(args.get("command"), str):
        line = args["command"].split("\n", 1)[0]
        if re.search(r"[|><;&]", line):
            return []
        try:
            words = shlex.split(line)
        except ValueError:
            return []
        while words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words.pop(0)
        if not words or words[0].rsplit("/", 1)[-1] != "cat":
            return []
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
        return [("cat", Path(os.path.join(cwd, w)), True) for w in words[1:] if not w.startswith("-")][:20]
    return []


def guard_check(data, mode: str, now: float | None = None):
    """The guard's answer for one hook input: the hook's JSON, or None to
    let the call run unremarked. A PreToolUse is checked and recorded, a
    Bash search counted and, at a context's GUARD_SEARCHES-th in an indexed
    project, answered once with the index; a SessionStart for compact or
    clear empties the record's reads, and one for startup names the
    project's CodeGraph index, if it has one."""
    if mode == "off" or not isinstance(data, dict):
        return None
    if data.get("hook_event_name") == "SessionStart":
        if data.get("source") in ("compact", "clear"):
            guard_forget(data)
        elif data.get("source") == "startup" and data.get("agent_id") is None and codegraph_index_at(data.get("cwd")):
            # The main conversation only: a subagent's start is not the
            # session's. Without the index there is nothing to say here; the
            # install advice is the inspect report's, not every session's.
            log(f"guard: named the CodeGraph index at {home_path(data['cwd'])} at startup")
            return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": GUARD_CODEGRAPH}}
        return None
    # A search is a Bash whose first program is one of inspect's; its
    # program is not cat, so it has no targets. Of its command line only
    # the program is looked at, and the record keeps the count alone.
    search = data.get("tool_name") == "Bash" and program(dict_at(data, "tool_input").get("command")) in SEARCH_PROGRAMS
    targets = guard_targets(data)
    record = guard_record_path(data) if targets or search else None
    if record is None:
        return None
    now = time.time() if now is None else now
    nudge = None
    with locked("guard"):  # parallel tool calls run their hooks at once
        kept = read_json(record)
        kept = kept if isinstance(kept, dict) else {}  # corrupt or missing: a fresh record
        reads = {k: v for k, v in dict_at(kept, "reads").items() if isinstance(v, dict)}
        fired = {k: as_int(dict_at(kept, "fired").get(k)) for k in GUARD_RULES}
        cost = {k: as_int(dict_at(kept, "bytes").get(k)) for k in GUARD_RULES}
        searches, nudged = as_int(kept.get("searches")) + (1 if search else 0), kept.get("nudged") is True
        if search and searches >= GUARD_SEARCHES and not nudged and codegraph_index_at(data.get("cwd")):
            nudged = True  # once per context: the count goes on, the note does not
            nudge = f"{GUARD_CODEGRAPH} This context has made {searches} searches."
        notes, seen = [], {}
        for how, path, whole in targets:
            try:
                if not path.is_file():
                    continue
                st = path.stat()
            except OSError:
                continue
            size, shown = st.st_size, home_path(str(path))
            # cp -p, rsync -a and touch -r keep the size and mtime of a file
            # they change; the ctime they cannot. A record from before the
            # ctime was kept has none, and is not a repeat once.
            stamp = {"size": size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
            before = reads.get(str(path), {})
            if whole and all(before.get(k) == v for k, v in stamp.items()):
                fired["repeat"] += 1
                cost["repeat"] += size
                at = as_number(before.get("at"))
                if at is None:  # a corrupt time: still a repeat
                    when = "earlier"
                else:
                    ago = max(0, int(now - at))
                    when = f"{ago // 60} min ago" if ago >= 60 else f"{ago} s ago"
                notes.append(f"{shown} ({size:,} bytes) was read in full {when} and has not changed since: "
                             "it is already in the context. For a part of it, use Read with offset and limit, or "
                             "grep -n for the lines you need.")
            elif whole and size > GUARD_BIG and how == "cat":
                fired["cat"] += 1
                cost["cat"] += size
                notes.append(f"`cat` of {shown} ({size:,} bytes) puts the whole file into the context. "
                             "Use head, sed -n with a range, or grep -n for the part you need.")
            elif whole and size > GUARD_BIG and path.suffix.lower() not in GUARD_NO_PART:
                fired["big"] += 1
                cost["big"] += size
                notes.append(f"{shown} is {size:,} bytes, and a whole Read puts all of it into the context. "
                             "Read it with offset and limit, or grep -n for the part you need.")
            if whole:
                seen[str(path)] = {**stamp, "at": round(now, 1)}
        if not (mode == "block" and notes):  # a denied call read nothing
            reads.update(seen)
        if len(reads) > GUARD_KEEP:
            reads = dict(sorted(reads.items(), key=lambda kv: as_number(kv[1].get("at")) or 0)[-GUARD_KEEP:])
        write_json(record, {"reads": reads, "fired": fired, "bytes": cost, "searches": searches, "nudged": nudged})
    if nudge:  # a search fires no rule, so the note is the whole answer; in block mode too, it is never a denial
        log(f"guard: {record.stem} was told of the CodeGraph index after {searches} searches")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": nudge}}
    if not notes:
        return None
    if mode == "block":  # how to turn the guard off is for the user, not the model
        text = "longhaul guard blocked this call. " + " ".join(notes) + " Make the narrower call instead."
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": text}}
    text = "longhaul guard (advice; the call went ahead): " + " ".join(notes)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def guard_records(sid: str) -> list:
    """A conversation's record and its subagents'."""
    folder = LONGHAUL_HOME / "guard"
    return [folder / f"{sid}.json"] + sorted(folder.glob(f"{sid}+*.json"))


def guard_forget(data: dict) -> None:
    """After a compaction or a clear: the session's records, its subagents'
    too, forget their reads and their search count (the note goes with it:
    the new context has made none) and keep their counts. A record that
    cannot be rewritten is removed instead, counts and all: kept, its
    reads would have block mode deny a read of a file no longer in the
    context."""
    sid = data.get("session_id")
    if not isinstance(sid, str) or not re.fullmatch(GUARD_ID, sid):
        return
    with locked("guard"):
        for file in guard_records(sid):
            kept = read_json(file) if file.is_file() else None
            if not (isinstance(kept, dict) and (dict_at(kept, "reads") or kept.get("searches") or kept.get("nudged"))):
                continue
            try:
                write_json(file, {"reads": {}, "fired": dict_at(kept, "fired"), "bytes": dict_at(kept, "bytes"),
                                  "searches": 0, "nudged": False})
                continue
            except OSError as e:
                problem = e.strerror or e
            try:  # an unlink needs no room on the disk, where a new file does
                file.unlink()
                log(f"guard: {file.name} could not be rewritten ({problem}), so it was removed, its counts with it")
            except OSError as e:
                log(f"guard: the guard's record could not be cleared; block mode may deny a read wrongly until it is "
                    f"({file.name}: {e.strerror or e})")


def guard_counts(sid: str):
    """What the guard counted for a conversation, its subagents included;
    None when it kept no record (the hook was off, or saw no whole Read,
    cat or search). The search count is the record's own: inspect counts
    searches from the transcript, so it is not passed on here."""
    if not re.fullmatch(GUARD_ID, sid or ""):
        return None
    total = {k: 0 for k in GUARD_RULES}
    total["bytes"] = {k: 0 for k in GUARD_RULES}
    found = False
    for file in guard_records(sid):
        kept = read_json(file) if file.is_file() else None
        if not isinstance(kept, dict):
            continue
        found = True
        for k in GUARD_RULES:
            total[k] += as_int(dict_at(kept, "fired").get(k))
            total["bytes"][k] += as_int(dict_at(kept, "bytes").get(k))
    return total if found else None


def run_hook(check, name: str) -> int:
    """A hook command: the engine's JSON on stdin, `check`'s answer as
    JSON on stdout or nothing, exit 0 always: a hook must never stop
    what it did not mean to. `check` takes the input as an object ({}
    for none); what it raises is logged under `name`, and the engine
    goes on. A SystemExit is not an Exception and ends the command."""
    try:
        raw = sys.stdin.read(1_000_000)
        answer = check(json.loads(raw) if raw.strip() else {})
        if answer:
            print(json.dumps(answer))
    except Exception as e:
        log(f"{name} hook: no answer ({e!r}); the engine goes on")
    return 0


def guard(_opts) -> int:
    """The hook command: stdin in, JSON or nothing out, exit 0 always."""
    return run_hook(lambda data: guard_check(data, guard_mode()), "guard")


# ----------------------------------------------------------- checkpoint
#
# `longhaul checkpoint save <session>`: the model's own structured state,
# written at a moment the user chose (skills/checkpoint/SKILL.md) as
# LONGHAUL_HOME/checkpoints/<session>.json, whole and private, after a
# check against schema v1. The skill writes the state with the Write
# tool as <session>.state.json beside it and passes --file (its
# permission rule does not match a heredoc: live check, 2026-10-20); a
# save that took it removes that copy (0644, the model's text beside
# the 0600 checkpoint). `longhaul checkpoint`, with no action, is the
# plugin's second SessionStart hook: after a compaction or a resume it
# puts the state back through additionalContext, with its age and which
# of the files it names changed since. The engine's own summary is made
# when the context is already full; this one is made when the model
# knows what matters, and it survives a crash and a move to another
# account. (A /clear gives the session a new id, so the hook, which also
# answers a clear, finds nothing under it; until the live check says
# otherwise no text promises a return after one.) The file holds the
# model's text on purpose. It is
# read by the hook alone; the log, inspect and every line a person or the
# model reads name it by session, size, field and reason, never by its
# content. Like the guard, the hook prints nothing and exits 0 whatever
# goes wrong: a session start is never stopped by its checkpoint.

CHECKPOINT_TEXTS = ("goal", "next_action")  # texts, required
CHECKPOINT_LISTS = ("decisions", "done", "in_progress", "blocked", "changed_files", "verification")  # lists of texts
CHECKPOINT_TEXT_MAX = 1_000  # characters per text
CHECKPOINT_ITEMS_MAX = 20  # per list
# The whole file. The engine caps a hook's additionalContext at 10,000
# characters, past which the model gets a file path and a preview instead
# of the text; the largest file this takes comes back as under 10,000.
CHECKPOINT_BYTES_MAX = 8_000
CHECKPOINT_FILE_MAX = 4 * CHECKPOINT_BYTES_MAX  # what the hook will read of a file: a save never writes more than the cap
CHECKPOINT_SECRET_SHAPES = ("sk-ant-", "AKIA", "-----BEGIN", "ghp_")  # the start of a known kind of credential
CHECKPOINT_CLI = ("schema_version", "session_id", "written_at", "cwd")  # set by the CLI, never by the model
CHECKPOINT_SOURCES = ("compact", "clear", "resume")  # the session starts whose context is gone


def checkpoint_path(sid: str | None):
    """LONGHAUL_HOME/checkpoints/<session>.json, or None for anything but
    a plain session id (the guard's rule for ids)."""
    if not isinstance(sid, str) or not re.fullmatch(GUARD_ID, sid):
        return None
    return LONGHAUL_HOME / "checkpoints" / f"{sid}.json"


def checkpoint_validate(state):
    """The model's part of a checkpoint against schema v1: (the state,
    cleaned, with every list present, None, None), or (None, why not,
    why not for the log). The reason names a key and a length and never
    a value: it is printed, logged and read by the model. An unknown
    key's name is the model's text too: the line printed names it when
    it is not shaped like a credential, the line logged never does.
    Control characters go, as shown() prints them, so a text can never
    move or retitle a terminal."""
    if not isinstance(state, dict):
        return None, "the state is not a JSON object", "the state is not a JSON object"
    for key in state:
        if key not in CHECKPOINT_TEXTS + CHECKPOINT_LISTS:
            name = ("(looks like a credential)" if any(shape in key for shape in CHECKPOINT_SECRET_SHAPES)
                    else repr(shown(key, 60)))
            return None, f"unknown key {name}", f"unknown key ({len(key)} characters)"

    def problem(name, value):
        if not isinstance(value, str):
            return f"{name} is not text"
        if not value.strip():
            return f"{name} is empty"
        if len(value) > CHECKPOINT_TEXT_MAX:
            return f"{name} is {len(value):,} characters; the cap is {CHECKPOINT_TEXT_MAX:,}"
        if any(shape in value for shape in CHECKPOINT_SECRET_SHAPES):
            return f"{name} holds what looks like a credential"
        return None

    clean = {}
    for key in CHECKPOINT_TEXTS:
        if key not in state:
            return None, f"{key} is missing", f"{key} is missing"
        why = problem(key, state[key])
        if why:
            return None, why, why
        clean[key] = shown(state[key], CHECKPOINT_TEXT_MAX)
    for key in CHECKPOINT_LISTS:
        items = state.get(key, [])
        if not isinstance(items, list):
            return None, f"{key} is not a list", f"{key} is not a list"
        if len(items) > CHECKPOINT_ITEMS_MAX:
            why = f"{key} has {len(items)} items; the cap is {CHECKPOINT_ITEMS_MAX}"
            return None, why, why
        for i, item in enumerate(items):
            why = problem(f"{key}[{i}]", item)
            if why:
                return None, why, why
        clean[key] = [shown(item, CHECKPOINT_TEXT_MAX) for item in items]
    return clean, None, None


def remove_state_file(state_file: str, sid) -> bool | None:
    """The skill's state file, gone once the save has read it, saved or
    refused: the one under LONGHAUL_HOME/checkpoints named
    <anything>.state.json (a session id holds no dot, so never a
    checkpoint's own name), the Write tool's copy of the model's text,
    with the user's default mode, beside the 0600 checkpoint. Left after
    a refusal it kept that text, a credential among it perhaps, for good
    (review, Sprint 5). Resolved first, so a link to a file elsewhere is
    left like any other file the user named. True when it went; None
    when it is not the skill's file, which is left without a word; False
    when it is and would not go, which the log says."""
    try:
        file = Path(state_file).resolve()
        if not (file.name.endswith(".state.json") and file.parent == (LONGHAUL_HOME / "checkpoints").resolve()):
            return None
        file.unlink()
        return True
    except (OSError, RuntimeError) as e:  # gone already, a folder that will not let go; a loop of links is RuntimeError from resolve() on 3.10 and 3.11
        log(f"checkpoint for {shown(sid or '?', 100)}: the state file was not removed: {getattr(e, 'strerror', None) or e}")
        return False


def checkpoint_save(sid: str | None, raw: str, cwd: str, here: str | None, state_file: str | None = None) -> int:
    """`longhaul checkpoint save [session] [--file PATH]`: the state, JSON
    on stdin or in `state_file`, checked, capped and written whole and
    private (write_json: mkstemp, 0600, then replace, in a checkpoints/
    folder made 0700 when the save makes it), with one line saying so; or
    refused with one line naming the problem, exit 2, nothing written.
    `here` is the session the command runs in, from the Bash tool's
    environment, or None in a terminal: it is the session when none is
    given, and the only one a save may name, so no session can plant a
    state for another (the skill's command passes no id for that reason).
    Saved or refused, the skill's state file goes (remove_state_file), and
    a refusal says so: the model writes it again."""
    sid = sid or here
    path = checkpoint_path(sid)
    state = problem = logged = None
    if path is None:
        problem = ("save needs the session id: pass it, or run from inside a Claude Code session (CLAUDE_CODE_SESSION_ID)"
                   if sid is None else "the session id must be letters, digits, - or _ (at most 100)")
    elif here and sid != here:
        problem = f"the session id is not this session's: {sid} was given and this session is {shown(here, 100)}"
    elif not raw.strip():
        problem = "nothing on stdin: the state goes there, as JSON"
    else:
        try:
            state, problem, logged = checkpoint_validate(json.loads(raw))
        except json.JSONDecodeError as e:
            problem = f"not JSON: {e.msg} at line {e.lineno} column {e.colno}"
    if state is not None:
        record = {"schema_version": 1, "session_id": sid, "written_at": time.time(), "cwd": cwd, **state}
        size = len(json.dumps(record, indent=2)) + 1  # what write_json writes: ASCII, so characters are bytes
        if size > CHECKPOINT_BYTES_MAX:
            problem = f"{size:,} bytes in all; the cap is {CHECKPOINT_BYTES_MAX:,}"
        else:
            try:
                if not path.parent.is_dir():
                    # write_json() would make the folder with the umask's mode
                    # (0755): private, like its files, when the save makes it.
                    # One that is there already (the Write tool makes it first
                    # in the skill's flow) is the user's and keeps its mode.
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.parent.chmod(0o700)
                write_json(path, record)
            except OSError as e:  # a full disk, a home that is not a writable folder
                problem = f"could not write the file: {e.strerror or e}"
    removed = remove_state_file(state_file, sid) if state_file is not None else None
    if problem:
        if removed:
            problem += "; the state file was removed, write it again"
        print(f"longhaul checkpoint: not saved: {problem}", file=sys.stderr)
        if path is not None:
            log(f"checkpoint for {sid} not saved: {logged or problem}")
        return 2
    log(f"checkpoint for {sid} saved ({size:,} bytes)")
    print(f"longhaul checkpoint: saved for {sid} ({size:,} bytes); it comes back after a compaction or a resume"
          f"{'; the state file could not be removed' if removed is False else ''}")
    return 0


def checkpoint_load(raw: bytes, sid: str, now: float):
    """A checkpoint file read back: (the record, its model's part cleaned,
    None), or (None, why it is not this session's whole checkpoint, for
    the log). A half-written file is not JSON; a hand-edited one is held
    to the schema as a save is, and to what the text will do with it: a
    time fromtimestamp() takes, a path os.stat() takes."""
    if len(raw) > CHECKPOINT_FILE_MAX:
        return None, f"over {CHECKPOINT_FILE_MAX:,} bytes"
    try:
        kept = json.loads(raw)
    except (ValueError, RecursionError):  # a nest deeper than json reads
        return None, f"not JSON ({len(raw)} bytes)"
    if not isinstance(kept, dict):
        return None, "not an object"
    if as_int(kept.get("schema_version")) != 1:
        return None, "schema_version is not 1"
    if kept.get("session_id") != sid:
        return None, "the session_id inside is another session's"
    at = as_number(kept.get("written_at"))
    if at is None:
        return None, "written_at is not a number"
    if not 0 <= at <= now + 86400:
        return None, "written_at is out of range"
    cwd = kept.get("cwd")
    if not isinstance(cwd, str) or not cwd or shown(cwd, 1024) != cwd:  # a NUL would raise ValueError, not OSError
        return None, "cwd is not a plain path"
    state, _, logged = checkpoint_validate({k: v for k, v in kept.items() if k not in CHECKPOINT_CLI})
    if logged:
        return None, logged
    return {**kept, **state, "written_at": at}, None


def checkpoint_text(kept: dict, now: float) -> str:
    """What the model reads after a compaction or a resume: when and
    where the checkpoint was written, each field by its name in the
    schema, and which of the files it names changed (an mtime after
    written_at) or went since. Staleness is shown, not withheld: there
    is no age cutoff."""
    at, cwd = kept["written_at"], kept["cwd"]
    when = dt.datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")
    lines = [f"longhaul checkpoint, written {when} ({ago_text(now - at)}) in {home_path(cwd)}:"]
    for key in CHECKPOINT_TEXTS:
        lines.append(f"{key}: {kept[key]}")
    for key in CHECKPOINT_LISTS:
        lines.append(f"{key}:" + ("".join(f"\n- {item}" for item in kept[key]) if kept[key] else " (none)"))
    changed = []
    for name in kept["changed_files"]:
        try:
            if os.stat(os.path.join(cwd, name)).st_mtime > at:
                changed.append(f"{name} (changed)")
        except FileNotFoundError:
            changed.append(f"{name} (gone)")
        except OSError:
            changed.append(f"{name} (could not be checked)")
    if changed:
        lines.append("Changed since the checkpoint: " + ", ".join(changed) + "; check them before trusting this.")
    elif kept["changed_files"]:
        n = len(kept["changed_files"])
        lines.append(f"Changed since the checkpoint: none of the {n} file{'s' if n != 1 else ''} named.")
    else:
        lines.append("Changed since the checkpoint: no files were named.")
    return "\n".join(lines)


def checkpoint_check(data, now: float | None = None):
    """The hook's answer for one input: the JSON that puts the session's
    checkpoint back, or None. Only a SessionStart for compact, clear or
    resume of a plain session has one (a subagent has no checkpoint),
    and only from a file that is this session's, schema 1 and whole; a
    file that is not is one log line naming the reason, never the text.
    No file is the usual session start, and not worth a line."""
    if not isinstance(data, dict) or data.get("hook_event_name") != "SessionStart":
        return None
    if data.get("source") not in CHECKPOINT_SOURCES or data.get("agent_id") is not None:
        return None
    sid = data.get("session_id")
    path = checkpoint_path(sid)
    if path is None:
        return None
    try:
        with path.open("rb") as f:
            raw = f.read(CHECKPOINT_FILE_MAX + 1)  # a hand-made file can be any size; past the cap it is not read
    except FileNotFoundError:
        return None
    except OSError as e:
        log(f"checkpoint for {sid} not put back: {e.strerror or e}")
        return None
    now = time.time() if now is None else now
    kept, problem = checkpoint_load(raw, sid, now)
    if problem:
        log(f"checkpoint for {sid} not put back: {problem}")
        return None
    text = checkpoint_text(kept, now)  # before the log line: a text that fails leaves no "put back" behind
    log(f"checkpoint for {sid} put back after {data['source']} ({len(raw)} bytes, "
        f"written {max(0, int(now - kept['written_at']))} s earlier)")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def checkpoint(opts) -> int:
    """`longhaul checkpoint save [session] [--file PATH]`, or with no action
    the hook: stdin in, JSON or nothing out, exit 0 always."""
    if sys.stdin.isatty() and not (opts.action == "save" and opts.file):
        print("longhaul checkpoint: the state (save) or the hook's JSON goes on stdin; nothing to read from a terminal",
              file=sys.stderr)
        return 2
    here = os.environ.get("CLAUDE_CODE_SESSION_ID") or None  # the session this runs in; SESSION_ENV names it
    if opts.action == "save":
        try:
            cwd = os.getcwd()
        except OSError as e:  # deleted under the shell
            print(f"longhaul checkpoint: not saved: could not read the working directory: {e.strerror or e}", file=sys.stderr)
            return 2
        if opts.file:
            try:
                with open(opts.file, encoding="utf-8") as f:
                    raw = f.read(1_000_000)
            except (OSError, ValueError) as e:  # a UnicodeDecodeError is a ValueError
                why = e.strerror if isinstance(e, OSError) and e.strerror else "not UTF-8 text"
                # A refusal too: the skill's state file goes with it (a file
                # that could not be opened is not there to remove).
                if isinstance(e, ValueError) and remove_state_file(opts.file, opts.session or here):
                    why += "; the state file was removed, write it again"
                print(f"longhaul checkpoint: not saved: could not read {home_path(opts.file) or '?'}: {why}", file=sys.stderr)
                return 2
        else:
            raw = sys.stdin.read(1_000_000)
        return checkpoint_save(opts.session, raw, cwd, here, opts.file)

    def check(data):
        # Run by hand from inside session `here` for another session's
        # checkpoint (the shim refuses the hook form; python3 longhaul.py
        # does not): refuse, one line and exit 2. The engine's environment
        # never names a different session, so the real hook is untouched.
        sid = data.get("session_id") if isinstance(data, dict) else None
        if here and isinstance(sid, str) and sid != here:
            print(f"longhaul checkpoint: the hook is the engine's; from inside a session it runs for that session only, "
                  f"not for {shown(sid, 100)}", file=sys.stderr)
            log(f"checkpoint for {shown(sid, 100)} not put back: run by hand from inside session {shown(here, 100)}")
            raise SystemExit(2)
        return checkpoint_check(data)

    return run_hook(check, "checkpoint")


# ------------------------------------------------------------- VS Code
#
# The Claude Code VS Code extension (2.1.300, 2.1.301) runs the program in
# its claudeCode.claudeProcessWrapper setting with the bundled claude
# binary's path first and claude's own arguments after it: spawn(wrapper,
# [binary, ...args]) (extension.js: executableArgs: [binary],
# viaProcessWrapper). The setting has machine scope, so it covers every
# window on this Mac. Every claude the extension starts goes through it:
# chats (--input-format stream-json) and short jobs (auth status --json,
# auth logout, drop-worktree-registrations). With a wrapper set the
# extension stops following CLAUDE_CONFIG_DIR itself, so the account is the
# wrapper's to choose. longhaul-vscode (shell) runs longhaul_vscode.py, which runs
# wrap() and runs claude unchanged if longhaul cannot even be imported.
#
# The chat protocol, recorded from 2.1.300: the extension opens with a
# control_request "initialize" (its hook callback ids, its system prompt
# addition), answered by a control_response with the same request_id; a
# message is a {"type": "user", "uuid": ...} line, which claude replays
# (--replay-user-messages) before command_lifecycle, system/init (at the
# start of every turn), assistant (an "error" field on API errors),
# rate_limit_event (the account's usage) and result lines. A line's "type"
# is not always its first field.

VSCODE_CONFIG = LONGHAUL_HOME / "vscode.json"
AUTH_CACHE = LONGHAUL_HOME / "auth-cache.json"
# The panel starts a claude for every chat and every status check, and
# asking claude who is signed in takes about a second; a sign-in changed
# in the panel or a terminal shows within this long.
AUTH_TTL = setting("LONGHAUL_AUTH_TTL", 120, float)
# Requests the panel sends that change a running chat (extension.js
# 2.1.301), sent again to a claude started by a switch, in the order they
# came: the latest of each, per MCP server where they name one, and every
# apply_flag_settings (each sets some flags; the last 20 are kept).
LATEST = ("set_permission_mode", "set_model", "set_max_thinking_tokens", "mcp_set_servers")
PER_SERVER = ("mcp_toggle", "set_mcp_permission_mode_override")
EACH = ("apply_flag_settings",)
# Signing in happens inside a chat; signing out is a short job.
SIGN_IN = ("claude_authenticate", "claude_oauth_callback")
SIGN_IN_JOBS = (["auth", "login"], ["auth", "logout"], ["setup-token"])
HOLD_MAX = setting("LONGHAUL_HOLD_MAX_SECONDS", 20, float)
INIT_WAIT = setting("LONGHAUL_INIT_WAIT_SECONDS", 90, float)
RELAY_DEBUG = bool(os.environ.get("LONGHAUL_RELAY_DEBUG"))
# A line's "type" is not always its first field: a 2.1.300 result line
# starts with "duration_api_ms" and has "type":"result" 1,600 bytes in.
# Matching only the start of a line missed every result (2026-10-16).
_TYPE = {k: re.compile(rb'"type":\s*"' + k.encode() + rb'"') for k in (
    "result", "assistant", "stream_event", "control_response", "control_request", "auth_status")}


def has_type(line: bytes, kind: str) -> bool:
    return _TYPE[kind].search(line) is not None


def trace(direction: str, pid, line: bytes, note: str = "") -> None:
    """With LONGHAUL_RELAY_DEBUG set: one log line per message through the relay,
    its kind and ids only, never its text."""
    if not RELAY_DEBUG:
        return
    try:
        m = json.loads(line)
    except ValueError:
        log(f"relay {direction} {pid} not-json {note}")
        return
    if m.get("type") == "stream_event":
        return
    req = m.get("request") or {}
    res = m.get("response") or {}
    ids = m.get("request_id") or res.get("request_id") or m.get("uuid") or ""
    log(f"relay {direction} {pid} {m.get('type')}/{m.get('subtype') or req.get('subtype') or res.get('subtype') or ''} "
        f"{ids} {note}".rstrip())


def vscode_config() -> dict:
    """{"switch_mid_session": bool, "switch": "ask"|"auto"|"never"}.
    switch_mid_session off (the default): a panel chat only starts on the
    account longhaul picks. On, and only when it is the JSON value true: longhaul also
    stays in between, and moves the chat at a limit when the mode is auto
    (LONGHAUL_SWITCH in the wrapper's environment, then "switch" here, then
    "ask"; see panel_switch_mode)."""
    found = read_json(VSCODE_CONFIG)
    return {"switch_mid_session": False, **(found if isinstance(found, dict) else {})}


def panel_switch_mode() -> str:
    """The panel's switching: LONGHAUL_SWITCH in the wrapper's environment, then
    "switch" in vscode.json, then "ask". An unknown value never stops a
    chat: it runs and never moves, and the log says why (ADR-0002)."""
    value = os.environ.get("LONGHAUL_SWITCH") or vscode_config().get("switch") or "ask"
    if isinstance(value, str) and value in SWITCH_MODES:
        return value
    where = "LONGHAUL_SWITCH" if os.environ.get("LONGHAUL_SWITCH") else f'"switch" in {VSCODE_CONFIG}'
    log(f"panel: switching {value!r} (from {where}) is not ask, auto or never, so the chat runs and "
        "never moves. Set it to one of the three, or remove it.")
    return "never"


def is_chat(args: list[str]) -> bool:
    for i, a in enumerate(args):
        if a == "--input-format=stream-json" or (a == "--input-format" and args[i + 1:i + 2] == ["stream-json"]):
            return True
    return False


def resume_id(args: list[str]):
    for i, a in enumerate(args):
        if a.startswith("--resume="):
            return a.split("=", 1)[1]
        if a in ("--resume", "-r") and i + 1 < len(args) and not args[i + 1].startswith("-"):
            return args[i + 1]
    return None


def without_resume(args: list[str]) -> list[str]:
    """claude's arguments with every way of choosing a conversation taken
    out, so a switch can add --resume=<the conversation> alone."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        name = a.split("=", 1)[0]
        if name in ("--resume", "-r", "--session-id"):
            skip = "=" not in a
            continue
        if name in ("--continue", "-c", "--fork-session"):
            continue
        out.append(a)
    return out


def wrapper_env(acct: dict) -> dict:
    """The extension's own environment, kept whole: claude shapes itself
    for the panel by CLAUDE_CODE_ENTRYPOINT=claude-vscode and the rest.
    Only the account changes, and no key may outrank its sign-in."""
    env = os.environ.copy()
    for k in AUTH_ENV:
        env.pop(k, None)
    if acct.get("config_dir"):
        env["CLAUDE_CONFIG_DIR"] = str(config_of(acct))
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def forget_auth(acct: dict) -> None:
    with locked("auth"):
        cache = read_json(AUTH_CACHE) or {}
        if cache.pop(str(config_of(acct)), None) is not None:
            write_json(AUTH_CACHE, cache)


def signed_in_cached(acct: dict, binary: str) -> bool:
    """signed_in_as_expected for the panel, remembered for AUTH_TTL. Only a
    definite answer is remembered: a check that hangs or fails says
    nothing about the account, so it is trusted for this launch (a hang
    there would most likely stop its chat too) and asked again next time."""
    want = acct.get("email")
    if not want:
        return True
    key = str(config_of(acct))
    hit = (read_json(AUTH_CACHE) or {}).get(key) or {}
    fresh = time.time() - hit.get("at", 0) >= AUTH_TTL
    if fresh:
        status = auth_status(acct, binary)
        if status.get("unknown"):
            log(f"{acct['name']}: could not check who is signed in ({status.get('why')}); trusting it for this launch")
            return True
        hit = {"email": status.get("email"), "at": time.time()}
        with locked("auth"):
            cache = read_json(AUTH_CACHE) or {}
            cache[key] = hit
            write_json(AUTH_CACHE, cache)
    if (hit.get("email") or "").lower() == want.lower():
        return True
    if fresh:
        msg = f"{acct['name']} should be {want} but is signed in as {hit.get('email') or 'nobody'}; longhaul will not use it."
        log(msg)
        notify(msg)
    return False


def choose_for_panel(accounts: list[dict], binary: str, start: int = 0, state: dict | None = None,
                     wrong: set | None = None):
    """The account a panel claude runs on: pick()'s rules and the email
    check, also when every account is at a limit (an account signed in as
    someone else is never used, limited or not). (index, until) as pick()
    returns; (None, None) when no account is signed in as it should be."""
    state = load_state() if state is None else state
    wrong = set() if wrong is None else wrong
    seen = readings(accounts)
    while True:
        i, until = pick(accounts, state, start, skip=wrong, seen=seen)
        if i is None:
            return None, None
        if signed_in_cached(accounts[i], binary):
            return i, until
        wrong.add(accounts[i]["name"])


def wrap(argv: list[str]) -> int:
    """The extension's process wrapper. Runs claude as the extension asked
    on the account longhaul picks (a chat reopened by id: the account it last
    ran on, in ask and never); if anything in longhaul fails before the chat is
    under way, runs it unchanged, since a broken wrapper would stop every
    new chat in the panel."""
    global QUIET
    QUIET = True
    if argv and os.path.isabs(argv[0]) and os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK):
        binary, args = argv[0], argv[1:]
    else:  # a later extension may pass claude's arguments alone
        binary, args = shutil.which("claude") or str(HOME / ".local" / "bin" / "claude"), argv
    relay = None
    try:
        if any(args[:len(job)] == job for job in SIGN_IN_JOBS):
            # The extension signs out through a short job. Run on an account
            # longhaul manages, it would sign that account out (the 2026-10-15 mix
            # of the panel's sign-in with longhaul's, again); on ~/.claude it is
            # the panel's own business.
            log(f"panel: '{' '.join(args[:2])}' runs as the extension asked, not on an account longhaul manages")
            notify("Signing in or out in the panel does not change longhaul's accounts; use longhaul setup for those.")
            os.execv(binary, [binary] + args)
        accounts = load_accounts()
        chat = is_chat(args)
        mode = panel_switch_mode() if chat else "never"
        resumed = resume_id(args) if chat else None
        idx = resumed_index(resumed, accounts, mode, "panel", binary)  # a reopened chat: where it last ran
        if idx is not None:
            until = load_state()["limited_until"].get(accounts[idx]["name"], 0)
            if until > time.time():
                # The terminal says this and names --account, which the panel
                # has not: without it the chat got Claude's own limit error and
                # no word of why (review, Sprint 5).
                notify(f"{accounts[idx]['name']} is at its limit until {clock(until)}; the chat stays on it "
                       f"(switching is {mode})")
        if idx is None:
            idx, _ = choose_for_panel(accounts, binary)
        if idx is None:
            log("panel: no account is signed in as expected; running Claude as the extension asked")
            os.execv(binary, [binary] + args)
        acct = accounts[idx]
        prepare(acct, chat)
        shown = " ".join(a for a in args if not a.startswith("--debug"))
        log(f"panel {'chat' if chat else 'job'} on {acct['name']}: {shown[:240]}")
        if resumed:
            # The record the next reopen reads (D3d). With switch_mid_session
            # off claude runs in this process's place and nothing of longhaul
            # sees the chat, and the relay's note_cost() writes the record
            # only once a turn has cost something: a chat reopened by id with
            # no record was picked, and could be picked again, elsewhere, at
            # the next reopen (review, Sprint 5). A new chat's id is not known
            # before the exec: the status line or the relay records it.
            note_session_account(resumed, acct["name"])
        if not chat or vscode_config()["switch_mid_session"] is not True:  # "false" in the file is not on
            os.execve(binary, [binary] + args, wrapper_env(acct))
        relay = Relay(binary, args, accounts, idx, mode=mode)
        relay.start()
    except Exception as e:
        try:
            log(f"panel wrapper error, running Claude unchanged: {e!r}")
            if relay and relay.child and relay.child.poll() is None:
                relay.child.kill()
        except Exception:
            pass
        os.execv(binary, [binary] + args)
    return relay.loop()


def pipe(argv: list[str]) -> int:
    """longhaul pipe: another program (the driver) runs claude over stream-json
    through longhaul. It is the panel's relay with the driver where the panel
    was: the driver's lines go to claude and claude's to the driver
    unchanged, on the account longhaul picks (a session resumed by id: the
    account it last ran on, in ask and never; --account wins). At a limit the session moves to
    another account only with --swap and the mode auto (--switch auto, or
    LONGHAUL_SWITCH=auto); with --swap in ask or never it stays, and longhaul says so
    in the stream.

    longhaul's own lines in the stream. Each is a JSON object with "type": "longhaul"
    and a "subtype". A driver must ignore a subtype it does not know.
    Times are epoch seconds; "why" is Claude's rateLimitType.
    - moved {"from", "to", "why"}: the session moved from one account to
      another. Written only with --swap, in auto, before the carry-on
      message.
    - all_limited {"until"}: a limit, and no account has room until then.
      Written only with --swap, in auto.
    - stays {"on", "until", "why", "switch"}: a limit, and the session
      stays on account "on", which resets at "until"; "switch" is the mode
      (ask or never). Written only with --swap, when the mode is not auto.
    Without --swap there is no longhaul line: the turn ends with claude's own
    limit error, which the driver already has. Since --switch exists, the
    abbreviation --sw is ambiguous and refused; write --swap in full.

    What differs from wrap:
    - it never runs claude unchanged. With no accounts file, or no account
      in it signed in as expected, it exits 1 before starting one, since a
      session on an account longhaul does not manage could not be moved or
      accounted for;
    - it never runs Remote Control: stdout is the driver's stream and the
      driver is the one watching, so a driver asking for it gets exit 2
      and no claude;
    - with --no-git-sign-in its environment is no_git_env's, with no
      GitHub or git sign-in in it."""
    global QUIET
    QUIET = True  # stdout is the driver's stream, as it is the panel's for wrap
    args: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, args = argv[:cut], argv[cut + 1:]
    p = argparse.ArgumentParser(prog="longhaul pipe")
    p.add_argument("--name", help="the session's name in longhaul usage")
    p.add_argument("--account", help="the account to start on")
    p.add_argument("--swap", action="store_true",
                   help="this driver accepts a move to another account at a limit; the session moves only "
                        "when --switch (or LONGHAUL_SWITCH) is also auto")
    p.add_argument("--switch", choices=list(SWITCH_MODES),
                   help="what to do at a limit. auto: move the session (needs --swap). ask (the default) or "
                        "never: keep it on its account, since no one can be asked here. Overrides LONGHAUL_SWITCH")
    p.add_argument("--no-git-sign-in", action="store_true",
                   help="hand claude no GitHub or git sign-in from this environment")
    opts = p.parse_args(argv)
    try:
        mode = switch_mode(opts.switch)
    except ValueError as e:
        log(f"pipe: {e}")
        return 2
    if any(a.split("=", 1)[0] in ("--remote-control", "--rc") or a == "remote-control" for a in args):
        log("pipe: longhaul pipe never runs Remote Control")
        return 2
    if settings_note():
        log(settings_note())
    relay = None
    try:
        # Read here, not with load_accounts(): its fallback is one account
        # on ~/.claude with no email, which the sign-in check lets through,
        # so a missing or broken file would have run the session on the
        # panel's own sign-in.
        accounts = read_json(LONGHAUL_HOME / "accounts.json")
        if not (isinstance(accounts, list) and accounts
                and all(isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"] for a in accounts)):
            log("pipe: accounts.json is missing or names no accounts, so longhaul did not start Claude")
            return 1
        names = [a["name"] for a in accounts]
        if opts.account and opts.account not in names:
            log(f"pipe: no account called {opts.account}; the accounts are {', '.join(names)}")
            return 2
        # An account with no email cannot be checked, and one with no folder
        # of its own is ~/.claude, the panel's sign-in: neither is an account
        # you gave longhaul, so a driven session never runs or moves there.
        unmanaged = {a["name"] for a in accounts if not a.get("email") or not a.get("config_dir")}
        start = names.index(opts.account) if opts.account else 0
        idx, resumed = None, resume_id(args)
        if opts.account:
            # As `longhaul --account` does: that account while it is not at its
            # limit and is signed in as expected, else the one longhaul picks.
            limited = load_state()["limited_until"].get(opts.account, 0) > time.time()
            if not limited and opts.account not in unmanaged and signed_in_cached(accounts[start], CLAUDE):
                idx = start
        if idx is None:
            # A resumed session: where it last ran, if that account is still
            # one longhaul manages. A new session (--session-id) is picked.
            idx = resumed_index(resumed, accounts, mode, "pipe", CLAUDE, unmanaged)
        if idx is None:
            idx, _ = choose_for_panel(accounts, CLAUDE, start, wrong=set(unmanaged))
        if idx is None:
            log("pipe: no account is signed in as expected, so longhaul did not start Claude")
            return 1
        acct = accounts[idx]
        prepare(acct, True)
        sid = resumed
        for i, a in enumerate(args):
            if not sid and a.startswith("--session-id="):
                sid = a.split("=", 1)[1]
            elif not sid and a == "--session-id" and i + 1 < len(args):
                sid = args[i + 1]
        if sid and opts.name:
            save_label(sid, opts.name)
        at_limit = (", swapping at a limit" if opts.swap and mode == "auto"
                    else f", staying at a limit (switching: {mode})" if opts.swap else "")
        log(f"pipe: {opts.name or 'a session'} on {acct['name']}{at_limit}")
        relay = Relay(CLAUDE, args, accounts, idx, env=no_git_env if opts.no_git_sign_in else env_for,
                      swap=opts.swap, says=True, mode=mode)
        relay.bad.update(unmanaged)  # switch() never moves the session onto an account in bad
        relay.start()
    except Exception as e:
        log(f"pipe: could not start: {e!r}")
        if relay and relay.child and relay.child.poll() is None:
            relay.child.kill()
        return 1
    return relay.loop()


class Relay:
    """longhaul between the panel and claude for a whole chat: every line is
    passed on as it is, and at a limit stop claude is swapped for one on
    another account without the panel seeing it.

    The swap: hold the panel's lines; end claude; start claude again with
    --resume=<the conversation> on the next account; send it the panel's
    own initialize (with a request id of longhaul's own) and the requests that
    changed the chat since (LATEST, PER_SERVER, EACH), keeping their
    answers and the new claude's auth_status from the panel, which already
    has its own (system/init is passed on: claude sends one every turn);
    then the carry-on message, whose replay the panel shows in the chat;
    then the held lines. A claude that will not start on the next account
    is replaced by one on the account the chat was on, so the held lines
    still reach a live claude; only if that fails too does the chat end,
    as a crash would, for the panel to reopen it.

    The process ends with os._exit and claude's exit code: a normal exit
    would wait for the thread blocked on the panel's stdin and abort
    (Fatal Python error, exit 134)."""

    def __init__(self, binary: str, args: list[str], accounts: list[dict], idx: int, *,
                 env=wrapper_env, swap: bool = True, says: bool = False, mode: str = "never"):
        self.binary, self.args, self.accounts, self.idx = binary, args, accounts, idx
        # The panel's relay keeps its defaults. longhaul pipe passes its own
        # environment, whether it may swap accounts, and says=True for the
        # lines of its own it writes into a driven session's stream.
        # `swap` is the front end's consent, `mode` the user's switching: a
        # move needs both (the owner's decision record, not shipped).
        self.env, self.swap, self.says, self.mode = env, swap, says, mode
        self.cond = threading.Condition()
        self.queue: collections.deque = collections.deque()
        self.paused = False
        self.held_since = None
        self.swap_started = None
        self.panel_closed = False
        self.swapping = False
        self.out_lock = threading.Lock()
        self.init_request = None
        self.replay: collections.OrderedDict = collections.OrderedDict()
        self.flag_seq = 0
        self.ids = 0
        self.hidden: dict = {}
        self.readers: dict = {}
        self.answered = threading.Event()
        self.switches = 0
        self.bad: set = set()
        self.child = None
        self.watch = None
        self.limits: dict = {}

    # -- the pipes

    def spawn(self, idx: int, args: list[str], hide: dict | None = None) -> subprocess.Popen:
        child = subprocess.Popen([self.binary] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 env=self.env(self.accounts[idx]))
        self.hidden[child.pid] = hide
        reader = threading.Thread(target=self.from_claude, args=(child,), daemon=True)
        self.readers[child.pid] = reader
        reader.start()
        return child

    def next_id(self, kind: str) -> str:
        self.ids += 1
        return f"longhaul-{kind}-{self.ids}"

    def remember(self, req: dict) -> None:
        r = req.get("request") or {}
        sub = r.get("subtype")
        if sub == "initialize":
            self.init_request = req
            return
        if sub in SIGN_IN:
            acct = self.accounts[self.idx]
            forget_auth(acct)
            log(f"panel: a sign-in started in a chat on {acct['name']}; longhaul checks who {acct['name']} is "
                "before its next launch")
            return
        if sub in LATEST:
            key = (sub,)
        elif sub in PER_SERVER:
            key = (sub, r.get("serverName"))
        elif sub in EACH:
            self.flag_seq += 1
            key = (sub, self.flag_seq)
            for old in [k for k in self.replay if k[0] == sub][:-19]:
                del self.replay[old]
        else:
            return
        self.replay.pop(key, None)  # a newer one goes to the end: replay keeps the panel's order
        self.replay[key] = req

    def from_panel(self) -> None:
        for line in sys.stdin.buffer:
            trace("panel>", "-", line)
            if has_type(line, "control_request"):
                try:
                    self.remember(json.loads(line))
                except (ValueError, AttributeError):
                    pass
            with self.cond:
                self.queue.append(line)
                self.cond.notify_all()
        with self.cond:
            self.panel_closed = True
            self.cond.notify_all()

    def to_claude(self) -> None:
        while True:
            with self.cond:
                while self.paused or (not self.queue and not self.panel_closed):
                    self.cond.wait()
                child = self.child
                line = self.queue.popleft() if self.queue else None
            if line is None:  # the panel closed the chat: so does claude
                try:
                    child.stdin.close()
                except OSError:
                    pass
                return
            try:
                child.stdin.write(line)
                child.stdin.flush()
                trace("claude<", child.pid, line)
            except (OSError, ValueError):
                with self.cond:
                    if self.paused or child is not self.child:
                        self.queue.appendleft(line)  # for the claude a swap is starting
                        continue
                return

    def from_claude(self, child: subprocess.Popen) -> None:
        out = sys.stdout.buffer
        for line in child.stdout:
            if not line.endswith(b"\n"):
                # A claude ended mid-line (a swap stops it): the half line
                # would be glued to the next claude's first line, and the
                # panel would drop both.
                trace("<claude", child.pid, b"{}", f"partial line of {len(line)} bytes dropped")
                continue
            if not self.keep(child, line):
                trace("<claude", child.pid, line, "HIDDEN")
                continue
            trace("<claude", child.pid, line)
            if child is self.child:
                if b'"rate_limit' in line:
                    self.hold_if_limit(line)
                if has_type(line, "result"):
                    self.note_cost(child, line)
            try:
                with self.out_lock:
                    out.write(line)
                    out.flush()
            except (OSError, ValueError):
                return

    def keep(self, child: subprocess.Popen, line: bytes) -> bool:
        hide = self.hidden.get(child.pid)
        if not hide:
            return True
        if hide["auth"] and (has_type(line, "stream_event") or has_type(line, "assistant")
                             or has_type(line, "result")):
            hide["auth"] = False  # past the start-up: the rest is for the panel
        if not (has_type(line, "control_response") or has_type(line, "auth_status")):
            return True
        try:
            msg = json.loads(line)
        except ValueError:
            return True
        kind = msg.get("type")
        if kind == "control_response":
            rid = (msg.get("response") or {}).get("request_id")
            if rid in hide["ids"]:
                if rid == hide.get("init_id"):
                    self.answered.set()
                return False
        if kind == "auth_status" and hide["auth"]:
            return False
        return True

    def hold_if_limit(self, line: bytes) -> None:
        """A limit stop on its way to the panel: hold the panel's next
        lines from now, before the panel even shows the stop, so a message
        typed straight after it reaches the next account rather than this
        one. The conversation file confirms the stop (and its reset time)
        a moment later; without that, the lines are let go after HOLD_MAX."""
        try:
            msg = json.loads(line)
        except ValueError:
            return
        info = msg.get("rate_limit_info") or {}
        if msg.get("type") == "rate_limit_event":
            self.note_reading(info)
        stop = (msg.get("type") == "assistant" and msg.get("error") == "rate_limit"
                and not msg.get("parent_tool_use_id")) or (
                msg.get("type") == "rate_limit_event" and info.get("status") == "rejected")
        if stop:
            with self.cond:
                self.paused = True
                self.held_since = time.time()
            if RELAY_DEBUG:
                log("relay HOLD: a rate_limit stop in the stream")

    def note_reading(self, info: dict) -> None:
        """A panel chat's usage, from the rate_limit_event claude sends with
        every reply: {unifiedWindows: {five_hour: {utilization: 0.1,
        resetsAt}, seven_day: {...}}} (a fraction, 2.1.300). Kept in the
        shape the status line gives, so panel chats count for their account
        in pick() and `longhaul usage` like terminal ones."""
        windows_ = info.get("unifiedWindows") or {}
        limits = {}
        for key in ("five_hour", "seven_day"):
            w = windows_.get(key) or {}
            if w.get("utilization") is not None and w.get("resetsAt"):
                limits[key] = {"used_percentage": round(float(w["utilization"]) * 100), "resets_at": w["resetsAt"]}
        if limits:
            self.limits = limits

    def note_cost(self, child: subprocess.Popen, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if msg.get("type") != "result" or not self.limits:
            return
        try:
            record_usage({"session_id": msg.get("session_id"), "rate_limits": self.limits, "proc": child.pid,
                          "cost": {"total_cost_usd": msg.get("total_cost_usd") or 0}},
                         str(config_of(self.accounts[self.idx])))
        except Exception as e:
            log(f"panel usage not recorded: {e!r}")

    def release(self) -> None:
        if RELAY_DEBUG:
            log("relay RELEASE")
        with self.cond:
            self.paused = False
            self.held_since = None
            self.cond.notify_all()

    def end_swap(self) -> None:
        """Let the panel's lines go after a swap, unless the new claude
        stopped at a limit itself while the swap was finishing: that hold
        stays, for the next switch (or HOLD_MAX) to end."""
        with self.cond:
            if self.held_since and self.swap_started and self.held_since >= self.swap_started:
                return
        self.release()

    def send(self, child: subprocess.Popen, obj: dict) -> None:
        line = json.dumps(obj).encode() + b"\n"
        child.stdin.write(line)
        child.stdin.flush()
        trace("claude<", child.pid, line, "(longhaul)")

    def say_in_stream(self, obj: dict) -> None:
        """One line of longhaul's own into a driven session's stream (longhaul pipe
        only): the driver does not read longhaul's files, so a move or a wait for
        a reset reaches it here, and it tells a move from a close by it.
        Under out_lock, so it
        never lands inside one of claude's lines. A driver that has gone
        has no one left to tell."""
        try:
            with self.out_lock:
                sys.stdout.buffer.write(json.dumps(obj).encode() + b"\n")
                sys.stdout.buffer.flush()
        except (OSError, ValueError):
            pass

    def mark_limited(self, here: dict, limit: dict) -> dict:
        """Record that `here` is at its limit until the reset, and that this
        limit line was acted on, so no relay takes it for a new one."""
        def mark(state):
            state["limited_until"][here["name"]] = limit["resets_at"]
            if limit.get("uuid"):
                state["handled_limits"] = (state.get("handled_limits", []) + [limit["uuid"]])[-50:]

        return update_state(mark)

    def no_swap(self) -> None:
        """A limit stop in a session longhaul may not move. Either its front end
        did not allow it (longhaul pipe without --swap: a driver that set up the
        session with flags at its launch may not want it reopened
        elsewhere, since longhaul cannot be sure a conversation reopened with
        --resume keeps them), or the user's switching is ask or never,
        and no one can be asked here (the owner's decision record on
        switching without a terminal, not shipped). The account is marked as switch() marks it, and the
        turn ends with claude's own limit error, which the driver or the
        panel already has."""
        here, limit = self.accounts[self.idx], self.watch.limit
        self.mark_limited(here, limit)
        self.watch.limit = None
        # The held lines go first: a notification can take up to 10 s, and
        # the panel sat frozen behind it (review, Sprint 2).
        self.release()
        if not self.swap:
            log(f"pipe: {here['name']} hit its {limit['kind']} limit; swapping is off, so the turn ends there")
        elif self.says:  # a driver tells its user, as it does from `moved`
            log(f"pipe: {here['name']} hit its {limit['kind']} limit; switching is {self.mode}, "
                "so the session stays on it")
            self.say_in_stream({"type": "longhaul", "subtype": "stays", "on": here["name"], "until": limit["resets_at"],
                                "why": limit["type"], "switch": self.mode})
        else:
            # No quotes: notify() turns " into ', which made a JSON snippet
            # wrong. The mode may come from the wrapper's LONGHAUL_SWITCH too.
            why = ("switching is never, so" if self.mode == "never"
                   else "longhaul cannot ask in the panel, so")
            msg = (f"{here['name']} hit its {limit['kind']} limit (resets {clock(limit['resets_at'])}); "
                   f"{why} the chat stays on {here['name']}. To let longhaul move it, set the key switch to auto "
                   f"in {VSCODE_CONFIG}, or start VS Code with LONGHAUL_SWITCH=auto.")
            log(f"panel: {msg}")
            notify(msg)

    def stop_child(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        reader = self.readers.get(child.pid)
        if reader:
            reader.join(timeout=5)

    # -- the chat

    def start(self) -> None:
        """Everything that can fail before the chat is under way, run in
        wrap()'s try so a failure still gets the panel its claude."""
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, self.on_signal)
        started = time.time()
        marks = transcript_size(resume_id(self.args))
        handled = load_state().get("handled_limits", [])
        self.child = self.spawn(self.idx, self.args)
        self.watch = Watch(self.accounts[self.idx], self.child.pid, started, False, offsets=marks, handled=handled)

    def loop(self) -> int:
        code = 1
        try:
            threading.Thread(target=self.from_panel, daemon=True).start()
            threading.Thread(target=self.to_claude, daemon=True).start()
            while True:
                try:
                    ended = self.child.wait(timeout=POLL)
                except subprocess.TimeoutExpired:
                    ended = None
                if ended is not None and not self.swapping:
                    self.readers[self.child.pid].join(timeout=10)
                    code = ended if ended >= 0 else 128 - ended
                    log(f"panel chat {self.watch.sid} ended (exit {code}) after {self.switches} switch(es)")
                    break
                try:
                    self.watch.step()
                    if self.watch.should_switch():
                        # No one can be asked here, so "ask" never moves.
                        if decide_move(self.mode, consent=self.swap, can_ask=False) == "move":
                            self.switch()
                        else:
                            self.no_swap()
                    elif self.held_since and time.time() - self.held_since > HOLD_MAX and not self.watch.limit:
                        log("panel: a rate_limit in the stream was not a usage limit stop; letting the panel's lines go")
                        self.release()
                except Exception as e:  # watching must never take the chat down
                    log(f"panel relay error: {e!r}")
                    self.release()
        finally:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            os._exit(code)

    def start_claude(self, idx: int, sid: str):
        """A claude on accounts[idx] resuming the chat, initialized and given
        the panel's changes; None if it exits or does not answer (checked
        every quarter second, not after INIT_WAIT: a claude that dies at
        start-up would otherwise hold the panel for 90 s)."""
        acct = self.accounts[idx]
        prepare(acct)  # the target's links and MCP servers, as before any launch
        init_id = self.next_id("initialize")
        hide = {"ids": {init_id}, "init_id": init_id, "auth": True}
        self.answered.clear()
        self.idx = idx
        child = self.spawn(idx, without_resume(self.args) + [f"--resume={sid}"], hide)
        self.child = child
        try:
            self.send(child, {**self.init_request, "request_id": init_id})
        except (OSError, ValueError):
            pass
        deadline = time.time() + INIT_WAIT
        while not self.answered.wait(0.25):
            if child.poll() is not None or time.time() > deadline:
                why = f"exited ({child.poll()})" if child.poll() is not None else f"did not answer in {INIT_WAIT:g}s"
                log(f"panel: claude on {acct['name']} {why}")
                self.stop_child(child)
                return None
        for req in list(self.replay.values()):
            rid = self.next_id("replay")
            hide["ids"].add(rid)
            self.send(child, {**req, "request_id": rid})
        return child

    def switch(self) -> None:
        here_idx = self.idx
        here, limit, sid = self.accounts[here_idx], self.watch.limit, self.watch.sid
        state = self.mark_limited(here, limit)
        self.watch.limit = None
        nxt, until = choose_for_panel(self.accounts, self.binary, (here_idx + 1) % len(self.accounts), state,
                                      wrong=set(self.bad))
        if self.says and self.init_request is None:
            self.init_request = PIPE_INITIALIZE  # the driver sent none
        if nxt is None or until or nxt == here_idx or not sid or self.init_request is None:
            log(f"panel: {here['name']} hit its {limit['kind']} limit; no other account has room, "
                "so the chat stays as it is")
            if self.says and (nxt is None or until or nxt == here_idx):
                # Not for an unknown conversation id: other accounts may have room.
                self.say_in_stream({"type": "longhaul", "subtype": "all_limited", "until": until})
            self.release()
            return
        new = self.accounts[nxt]["name"]
        with self.cond:
            self.paused = True
        self.swapping = True
        self.swap_started = time.time()
        try:
            self.stop_child(self.child)
            self.switches += 1
            started = time.time()
            marks = transcript_size(sid)
            handled = load_state().get("handled_limits", [])
            self.limits = {}  # readings so far were the old account's
            child = self.start_claude(nxt, sid)
            if child is None:
                self.bad.add(new)
                log(f"panel: the chat stays on {here['name']}, since claude would not start on {new}")
                child = self.start_claude(here_idx, sid)
                if child is None:
                    log("panel: claude would not start again on either account; "
                        "ending the chat so the panel can reopen it")
                    return
                self.watch = Watch(here, child.pid, started, False, offsets=marks, handled=handled)
                return
            if self.says:  # before the carry-on, so it comes before the new claude's reply
                self.say_in_stream({"type": "longhaul", "subtype": "moved", "from": here["name"], "to": new,
                                    "why": limit["type"]})
            text = CARRY_ON.format(old=here["name"], kind=limit["kind"], new=new)
            self.send(child, {"type": "user", "uuid": str(uuid.uuid4()), "session_id": "",
                              "parent_tool_use_id": None,
                              "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
            self.watch = Watch(self.accounts[nxt], child.pid, started, False, offsets=marks, handled=handled)
            msg = (f"{here['name']} hit its {limit['kind']} limit (resets {clock(limit['resets_at'])}); "
                   f"the panel chat carries on with {new}.")
            log(msg)
            if not self.says:  # a driver tells its user from the `moved` line; a notification would say it twice
                notify(msg)
        finally:
            self.swapping = False
            self.end_swap()

    def on_signal(self, sig, _frame) -> None:
        child = self.child
        if child and child.poll() is None:
            child.send_signal(sig)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        os._exit(128 + sig)


# ---------------------------------------------------------------- setup

AUTH_TIMEOUT = setting("LONGHAUL_AUTH_TIMEOUT", 10, float)


def auth_status(acct: dict, binary: str | None = None) -> dict:
    """claude auth status --json for the account. {"unknown": True, "why"}
    when claude could not say (no answer within AUTH_TIMEOUT, not JSON):
    that is not "signed out", and the panel's launch waits on this."""
    try:
        out = subprocess.run([binary or CLAUDE, "auth", "status", "--json"], env=env_for(acct),
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=AUTH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"loggedIn": False, "unknown": True, "why": f"no answer in {AUTH_TIMEOUT:g}s"}
    except OSError as e:
        return {"loggedIn": False, "unknown": True, "why": repr(e)}
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {"loggedIn": False, "unknown": True, "why": f"exit {out.returncode}, no JSON"}


def link_command() -> bool:
    """Link ~/.local/bin/longhaul to this file; True when the link points here."""
    target = HOME / ".local" / "bin" / "longhaul"
    here = Path(__file__).resolve()
    if (HOME / ".claude" / "plugins").resolve() in here.parents:
        # An installed plugin's folder changes at every update and goes at
        # uninstall; a link into it dies with it (ADR-0001, decision 3).
        say(f"not linking {target}: this longhaul is an installed plugin's copy ({here.parent}), "
            "which moves at every update. Run `python3 longhaul.py setup` from a checkout of your own.")
        return False
    if target.is_symlink() and target.resolve() == here:
        return True
    if target.exists() or target.is_symlink():
        say(f"{target} already exists and is not longhaul; left it alone")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(here)
    say(f"linked {target}, so `longhaul` works from any folder")
    return True


def setup(opts) -> int:
    """Sign each account in to its own folder, linked into ~/.claude.

    Every account gets its own folder. ~/.claude is what the VS Code panel
    signs in to: on 2026-10-15 a sign-in there moved it to the second
    account, and longhaul lost its first. Accounts and emails are in
    accounts.json; `longhaul setup Max` does one account, `longhaul setup` each that
    is not signed in as its email. With no accounts.json (one subscription)
    there is nothing to sign in: setup links the command, for the status line.
    A file that cannot be read is an error, exit 2, not "no accounts"."""
    accounts, problem = read_accounts_file()
    if problem:
        say(f"accounts.json is not readable: {problem}; fix or remove it")
        return 2
    if not accounts:
        if opts.account:
            say(f"no account called {opts.account}: none is on the list yet; `longhaul add NAME EMAIL` adds one")
            return 2
        if not link_command():
            say("`longhaul` is not linked as a command (see above). A second subscription is `longhaul add NAME EMAIL`.")
            return 1
        say("Ready: point the status line at `~/.local/bin/longhaul statusline` in your own ~/.claude/settings.json. "
            "A second subscription is `longhaul add NAME EMAIL`.")
        return 0
    names = [a["name"] for a in accounts]
    if opts.account and opts.account not in names:
        say(f"no account called {opts.account}; the accounts are {', '.join(names)}")
        return 2
    emails = {}
    for acct in accounts:
        if opts.account and acct["name"] != opts.account:
            emails[acct["name"]] = (auth_status(acct).get("email") or "").lower()
            continue
        folder, want = config_of(acct), acct.get("email")
        if folder == DEFAULT_CONFIG:
            say(f"{acct['name']} uses ~/.claude, which VS Code signs in to; give it its own "
                "config_dir in accounts.json (like ~/.claude-max) and run `longhaul setup` again")
            return 2
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        relink(acct)
        status = auth_status(acct)
        right = want and (status.get("email") or "").lower() == want.lower()
        if not right or opts.relogin:
            say(f"{acct['name']}: sign in as {want or 'this account'} in the browser that opens. "
                "If the browser is signed in to claude.ai as someone else, copy the link into a "
                "private window instead.")
            login = [CLAUDE, "auth", "login"] + (["--email", want] if want else [])
            if subprocess.run(login, env=env_for(acct)).returncode != 0:
                say("sign-in did not finish; run `longhaul setup` again")
                return 1
            status = auth_status(acct)
        got = (status.get("email") or "").lower()
        if want and got != want.lower():
            say(f"{acct['name']} signed in as {got or 'nobody'}, not {want}. "
                f"Run `longhaul setup {acct['name']}` again and sign in as {want}.")
            return 1
        emails[acct["name"]] = got

        # MCP servers, project trust and onboarding, so this account starts
        # like ~/.claude; only keys it does not have yet.
        src = read_json(DEFAULT_GLOBAL_JSON) or {}
        dst_path = folder / ".claude.json"
        dst = read_json(dst_path) or {}
        added = [k for k in COPIED_KEYS if k in src and k not in dst]
        for k in added:
            dst[k] = src[k]
        if added:
            write_json(dst_path, dst)
            dst_path.chmod(0o600)
        sync_mcp(acct)
        say(f"{acct['name']}: {got} ({status.get('subscriptionType')}) in {folder}")

    seen = [e for e in emails.values() if e]
    if len(seen) != len(set(seen)):
        say("two accounts are signed in as the same email; run `longhaul status`")
        return 1
    for acct in accounts:  # the panel's remembered sign-ins are out of date now
        if not opts.account or acct["name"] == opts.account:
            forget_auth(acct)
    if link_command():
        say("Ready: run `longhaul`.")
    else:
        say("The accounts are set up, but `longhaul` is not linked as a command (see above): run `python3 longhaul.py` instead.")
    return 0


def add(opts) -> int:
    """longhaul add NAME EMAIL: a new account in its own folder, signed in once in
    the browser. Only that email is accepted for it from then on."""
    name, email = opts.name.strip(), opts.email.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,30}", name):
        say("a name is letters and digits (and - or _), up to 31 of them")
        return 2
    if "@" not in email:
        say(f"{email} does not look like an email")
        return 2
    folder = opts.dir or f"~/.claude-{name.lower()}"
    path = Path(os.path.expanduser(folder)).resolve()
    if path in (DEFAULT_CONFIG.resolve(), (HOME / ".claude").resolve()):
        say("each account needs its own folder: ~/.claude is what the VS Code panel signs in to")
        return 2
    with locked("accounts"):
        accounts, problem = read_accounts_file()
        if problem:  # never written over: it may be the list, half-written or hand-edited
            say(f"accounts.json is not readable: {problem}; fix or remove it")
            return 2
        for a in accounts:
            if a["name"].lower() == name.lower():
                say(f"there is already an account called {a['name']}")
                return 2
            if (a.get("email") or "").lower() == email.lower():
                say(f"{email} is already the account {a['name']}")
                return 2
            if a.get("config_dir") and config_of(a).resolve() == path:
                say(f"{folder} is already the folder of {a['name']}")
                return 2
        accounts.append({"name": name, "config_dir": folder, "email": email})
        write_json(LONGHAUL_HOME / "accounts.json", accounts)
    say(f"added {name} ({email}), folder {folder}; signing it in now")
    code = setup(argparse.Namespace(account=name, relogin=False))
    if code != 0:
        say(f"{name} is on the list but not signed in, so longhaul will not use it yet: run `longhaul setup {name}`")
    return code


def remove(opts) -> int:
    """longhaul remove NAME: off longhaul's list. Its folder and sign-in are kept."""
    with locked("accounts"):
        accounts, problem = read_accounts_file()
        if problem:
            say(f"accounts.json is not readable: {problem}; fix or remove it")
            return 2
        gone = [a for a in accounts if a["name"].lower() == opts.name.lower()]
        if not gone:
            say(f"no account called {opts.name}; the accounts are {', '.join(a['name'] for a in accounts)}")
            return 2
        if len(accounts) == 1:
            say("that is the only account; longhaul needs at least one")
            return 2
        write_json(LONGHAUL_HOME / "accounts.json", [a for a in accounts if a not in gone])
    forget_auth(gone[0])
    say(f"removed {gone[0]['name']} from longhaul's accounts. Its folder {config_of(gone[0])} and its sign-in are kept; "
        "delete the folder yourself if you no longer want them.")
    return 0


def status(_opts) -> int:
    state = load_state()
    now = time.time()
    for acct in load_accounts():
        a = auth_status(acct)
        until = state["limited_until"].get(acct["name"], 0)
        limit = f"at its limit until {clock(until)}" if until > now else "available"
        who = (f"{a.get('email')} ({a.get('subscriptionType')})" if a.get("loggedIn")
               else f"could not check ({a.get('why')})" if a.get("unknown") else "NOT signed in")
        print(f"{acct['name']:<6} {who:<44} {limit}   [{config_of(acct)}]")
        want = acct.get("email")
        if want and (a.get("email") or "").lower() != want.lower():
            print(f"       should be {want}: longhaul will not use this account until `longhaul setup` signs it in")
        if acct.get("config_dir"):
            broken = [i for i in SHARED if (DEFAULT_CONFIG / i).exists() and not (config_of(acct) / i).is_symlink()]
            if broken:
                print(f"       not linked: {', '.join(broken)} (longhaul links them again at the next launch)")
    version = claude_version()
    print(f"Claude Code {'.'.join(map(str, version)) if version else '(version unknown)'}: "
          f"{version_note(version) or 'checked with longhaul'}")
    try:
        mode = switch_mode()
    except ValueError as e:  # for scripts that read this: the mode is always the second word
        print(f"Switching: invalid. {e}")
        return 2
    source = "set by LONGHAUL_SWITCH" if os.environ.get("LONGHAUL_SWITCH") else "the default"
    panel = vscode_config()
    if panel["switch_mid_session"] is not True:
        tail = "The VS Code panel never moves a chat (switch_mid_session is off)."
    else:
        # From the file only: this terminal's LONGHAUL_SWITCH may not be the panel's.
        key = panel.get("switch")
        shown = (f"{key} (from vscode.json)" if isinstance(key, str) and key in SWITCH_MODES
                 else "ask (the default)" if not key else f"never ({key!r} in vscode.json is not a mode)")
        tail = (f"The VS Code panel moves a chat at a limit only in auto; it is {shown}, "
                "unless VS Code was started with LONGHAUL_SWITCH set.")
    print(f"Switching: {mode} ({source}): {SWITCH_MODES[mode]}. Change it with --switch ask|auto|never "
          f"or LONGHAUL_SWITCH. {tail} longhaul pipe moves a session only when its driver passes --swap and the mode "
          "there is auto. A conversation resumed by id starts on the account it last ran on in ask and never.")
    if settings_note():
        print(settings_note())
    return 0


def clear(_opts) -> int:
    update_state(lambda state: state.update(limited_until={}))
    say("forgot the recorded limits")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "pipe":  # another program drives claude through longhaul
        return pipe(argv[1:])
    if argv and argv[0] == "wrap":  # what longhaul_vscode.py runs, here for tests and by hand
        return wrap(argv[1:])
    if argv and argv[0] in ("setup", "status", "clear", "statusline", "usage", "inspect", "guard", "checkpoint",
                            "add", "remove"):
        cmd, rest = argv[0], argv[1:]
        p = argparse.ArgumentParser(prog=f"longhaul {cmd}")
        if cmd == "usage":
            p.add_argument("--days", type=float, default=1, help="how far back (default: 1 day)")
        if cmd == "checkpoint":
            p.add_argument("action", nargs="?", choices=["save"], help="save: the state on stdin (default: the hook)")
            p.add_argument("session", nargs="?",
                           help="the conversation the state is for (default: this one, from CLAUDE_CODE_SESSION_ID)")
            p.add_argument("--file", metavar="PATH",
                           help="save: read the state from this file instead of stdin "
                                "(a .state.json under LONGHAUL_HOME/checkpoints is removed once saved)")
        if cmd == "inspect":
            p.add_argument("target", nargs="?", metavar="ID|PATH",
                           help="a conversation (default: the latest one in the ledger)")
            p.add_argument("--json", action="store_true",
                           help="one record per number: metric, value, unit, source, confidence")
        if cmd == "add":
            p.add_argument("name", help="what to call it (Max, Pro, Team...)")
            p.add_argument("email", help="the only email longhaul will accept for it")
            p.add_argument("--dir", help="its folder (default: ~/.claude-<name>)")
        if cmd == "remove":
            p.add_argument("name")
        if cmd == "setup":
            p.add_argument("account", nargs="?", help="only this account (default: each one not signed in)")
            p.add_argument("--relogin", action="store_true", help="sign in again even if signed in")
        opts = p.parse_args(rest)
        return {"setup": setup, "status": status, "clear": clear, "statusline": statusline, "usage": usage,
                "inspect": inspect, "guard": guard, "checkpoint": checkpoint, "add": add, "remove": remove}[cmd](opts)

    extra: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    p = argparse.ArgumentParser(prog="longhaul", description=__doc__.split("\n\n")[0],
                                # The subcommands are dispatched above, not by argparse, so -h
                                # would not list them without the docstring's Usage block.
                                epilog=__doc__[__doc__.index("Usage:"):],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the latest conversation here")
    p.add_argument("-r", "--resume", nargs="?", const="", default=None, help="resume a conversation (no ID: pick)")
    p.add_argument("--account", help="the account to start on")
    p.add_argument("--name", help="the session's name on the phone (default: the folder)")
    p.add_argument("--no-rc", dest="rc", action="store_false", help="no Remote Control")
    p.add_argument("--first", metavar="FILE", help="the first message, sent on the first launch only")
    p.add_argument("--switch", choices=list(SWITCH_MODES),
                   help="ask (the default): ask here before moving a conversation to another account; "
                        "auto: move at a limit, and early when one is nearly full; "
                        "never: wait for the reset. With no flag, LONGHAUL_SWITCH is used")
    opts = p.parse_args(argv)
    opts.extra = extra
    return run(opts)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
