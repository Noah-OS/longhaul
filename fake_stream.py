#!/usr/bin/env python3
"""A stand-in for the claude binary as the VS Code extension runs it, for
longhaul's panel tests. It speaks the stream-json protocol recorded from 2.1.300
(initialize answered by a control_response, user messages replayed,
system/init at the first message, assistant and result lines), writes the
session and conversation files Claude Code writes, and acts out
stream_plan.json, one step per user message across launches:

  reply | limit

Without --input-format stream-json it is a short job: it records the call
and prints one line. `auth status --json` answers from FAKE_EMAILS, a JSON
map of config folder to email. Each call's record names which sign-in
variables reached it, for longhaul pipe's tests.
"""
import datetime as dt
import json
import os
import sys
import time
import uuid
from pathlib import Path

home = Path(os.environ["LONGHAUL_HOME"])
args = sys.argv[1:]
config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
cfg = Path(config_dir or os.environ["LONGHAUL_DEFAULT_CONFIG_DIR"])

if args[:2] == ["auth", "login"]:  # the browser sign-in: FAKE_EMAILS says who it was
    with (home / "logins.jsonl").open("a") as log_file:
        log_file.write(json.dumps({"config": config_dir, "args": args}) + "\n")
    sys.exit(0)

if args[:2] == ["auth", "status"]:
    if os.environ.get("FAKE_AUTH_HANG"):
        time.sleep(30)
    if os.environ.get("FAKE_AUTH_GARBAGE"):
        print("not json at all")
        sys.exit(1)
    email = json.loads(os.environ.get("FAKE_EMAILS", "{}")).get(config_dir or "default")
    if os.environ.get("FAKE_REQUIRE_LOGIN"):  # a new folder is signed out until its sign-in
        logins = home / "logins.jsonl"
        if not (logins.exists() and any(json.loads(l)["config"] == config_dir for l in logins.read_text().splitlines())):
            email = None
    print(json.dumps({"loggedIn": bool(email), "email": email, "subscriptionType": "max"}))
    sys.exit(0)

with (home / "calls.jsonl").open("a") as f:
    f.write(json.dumps({"pid": os.getpid(), "args": args, "config": config_dir,
                        "api_key": os.environ.get("ANTHROPIC_API_KEY"),
                        "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT"),
                        # names only, never values: which sign-ins reached claude (longhaul pipe)
                        "sign_ins": sorted(k for k in ("GITHUB_TOKEN", "SSH_AUTH_SOCK", "GIT_CONFIG_COUNT")
                                           if k in os.environ)}) + "\n")

chat = "--input-format=stream-json" in args or (
    "--input-format" in args and args[args.index("--input-format") + 1] == "stream-json")
if not chat:
    print("fake job done")
    sys.exit(0)

sid = "sid-1"
for a in args:
    if a.startswith("--resume="):
        sid = a.split("=", 1)[1]
if config_dir and config_dir == os.environ.get("FAKE_DIE_ON_RESUME_IN") and any(a.startswith("--resume=") for a in args):
    sys.exit(1)  # a claude that dies at start-up on this account
sessions = cfg / "sessions"
sessions.mkdir(parents=True, exist_ok=True)
transcript = cfg / "projects" / "-tmp-proj" / f"{sid}.jsonl"
transcript.parent.mkdir(parents=True, exist_ok=True)
transcript.touch()


def emit(obj):
    # Compact, like claude; a result line carries its "type" last, as
    # 2.1.300 does ("duration_api_ms" first, "type" 1,600 bytes in).
    if obj.get("type") == "result":
        front = {"duration_api_ms": 1, "stop_reason": "end_turn", "session_id": obj.get("session_id"),
                 "total_cost_usd": obj.get("total_cost_usd", 0), "usage": {"padding": "x" * 1500}}
        obj = {**front, **{k: v for k, v in obj.items() if k not in front and k != "type"}, "type": "result"}
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def status(value):
    (sessions / f"{os.getpid()}.json").write_text(json.dumps(
        {"pid": os.getpid(), "sessionId": sid, "status": value, "statusUpdatedAt": int(time.time() * 1000)}))


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


