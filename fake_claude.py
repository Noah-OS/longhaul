#!/usr/bin/env python3
"""A stand-in for `claude` in longhaul's tests: it writes the same files Claude
Code writes (sessions/<pid>.json, projects/*/<sid>.jsonl) and acts out one
step of FAKE_PLAN per launch, so longhaul's loop runs without an account, a
network or a terminal.

Steps: limit:<resetsAt> | limit_busy:<resetsAt> | exit | crash | killed |
rc_drop | rc_drop_then_exit | idle_wait | idle_wait_agents |
exit_after_signin_change | clear_then_crash | stay |
session_end:<reason>:<code>[:<sid>] | stop_failure:<error>[:<sid>] |
quota_note:<type>[:<sid>]

`exit` ends with code 0, as `/exit` and Ctrl+C twice do in Claude Code
2.1.304; `crash` ends with code 1; `killed` dies of SIGKILL, which the parent
sees as -9. `session_end` runs the SessionEnd hooks hooks/hooks.json
registers, as Claude Code does as it closes, with that reason and this
session's id (or <sid>, another session's), then ends with <code>.
`stop_failure` runs the StopFailure hooks, as Claude Code does when a turn
ends on an API error, with that error and this session's id (or <sid>),
then acts out the plan's next step in the same launch: followed by
`limit:<resetsAt>` it is one stop seen both ways, by `stay` a stop after
which Claude sits idle. It first waits for the next whole second after
this process started: the hook keeps whole seconds, and a real turn ends
seconds after a launch, never within its first second.
`quota_note` runs the Notification hooks with that notification_type, as
Claude Code does at a moment of its wait at a usage limit (2.1.234+), the
same way: first in a launch, then the plan's next step. Right after a
`limit_busy:<resetsAt>` it is the end of the wait Claude Code started at
that stop: a second on, with the status file still saying busy.
`--version` prints FAKE_VERSION.
"""

import datetime as dt
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

born = time.time()
home = Path(os.environ["LONGHAUL_HOME"])
cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ["LONGHAUL_DEFAULT_CONFIG_DIR"])
args = sys.argv[1:]

if args == ["--version"]:
    print(f"{os.environ.get('FAKE_VERSION', '2.1.300')} (Claude Code)")
    sys.exit(0)

emails_file = home / "emails.json"
if args[:2] == ["auth", "status"]:
    key = "FAKE_EMAIL_PRO" if os.environ.get("CLAUDE_CONFIG_DIR") else "FAKE_EMAIL_MAX"
    emails = json.loads(emails_file.read_text()) if emails_file.exists() else {}
    email = emails.get(key, os.environ.get(key))
    print(json.dumps({"loggedIn": bool(email), "email": email, "subscriptionType": "max"}))
    sys.exit(0)

with (home / "calls.jsonl").open("a") as f:
    f.write(json.dumps({"args": args, "config": os.environ.get("CLAUDE_CONFIG_DIR"),
                        "api_key": os.environ.get("ANTHROPIC_API_KEY")}) + "\n")

plan_file = home / "plan.json"


def save_plan() -> None:
    """The steps left, written whole: longhaul stops a launch with SIGTERM
    at any moment, and a plan cut off mid-write crashed the next launch on
    an empty file (CI, 2026-10-27)."""
    tmp = plan_file.with_name(f".plan.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(plan))
    os.replace(tmp, plan_file)


plan = json.loads(plan_file.read_text())
step = plan.pop(0) if plan else "exit"
save_plan()

# A new conversation is sid-1, or the id --session-id gives it, as Claude Code's is.
sid = (args[args.index("--resume") + 1] if "--resume" in args
       else args[args.index("--session-id") + 1] if "--session-id" in args else "sid-1")
sessions = cfg / "sessions"
sessions.mkdir(parents=True, exist_ok=True)
transcript = cfg / "projects" / "-tmp-proj" / f"{sid}.jsonl"
transcript.parent.mkdir(parents=True, exist_ok=True)
transcript.touch()


def session(**extra):
    data = {"pid": os.getpid(), "sessionId": sid, "status": "idle", "kind": "interactive"}
    data.update(extra)
    (sessions / f"{os.getpid()}.json").write_text(json.dumps(data))


def run_hooks(event: str, **fields) -> None:
    """The `event` hooks hooks/hooks.json registers, run as Claude Code runs
    them: CLAUDE_PLUGIN_ROOT set, the event's fields on stdin; what a hook
    prints is not shown, and one past its timeout is ended."""
    root = str(Path(__file__).resolve().parent)
    data = {"session_id": sid, "transcript_path": str(transcript), "cwd": os.getcwd(),
            "hook_event_name": event, **fields}
    for entry in json.loads((Path(root) / "hooks" / "hooks.json").read_text())["hooks"].get(event, []):
        for hook in entry["hooks"]:
            try:
                subprocess.run([hook["command"], *(a.replace("${CLAUDE_PLUGIN_ROOT}", root) for a in hook["args"])],
                               input=json.dumps(data), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=root),
                               capture_output=True, text=True, timeout=hook.get("timeout", 60))
            except subprocess.TimeoutExpired:
                pass


signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
session(status="busy")

def quota_note(step: str) -> None:
    """A `quota_note:<type>[:<sid>]` step's Notification hooks."""
    _, kind, *named = step.split(":", 2)
    run_hooks("Notification", session_id=named[0] if named else sid, notification_type=kind)


while step.startswith(("stop_failure:", "quota_note:")):
    # The turn ends on an API error: Claude Code runs StopFailure's hooks;
    # or a moment of its wait at a usage limit: Notification's. The launch
    # goes on with the plan's next step, taken before the hooks run:
    # longhaul may stop this launch the moment a hook's row is there, and
    # the next launch must not get this one's step (CI, 2026-10-27).
    time.sleep(max(0.0, math.floor(born) + 1 - time.time()))
    hooked, step = step, (plan.pop(0) if plan else "exit")
    save_plan()
    if hooked.startswith("quota_note:"):
        quota_note(hooked)
    else:
        _, error, *named = hooked.split(":", 2)
        run_hooks("StopFailure", session_id=named[0] if named else sid, error=error)

if step.startswith(("limit:", "limit_busy:")):
    resets = float(step.split(":", 1)[1])
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    line = {"type": "assistant", "isSidechain": False, "isApiErrorMessage": True,
            "error": "rate_limit", "timestamp": now, "sessionId": sid, "uuid": f"limit-{os.getpid()}",
            "quotaLimits": {"status": "rejected", "resetsAt": resets, "rateLimitType": "five_hour"},
            "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "You've hit your session limit"}]}}
    with transcript.open("a") as f:
        f.write(json.dumps(line) + "\n")
    # limit_busy: the status file still says busy after the stop, as it may
    # while Claude Code waits for the reset itself (autoContinueAtUsageLimit).
    session(status="busy" if step.startswith("limit_busy:") else "idle")
    if step.startswith("limit_busy:") and plan and plan[0].startswith("quota_note:"):
        # The wait ends a second on, the watcher holding off meanwhile.
        ended = plan.pop(0)
        save_plan()
        time.sleep(1)
        quota_note(ended)
    while True:
        time.sleep(0.05)
elif step.startswith("rc_drop"):
    session(status="idle", bridgeSessionId="cse_fake")
    time.sleep(0.3)
    session(status="idle", bridgeSessionId=None)
    deadline = time.time() + (2 if step == "rc_drop_then_exit" else 60)
    while time.time() < deadline:
        time.sleep(0.05)
elif step.startswith("idle_wait"):
    # Idle for ten minutes already, as the status file says; with
    # "_agents", a background agent is still writing.
    session(status="idle", statusUpdatedAt=int((time.time() - 600) * 1000))
    if step == "idle_wait_agents":
        sub = transcript.with_suffix("") / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-1.jsonl").write_text("{}\n")
    deadline = time.time() + 3
    while time.time() < deadline:
        time.sleep(0.05)
elif step == "exit_after_signin_change":
    # Someone signs ~/.claude in to another account while this one runs,
    # and this one then crashes, so longhaul launches Claude again.
    emails_file.write_text(json.dumps({"FAKE_EMAIL_MAX": "stranger@example.com"}))
    session(status="idle")
    time.sleep(0.2)
    sys.exit(1)
elif step == "clear_then_crash":
    # /clear starts a new conversation in the same process: the session
    # file names the new id, then Claude crashes.
    session(status="idle", sessionId="sid-2")
    time.sleep(0.3)
    sys.exit(1)
elif step == "stay":
    # Left open and idle, as when you walk away from it; longhaul has to end it.
    session(status="idle")
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(0.05)
elif step == "crash":
    session(status="idle")
    time.sleep(0.2)
    sys.exit(1)
elif step.startswith("session_end:"):
    # Claude Code runs SessionEnd's hooks as it closes, with CLAUDE_PLUGIN_ROOT
    # set and the event's fields on stdin; what a hook prints is not shown.
    _, reason, code, *named = step.split(":", 3)
    session(status="idle")
    time.sleep(0.2)  # the watcher sees the session's id first
    run_hooks("SessionEnd", session_id=named[0] if named else sid, reason=reason)  # then the close goes on
    sys.exit(int(code))
elif step == "killed":
    # What the Sprint 6 live check does to a real claude (`kill -9`): no exit
    # code of its own, so the parent sees -9, not 1.
    session(status="idle")
    time.sleep(0.2)
    os.kill(os.getpid(), signal.SIGKILL)
else:
    session(status="idle")
    time.sleep(0.2)
