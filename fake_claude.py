#!/usr/bin/env python3
"""A stand-in for `claude` in longhaul's tests: it writes the same files Claude
Code writes (sessions/<pid>.json, projects/*/<sid>.jsonl) and acts out one
step of FAKE_PLAN per launch, so longhaul's loop runs without an account, a
network or a terminal.

Steps: limit:<resetsAt> | limit_busy:<resetsAt> | exit | crash | rc_drop |
rc_drop_then_exit | idle_wait | idle_wait_agents | exit_after_signin_change |
clear_then_crash | stay

`exit` ends with code 0, as `/exit` and Ctrl+C twice do in Claude Code
2.1.304; `crash` ends with code 1. `--version` prints FAKE_VERSION.
"""

import datetime as dt
import json
import os
import signal
import sys
import time
from pathlib import Path

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
plan = json.loads(plan_file.read_text())
step = plan.pop(0) if plan else "exit"
plan_file.write_text(json.dumps(plan))

sid = args[args.index("--resume") + 1] if "--resume" in args else "sid-1"
sessions = cfg / "sessions"
sessions.mkdir(parents=True, exist_ok=True)
transcript = cfg / "projects" / "-tmp-proj" / f"{sid}.jsonl"
transcript.parent.mkdir(parents=True, exist_ok=True)
transcript.touch()


def session(**extra):
    data = {"pid": os.getpid(), "sessionId": sid, "status": "idle", "kind": "interactive"}
    data.update(extra)
    (sessions / f"{os.getpid()}.json").write_text(json.dumps(data))


signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
session(status="busy")

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
else:
    session(status="idle")
    time.sleep(0.2)