status("idle")
cost = 0.0
plan_file = home / "stream_plan.json"
for raw in sys.stdin:
    msg = json.loads(raw)
    with (home / "received.jsonl").open("a") as f:
        f.write(json.dumps({"pid": os.getpid(), "msg": msg}) + "\n")
    if msg.get("type") == "control_request":
        sub = msg["request"]["subtype"]
        emit({"type": "control_response", "response": {
            "subtype": "success", "request_id": msg["request_id"],
            "response": {"pid": os.getpid()} if sub == "initialize" else {}}})
        if sub == "initialize":
            emit({"type": "auth_status", "isAuthenticating": False, "output": [],
                  "uuid": str(uuid.uuid4()), "session_id": sid})
        continue
    if msg.get("type") != "user":
        continue
    # like 2.1.300: a system/init at the start of every turn
    emit({"type": "system", "subtype": "init", "session_id": sid, "cwd": os.getcwd()})
    emit({"type": "user", "uuid": msg["uuid"], "isReplay": True, "session_id": sid,
          "parent_tool_use_id": None, "message": msg["message"]})
    status("busy")
    plan = json.loads(plan_file.read_text()) if plan_file.exists() else []
    step = plan.pop(0) if plan else "reply"
    plan_file.write_text(json.dumps(plan))
    if step == "limit_partial":
        # stopped by longhaul mid-line: the half line is what a killed claude leaves
        import signal as _signal
        _signal.signal(_signal.SIGTERM, lambda *_: (sys.stdout.write('{"type":"assist'), sys.stdout.flush(), os._exit(0)))
        step = "limit"
    if step.startswith("exit:"):
        emit({"type": "assistant", "session_id": sid, "user_message_uuid": msg["uuid"],
              "message": {"role": "assistant", "content": [{"type": "text", "text": "bye"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "bye", "session_id": sid,
              "user_message_uuid": msg["uuid"], "total_cost_usd": 0.1})
        status("idle")
        sys.exit(int(step.split(":", 1)[1]))
    if step == "limit":
        line = {"type": "assistant", "isSidechain": False, "isApiErrorMessage": True, "error": "rate_limit",
                "timestamp": now(), "sessionId": sid, "uuid": f"limit-{os.getpid()}-{uuid.uuid4().hex[:6]}",
                "quotaLimits": {"status": "rejected", "resetsAt": time.time() + 3600, "rateLimitType": "five_hour"},
                "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "You've hit your session limit"}]}}
        with transcript.open("a") as f:
            f.write(json.dumps(line) + "\n")
        emit({"type": "assistant", "error": "rate_limit", "is_api_error_message": True, "session_id": sid,
              "message": line["message"], "user_message_uuid": msg["uuid"]})
        emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
              "resetsAt": line["quotaLimits"]["resetsAt"], "unifiedWindows": {
                  "five_hour": {"utilization": 1.0, "resetsAt": line["quotaLimits"]["resetsAt"]}}}})
        emit({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
              "result": "You've hit your session limit", "session_id": sid, "user_message_uuid": msg["uuid"]})
    else:
        text = msg["message"]["content"][0]["text"]
        emit({"type": "assistant", "session_id": sid, "user_message_uuid": msg["uuid"],
              "message": {"role": "assistant", "content": [{"type": "text", "text": f"reply to: {text}"}]}})
        if config_dir != os.environ.get("FAKE_NO_EVENT_IN"):
          emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
              "five_hour": {"utilization": float(os.environ.get("FAKE_UTILIZATION", "0.3")), "resetsAt": time.time() + 3600},
              "seven_day": {"utilization": 0.2, "resetsAt": time.time() + 86400}}}})
        cost += 0.25
        emit({"type": "result", "subtype": "success", "is_error": False, "result": f"reply to: {text}",
              "session_id": sid, "user_message_uuid": msg["uuid"], "total_cost_usd": cost})
    status("idle")
