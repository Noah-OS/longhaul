"""longhaul's tests: the loop is played against fake_claude.py, which writes the
files Claude Code writes, so nothing here needs an account or the network.

Run: python3 -m unittest -v  (from the checkout, with the scratch LONGHAUL_* paths)
"""

import collections
import contextlib
import datetime as dt
import fcntl
import io
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest import mock
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import longhaul  # noqa: E402


def longhaul_lock(home: Path, sid: str):
    """Hold the lock a running longhaul takes on a conversation, as another longhaul would."""
    import fcntl
    (home / "locks").mkdir(exist_ok=True)
    f = (home / "locks" / f"{sid}.lock").open("a")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def registered_hook(event: str, command: str) -> list:
    """The command line hooks/hooks.json registers for `command` on `event`,
    run with this Python and this checkout as the plugin's root."""
    entries = json.loads((HERE / "hooks" / "hooks.json").read_text())["hooks"][event]
    [args] = [e["hooks"][0]["args"] for e in entries if f"main(['{command}'])" in e["hooks"][0]["args"][2]]
    return [sys.executable, *args[:-1], str(HERE)]


def requires_git(test: unittest.TestCase):
    """Skip a test that asks git about the checkout, so the suite runs from
    a tarball (no .git) and where git is not installed."""
    if not (HERE / ".git").exists() or not shutil.which("git"):
        test.skipTest("not a git checkout")


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "relay"
        self.max = self.tmp / "max"
        self.pro = self.tmp / "pro"
        for d in (self.home, self.max, self.pro):
            d.mkdir()
        (self.home / "accounts.json").write_text(json.dumps(
            [{"name": "Max", "config_dir": None}, {"name": "Pro", "config_dir": str(self.pro)}]))

    def run_longhaul(self, plan, *argv, runs=2, timeout=30, answer=None, cwd=None, **env):
        """longhaul against the fake Claude. `answer`: keys typed, again and again,
        at a terminal longhaul gets as its stdin; without it there is no terminal.
        `cwd`: the folder longhaul runs in (default: the scratch folder)."""
        (self.home / "plan.json").write_text(json.dumps(plan))
        # LONGHAUL_SWITCH=auto: the tests written before Sprint 1 assert today's
        # moves, which became the "auto" mode when "ask" became the default.
        full = dict(os.environ, LONGHAUL_HOME=str(self.home), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.max),
                    LONGHAUL_CLAUDE_BIN=str(HERE / "fake_claude.py"), LONGHAUL_POLL_SECONDS="0.1",
                    LONGHAUL_REOPEN_DELAY="0", LONGHAUL_MAX_RUNS=str(runs), LONGHAUL_NO_NOTIFY="1",
                    LONGHAUL_NO_CAFFEINATE="1", LONGHAUL_SKIP_NET_CHECK="1", LONGHAUL_RC_GRACE_SECONDS="0.3",
                    LONGHAUL_RESET_MARGIN="0", ANTHROPIC_API_KEY="sk-test-not-real", LONGHAUL_SWITCH="auto")
        full.update(env)
        full.pop("CLAUDE_CONFIG_DIR", None)
        cmd = [sys.executable, str(HERE / "longhaul.py"), *argv]
        master, slave = pty.openpty() if answer is not None else (None, None)
        done, typist = threading.Event(), None

        def typing():  # and reading what the terminal shows, as a real one does
            while not done.wait(0.1):
                try:
                    os.write(master, answer)
                    while select.select([master], [], [], 0)[0]:
                        os.read(master, 1024)
                except OSError:
                    return

        try:
            # Its own process group: killed on a timeout, longhaul cannot stop the
            # Claude it started, which then holds the pipes open (review, Sprint 1).
            proc = subprocess.Popen(cmd, env=full, cwd=cwd or self.tmp,
                                    stdin=slave if slave is not None else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
            if slave is not None:
                os.close(slave)
                slave = None
                typist = threading.Thread(target=typing, daemon=True)
                typist.start()
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=10)
                raise
        finally:
            done.set()
            if typist:
                typist.join(2)
            for fd in (slave, master):
                if fd is not None:
                    os.close(fd)
        out = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        log = self.home / "calls.jsonl"
        calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
        return out, calls

    def pin_emails(self):
        (self.home / "accounts.json").write_text(json.dumps([
            {"name": "Max", "config_dir": None, "email": "first@example.com"},
            {"name": "Pro", "config_dir": str(self.pro), "email": "second@example.com"}]))

    def test_the_first_accounts_limit_line_is_not_taken_for_the_second_accounts(self):
        # 2026-10-15: the Max line written a second before the switch was
        # read again under Pro and marked a free Pro account as limited.
        out, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"])
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertEqual(calls[1]["config"], str(self.pro))
        self.assertTrue((self.pro / "projects").is_symlink(), "Pro reads the same conversation file")
        log = (self.home / "longhaul.log").read_text()
        self.assertIn("Pro ended: completed", log)
        self.assertNotIn("Pro ended: limit", log)

    def test_a_limit_stop_is_acted_on_once(self):
        out, _ = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"])
        self.assertEqual(len(self.state()["handled_limits"]), 1)
        self.assertEqual((self.home / "longhaul.log").read_text().count("ended: limit"), 1)

    def test_the_sign_in_is_checked_again_before_every_launch(self):
        self.pin_emails()
        out, calls = self.run_longhaul(["exit_after_signin_change", "exit"], FAKE_EMAIL_MAX="first@example.com",
                                 FAKE_EMAIL_PRO="second@example.com")
        self.assertEqual(len(calls), 2, out.stdout)
        self.assertIsNone(calls[0]["config"])
        self.assertEqual(calls[1]["config"], str(self.pro), "Max is now someone else, so longhaul moves to Pro")
        self.assertIn("should be first@example.com but is signed in as stranger@example.com", out.stdout)

    def test_an_account_signed_in_as_someone_else_is_never_used(self):
        self.pin_emails()
        out, calls = self.run_longhaul(["exit"], runs=1, FAKE_EMAIL_MAX="stranger@example.com",
                                 FAKE_EMAIL_PRO="second@example.com")
        self.assertEqual(len(calls), 1, out.stdout)
        self.assertEqual(calls[0]["config"], str(self.pro))
        self.assertIn("should be first@example.com but is signed in as stranger@example.com", out.stdout)

    def test_longhaul_stops_when_no_account_is_signed_in_as_expected(self):
        self.pin_emails()
        out, calls = self.run_longhaul(["exit"], runs=1, FAKE_EMAIL_MAX="stranger@example.com")
        self.assertEqual(calls, [])
        self.assertEqual(out.returncode, 1)

    def test_accounts_signed_in_as_expected_are_used_in_order(self):
        self.pin_emails()
        _, calls = self.run_longhaul(["exit"], runs=1, FAKE_EMAIL_MAX="first@example.com",
                               FAKE_EMAIL_PRO="second@example.com")
        self.assertIsNone(calls[0]["config"])

    def state(self):
        return json.loads((self.home / "state.json").read_text())

    def test_a_limit_moves_the_conversation_to_the_other_account_with_carry_on(self):
        resets = time.time() + 3600
        out, calls = self.run_longhaul([f"limit:{resets}", "exit"])
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertIsNone(calls[0]["config"])
        self.assertEqual(calls[1]["config"], str(self.pro))
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertTrue(calls[1]["args"][-1].startswith("The Max account hit its session limit"))
        self.assertIn("--remote-control", calls[1]["args"])
        self.assertAlmostEqual(self.state()["limited_until"]["Max"], resets, places=0)

    def test_when_every_account_is_at_its_limit_longhaul_waits_for_the_first_reset(self):
        (self.home / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3600}}))
        started = time.time()
        out, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"])
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertIsNone(calls[1]["config"], "Max resets first, so longhaul goes back to Max")
        self.assertGreaterEqual(time.time() - started, 2)
        self.assertIn("Every account is at its limit", out.stdout)
        self.assertTrue(calls[1]["args"][-1].startswith("The Max account hit"))

    # Sprint 1, D3: longhaul asks before it moves a conversation to another account
    # ("ask", the default), moves as before ("auto"), or never moves ("never").

    def test_by_default_a_limit_asks_and_with_no_answer_waits_on_the_same_account(self):
        started = time.time()
        out, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"], LONGHAUL_SWITCH="")
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertEqual([c["config"] for c in calls], [None, None], "it stays on Max")
        self.assertGreaterEqual(time.time() - started, 2, "after Max's reset")
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertTrue(calls[1]["args"][-1].startswith("The Max account hit its session limit, so longhaul waited"))
        self.assertIn("Move this conversation to Pro now?", out.stdout)
        self.assertIn("no terminal to answer in, so longhaul waits and carries on with Max at", out.stdout)

    def test_in_ask_mode_a_yes_moves_the_conversation_as_before(self):
        out, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], LONGHAUL_SWITCH="ask", answer=b"y")
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertEqual(calls[1]["config"], str(self.pro))
        self.assertTrue(calls[1]["args"][-1].startswith("The Max account hit its session limit, so longhaul reopened"))
        # Review, Sprint 1: after the y, the limit was said a second time.
        self.assertEqual(out.stdout.count("hit its session limit"), 1, out.stdout)
        self.assertIn("Moving to Pro", out.stdout)

    def test_in_ask_mode_q_at_the_question_stops_longhaul(self):
        out, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], LONGHAUL_SWITCH="ask", answer=b"q")
        self.assertEqual((out.returncode, len(calls)), (0, 1), out.stderr)
        self.assertIn("Stopped at your request", out.stdout)
        self.assertIn("longhaul -r sid-1", out.stdout)

    def test_with_no_answer_by_the_reset_longhaul_still_waits_the_safety_margin(self):
        # Review, Sprint 1: the question ran to the reset second, and the
        # reopen then skipped LONGHAUL_RESET_MARGIN; a limit not yet lifted by the
        # server would stop Claude again at once.
        # Two seconds to the reset, as the neighbouring tests give: with one,
        # a loaded machine reached the reset before the question was asked
        # and the test failed once in a full run (Sprint 5).
        started = time.time()
        out, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"], LONGHAUL_SWITCH="ask", answer=b"",
                                 LONGHAUL_RESET_MARGIN="2")
        self.assertEqual([c["config"] for c in calls], [None, None], out.stderr)
        self.assertGreaterEqual(time.time() - started, 4)
        self.assertIn("No answer by the reset", out.stdout)

    def test_a_yes_never_moves_to_an_account_signed_in_as_someone_else(self):
        # Review, Sprint 1: the question offered Pro unchecked, and the yes
        # then ended longhaul saying it "does not move the conversation by itself".
        self.pin_emails()
        out, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"], LONGHAUL_SWITCH="ask", answer=b"y",
                                 FAKE_EMAIL_MAX="first@example.com", FAKE_EMAIL_PRO="stranger@example.com")
        self.assertEqual([c["config"] for c in calls], [None, None], out.stdout)
        self.assertNotIn("Move this conversation", out.stdout)
        self.assertIn("should be second@example.com but is signed in as stranger@example.com", out.stdout)

    def test_in_never_mode_a_reopen_after_a_crash_stays_on_the_same_account(self):
        self.reading("Max", five=50, five_in=7200)
        self.reading("Pro", five=10, week=40, week_in=1200)
        _, calls = self.run_longhaul(["crash", "exit"], "--account", "Max", LONGHAUL_SWITCH="never")
        self.assertEqual([c["config"] for c in calls], [None, None])

    def test_the_switch_flag_never_wins_over_auto_in_the_environment(self):
        _, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"], "--switch", "never", LONGHAUL_SWITCH="auto")
        self.assertEqual([c["config"] for c in calls], [None, None])

    def test_the_helper_leaves_no_claude_behind_when_longhaul_hangs(self):
        # Review, Sprint 1: killing longhaul alone left its fake Claude running.
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_longhaul(["stay"], runs=1, timeout=2)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", self.tmp.name], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [], "processes left running")

    def test_in_ask_mode_a_reopen_after_a_crash_stays_on_the_same_account(self):
        # Pro's weekly allowance runs out first, so pick() alone would reopen on Pro.
        self.reading("Max", five=50, five_in=7200)
        self.reading("Pro", five=10, week=40, week_in=1200)
        _, calls = self.run_longhaul(["crash", "exit"], "--account", "Max", LONGHAUL_SWITCH="ask")
        self.assertEqual([c["config"] for c in calls], [None, None])

    def test_in_ask_mode_a_nearly_full_idle_session_is_not_moved(self):
        self.reading("Max", five=96)
        self.run_longhaul(["idle_wait"], "--account", "Max", runs=1, LONGHAUL_IDLE_MOVE_SECONDS="0", LONGHAUL_SWITCH="ask")
        self.assertIn("Max ended: completed", (self.home / "longhaul.log").read_text())

    def test_in_ask_mode_with_every_account_at_its_limit_longhaul_waits_for_its_own_without_asking(self):
        # Pro resets first, so "auto" would carry on there. With no one asked,
        # "ask" waits for Max.
        started = time.time()
        (self.home / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3}}))
        out, calls = self.run_longhaul([f"limit:{time.time() + 5}", "exit"], LONGHAUL_SWITCH="ask", answer=b"y")
        self.assertEqual([c["config"] for c in calls], [None, None], out.stderr)
        self.assertGreaterEqual(time.time() - started, 5)
        self.assertNotIn("Move this conversation", out.stdout)

    def test_in_never_mode_a_limit_waits_for_the_reset_without_asking(self):
        out, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"], LONGHAUL_SWITCH="never", answer=b"y")
        self.assertEqual([c["config"] for c in calls], [None, None], out.stderr)
        self.assertNotIn("Move this conversation", out.stdout)

    def test_the_switch_flag_wins_over_the_environment(self):
        _, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], "--switch", "auto", LONGHAUL_SWITCH="never")
        self.assertEqual(calls[1]["config"], str(self.pro))

    def test_an_unknown_switch_mode_stops_longhaul_before_claude_starts(self):
        out, calls = self.run_longhaul(["exit"], runs=1, LONGHAUL_SWITCH="sometimes")
        self.assertEqual((out.returncode, calls), (2, []))
        self.assertIn("Set it to ask, auto or never, or unset it", out.stdout + out.stderr)

    def test_longhaul_status_names_the_switch_mode_in_force_and_what_ignores_it(self):
        # The mode is always the second word, for scripts.
        out, _ = self.run_longhaul([], "status", LONGHAUL_SWITCH="never")
        self.assertIn("Switching: never (set by LONGHAUL_SWITCH)", out.stdout)
        out, _ = self.run_longhaul([], "status", LONGHAUL_SWITCH="")
        self.assertIn("Switching: ask (the default)", out.stdout)
        # Sprint 2, D3c: what pipe and the panel do, not "still move as auto".
        # The Switching line is the last line and the mode its second word.
        last = out.stdout.splitlines()[-1]
        self.assertEqual(last.split()[:2], ["Switching:", "ask"])
        self.assertIn("longhaul pipe moves a session only when its driver passes --swap and the mode there is auto", last)
        self.assertIn("The VS Code panel never moves a chat (switch_mid_session is off)", last)
        # Sprint 5, D3d: a resume by id is a move too.
        self.assertIn("A conversation resumed by id starts on the account it last ran on in ask and never", last)
        (self.home / "vscode.json").write_text(json.dumps({"switch_mid_session": True, "switch": "never"}))
        out, _ = self.run_longhaul([], "status", LONGHAUL_SWITCH="")
        self.assertIn("The VS Code panel moves a chat at a limit only in auto; it is never (from vscode.json)",
                      out.stdout.splitlines()[-1])

    def test_longhaul_status_with_an_unknown_switch_mode_says_so_and_fails(self):
        out, _ = self.run_longhaul([], "status", LONGHAUL_SWITCH="sometimes")
        self.assertEqual(out.returncode, 2)
        self.assertIn("Switching: invalid", out.stdout)

    def test_a_lost_remote_control_link_reopens_the_conversation_on_the_same_account(self):
        out, calls = self.run_longhaul(["rc_drop", "exit"])
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertIsNone(calls[1]["config"])
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertFalse(calls[1]["args"][-1].startswith("The "), "no carry-on message after a reconnect")
        self.assertIn("Remote Control was lost", out.stdout)

    def test_reconnecting_stops_after_the_limit_and_claude_carries_on_without_remote_control(self):
        # Before: a link that kept dropping was reopened all night, every
        # RC_GRACE, with nothing said.
        out, calls = self.run_longhaul(["rc_drop_then_exit"] * 5, runs=0, LONGHAUL_MAX_RECONNECTS="2")
        self.assertEqual(len(calls), 3, out.stdout + out.stderr)  # the first launch and 2 reconnects
        self.assertEqual(out.returncode, 0, "the third Claude was left running and closed normally")
        self.assertEqual(out.stdout.count("Remote Control was lost 3 times in 30 minutes"), 1, out.stdout)
        log = (self.home / "longhaul.log").read_text()
        self.assertEqual(log.count("running without Remote Control"), 1, "said and notified once, not at every poll")
        self.assertEqual(log.count("Remote Control was lost 3 times"), 1, "logged when it happened, not again when shown")

    def test_remote_control_that_moved_to_another_window_is_left_alone(self):
        sessions = self.max / "sessions"
        sessions.mkdir()
        (sessions / "other.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "sid-1"}))
        out, calls = self.run_longhaul(["rc_drop_then_exit"], runs=1)
        self.assertEqual(len(calls), 1, out.stderr)
        self.assertIn("leaving it there", (self.home / "longhaul.log").read_text())

    def test_an_old_limit_line_in_a_resumed_conversation_does_not_switch(self):
        transcript = self.max / "projects" / "-tmp-proj" / "sid-old.jsonl"
        transcript.parent.mkdir(parents=True)
        yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        transcript.write_text(json.dumps({
            "type": "assistant", "isApiErrorMessage": True, "error": "rate_limit", "timestamp": yesterday,
            "quotaLimits": {"status": "rejected", "resetsAt": time.time() + 3600}}) + "\n")
        out, calls = self.run_longhaul(["exit"], "-r", "sid-old", runs=1)
        self.assertEqual(calls[0]["args"][:2], ["--resume", "sid-old"])
        self.assertFalse((self.home / "state.json").exists(), out.stdout)

    def test_a_crashed_claude_is_reopened_on_the_same_conversation(self):
        out, calls = self.run_longhaul(["crash", "exit"])
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertEqual(calls[0]["args"][0], "--remote-control")
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])

    # -- Phase 1: a session left alone overnight carries on, waits, or stops
    # and says why; it never loops and never runs twice.

    def test_a_clean_exit_ends_longhaul_instead_of_reopening(self):
        # /exit and Ctrl+C twice both end Claude Code 2.1.304 with code 0.
        # Reopened, a finished overnight task would start again every 12 s.
        out, calls = self.run_longhaul(["exit", "exit"], runs=0)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn("closed normally", out.stdout)
        self.assertIn("running -> completed", (self.home / "longhaul.log").read_text())

    def test_reopening_after_crashes_stops_after_the_limit_and_says_so(self):
        # Before: a conversation that crashed on every start was reopened
        # for as long as the Mac stayed awake.
        out, calls = self.run_longhaul(["crash"] * 10, runs=0, LONGHAUL_MAX_REOPENS="3")
        self.assertEqual(len(calls), 4, out.stdout + out.stderr)  # the first launch and 3 reopens
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("crashed 4 times in 30 minutes", out.stdout)
        self.assertEqual(out.returncode, 1)
        self.assertIn("crashed -> failed", (self.home / "longhaul.log").read_text())

    def test_crashes_spread_far_apart_do_not_add_up_to_the_limit(self):
        out, calls = self.run_longhaul(["crash", "crash", "crash", "exit"], runs=0,
                                 LONGHAUL_MAX_REOPENS="1", LONGHAUL_REOPEN_WINDOW="0")
        self.assertEqual(len(calls), 4, out.stdout + out.stderr)
        self.assertEqual(out.returncode, 0)

    def test_a_claude_killed_by_a_signal_is_reopened_and_the_signal_is_said(self):
        # Sprint 6, R5: a real crash's exit code has not been observed, and the
        # live check kills claude with SIGKILL, so the suite has that case first.
        # The code is -9, not 1: the log must say which.
        out, calls = self.run_longhaul(["killed", "exit"], runs=2)
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertIn("Max ended: crashed (session sid-1, exit -9)", (self.home / "longhaul.log").read_text())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)  # MAX_RUNS reached

    def test_a_claude_killed_on_the_last_run_still_ends_longhaul_with_exit_0(self):
        # The killed run is the one that reaches MAX_RUNS, so its -9 is what
        # exit_code() gets: the default comes back, not -9 (247 to the shell).
        out, calls = self.run_longhaul(["killed"], runs=1)
        self.assertEqual(len(calls), 1, out.stdout + out.stderr)
        self.assertIn("Max ended: crashed (session sid-1, exit -9)", (self.home / "longhaul.log").read_text())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_conversation_another_longhaul_supervises_is_refused(self):
        held = longhaul_lock(self.home, "sid-held")
        out, calls = self.run_longhaul(["exit"], "-r", "sid-held", runs=1)
        held.close()
        self.assertEqual(out.returncode, 1)
        self.assertEqual(calls, [])
        self.assertIn("another longhaul is already looking after this conversation", out.stdout)

    def test_a_new_conversation_is_not_reopened_while_another_longhaul_holds_it(self):
        # A second `longhaul -r sid-1` started while this one waited between
        # launches: only one of them may carry the conversation on.
        held = longhaul_lock(self.home, "sid-1")
        out, calls = self.run_longhaul(["crash", "exit"], runs=0)
        held.close()
        self.assertEqual(len(calls), 1, out.stdout + out.stderr)
        self.assertEqual(out.returncode, 1)
        self.assertIn("another longhaul is already looking after this conversation", out.stdout)

    def test_a_continued_conversation_another_longhaul_holds_is_let_go_as_soon_as_claude_names_it(self):
        # `longhaul -c` learns which conversation --continue opened only from
        # Claude's session file. Before, it locked it when that launch ended,
        # so two `longhaul -c` started together ran one conversation side by side.
        held = longhaul_lock(self.home, "sid-1")
        started = time.time()
        out, calls = self.run_longhaul(["stay"], "-c", runs=0, timeout=25)
        held.close()
        self.assertEqual(len(calls), 1, out.stdout + out.stderr)
        self.assertEqual(out.returncode, 1)
        self.assertIn("another longhaul is already looking after this conversation", out.stdout)
        self.assertLess(time.time() - started, 15, "longhaul stopped its own Claude rather than leave it running")
        self.assertIn("ended: held", (self.home / "longhaul.log").read_text())

    def test_the_lock_is_let_go_when_longhaul_ends(self):
        self.run_longhaul(["exit"], "-r", "sid-1", runs=1)
        out, calls = self.run_longhaul(["exit"], "-r", "sid-1", runs=1)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_lock_that_cannot_be_made_never_stops_claude_starting(self):
        # A read-only or network LONGHAUL_HOME: the guard is off, and said so,
        # but Claude still starts and is still looked after.
        (self.home / "locks").write_text("")  # a file where the folder should be
        out, calls = self.run_longhaul(["crash", "exit"], "-r", "sid-1", runs=0)
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(out.returncode, 0)
        self.assertIn("cannot guard this conversation against a second longhaul", out.stdout)

    def test_the_lock_follows_the_conversation_when_its_id_changes(self):
        # After /clear the session file names a new conversation, which is
        # the one longhaul reopens; another longhaul holding that one wins.
        held = longhaul_lock(self.home, "sid-2")
        out, calls = self.run_longhaul(["clear_then_crash", "exit"], "-r", "sid-1", runs=0)
        held.close()
        self.assertEqual(len(calls), 1, out.stdout + out.stderr)
        self.assertEqual(out.returncode, 1)
        self.assertIn("another longhaul is already looking after this conversation", out.stdout)

    def test_a_conversation_id_cannot_name_a_lock_outside_the_lock_folder(self):
        self.assertIs(longhaul.hold("../escape"), longhaul.NO_LOCK)

    def test_an_untested_claude_code_version_is_said_and_claude_still_starts(self):
        out, calls = self.run_longhaul(["exit"], runs=1, FAKE_VERSION="2.1.999")
        self.assertEqual(len(calls), 1)
        self.assertIn("2.1.999", out.stdout)
        self.assertIn("not been checked", out.stdout)

    def test_a_tested_claude_code_version_is_not_mentioned(self):
        out, _ = self.run_longhaul(["exit"], runs=1, FAKE_VERSION="2.1.300")
        self.assertNotIn("not been checked", out.stdout)
        self.assertIn("claude 2.1.300", (self.home / "longhaul.log").read_text())

    def test_a_limit_is_acted_on_even_while_claude_still_says_busy(self):
        # Claude Code 2.1.234+ waits for the reset itself; if its status
        # file says busy meanwhile, longhaul still switches after the grace.
        out, calls = self.run_longhaul([f"limit_busy:{time.time() + 3600}", "exit"],
                                 LONGHAUL_LIMIT_BUSY_GRACE="0.5")
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(calls[1]["config"], str(self.pro))

    def test_an_api_key_in_the_shell_never_reaches_claude(self):
        _, calls = self.run_longhaul(["exit"], runs=1)
        self.assertIsNone(calls[0]["api_key"])

    def test_a_short_id_resumes_the_one_conversation_it_matches_under_its_name(self):
        transcript = self.max / "projects" / "-tmp-proj" / "557ce21f-c99c-49f0-93e5-22ae7d1d1ae7.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("")
        _, calls = self.run_longhaul(["exit"], "-r", "557ce21f", "--name", "docs", runs=1)
        self.assertEqual(calls[0]["args"][:2], ["--resume", "557ce21f-c99c-49f0-93e5-22ae7d1d1ae7"])
        self.assertIn("docs · Max", calls[0]["args"])

    def test_a_conversation_still_open_elsewhere_is_not_opened_twice(self):
        sessions = self.max / "sessions"
        sessions.mkdir()
        (sessions / "1.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": "sid-open", "entrypoint": "claude-vscode"}))
        out, calls = self.run_longhaul(["exit"], "-r", "sid-open", runs=1)
        self.assertEqual(calls, [])
        self.assertEqual(out.returncode, 1)
        self.assertIn("still open in the VS Code panel", out.stdout)

    def test_extra_arguments_after_the_double_dash_reach_every_launch(self):
        _, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], "--", "--permission-mode", "auto")
        for call in calls:
            self.assertIn("--permission-mode", call["args"])

    # -- a session's first message is sent once. Put after `--` it
    # went to Claude on every launch, so each account move or reopen would
    # have given the session its task again from the start.

    FIRST = "Work on TASK-1 in this folder.\nRun the tests before you commit."

    def first(self, text=FIRST + "\n"):
        path = self.tmp / "first.txt"
        path.write_text(text)
        return path

    def test_the_first_message_is_sent_on_the_first_launch_only(self):
        out, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], "--first", str(self.first()))
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(calls[0]["args"][-1], self.FIRST)
        self.assertEqual(calls[1]["config"], str(self.pro))
        self.assertTrue(calls[1]["args"][-1].startswith("The Max account hit its session limit"),
                        "the new account is told to carry on, not given the task again")
        self.assertNotIn(self.FIRST, calls[1]["args"])

    def test_the_first_message_is_not_sent_again_after_an_idle_move(self):
        self.reading("Max", five=96)
        out, calls = self.run_longhaul(["idle_wait", "exit"], "--account", "Max", "--first", str(self.first()),
                                 LONGHAUL_IDLE_MOVE_SECONDS="0")
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(calls[0]["args"][-1], self.FIRST)
        self.assertEqual(calls[1]["config"], str(self.pro))
        self.assertNotIn(self.FIRST, calls[1]["args"])

    def test_the_first_message_is_not_sent_again_when_claude_is_reopened(self):
        out, calls = self.run_longhaul(["crash", "exit"], "--first", str(self.first()))
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(calls[0]["args"][-1], self.FIRST)
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertNotIn(self.FIRST, calls[1]["args"])

    def test_the_first_message_is_not_sent_again_when_remote_control_is_reconnected(self):
        out, calls = self.run_longhaul(["rc_drop", "exit"], "--first", str(self.first()))
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        self.assertEqual(calls[0]["args"][-1], self.FIRST)
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertNotIn(self.FIRST, calls[1]["args"])

    def test_claude_flags_after_the_double_dash_reach_every_launch_beside_a_first_message(self):
        settings = self.tmp / "session-settings.json"
        settings.write_text("{}")
        out, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], "--first", str(self.first()),
                                 "--", "--settings", str(settings), "--permission-mode", "default")
        self.assertEqual(len(calls), 2, out.stdout + out.stderr)
        for call in calls:
            args = call["args"]
            self.assertEqual(args[args.index("--settings") + 1], str(settings))
            self.assertEqual(args[args.index("--permission-mode") + 1], "default")
        self.assertEqual(calls[0]["args"][-1], self.FIRST, "the message comes after Claude's own flags")

    def test_a_first_message_waits_for_the_first_launch_when_every_account_is_at_its_limit(self):
        now = time.time()
        (self.home / "state.json").write_text(json.dumps({"limited_until": {"Max": now + 2, "Pro": now + 3600}}))
        out, calls = self.run_longhaul(["exit"], "--first", str(self.first()), runs=1)
        self.assertIn("Every account is at its limit", out.stdout)
        self.assertEqual(len(calls), 1, out.stdout + out.stderr)
        self.assertEqual(calls[0]["args"][-1], self.FIRST)

    def test_the_first_message_is_kept_out_of_cws_log(self):
        out, calls = self.run_longhaul(["exit"], "--first", str(self.first()), runs=1)
        self.assertEqual(len(calls), 1, out.stdout + out.stderr)
        log = (self.home / "longhaul.log").read_text()
        self.assertIn("launch Max", log)
        self.assertNotIn("TASK-1", log)

    def test_a_first_message_that_cannot_be_read_stops_longhaul_before_any_launch(self):
        out, calls = self.run_longhaul(["exit"], "--first", str(self.tmp / "missing.txt"), runs=1)
        self.assertEqual(calls, [])
        self.assertEqual(out.returncode, 2)
        self.assertIn("could not read the first message", out.stdout)

    def test_an_empty_first_message_stops_longhaul_before_any_launch(self):
        out, calls = self.run_longhaul(["exit"], "--first", str(self.first(" \n\n")), runs=1)
        self.assertEqual(calls, [])
        self.assertEqual(out.returncode, 2)
        self.assertIn("is empty", out.stdout)

    def test_an_empty_first_message_path_stops_longhaul_before_any_launch(self):
        # A launcher that quotes an empty path variable sends `--first ""`;
        # treating that as "no first message" starts Claude with no task.
        out, calls = self.run_longhaul(["exit"], "--first", "", runs=1)
        self.assertEqual(calls, [])
        self.assertEqual(out.returncode, 2)

    def reading(self, account, five=None, five_in=3600, week=None, week_in=86400):
        now = time.time()
        data = {"at": now}
        if five is not None:
            data["five_hour"] = {"used_percentage": five, "resets_at": now + five_in}
        if week is not None:
            data["seven_day"] = {"used_percentage": week, "resets_at": now + week_in}
        (self.home / "usage").mkdir(exist_ok=True)
        (self.home / "usage" / f"{account}.json").write_text(json.dumps(data))

    def test_a_nearly_full_idle_session_moves_to_the_account_with_room(self):
        self.reading("Max", five=96)
        out, calls = self.run_longhaul(["idle_wait", "exit"], "--account", "Max", LONGHAUL_IDLE_MOVE_SECONDS="0")
        self.assertEqual(len(calls), 2, out.stderr)
        self.assertEqual(calls[1]["config"], str(self.pro))
        self.assertEqual(calls[1]["args"][:2], ["--resume", "sid-1"])
        self.assertFalse(calls[1]["args"][-1].startswith("The "), "idle: no carry-on message")
        self.assertIn("Max is at 96% of its allowance", out.stdout)
        self.assertIn("Max ended: move", (self.home / "longhaul.log").read_text())

    def test_a_nearly_full_session_is_not_moved_while_an_agent_works(self):
        self.reading("Max", five=96)
        out, calls = self.run_longhaul(["idle_wait_agents"], "--account", "Max", runs=1, LONGHAUL_IDLE_MOVE_SECONDS="0")
        self.assertEqual(len(calls), 1)
        self.assertIn("Max ended: completed", (self.home / "longhaul.log").read_text())

    def test_a_nearly_full_session_stays_when_the_other_account_has_no_room(self):
        self.reading("Max", five=96)
        self.reading("Pro", week=98)
        out, calls = self.run_longhaul(["idle_wait"], "--account", "Max", runs=1, LONGHAUL_IDLE_MOVE_SECONDS="0")
        self.assertIn("Max ended: completed", (self.home / "longhaul.log").read_text())

    def test_a_session_idle_for_less_than_the_wait_is_not_moved(self):
        self.reading("Max", five=96)
        out, calls = self.run_longhaul(["idle_wait"], "--account", "Max", runs=1, LONGHAUL_IDLE_MOVE_SECONDS="3600")
        self.assertIn("Max ended: completed", (self.home / "longhaul.log").read_text())

    def test_a_new_launch_avoids_a_nearly_full_account(self):
        self.reading("Max", five=96)
        _, calls = self.run_longhaul(["exit"], runs=1)
        self.assertEqual(calls[0]["config"], str(self.pro))

    def test_a_launch_goes_where_unused_allowance_expires_first(self):
        self.reading("Max", five=50, five_in=7200)
        self.reading("Pro", five=10, week=40, week_in=1200)
        _, calls = self.run_longhaul(["exit"], runs=1)
        self.assertEqual(calls[0]["config"], str(self.pro), "Pro's weekly room is lost in 20 minutes")

    def test_the_account_flag_decides_the_first_launch(self):
        self.reading("Max", five=50, five_in=7200)
        self.reading("Pro", five=10, week=40, week_in=1200)
        _, calls = self.run_longhaul(["exit"], "--account", "Max", runs=1)
        self.assertIsNone(calls[0]["config"])

    def test_longhaul_remembers_a_named_session_for_longhaul_usage(self):
        self.run_longhaul(["exit"], "--name", "docs", runs=1)
        self.assertEqual(json.loads((self.home / "labels.json").read_text()), {"sid-1": "docs"})

    def test_no_rc_starts_claude_without_remote_control(self):
        _, calls = self.run_longhaul(["exit"], "--no-rc", runs=1)
        self.assertNotIn("--remote-control", calls[0]["args"])

    # Sprint 5, D3d: a conversation resumed by id starts on the account it
    # last ran on in ask and never; auto and --account choose as before
    # (docs/adr/0002-switching-without-a-terminal.md, decision 6).

    def record(self, sid, account, **more):
        """The session's record under usage/sessions, as the status line,
        the panel relay or the supervisor leaves it."""
        (self.home / "usage" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.home / "usage" / "sessions" / f"{sid}.json").write_text(json.dumps({"account": account, **more}))

    def transcript(self, sid):
        path = self.max / "projects" / "-tmp-proj" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def test_in_ask_mode_a_resumed_conversation_starts_on_the_account_it_last_ran_on(self):
        # Max's weekly room is lost first, so pick() alone would start on Max.
        self.reading("Max", five=10, week=40, week_in=1200)
        self.reading("Pro", five=50, five_in=7200)
        self.transcript("sid-old")
        self.record("sid-old", "Pro")
        out, calls = self.run_longhaul(["exit"], "-r", "sid-old", runs=1, LONGHAUL_SWITCH="ask")
        self.assertEqual(len(calls), 1, out.stderr)
        self.assertEqual((calls[0]["config"], calls[0]["args"][:2]), (str(self.pro), ["--resume", "sid-old"]))
        self.assertIn("sid-old last ran on Pro; with switching set to ask it starts there", out.stdout)
        self.record("sid-old", "Pro")  # each run records where it ran: put the record back
        _, calls = self.run_longhaul(["exit"], "-r", "sid-old", runs=1, LONGHAUL_SWITCH="never")
        self.assertEqual(calls[-1]["config"], str(self.pro), "never too")
        self.record("sid-old", "Pro")
        _, calls = self.run_longhaul(["exit"], "-r", "sid-old", runs=1, LONGHAUL_SWITCH="auto")
        self.assertIsNone(calls[-1]["config"], "auto keeps pick()")
        # Pro's weekly room is lost first now, so pick() would start there
        # too: --account wins over both it and the record (review, Sprint 5, F6c).
        self.reading("Max", five=50, five_in=7200)
        self.reading("Pro", five=10, week=40, week_in=1200)
        self.record("sid-old", "Pro")
        _, calls = self.run_longhaul(["exit"], "-r", "sid-old", "--account", "Max", runs=1, LONGHAUL_SWITCH="ask")
        self.assertIsNone(calls[-1]["config"], "--account wins over the record and pick()")
        # No record (the conversation never ran under longhaul, or a short id
        # that several transcripts match is not expanded): pick() decides,
        # and this terminal is told, not only the log (review, Sprint 5, B5).
        self.transcript("sid-new")
        out, calls = self.run_longhaul(["exit"], "-r", "sid-new", runs=1, LONGHAUL_SWITCH="ask")
        self.assertEqual(calls[-1]["args"][:2], ["--resume", "sid-new"], out.stderr)
        self.assertIn("no record of where sid-new last ran; picking an account", out.stdout)

    def test_a_resumed_conversation_on_an_account_at_its_limit_waits_for_it_in_never_mode(self):
        (self.home / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 2}}))
        self.transcript("sid-old")
        self.record("sid-old", "Pro")
        started = time.time()
        out, calls = self.run_longhaul(["exit"], "-r", "sid-old", runs=1, LONGHAUL_SWITCH="never")
        self.assertEqual(len(calls), 1, out.stderr)
        self.assertEqual(calls[0]["config"], str(self.pro))
        self.assertGreaterEqual(time.time() - started, 2, "after Pro's reset")
        self.assertIn("Pro is at its limit", out.stdout)
        self.assertIn("--account", out.stdout, "how to start it elsewhere instead")

    def test_the_supervisor_records_the_account_a_conversation_ran_on_without_a_status_line(self):
        out, _ = self.run_longhaul(["exit"], "--account", "Pro", runs=1)
        path = self.home / "usage" / "sessions" / "sid-1.json"
        self.assertEqual(json.loads(path.read_text())["account"], "Pro", out.stderr)
        # Merged into what the status line or the relay left, never over it.
        self.record("sid-1", "Pro", cost=0.5)
        self.run_longhaul(["exit"], "-r", "sid-1", "--account", "Max", runs=1)
        self.assertEqual(json.loads(path.read_text()), {"account": "Max", "cost": 0.5})

    def test_a_state_file_that_is_a_list_still_launches_claude(self):
        # T6: run() reads state.json outside any try, so a file a crash or a
        # hand left as a list stopped the supervisor with a traceback before
        # any launch (an empty list passed: `[] or {}` is {}).
        (self.home / "state.json").write_text(json.dumps(["junk"]))
        out, calls = self.run_longhaul(["exit"], runs=1)
        self.assertEqual(len(calls), 1, out.stderr)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    # Sprint 7, A2: an account restricted to repositories ("repositories" in
    # accounts.json) is never started, resumed or moved to outside them,
    # whatever --account or the session's record says.

    def repositories(self):
        """work/.git and personal/.git under the scratch folder: (work, personal)."""
        for name in ("work", "personal"):
            (self.tmp / name / ".git").mkdir(parents=True)
        return self.tmp / "work", self.tmp / "personal"

    def restrict(self, name, *repos):
        """`name` restricted to `repos` in accounts.json; the rest as they were."""
        accounts = json.loads((self.home / "accounts.json").read_text())
        for a in accounts:
            if a["name"] == name:
                a["repositories"] = [str(r) for r in repos]
        (self.home / "accounts.json").write_text(json.dumps(accounts))

    def shown(self, folder):
        """`folder` as longhaul names the session's: its real path (what
        os.getcwd() gives, /private/var for /var on a Mac), ~ for the home."""
        return longhaul.home_path(os.path.realpath(folder))

    def favour_pro(self):
        """Readings with which pick() prefers Pro: its weekly room is lost first."""
        self.reading("Max", five=50, five_in=7200)
        self.reading("Pro", five=10, week=40, week_in=1200)

    def test_a_new_session_in_an_allowed_repository_may_use_the_restricted_account(self):
        work, _ = self.repositories()
        self.restrict("Pro", work)
        self.favour_pro()
        out, calls = self.run_longhaul(["exit"], runs=1, cwd=work)
        self.assertEqual(calls[0]["config"], str(self.pro), out.stdout)

    def test_a_new_session_in_another_repository_never_uses_the_restricted_account(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        self.favour_pro()
        out, calls = self.run_longhaul(["exit"], runs=1, cwd=personal)
        self.assertEqual(len(calls), 1, out.stderr)
        self.assertIsNone(calls[0]["config"], "Pro may not be used in personal, so Max")

    def test_a_limit_moves_only_to_an_account_allowed_in_the_repository(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        started = time.time()
        out, calls = self.run_longhaul([f"limit:{time.time() + 2}", "exit"], cwd=personal)
        self.assertEqual([c["config"] for c in calls], [None, None], out.stderr)
        self.assertGreaterEqual(time.time() - started, 2, "Max waited for its own reset")
        self.assertIn(f"Every account allowed in {self.shown(personal)} is at its limit", out.stdout)
        # Pro was not at its limit, only not allowed here: the carry-on must
        # not tell Claude that every account was.
        self.assertTrue(calls[1]["args"][-1].startswith(
            "The Max account hit its session limit while every account allowed here was at its limit, "
            "so longhaul waited for the Max account to reset"), calls[1]["args"][-1])
        out, calls = self.run_longhaul([f"limit:{time.time() + 3600}", "exit"], cwd=work)
        self.assertEqual([c["config"] for c in calls[-2:]], [None, str(self.pro)], "in work it moves to Pro")

    def test_a_nearly_full_idle_session_never_moves_early_to_an_account_not_allowed_here(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        self.reading("Max", five=96)
        out, calls = self.run_longhaul(["idle_wait"], "--account", "Max", runs=1, LONGHAUL_IDLE_MOVE_SECONDS="0",
                                       cwd=personal)
        log = (self.home / "longhaul.log").read_text()
        self.assertIn("Max ended: completed", log)
        self.assertNotIn("Max ended: move", log)

    def test_a_resumed_conversation_whose_recorded_account_is_no_longer_allowed_is_not_resumed_there(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        self.transcript("sid-old")
        self.record("sid-old", "Pro")
        # Pro at its limit: the record check comes before the wait, or
        # longhaul would wait for a reset it then refuses.
        (self.home / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 10}}))
        started = time.time()
        out, calls = self.run_longhaul(["exit"], "-r", "sid-old", runs=1, LONGHAUL_SWITCH="ask", cwd=personal)
        self.assertEqual(len(calls), 1, out.stderr)
        self.assertEqual((calls[0]["config"], calls[0]["args"][:2]), (None, ["--resume", "sid-old"]), "Max, picked")
        self.assertLess(time.time() - started, 10, "no wait for Pro's reset")
        self.assertIn(f"sid-old last ran on Pro, which may not be used in {self.shown(personal)}; picking an account",
                      out.stdout)

    def test_the_account_flag_is_refused_where_the_account_may_not_be_used(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        out, calls = self.run_longhaul(["exit"], "--account", "Pro", runs=1, cwd=personal)
        self.assertEqual((out.returncode, calls), (2, []), out.stdout)
        self.assertIn(f"Pro may not be used in {self.shown(personal)} (its repositories: {work}); "
                      "longhaul has not started Claude", out.stdout)

    def test_with_no_account_allowed_in_the_repository_longhaul_stops_before_starting_claude(self):
        work, personal = self.repositories()
        self.restrict("Max", work)
        self.restrict("Pro", work / "sub")
        out, calls = self.run_longhaul(["exit"], runs=1, cwd=personal)
        self.assertEqual((out.returncode, calls), (1, []), out.stdout)
        self.assertIn(f"No account may be used in {self.shown(personal)}: Max only in {work}, Pro only in {work / 'sub'}. "
                      f'Edit "repositories" in {self.home / "accounts.json"}, or run from one of those.', out.stdout)

    def test_with_the_allowed_account_signed_in_wrong_the_denied_one_is_never_launched(self):
        # Final review, Sprint 7 (G5): Pro, signed in as expected, may not be
        # used here, and Max, the one allowed, is signed in as someone else.
        # Nothing starts, and the line says the accounts allowed here, as
        # the panel's refusal does: "No account is signed in" read as false.
        work, personal = self.repositories()
        self.pin_emails()
        self.restrict("Pro", work)
        out, calls = self.run_longhaul(["exit"], runs=1, FAKE_EMAIL_MAX="stranger@example.com",
                                       FAKE_EMAIL_PRO="second@example.com", cwd=personal)
        self.assertEqual((out.returncode, calls), (1, []), out.stdout)
        self.assertIn("No account allowed here is signed in as expected. Run `longhaul status`, then `longhaul setup`.",
                      out.stdout)
        out, calls = self.run_longhaul(["exit"], runs=1, FAKE_EMAIL_MAX="stranger@example.com",
                                       FAKE_EMAIL_PRO="stranger@example.com", cwd=work)
        self.assertEqual((out.returncode, calls), (1, []), out.stdout)
        self.assertIn("No account is signed in as expected. Run `longhaul status`, then `longhaul setup`.",
                      out.stdout, "nothing denied in work: as before")

    def test_a_session_started_through_a_symlink_to_an_allowed_repository_is_allowed(self):
        work, _ = self.repositories()
        link = self.tmp / "link"
        link.symlink_to(work)
        self.favour_pro()
        self.restrict("Pro", link)
        _, calls = self.run_longhaul(["exit"], runs=1, cwd=work)
        self.assertEqual(calls[-1]["config"], str(self.pro), "an entry that is a link to the repository")
        self.restrict("Pro", work)
        _, calls = self.run_longhaul(["exit"], runs=1, cwd=link)
        self.assertEqual(calls[-1]["config"], str(self.pro), "a session started through a link to it")


class Usage(unittest.TestCase):
    """The status line reading, the ledger and `longhaul usage`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.relay, self.pro = self.tmp / "relay", self.tmp / "pro"
        self.relay.mkdir()
        self.pro.mkdir()
        (self.relay / "accounts.json").write_text(json.dumps(
            [{"name": "Max", "config_dir": str(self.tmp / "max")}, {"name": "Pro", "config_dir": str(self.pro)}]))
        self.saved = {k: getattr(longhaul, k) for k in ("LONGHAUL_HOME", "USAGE_DIR", "LEDGER", "LABELS")}
        longhaul.LONGHAUL_HOME, longhaul.USAGE_DIR = self.relay, self.relay / "usage"
        longhaul.LEDGER, longhaul.LABELS = self.relay / "ledger.jsonl", self.relay / "labels.json"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(longhaul, k, v)

    def reading(self, five=23, week=51, cost=0.03, sid="s1", resets=None):
        resets = resets or time.time() + 3600
        return {"session_id": sid, "cost": {"total_cost_usd": cost},
                "rate_limits": {"five_hour": {"used_percentage": five, "resets_at": resets},
                                "seven_day": {"used_percentage": week, "resets_at": resets + 86400}}}

    def ledger(self):
        return [json.loads(l) for l in longhaul.LEDGER.read_text().splitlines()]

    def full_reading(self, ctx=8, cost=0.03, model="claude-opus-5-5"):
        """A reading with the documented fields longhaul did not keep before
        Phase 2 (code.claude.com/docs/en/statusline)."""
        r = self.reading(cost=cost)
        r["cost"]["total_api_duration_ms"] = 2300
        r.update({"model": {"id": model, "display_name": "Opus"}, "version": "2.1.304",
                  "transcript_path": "/t/proj/s1.jsonl",
                  "context_window": {"used_percentage": ctx, "total_input_tokens": 15500,
                                     "context_window_size": 200000, "current_usage": None},
                  "prompt_cache": {"hit_ratio": 0.91, "misses": 2, "miss_recache_tokens": 310200}})
        return r

    def test_the_ledger_keeps_the_documented_context_cache_and_model_readings(self):
        longhaul.record_usage(self.full_reading(), str(self.pro))
        row = self.ledger()[-1]
        self.assertEqual({k: row.get(k) for k in ("ctx", "ctx_tokens", "ctx_size", "model", "cache_hit",
                                                   "cache_misses", "recache", "api_ms")},
                         {"ctx": 8, "ctx_tokens": 15500, "ctx_size": 200000, "model": "claude-opus-5-5",
                          "cache_hit": 0.91, "cache_misses": 2, "recache": 310200, "api_ms": 2300})
        last = json.loads((longhaul.USAGE_DIR / "sessions" / "s1.json").read_text())
        self.assertEqual(last["transcript"], "/t/proj/s1.jsonl")
        self.assertNotIn("transcript", row, "the path is kept once per session, not on every row")

    def test_the_transcript_path_is_kept_when_a_later_reading_has_none(self):
        # Review, Phase 2: the panel relay sends no transcript_path, and its
        # row replaced the session's file and lost the path.
        longhaul.record_usage(self.full_reading(), str(self.pro))
        longhaul.record_usage(self.reading(cost=0.5), str(self.pro))
        last = json.loads((longhaul.USAGE_DIR / "sessions" / "s1.json").read_text())
        self.assertEqual((last["cost"], last.get("transcript")), (0.5, "/t/proj/s1.jsonl"))

    def test_a_reading_from_the_default_folder_is_the_one_account_on_it_else_main(self):
        # Review, Sprint 5 (B3): account_for_config(None, ...) was "main"
        # without a look at the accounts, so with an account shaped
        # {"name": "Max", "config_dir": null} (a subscription that keeps
        # ~/.claude) every status-line reading wrote "main" over the
        # supervisor's note of Max, and a resume lost where it ran.
        on_default, pro = {"name": "Max", "config_dir": None}, {"name": "Pro", "config_dir": str(self.pro)}
        self.assertEqual(longhaul.account_for_config(None, [on_default, pro]), "Max")
        self.assertEqual(longhaul.account_for_config("", [on_default, pro]), "Max")
        self.assertEqual(longhaul.account_for_config(None, [pro]), "main", "no account keeps ~/.claude")
        self.assertEqual(longhaul.account_for_config(None, []), "main")
        self.assertEqual(longhaul.account_for_config(None, [on_default, {"name": "Team"}]), "main", "two: no telling which")
        self.assertEqual(longhaul.account_for_config(str(self.pro), [on_default, pro]), "Pro", "a folder, as before")
        record = longhaul.USAGE_DIR / "sessions" / "s1.json"
        (self.relay / "accounts.json").write_text(json.dumps([on_default, pro]))
        longhaul.record_usage(self.reading(cost=0.4), None)
        self.assertEqual(json.loads(record.read_text())["account"], "Max")
        self.assertTrue((longhaul.USAGE_DIR / "Max.json").is_file(), "the allowance reading is Max's too")
        # With no accounts.json at all a reading from ~/.claude is "main",
        # as before: load_accounts()'s stand-in account must not name it.
        (self.relay / "accounts.json").unlink()
        longhaul.record_usage(self.reading(cost=0.5), None)
        self.assertEqual(json.loads(record.read_text())["account"], "main")

    def test_a_compaction_writes_a_ledger_row_though_the_cost_stays(self):
        longhaul.record_usage(self.full_reading(ctx=80), str(self.pro))
        longhaul.record_usage(self.full_reading(ctx=9), str(self.pro))
        self.assertEqual([r["ctx"] for r in self.ledger()], [80, 9])

    def test_a_reading_without_the_newer_fields_leaves_them_out(self):
        # The panel's relay and older Claude Code versions send none of them.
        longhaul.record_usage(self.reading(), str(self.pro))
        self.assertFalse({"ctx", "model", "cache_hit"} & set(self.ledger()[-1]))

    def test_odd_shapes_in_the_newer_fields_never_blank_the_status_line(self):
        data = self.reading()
        data.update({"context_window": "full", "prompt_cache": None, "model": ["x"]})
        self.assertIn("Pro", longhaul.record_usage(data, str(self.pro)))

    def test_a_reading_is_kept_for_its_account_and_shown_on_the_line(self):
        line = longhaul.record_usage(self.reading(), str(self.pro))
        self.assertIn("Pro", line)
        self.assertIn("23%", line)
        self.assertIn("51%", line)
        self.assertEqual(json.loads((longhaul.USAGE_DIR / "Pro.json").read_text())["five_hour"]["used_percentage"], 23)

    def test_the_line_shows_the_last_reading_before_the_first_reply(self):
        longhaul.record_usage(self.reading(five=40), str(self.pro))
        line = longhaul.record_usage({"session_id": "s2", "cost": {"total_cost_usd": 0}}, str(self.pro))
        self.assertIn("40%", line)

    def test_a_session_outside_longhaul_is_called_main(self):
        self.assertTrue(longhaul.record_usage(self.reading(), None).startswith("\033[1mmain"))

    def test_an_unchanged_reading_adds_no_ledger_row(self):
        longhaul.record_usage(self.reading(), str(self.pro))
        longhaul.record_usage(self.reading(), str(self.pro))
        longhaul.record_usage(self.reading(cost=0.05), str(self.pro))
        self.assertEqual(len(self.ledger()), 2)

    def test_work_is_split_per_task_and_account(self):
        r = 1000
        rows = [
            {"at": 1, "session": "A", "account": "Max", "cost": 0.1, "five": 20, "five_resets": r},
            {"at": 2, "session": "A", "account": "Max", "cost": 0.5, "five": 30, "five_resets": r},
            {"at": 3, "session": "B", "account": "Max", "cost": 0.2, "five": 35, "five_resets": r},
            # A moved to Pro: a new process, so its cost starts again
            {"at": 4, "session": "A", "account": "Pro", "cost": 0.05, "five": 10, "five_resets": r},
            {"at": 5, "session": "A", "account": "Pro", "cost": 0.3, "five": 12, "five_resets": r},
        ]
        s = longhaul.summarize(rows)
        self.assertAlmostEqual(s[("A", "Max")]["cost"], 0.5)
        self.assertEqual(s[("A", "Max")]["five"], 10)
        self.assertAlmostEqual(s[("B", "Max")]["cost"], 0.2)
        self.assertEqual(s[("B", "Max")]["five"], 5)
        self.assertAlmostEqual(s[("A", "Pro")]["cost"], 0.3)
        self.assertEqual(s[("A", "Pro")]["five"], 2)

    def test_a_new_five_hour_window_is_not_counted_as_use(self):
        rows = [{"at": 1, "session": "A", "account": "Max", "cost": 0.1, "five": 90, "five_resets": 100},
                {"at": 2, "session": "A", "account": "Max", "cost": 0.2, "five": 95, "five_resets": 200}]
        self.assertEqual(longhaul.summarize(rows)[("A", "Max")]["five"], 0)

    def test_two_sessions_saving_one_account_at_once_both_succeed(self):
        # 2026-10-16: two sessions on Max shared one temporary file name,
        # and the second save found it already moved (FileNotFoundError).
        code = ("import sys,time; sys.path.insert(0, sys.argv[1]); import longhaul\n"
                "from pathlib import Path\n"
                "for i in range(300): longhaul.write_json(Path(sys.argv[2]), {'i': i})\n")
        target = self.relay / "usage" / "Max.json"
        procs = [subprocess.Popen([sys.executable, "-c", code, str(HERE), str(target)], stderr=subprocess.PIPE)
                 for _ in range(4)]
        errors = [p.communicate()[1].decode() for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0, 0, 0], errors)
        self.assertEqual(json.loads(target.read_text())["i"], 299)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_the_status_line_never_fails_on_bad_input(self):
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "statusline"], input="not json",
                             capture_output=True, text=True, env=dict(os.environ, LONGHAUL_HOME=str(self.relay)))
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "longhaul")

    def test_the_log_and_the_ledger_are_readable_by_you_alone(self):
        # They name your accounts, sessions and spending. Made with the
        # default umask, anyone on the Mac could read them.
        longhaul.LEDGER.parent.mkdir(parents=True, exist_ok=True)
        longhaul.LEDGER.write_text("")
        longhaul.LEDGER.chmod(0o644)  # as a ledger made before this fix is
        longhaul.log("hello")
        longhaul.record_usage(self.reading(), str(self.pro))
        for path in (self.relay / "longhaul.log", longhaul.LEDGER):
            self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o600), path.name)

    def test_the_log_starts_a_new_file_past_its_cap_and_keeps_one_old_one(self):
        # The ledger grew 355 KB in five days and nothing ever trimmed it.
        old, longhaul.LOG_MAX = longhaul.LOG_MAX, 500
        try:
            for i in range(100):
                longhaul.log(f"line {i:03d} " + "x" * 40)
        finally:
            longhaul.LOG_MAX = old
        self.assertEqual(sorted(p.name for p in self.relay.glob("longhaul.log*")), ["longhaul.log", "longhaul.log.1"])
        self.assertLess((self.relay / "longhaul.log").stat().st_size, 600)
        self.assertIn("line 099", (self.relay / "longhaul.log").read_text())

    def test_a_log_moved_aside_is_private_too_and_a_refused_chmod_loses_no_line(self):
        # Review, Phase 1c: a 0644 log past its cap became a 0644 longhaul.log.1,
        # and a chmod the disk refused dropped the line it came with.
        log = self.relay / "longhaul.log"
        log.write_text("x" * 600)
        log.chmod(0o644)  # made before longhaul kept it private
        old, longhaul.LOG_MAX = longhaul.LOG_MAX, 500
        try:
            longhaul.log("first")
        finally:
            longhaul.LOG_MAX = old
        self.assertEqual(oct((self.relay / "longhaul.log.1").stat().st_mode & 0o777), oct(0o600))
        log.chmod(0o644)
        with mock.patch.object(longhaul.os, "fchmod", side_effect=PermissionError("not yours")):
            longhaul.log("second")
        self.assertIn("second", log.read_text())

    def test_longhaul_usage_still_counts_the_work_in_the_ledger_moved_aside(self):
        longhaul.save_label("s1", "older task")
        longhaul.save_label("s2", "newer task")
        old, longhaul.LEDGER_MAX = longhaul.LEDGER_MAX, 1  # every row starts a new file
        try:
            longhaul.record_usage(self.reading(cost=0.4, sid="s1"), str(self.pro))
            longhaul.record_usage(self.reading(cost=0.3, sid="s2"), str(self.pro))
        finally:
            longhaul.LEDGER_MAX = old
        self.assertTrue(longhaul.LEDGER.with_name("ledger.jsonl.1").exists())
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "usage"], capture_output=True, text=True,
                             env=dict(os.environ, LONGHAUL_HOME=str(self.relay), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "none")))
        self.assertIn("older task", out.stdout)
        self.assertIn("newer task", out.stdout)
        self.assertIn("Pro: $0.70", out.stdout)

    def test_longhaul_usage_names_a_task_by_its_longhaul_name(self):
        # The label as shown() prints it: an escape byte in it went to the
        # terminal as it was (review, Sprint 5, D2).
        longhaul.save_label("s1", "docs\x1b]0;evil\x07")
        longhaul.record_usage(self.reading(cost=0.4), str(self.pro))
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "usage"], capture_output=True, text=True,
                             env=dict(os.environ, LONGHAUL_HOME=str(self.relay), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "none")))
        self.assertIn("docs?]0;evil?", out.stdout)
        self.assertNotIn("\x1b", out.stdout)
        self.assertIn("$0.40", out.stdout)
        self.assertIn("Pro", out.stdout)

    def test_a_tasks_name_is_shown_safely_whether_a_label_a_folder_or_the_record_names_it(self):
        # Review, Sprint 5 (D2, F5, F6e): task_name() copied find_transcript()'s
        # glob and ignored the session record's transcript path, where the
        # status line said the transcript was; a --name label went out
        # unescaped where the folder's name went through shown().
        claude = self.tmp / "claude"
        with mock.patch.object(longhaul, "DEFAULT_CONFIG", claude):
            self.assertEqual(longhaul.task_name("sid-none-0000", {}), "sid-none", "no transcript: the short id")
            self.assertEqual(longhaul.task_name("s1", {"s1": "docs\x1b]0;evil\x07"}), "docs?]0;evil?")
            folder = claude / "projects" / "-proj\x1b[2J"
            folder.mkdir(parents=True)
            (folder / "sid-f-0000.jsonl").write_text("")
            self.assertEqual(longhaul.task_name("sid-f-0000", {}), "-proj?[2J sid-f-00")
            # The record's transcript, outside the projects folder, comes first.
            elsewhere = self.tmp / "elsewhere" / "-other-proj"
            elsewhere.mkdir(parents=True)
            (elsewhere / "sid-r-0000.jsonl").write_text("")
            (longhaul.USAGE_DIR / "sessions").mkdir(parents=True)
            (longhaul.USAGE_DIR / "sessions" / "sid-r-0000.json").write_text(
                json.dumps({"account": "Pro", "transcript": str(elsewhere / "sid-r-0000.jsonl")}))
            self.assertEqual(longhaul.task_name("sid-r-0000", {}), "-other-proj sid-r-00")
            self.assertEqual(longhaul.find_transcript("sid-r-0000"), (elsewhere / "sid-r-0000.jsonl", ""))
            self.assertEqual(longhaul.task_name("../x", {}), "../x", "never a path: no transcript is looked for")

    def test_longhaul_usage_never_shows_a_word_of_the_conversation(self):
        # R1: with no --name, task_name() showed the first 40 characters of
        # the conversation's first message. Longhaul never shows message
        # text: a task without a label is its project folder and short id.
        sid, secret = "9f0e1d2c-0000-4000-8000-000000000000", "SECRET-PLAN-7"
        claude = self.tmp / "claude"
        transcript = claude / "projects" / "-Users-me-code-myproj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        line = {"type": "user", "message": {"role": "user", "content": f"{secret} is the goal"}}
        transcript.write_text(json.dumps(line, separators=(",", ":")) + "\n")  # as Claude Code writes it
        longhaul.record_usage(self.reading(cost=0.4, sid=sid), str(self.pro))
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "usage"], capture_output=True, text=True,
                             env=dict(os.environ, LONGHAUL_HOME=str(self.relay), LONGHAUL_DEFAULT_CONFIG_DIR=str(claude)))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(secret, out.stdout)
        self.assertNotIn(secret, out.stderr)
        self.assertIn("-Users-me-code-myproj 9f0e1d2c", out.stdout)
        self.assertNotIn(sid, out.stdout, "the short id, as everywhere else")


class Inspect(unittest.TestCase):
    """`longhaul inspect`: where one conversation's usage went. The transcript
    lines are shaped as Claude Code 2.1.304 writes them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.relay, self.claude = self.tmp / "relay", self.tmp / "claude"
        self.relay.mkdir()
        self.path = self.claude / "projects" / "-code-app" / "s1.jsonl"
        self.n = 0
        self.saved = {k: getattr(longhaul, k) for k in ("LONGHAUL_HOME", "USAGE_DIR", "LEDGER", "DEFAULT_CONFIG")}
        longhaul.LONGHAUL_HOME, longhaul.USAGE_DIR, longhaul.LEDGER = self.relay, self.relay / "usage", self.relay / "ledger.jsonl"
        longhaul.DEFAULT_CONFIG = self.claude

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(longhaul, k, v)

    def reply(self, *blocks, inp=2, write=0, read=0, out=10, model="claude-opus-5-5"):
        """One response: a line per content block, all with one message id."""
        self.n += 1
        usage = {"input_tokens": inp, "cache_creation_input_tokens": write,
                 "cache_read_input_tokens": read, "output_tokens": out}
        return [{"type": "assistant", "uuid": f"a{self.n}.{i}",
                 "message": {"id": f"msg_{self.n}", "model": model, "role": "assistant",
                             "content": [b], "usage": usage}}
                for i, b in enumerate(blocks or [{"type": "text", "text": "done"}])]

    def call(self, tid, name, **args):
        return {"type": "tool_use", "id": tid, "name": name, "input": args}

    def turn(self, tid, name, output="ok", error=None, **args):
        """A tool call and the user line holding its output."""
        result = {"type": "tool_result", "tool_use_id": tid, "content": output}
        if error is not None:
            result["is_error"] = error
        return self.reply(self.call(tid, name, **args)) + [
            {"type": "user", "message": {"role": "user", "content": [result]}}]

    def compaction(self, trigger="auto", pre=967957, post=18144):
        return {"type": "system", "subtype": "compact_boundary",
                "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post}}

    def write(self, lines, path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        return path

    def agent(self, lines, name="agent-a1"):
        return self.write([{**l, "isSidechain": True} for l in lines],
                          self.path.with_suffix("") / "subagents" / f"{name}.jsonl")

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(HERE / "longhaul.py"), "inspect", *args], capture_output=True,
                              text=True, cwd=cwd, env=dict(os.environ, LONGHAUL_HOME=str(self.relay),
                                                           LONGHAUL_DEFAULT_CONFIG_DIR=str(self.claude)))

    def at(self, second, lines):
        """The lines, written at that second: subagents write alongside the main conversation."""
        return [{**l, "timestamp": f"2026-10-20T10:00:{second:02d}.000Z"} for l in lines]

    def meta(self, name, **fields):
        """A subagent's meta.json, beside its transcript, as Claude Code 2.1.307 writes it."""
        path = self.path.with_suffix("") / "subagents" / f"{name}.meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fields))

    def trace(self, *lines):
        """The Advisor experiment's trace (Sprint 8), as its hooks write it; a string is written as it is."""
        path = self.relay / "advisor" / "trace.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))

    def decision(self, tid, reason=None, trigger="early", session="s1"):
        """A decision line: admitted with no reason, denied with one."""
        return {"t": 1000.0, "id": tid, "session": session, "prompt_id": "p1", "event": "decision",
                "decision": "deny" if reason else "allow", "reason": reason,
                "kind": None if not reason else "fixable" if reason.startswith("brief") else "hard",
                "trigger": trigger, "reconcile": False, "executor": "sonnet", "advisor": "opus",
                "brief_chars": 900, "brief_hash": "0123456789ab", "allowance": "checked"}

    def outcome(self, tid, verdict="proceed", chars=400, outcome="ok", session="s1"):
        return {"t": 1010.0, "id": tid, "session": session, "event": "outcome", "outcome": outcome,
                "verdict": verdict, "confidence": "high", "reply_chars": chars, "seconds": 9.5}

    def test_a_response_written_over_several_lines_is_counted_once(self):
        # One line per content block, one message id. In a subagent's
        # transcript the early lines carry a partial output count (16 where
        # the last says 308): adding the lines up would count it three times.
        lines = self.reply({"type": "thinking", "thinking": "..."}, {"type": "text", "text": "a"},
                           self.call("t1", "Read", file_path="/a.py"), inp=2, write=7105, out=308)
        for line in lines[:2]:
            line["message"]["usage"] = {**line["message"]["usage"], "output_tokens": 16}
        self.agent(lines)
        self.write(self.reply(inp=3, write=100, read=5000, out=40))
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["requests"], {"main": 1, "subagents": 1})
        self.assertEqual(r["subagents"], 1)
        self.assertEqual(r["tokens"]["main"], {"input": 3, "cache_write": 100, "cache_read": 5000, "output": 40})
        self.assertEqual(r["tokens"]["subagents"], {"input": 2, "cache_write": 7105, "cache_read": 0, "output": 308})

    def test_models_are_counted_per_request_and_an_api_error_is_not_a_request(self):
        error = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
                 "message": {"id": "e1", "model": "<synthetic>", "content": [],
                             "usage": {"input_tokens": 0, "output_tokens": 0}}}
        self.write(self.reply() + self.reply(model="claude-haiku-5-5") + [error])
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["requests"], {"main": 2, "subagents": 0})
        self.assertEqual(r["models"], {"claude-opus-5-5": 1, "claude-haiku-5-5": 1})
        self.assertEqual(r["api_errors"], {"rate_limit": 1})

    def test_tokens_are_summed_per_model_for_main_and_subagents(self):
        # Sprint 8, Region A: each model's input, cache write, cache read and
        # output, main and subagents apart: one model in both is two rows.
        self.write(self.reply(inp=3, write=100, read=5000, out=40) + self.reply(inp=1, read=6000, out=9)
                   + self.reply(inp=7, write=50, out=2, model="claude-haiku-5-5"))
        self.agent(self.reply(inp=2, write=7105, out=308, model="claude-haiku-5-5") + self.reply(inp=1, read=200, out=5))
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["model_tokens"], [
            {"model": "claude-opus-5-5", "scope": "main", "input": 4, "cache_write": 100, "cache_read": 11000,
             "output": 49},
            {"model": "claude-haiku-5-5", "scope": "main", "input": 7, "cache_write": 50, "cache_read": 0, "output": 2},
            {"model": "claude-haiku-5-5", "scope": "subagents", "input": 2, "cache_write": 7105, "cache_read": 0,
             "output": 308},
            {"model": "claude-opus-5-5", "scope": "subagents", "input": 1, "cache_write": 0, "cache_read": 200,
             "output": 5}])
        out = longhaul.inspect_lines(r)
        head = out.index("Tokens by model [transcript, measured: the API's own counts]")
        self.assertGreater(head, out.index("Tokens [transcript, measured: the API's own counts]"))
        self.assertEqual(out[head + 1:head + 6], [
            "                                   input   cache write    cache read      output",
            "  main      claude-opus-5-5            4           100        11,000          49",
            "  main      claude-haiku-5-5           7            50             0           2",
            "  subagents claude-haiku-5-5           2         7,105             0         308",
            "  subagents claude-opus-5-5            1             0           200           5"])
        self.assertTrue(out[head + 6].startswith("Context"), out[head + 6])

    def test_tokens_by_model_add_up_to_the_tokens_and_a_response_with_no_model_is_kept_as_unknown(self):
        unnamed = self.reply(inp=5, out=3)
        for line in unnamed:
            del line["message"]["model"]
        self.write(self.reply(inp=3, write=100, read=5000, out=40) + unnamed)
        self.agent(self.reply(out=308))
        r = longhaul.inspect_transcript(self.path)
        for scope, kinds in r["tokens"].items():
            for kind, n in kinds.items():
                self.assertEqual(sum(m[kind] for m in r["model_tokens"] if m["scope"] == scope), n, (scope, kind))
        self.assertIn({"model": "?", "scope": "main", "input": 5, "cache_write": 0, "cache_read": 0, "output": 3},
                      r["model_tokens"])
        self.assertEqual(r["models"], {"claude-opus-5-5": 2}, "the request count still names models only")

    def test_no_response_no_tokens_by_model(self):
        self.write([{"type": "user", "message": {"role": "user", "content": "hi"}}])
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["model_tokens"], [])
        out = longhaul.inspect_lines(r)
        head = out.index("Tokens by model [transcript, measured: the API's own counts]")
        self.assertEqual(out[head + 1], "  none")

    CONSULTATIONS = "Consultations [longhaul advisor trace, counted; own tokens: the advisor's transcripts, measured]"

    def test_consultations_are_counted_from_the_advisor_trace_for_this_conversation_only(self):
        # Sprint 8, Region A: the trace holds a line per decision and per
        # outcome, of every conversation; the report counts this one's.
        self.write(self.reply())
        self.trace(self.decision("c1"), self.outcome("c1", "proceed", 400),
                   self.decision("c2", trigger="stuck"), self.outcome("c2", "adjust", 700),
                   self.decision("c3", "cap-task", trigger="change"),
                   self.decision("c4", "brief-sections", trigger=None), self.decision("c5", "cap-task"),
                   self.decision("x1", trigger="done", session="s2"), self.outcome("x1", "stop", 999, session="s2"),
                   "not json", "[1, 2]", '{"session": "s1", "event": "other"}')
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["consultations"], {
            "attempts": 5, "admitted": 2, "denied": {"cap-task": 2, "brief-sections": 1},
            "triggers": {"early": {"attempts": 2, "admitted": 1}, "stuck": {"attempts": 1, "admitted": 1},
                         "change": {"attempts": 1, "admitted": 0}, "none": {"attempts": 1, "admitted": 0}},
            "outcomes": {"ok": 2}, "verdicts": {"proceed": 1, "adjust": 1},
            "reply_chars": 1100, "reply_chars_max": 700, "no_outcome": 0, "transcripts": 0, "unmatched": 0, "tokens": []})
        out = longhaul.inspect_lines(r)
        head = out.index(self.CONSULTATIONS)
        self.assertGreater(head, out.index("Guard [longhaul guard: no record: the hook was off, or saw no whole Read, "
                                           "cat or search]"))
        self.assertEqual(out[head + 1:head + 11], [
            "  5 consultations asked for: 2 admitted, 3 denied",
            "  denied by reason: cap-task 2, brief-sections 1",
            "  by trigger: early 2 (1 admitted), stuck 1 (1 admitted), change 1 (0 admitted), none 1 (0 admitted)",
            "  outcomes: ok 2",
            "  verdicts: proceed 1, adjust 1",
            "  replies: 1,100 chars in all, the longest 700",
            "  admitted with no outcome traced: 0",
            "  advisor transcripts with no admitted decision: 0",
            "  own tokens: no advisor transcript",
            "Failures [transcript, counted]"])
        got = {(x["metric"], x["scope"], x["item"]): (x["value"], x["unit"], x["source"], x["confidence"])
               for x in self.records("s1") if x["metric"].startswith("consultation_")}
        n = ("consultations", "advisor", "counted")
        self.assertEqual(got, {
            ("consultation_attempts", "all", None): (5, *n), ("consultation_admitted", "all", None): (2, *n),
            ("consultation_denied", "all", "cap-task"): (2, *n), ("consultation_denied", "all", "brief-sections"): (1, *n),
            ("consultation_trigger", "all", "early"): (2, *n), ("consultation_trigger", "all", "stuck"): (1, *n),
            ("consultation_trigger", "all", "change"): (1, *n), ("consultation_trigger", "all", "none"): (1, *n),
            ("consultation_trigger_admitted", "all", "early"): (1, *n),
            ("consultation_trigger_admitted", "all", "stuck"): (1, *n),
            ("consultation_trigger_admitted", "all", "change"): (0, *n),
            ("consultation_trigger_admitted", "all", "none"): (0, *n),
            ("consultation_outcomes", "all", "ok"): (2, *n),
            ("consultation_verdicts", "all", "proceed"): (1, *n), ("consultation_verdicts", "all", "adjust"): (1, *n),
            ("consultation_reply_chars", "all", None): (1100, "chars", "advisor", "counted"),
            ("consultation_reply_chars_max", "all", None): (700, "chars", "advisor", "counted"),
            ("consultation_no_outcome", "all", None): (0, *n),
            ("consultation_transcripts", "subagents", None): (0, "files", "transcript", "counted"),
            ("consultation_unadmitted_transcripts", "subagents", None): (0, "files", "transcript", "counted")})

    def test_a_consultations_own_tokens_come_from_the_subagents_whose_agent_type_is_the_advisor(self):
        self.write(self.reply())
        self.trace(self.decision("c1"), self.outcome("c1"))
        self.agent(self.reply(inp=3, write=100, read=5000, out=40), "agent-a1")
        self.meta("agent-a1", agentType="longhaul:advisor", toolUseId="c1", model="opus")
        self.agent(self.reply(inp=1, out=9, model="claude-sonnet-5-5"), "agent-a2")
        self.meta("agent-a2", agentType="general-purpose")
        self.agent(self.reply(inp=1, out=9, model="claude-sonnet-5-5"), "agent-a3")  # no meta.json
        self.agent(self.reply(inp=1, out=9, model="claude-sonnet-5-5"), "agent-a4")
        self.meta("agent-a4", agentType=["longhaul:advisor"])  # not a name
        self.agent(self.reply(inp=1, out=9, model="claude-sonnet-5-5"), "agent-a5")
        (self.path.with_suffix("") / "subagents" / "agent-a5.meta.json").write_text("[" * 100_000 + "]" * 100_000)  # RecursionError on 3.9
        r = longhaul.inspect_transcript(self.path)
        c = r["consultations"]
        self.assertEqual((r["subagents"], c["transcripts"]), (5, 1))
        self.assertEqual(c["tokens"], [{"model": "claude-opus-5-5", "input": 3, "cache_write": 100, "cache_read": 5000,
                                        "output": 40}])
        out = longhaul.inspect_lines(r)
        head = out.index("  own tokens, from 1 advisor transcript:")
        self.assertEqual(out[head + 1:head + 4], [
            "                          input   cache write    cache read      output",
            "    claude-opus-5-5           3           100         5,000          40",
            "Failures [transcript, counted]"])
        got = {(x["metric"], x["scope"], x["item"], x["kind"]): (x["value"], x["unit"], x["source"], x["confidence"])
               for x in self.records("s1") if x["metric"] in ("consultation_tokens", "consultation_transcripts")}
        t = ("tokens", "transcript", "measured")
        self.assertEqual(got, {
            ("consultation_transcripts", "subagents", None, None): (1, "files", "transcript", "counted"),
            ("consultation_tokens", "subagents", "claude-opus-5-5", "input"): (3, *t),
            ("consultation_tokens", "subagents", "claude-opus-5-5", "cache_write"): (100, *t),
            ("consultation_tokens", "subagents", "claude-opus-5-5", "cache_read"): (5000, *t),
            ("consultation_tokens", "subagents", "claude-opus-5-5", "output"): (40, *t)})

    def test_runs_the_hook_did_not_admit_are_counted(self):
        # Sprint 8, F1: an outcome line marked unadmitted, an admitted
        # decision with no outcome (the result hook never ran), and an
        # advisor's transcript (by its meta.json) with no admitted decision.
        self.write(self.reply())
        self.trace(self.decision("c1"), self.outcome("c1"), self.decision("c2"),
                   self.decision("c3", "brief-sections"), self.outcome("c3", verdict=None, outcome="unadmitted"),
                   self.outcome("c4", verdict=None, outcome="unadmitted"))
        for name, meta in (("agent-a1", {"agentType": "longhaul:advisor", "toolUseId": "c1"}),
                           ("agent-a2", {"agentType": "longhaul:advisor", "toolUseId": "c3"}),  # denied, and ran
                           ("agent-a3", {"agentType": " Advisor"}),  # the bare name, and no toolUseId
                           ("agent-a4", {"agentType": "general-purpose", "toolUseId": "c9"})):
            self.agent(self.reply(), name)
            self.meta(name, **meta)
        r = longhaul.inspect_transcript(self.path)
        c = r["consultations"]
        self.assertEqual((c["outcomes"], c["no_outcome"], c["unmatched"], c["transcripts"]),
                         ({"ok": 1, "unadmitted": 2}, 1, 2, 3))
        self.assertNotIn("admitted_ids", c, "the ids stay inside inspect")
        out = longhaul.inspect_lines(r)
        head = out.index(self.CONSULTATIONS)
        self.assertIn("  outcomes: ok 1, unadmitted 2", out[head:])
        self.assertIn("  admitted with no outcome traced: 1", out[head:])
        self.assertIn("  advisor transcripts with no admitted decision: 2", out[head:])
        got = {(x["metric"], x["scope"]): (x["value"], x["unit"], x["source"], x["confidence"]) for x in self.records("s1")
               if x["metric"] in ("consultation_no_outcome", "consultation_unadmitted_transcripts")}
        self.assertEqual(got, {("consultation_no_outcome", "all"): (1, "consultations", "advisor", "counted"),
                               ("consultation_unadmitted_transcripts", "subagents"): (2, "files", "transcript", "counted")})
        self.assertEqual([x["value"] for x in self.records("s1") if x["metric"] == "consultation_outcomes"
                          and x["item"] == "unadmitted"], [2])

    def test_an_advisor_transcript_with_no_trace_at_all_is_shown(self):
        # The hooks never ran (off, or python3 missing): no trace, and an
        # advisor ran all the same. The section says so.
        self.write(self.reply())
        self.agent(self.reply(inp=3, out=40), "agent-a1")
        self.meta("agent-a1", agentType="longhaul:advisor", toolUseId="c1")
        c = longhaul.inspect_transcript(self.path)["consultations"]
        self.assertEqual((c["attempts"], c["admitted"], c["unmatched"], c["no_outcome"], c["transcripts"]), (0, 0, 1, 0, 1))
        self.assertEqual(c["tokens"][0]["input"], 3)
        out = longhaul.inspect_lines(longhaul.inspect_transcript(self.path))
        self.assertIn("  0 consultations asked for: 0 admitted, 0 denied", out)
        self.assertIn("  advisor transcripts with no admitted decision: 1", out)

    def test_no_trace_line_of_this_conversation_no_consultations(self):
        self.write(self.reply())
        self.agent(self.reply(), "agent-a1")
        self.meta("agent-a1", agentType="general-purpose")  # no advisor's transcript either: no section
        for lines in ((), (self.decision("x1", session="s2"), "junk", '{"session": "s1", "event": "noise"}')):
            if lines:
                self.trace(*lines)
            r = longhaul.inspect_transcript(self.path)
            self.assertIsNone(r["consultations"], lines)
            self.assertFalse([l for l in longhaul.inspect_lines(r) if l.startswith("Consultations")], lines)
            self.assertFalse([x for x in longhaul.inspect_records(r) if x["metric"].startswith("consultation_")])

    def test_a_trace_value_that_is_not_one_of_its_codes_is_counted_as_other(self):
        # The trace is another program's file: what is not a code is never
        # passed on as written, and a decision neither allow nor deny is none.
        self.write(self.reply())
        self.trace(self.decision("c1", "Not A Code!", trigger="sideways"),
                   {**self.outcome("c1", verdict="maybe", outcome="partly"), "reply_chars": "many"},
                   {**self.decision("c2"), "decision": "perhaps"},
                   # A code's shape is not enough: the reasons are ADVISOR_DENIALS' keys (F6).
                   self.decision("c3", "made-up-code", trigger="early"), {**self.decision("c4", "off"), "reason": ["off"]})
        c = longhaul.inspect_transcript(self.path)["consultations"]
        self.assertEqual((c["attempts"], c["admitted"], c["denied"], c["triggers"]),
                         (3, 0, {"other": 3}, {"early": {"attempts": 2, "admitted": 0}, "other": {"attempts": 1, "admitted": 0}}))
        self.assertEqual((c["outcomes"], c["verdicts"], c["reply_chars"], c["reply_chars_max"]),
                         ({"other": 1}, {"other": 1}, 0, 0))

    def test_context_shows_its_first_peak_and_last_size_and_each_compaction(self):
        self.write(self.reply(inp=2, write=20000) + self.reply(inp=1, write=5000, read=900000)
                   + [self.compaction()] + self.reply(inp=5, write=18000))
        self.agent(self.reply(read=990000))  # a subagent's context is its own
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["context"], {"first": 20002, "peak": 905001, "last": 18005})
        self.assertEqual(r["compactions"], [{"trigger": "auto", "before": 967957, "after": 18144, "scope": "main"}])

    def test_the_cache_line_is_the_last_status_line_reading_of_that_conversation(self):
        self.write(self.reply())
        self.assertIsNone(longhaul.inspect_transcript(self.path)["cache"])
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1},
                         "prompt_cache": {"hit_ratio": 0.91, "misses": 2, "miss_recache_tokens": 310200}},
                        None, now=1000.0)
        self.assertEqual(longhaul.inspect_transcript(self.path)["cache"],
                         {"hit": 0.91, "misses": 2, "recache": 310200, "at": 1000.0})

    def test_the_largest_tool_outputs_come_first_with_only_their_file_or_program(self):
        lines = (self.turn("t1", "Read", "x" * 900, file_path=str(Path.home() / "code/app/big.py"))
                 + self.turn("t2", "Bash", "y" * 400, command="GIT_PAGER=cat /usr/bin/git log -p")
                 + self.turn("t3", "Grep", [{"type": "text", "text": "z" * 600}, {"type": "image", "source": {}}],
                             pattern="TODO", path="/srv/app"))
        for i in range(4):
            lines += self.turn(f"s{i}", "Glob", "w" * 10, pattern="*")
        self.write(lines)
        outputs = longhaul.inspect_transcript(self.path)["outputs"]
        self.assertEqual(len(outputs), 5)
        self.assertEqual(outputs[:3], [{"tool": "Read", "what": "~/code/app/big.py", "chars": 900, "scope": "main"},
                                       {"tool": "Grep", "what": "/srv/app", "chars": 600, "scope": "main"},
                                       {"tool": "Bash", "what": "git", "chars": 400, "scope": "main"}])

    def test_tool_calls_and_output_characters_are_totalled_per_tool(self):
        # Sprint 4, W2 (U4): a denied Read that the model replaces with
        # `sed -n 1,400p` moves characters from Read to Bash. Without a
        # total per tool, a run would show a saving that is not one.
        self.write(self.turn("t1", "Read", "x" * 300, file_path="/app/a.py")
                   + self.turn("t2", "Read", "x" * 200, file_path="/app/b.py")
                   + self.turn("t3", "Bash", "y" * 50, command="sed -n 1,400p /app/a.py"))
        self.agent(self.turn("x1", "Read", "x" * 40, file_path="/app/c.py"))  # its own scope
        self.assertEqual(longhaul.inspect_transcript(self.path)["by_tool"],
                         [{"tool": "Read", "calls": 2, "chars": 500, "scope": "main"},
                          {"tool": "Bash", "calls": 1, "chars": 50, "scope": "main"},
                          {"tool": "Read", "calls": 1, "chars": 40, "scope": "subagents"}])
        got = {(x["metric"], x["scope"], x["item"]): (x["value"], x["source"], x["confidence"])
               for x in self.records("s1")}
        self.assertEqual(got[("tool_calls", "main", "Read")], (2, "transcript", "counted"))
        self.assertEqual(got[("tool_chars", "main", "Read")], (500, "transcript", "counted"))
        self.assertEqual(got[("tool_calls", "main", "Bash")], (1, "transcript", "counted"))
        self.assertEqual(got[("tool_chars", "main", "Bash")], (50, "transcript", "counted"))
        self.assertEqual(got[("tool_calls", "subagents", "Read")], (1, "transcript", "counted"))
        out = self.cli("s1").stdout.splitlines()
        head = out.index("Tool calls [transcript, counted]")
        self.assertEqual(out[head + 1:head + 4], ["  Read: 2 calls, 500 chars", "  Bash: 1 call, 50 chars",
                                                  "  Read: 1 call, 40 chars  (a subagent)"])
        self.assertNotIn("1,400p", "\n".join(out), "the tool's name only, never what it ran")

    def test_a_file_read_again_with_no_edit_or_compaction_in_between_is_listed(self):
        a = {"file_path": "/app/a.py"}
        self.write(self.turn("t1", "Read", "x" * 50, **a)
                   + self.turn("t2", "Read", "x" * 50, **a)  # a repeat
                   + self.turn("t3", "Read", "x" * 20, offset=100, limit=20, **a)  # another part
                   + self.turn("t4", "Edit", old_string="x", new_string="y", **a)
                   + self.turn("t5", "Read", "x" * 50, **a)  # the file changed since
                   + [self.compaction("manual", 9, 1)]
                   + self.turn("t6", "Read", "x" * 50, **a)  # gone from the context, so needed again
                   + self.turn("t7", "Read", "x" * 50, **a))  # a repeat
        self.agent(self.turn("x1", "Read", "x" * 50, **a))  # its own context: not a repeat
        self.assertEqual(longhaul.inspect_transcript(self.path)["rereads"],
                         [{"path": "/app/a.py", "repeats": 2, "chars": 100, "images": 0, "scope": "main"}])

    def test_an_image_read_again_says_so_though_it_has_no_characters(self):
        # Seen on a real transcript: a screenshot read twice showed as
        # "1 more time, 0 chars", as if it had cost nothing.
        png = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"}}]
        self.write(self.turn("t1", "Read", png, file_path="/s.png") + self.turn("t2", "Read", png, file_path="/s.png"))
        self.assertEqual(longhaul.inspect_transcript(self.path)["rereads"],
                         [{"path": "/s.png", "repeats": 1, "chars": 0, "images": 1, "scope": "main"}])
        self.assertIn("/s.png: 1 more time, 0 chars and 1 image", self.cli("s1").stdout)

    def test_an_identical_search_with_no_edit_in_between_is_counted(self):
        g = {"pattern": "def main", "path": "/app"}
        self.write(self.turn("t1", "Grep", **g) + self.turn("t2", "Grep", **g)
                   + self.turn("t3", "Grep", output_mode="content", **g)  # other options
                   + self.turn("t4", "Glob", pattern="**/*.py") + self.turn("t5", "Glob", pattern="**/*.py")
                   + self.turn("t6", "Write", file_path="/app/new.py", content="")
                   + self.turn("t7", "Grep", **g))  # a file changed since: the answer may differ
        self.assertEqual(longhaul.inspect_transcript(self.path)["searches"],
                         [{"tool": "Glob", "where": "", "repeats": 1, "scope": "main"},
                          {"tool": "Grep", "where": "/app", "repeats": 1, "scope": "main"}])

    # Sprint 6, G3: the search numbers, and the advice naming a code index.

    def searching(self, n, name="Bash", **args):
        """n searches in a row, each its own call: Bash greps unless told otherwise."""
        args = args or {"command": "grep -rn main /app"}
        return sum((self.turn(f"q{i}", name, "ok", **args) for i in range(n)), [])

    def test_the_search_section_counts_searches_their_chains_and_their_big_results(self):
        # The count (BACKLOG, G3): a search is Grep, Glob, or a Bash whose
        # program is grep, rg, ag, find or fd; a chain is a run of
        # consecutive searches in one context; a result is big over 5,000
        # characters. A cat piped into grep is a cat. git and ls are no
        # search (review, Sprint 6: twenty `git status`es in an indexed
        # project nudged the model about CodeGraph with no search made), so
        # `git grep` goes uncounted: the program alone cannot tell it from
        # `git log`.
        self.write(self.turn("t1", "Grep", "x" * 6000, pattern="main", path="/app")  # big
                   + self.turn("t2", "Bash", "x" * 5000, command="grep -rn main /app")  # 5,000 exactly: not big
                   + self.turn("t3", "Bash", "x" * 375, command="rg main /app")
                   + self.turn("t4", "Read", "x" * 900, file_path="/app/a.py")  # ends a chain of 3
                   + self.turn("t5", "Bash", "ok", command="make")
                   + self.turn("t6", "Glob", "x" * 125, pattern="**/*.py")
                   + self.turn("t7", "Bash", "x" * 125, command="GIT_PAGER=cat /usr/bin/git log --oneline")  # no search
                   + self.turn("t8", "Bash", "x" * 900, command="cat /app/a.py | grep main")
                   + self.turn("t9", "Bash", "x" * 125, command="fd main /app")
                   + self.turn("t10", "Bash", "x" * 125, command="ag main /app")
                   + self.turn("t11", "Bash", "x" * 125, command="ls /app")  # no search: ends a chain of 2
                   + self.turn("t12", "Bash", "x" * 125, command="ls -R /app"))
        self.agent(self.turn("x1", "Grep", "x" * 125, pattern="main", path="/app")  # its own chain of 2
                   + self.turn("x2", "Grep", "x" * 125, pattern="main", path="/app")
                   + sum((self.turn(f"x{i}", "Read", "x", file_path=f"/app/{i}.py") for i in range(3, 9)), []))
        self.assertEqual(longhaul.inspect_transcript(self.path)["search"],
                         {"search_calls": 8, "search_share": 0.4, "search_chars": 12000, "search_chain_max": 3,
                          "search_chain_mean": 2.0, "search_big": 1, "search_big_share": 0.5,
                          "codegraph_calls": 0, "codegraph_index": None, "trail_calls": 0, "trail_index": None})
        out = self.cli("s1").stdout.splitlines()
        head = out.index("Search [transcript, counted: Grep, Glob, and Bash grep, rg, ag, find, fd]")
        self.assertGreater(head, out.index("Tool calls [transcript, counted]"))
        self.assertEqual(out[head + 1:head + 6],
                         ["  8 searches, 40% of calls, 12,000 chars", "  longest chain 3, mean 2.0",
                          "  1 result over 5,000 chars, 50% of search output",
                          "  CodeGraph: 0 calls, no cwd in the transcript to look for an index",
                          "  Trail: 0 calls, no cwd in the transcript to look for a map"])
        self.assertNotIn("Search-heavy", "\n".join(out))
        got = {(x["metric"], x["scope"], x["item"]): (x["value"], x["unit"], x["source"], x["confidence"])
               for x in self.records("s1")}
        for metric, value, unit in (("search_calls", 8, "calls"), ("search_share", 0.4, "ratio"),
                                    ("search_chars", 12000, "chars"), ("search_chain_max", 3, "searches"),
                                    ("search_chain_mean", 2.0, "searches"), ("search_big", 1, "results"),
                                    ("search_big_share", 0.5, "ratio"), ("codegraph_calls", 0, "calls"),
                                    ("trail_calls", 0, "calls")):
            self.assertEqual(got[(metric, "all", None)], (value, unit, "transcript", "counted"), metric)
        for metric in ("codegraph_index", "trail_index"):
            self.assertNotIn((metric, "all", None), got, "no cwd: the record is left out, not 0")

    def test_twenty_searches_bring_one_line_of_advice_and_nineteen_bring_none(self):
        self.write(self.searching(19))
        self.assertNotIn("Search-heavy", self.cli("s1").stdout)
        self.write(self.searching(20))
        out = self.cli("s1").stdout.splitlines()
        advice = [l for l in out if "Search-heavy" in l]
        self.assertEqual(advice, [
            "  Search-heavy: 20 searches (100% of calls), the longest chain 20, 0 results over 5,000 characters "
            "(0% of search output). A code index answers symbol and dependency questions in one call. CodeGraph "
            "(github.com/colbymchenry/codegraph, MIT): install with `npm i -g @colbymchenry/codegraph`, then run "
            "`codegraph init`. CodeGraph's installer asks about telemetry, and its setup may add a section to your "
            "CLAUDE.md and allow its tools in Claude Code's permissions unless told not to; `codegraph init` writes "
            "`.codegraph/` in the project. Longhaul installs nothing."])
        # The report enters the model's context as tool output through
        # /longhaul:inspect, so the pipe-to-shell install CodeGraph's README
        # also gives stays in ours and out of the report (review, Sprint 6).
        self.assertNotIn("curl", "\n".join(out))
        self.assertNotIn("| sh", "\n".join(out))
        head = out.index("Search [transcript, counted: Grep, Glob, and Bash grep, rg, ag, find, fd]")
        self.assertEqual(out[head + 6], advice[0], "the advice is the Search section's last row, after the Trail line")
        self.assertNotIn("Search-heavy", self.cli("s1", "--json").stdout, "the records carry numbers only")

    def test_a_codegraph_call_counts_only_when_it_did_not_fail_and_the_cli_counts_too(self):
        # A failed call (the MCP server down, no index yet) says nothing of
        # an index in use, so it must not withhold the advice; the CLI,
        # `codegraph explore` through Bash, is the same tool and no search.
        failed = self.turn("c1", "mcp__codegraph__codegraph_explore", "no index", error=True, query="who calls X")
        self.write(self.searching(20) + failed)
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual((r["search"]["search_calls"], r["search"]["codegraph_calls"]), (20, 0))
        self.assertEqual(r["tool_errors"], {"mcp__codegraph__codegraph_explore": 1})
        out = self.cli("s1").stdout
        self.assertIn("CodeGraph: 0 calls, no cwd in the transcript to look for an index", out)
        self.assertIn("Search-heavy", out, "a call that failed is no index in use")
        for command in ('codegraph explore "who calls X"', "/opt/homebrew/bin/codegraph query X"):
            self.write(self.searching(20) + self.turn("c2", "Bash", "ok", command=command))
            r = longhaul.inspect_transcript(self.path)
            self.assertEqual((r["search"]["search_calls"], r["search"]["codegraph_calls"]), (20, 1), command)
            out = self.cli("s1").stdout
            self.assertIn("CodeGraph: 1 call, no cwd in the transcript to look for an index", out)
            self.assertNotIn("Search-heavy", out, command)
        self.write(self.searching(20) + self.turn("c3", "Bash", "not found", error=True, command="codegraph explore X"))
        self.assertEqual(longhaul.inspect_transcript(self.path)["search"]["codegraph_calls"], 0, "the CLI failed")
        self.assertIn("Search-heavy", self.cli("s1").stdout)
        self.write(self.searching(20) + failed + self.turn("c4", "mcp__codegraph__codegraph_explore", "ok", query="X"))
        self.assertEqual(longhaul.inspect_transcript(self.path)["search"]["codegraph_calls"], 1, "one of two answered")

    def test_a_codegraph_call_or_an_index_under_the_cwd_is_seen_and_withholds_the_advice(self):
        self.write(self.searching(20) + self.turn("c1", "mcp__codegraph__codegraph_explore", "ok", query="who calls X"))
        s = longhaul.inspect_transcript(self.path)["search"]
        self.assertEqual((s["search_calls"], s["codegraph_calls"], s["codegraph_index"]), (20, 1, None))
        out = self.cli("s1").stdout
        self.assertIn("CodeGraph: 1 call, no cwd in the transcript to look for an index", out)
        self.assertNotIn("Search-heavy", out, "the index is in use: nothing to install")
        # The first line's cwd, with .codegraph/ at it, above it (a session
        # opened in a subfolder of an indexed repository) and without; a
        # relative cwd is unknown, like none.
        project = self.tmp / "project"
        (project / ".codegraph").mkdir(parents=True)
        lines = self.searching(20)
        lines[0]["cwd"] = str(project)
        self.write(lines)
        out = self.cli("s1").stdout
        self.assertIn("CodeGraph: 0 calls, a .codegraph/ index at or above the cwd", out)
        self.assertNotIn("Search-heavy", out)
        got = {x["metric"]: (x["value"], x["unit"], x["source"], x["confidence"]) for x in self.records("s1")}
        self.assertEqual(got["codegraph_index"], (1, "indexes", "transcript", "counted"))
        self.assertNotIn(str(project), out + self.cli("s1", "--json").stdout, "the cwd is looked under, never shown")
        lines[0]["cwd"] = str(project / "src" / "deep")
        self.write(lines)
        self.assertEqual(longhaul.inspect_transcript(self.path)["search"]["codegraph_index"], 1, "a subfolder")
        for cwd in (str(self.tmp / "elsewhere"), "project", 7):
            lines[0]["cwd"] = cwd
            self.write(lines)
            out = self.cli("s1").stdout
            self.assertIn("Search-heavy", out)
            if cwd == str(self.tmp / "elsewhere"):
                self.assertIn("CodeGraph: 0 calls, no .codegraph/ index at or above the cwd", out)
                self.assertEqual(longhaul.inspect_transcript(self.path)["search"]["codegraph_index"], 0)
            else:
                self.assertIn("no cwd in the transcript to look for an index", out)
                self.assertIsNone(longhaul.inspect_transcript(self.path)["search"]["codegraph_index"], cwd)

    def test_a_trail_map_or_a_trail_call_is_an_index_seen_and_withholds_the_advice(self):
        # Sprint 7, G4 part 5: Trail-Code's map, `.trail/`, and its tools
        # (`trail_find_code` and five more, named `mcp__trail__trail_*`
        # under Claude Code; the `trail` CLI through Bash) are an index
        # seen, counted as CodeGraph's are: a call only when it did not
        # fail, the map when it is at or above the first line's cwd, and
        # either withholds the advice. The Trail line follows the CodeGraph
        # line; without a cwd the map is unknown and its record left out.
        project = self.tmp / "project"
        (project / ".trail").mkdir(parents=True)
        lines = self.searching(20)
        lines[0]["cwd"] = str(project / "src")  # a session opened in a subfolder of the mapped repository
        self.write(lines)
        s = longhaul.inspect_transcript(self.path)["search"]
        self.assertEqual((s["codegraph_index"], s["codegraph_calls"], s["trail_index"], s["trail_calls"]), (0, 0, 1, 0))
        out = self.cli("s1").stdout.splitlines()
        head = out.index("Search [transcript, counted: Grep, Glob, and Bash grep, rg, ag, find, fd]")
        self.assertEqual(out[head + 4:head + 6], ["  CodeGraph: 0 calls, no .codegraph/ index at or above the cwd",
                                                  "  Trail: 0 calls, a .trail/ map at or above the cwd"])
        self.assertTrue(out[head + 6].startswith("Read again"), "no advice: the Trail line is the section's last row")
        self.assertNotIn("Search-heavy", "\n".join(out), "the map is there to use: nothing to install")
        got = {x["metric"]: (x["value"], x["unit"], x["source"], x["confidence"]) for x in self.records("s1")}
        self.assertEqual(got["trail_index"], (1, "indexes", "transcript", "counted"))
        self.assertEqual(got["trail_calls"], (0, "calls", "transcript", "counted"))
        self.assertNotIn(str(project), "\n".join(out) + self.cli("s1", "--json").stdout, "looked under, never shown")
        lines[0]["cwd"] = str(self.tmp / "elsewhere")
        self.write(lines)
        out = self.cli("s1").stdout
        self.assertIn("Trail: 0 calls, no .trail/ map at or above the cwd", out)
        self.assertIn("Search-heavy", out, "no map, no call: the advice runs")
        self.assertEqual(longhaul.inspect_transcript(self.path)["search"]["trail_index"], 0)
        # The calls: one of the six tools as Claude Code names it, one bare,
        # and the CLI through Bash; each is one call and withholds the advice.
        for name, args in (("mcp__trail__trail_find_code", {"query": "who calls X"}),
                           ("trail_trace_calls", {"symbol": "X"}), ("Bash", {"command": "trail find-code X"}),
                           ("Bash", {"command": "/opt/homebrew/bin/trail repo-map"})):
            self.write(self.searching(20) + self.turn("c1", name, "ok", **args))
            s = longhaul.inspect_transcript(self.path)["search"]
            self.assertEqual((s["search_calls"], s["trail_calls"], s["codegraph_calls"], s["trail_index"]),
                             (20, 1, 0, None), name)
            out = self.cli("s1").stdout
            self.assertIn("Trail: 1 call, no cwd in the transcript to look for a map", out)
            self.assertNotIn("Search-heavy", out, name)
        # A failed call (the server down, no map yet) says nothing of a map
        # in use; a name that merely begins like Trail's is no Trail call.
        for name, args in (("mcp__trail__trail_find_code", {"query": "X"}), ("Bash", {"command": "trail find-code X"})):
            self.write(self.searching(20) + self.turn("c1", name, "no map", error=True, **args)
                       + self.turn("c2", "mcp__trailhead__find", "ok", query="X")
                       + self.turn("c3", "Bash", "ok", command="trailer --version"))
            self.assertEqual(longhaul.inspect_transcript(self.path)["search"]["trail_calls"], 0, name)
            out = self.cli("s1").stdout
            self.assertIn("Trail: 0 calls, no cwd in the transcript to look for a map", out)
            self.assertIn("Search-heavy", out, name)
        got = {x["metric"] for x in self.records("s1")}
        self.assertIn("trail_calls", got)
        self.assertNotIn("trail_index", got, "no cwd: the record is left out, not 0")

    def test_tool_errors_are_counted_per_tool(self):
        self.write(self.turn("t1", "Bash", "Exit code 1", error=True, command="make")
                   + self.turn("t2", "Bash", "Exit code 2", error=True, command="make")
                   + self.turn("t3", "Edit", "<tool_use_error>", error=True, file_path="/a")
                   + self.turn("t4", "Bash", "fine", error=False, command="ls"))
        self.assertEqual(longhaul.inspect_transcript(self.path)["tool_errors"], {"Bash": 2, "Edit": 1})

    def test_the_copies_of_a_request_claude_code_also_writes_are_not_counted_again(self):
        # 2.1.304 also writes api-request-blob lines, which repeat the
        # request's tool_use and tool_result blocks.
        blob = {"type": "api-request-blob", "message": {"content": [
            self.call("t1", "Read", file_path="/a.py"),
            {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 5000, "is_error": True}]}}
        self.write(self.turn("t1", "Read", "x" * 50, file_path="/a.py") + [blob])
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual((r["rereads"], r["tool_errors"], [o["chars"] for o in r["outputs"]]), ([], {}, [50]))
        self.assertEqual(r["by_tool"], [{"tool": "Read", "calls": 1, "chars": 50, "scope": "main"}])

    def test_a_call_written_twice_is_one_call_and_its_last_result_is_its_output(self):
        # Review, Sprint 4 (TA): the per-tool totals against a line written
        # twice (a copy, a retried write): one call, the characters of the
        # last result only; a result whose call is nowhere lands under "?".
        def result(tid, text):
            return {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text}]}}
        twice = self.reply(self.call("t1", "Read", file_path="/a.py"))
        self.write(twice + twice + [result("t1", "x" * 50), result("t1", "y" * 70), result("t9", "z" * 30)])
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual(r["requests"]["main"], 1)
        self.assertEqual(r["by_tool"], [{"tool": "Read", "calls": 1, "chars": 70, "scope": "main"},
                                        {"tool": "?", "calls": 0, "chars": 30, "scope": "main"}])
        self.assertEqual([(o["tool"], o["chars"]) for o in r["outputs"]], [("Read", 70), ("?", 30)])

    def test_a_line_that_cannot_be_read_is_counted_not_skipped_silently(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(self.reply()[0]) + "\n{half a line\n[1, 2]\n\n")
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual((r["requests"]["main"], r["unreadable"]), (1, 2))

    def test_with_no_id_longhaul_inspect_reads_the_conversation_the_ledger_heard_from_last(self):
        self.write(self.reply())
        other = self.write(self.reply(model="claude-haiku-5-5"), self.claude / "projects" / "-code-web" / "s2.jsonl")
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1}}, None, now=1000.0)
        longhaul.record_usage({"session_id": "s2", "cost": {"total_cost_usd": 0.1}}, None, now=2000.0)
        out = self.cli()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("s2", out.stdout)
        self.assertIn("claude-haiku-5-5", out.stdout)

    def test_longhaul_inspect_finds_a_conversation_by_its_recorded_path_its_id_or_a_short_id(self):
        sid = "557ce21f-c99c-49f0-93e5-22ae7d1d1ae7"
        moved = self.write(self.reply(model="claude-haiku-5-5"), self.tmp / "moved" / f"{sid}.jsonl")
        longhaul.record_usage({"session_id": sid, "cost": {"total_cost_usd": 0.1}, "transcript_path": str(moved)}, None)
        self.write(self.reply(model="claude-sonnet-5-5"),
                   self.claude / "projects" / "-code-app" / "9f0e1d2c-0000-4000-8000-000000000000.jsonl")
        self.write(self.reply())
        self.assertIn("claude-haiku-5-5", self.cli(sid).stdout)
        self.assertIn("claude-sonnet-5-5", self.cli("9f0e1d2c").stdout)
        self.assertIn("claude-opus-5-5", self.cli("s1").stdout)
        self.assertIn("claude-opus-5-5", self.cli(str(self.path)).stdout)

    def test_a_conversation_that_cannot_be_found_is_an_error_not_an_empty_report(self):
        self.write(self.reply())
        # "*" must not match every transcript; [] is with an empty ledger
        for args in (["nope"], ["*"], [], ["nope", "--json"]):
            out = self.cli(*args)
            self.assertEqual((out.returncode, out.stdout), (1, ""), args)
            self.assertIn("longhaul inspect:", out.stderr)

    def test_each_part_of_the_report_says_where_its_numbers_came_from(self):
        self.write(self.turn("t1", "Read", "x" * 50, file_path="/a.py") + self.turn("t2", "Read", "x", file_path="/a.py")
                   + self.turn("t3", "Bash", "no", error=True, command="make") + [self.compaction()])
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1},
                         "prompt_cache": {"hit_ratio": 0.5, "misses": 1, "miss_recache_tokens": 9}}, None)
        self.trace(self.decision("c1"))  # Sprint 8: the Consultations section too
        out = self.cli("s1")
        self.assertEqual(out.returncode, 0, out.stderr)
        heads = [l for l in out.stdout.splitlines()[1:] if l.strip() and not l.startswith(" ")]
        self.assertGreaterEqual(len(heads), 7, out.stdout)
        self.assertIn(self.CONSULTATIONS, heads)
        for head in heads:
            self.assertRegex(head, r"\[(transcript|status line|longhaul guard|longhaul advisor trace)[^\]]*\]", head)
        for head in heads:
            if head.startswith(("Read again", "The same search")):
                self.assertIn("through Bash are not seen", head)

    # Review, Phase 2 (silent-failure-hunter): the tests below up to records().

    def test_a_quoted_or_escaped_assignment_never_passes_for_the_program(self):
        # Split on spaces, `API_KEY="abc def ghi" git push` gave "def".
        for command, name in (('API_KEY="abc SECRET ghi" git push', "git"), ("A=x\\ SECRET git log", "git"),
                              ("FOO='a b' BAR=c /usr/bin/make -j", "make"), ('X="unclosed SECRET', "?"),
                              ("git commit -F - <<'EOF'\nit's done\nEOF", "git"), ("", "?")):
            self.assertEqual(longhaul.program(command), name, command)

    def test_a_file_with_no_transcript_lines_is_an_error_not_an_empty_report(self):
        junk = self.tmp / "junk"
        junk.mkdir()
        (junk / "empty.jsonl").write_text("")
        (junk / "other.jsonl").write_text('{"a": 1}\n{"b": 2}\n')
        for name in ("empty.jsonl", "other.jsonl"):
            out = self.cli(str(junk / name))
            self.assertEqual((out.returncode, out.stdout), (1, ""), name)
            self.assertIn("no transcript lines", out.stderr)
        # A file in this folder named like a conversation is not taken for it.
        (junk / "s1").write_text("not a transcript\n")
        self.write(self.reply())
        out = self.cli("s1", cwd=junk)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("claude-opus-5-5", out.stdout)

    def test_a_file_a_subagent_changed_is_not_a_repeat_when_read_again(self):
        a, g = {"file_path": "/app/a.py"}, {"pattern": "x", "path": "/app"}
        self.write(self.at(1, self.turn("t1", "Read", "x" * 50, **a) + self.turn("g1", "Grep", **g))
                   + self.at(3, self.turn("t2", "Read", "x" * 50, **a) + self.turn("g2", "Grep", **g)))
        self.agent(self.at(2, self.turn("e1", "Edit", old_string="x", new_string="y", **a)))
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual((r["rereads"], r["searches"]), ([], []))

    def test_a_read_that_failed_is_neither_a_repeat_nor_repeated(self):
        a = {"file_path": "/app/a.py"}
        self.write(self.turn("t1", "Read", "File does not exist.", error=True, **a)
                   + self.turn("t2", "Read", "x" * 50, **a)  # the first that put the file into the context
                   + self.turn("t3", "Read", "<tool_use_error>", error=True, **a)
                   + self.turn("t4", "Read", "x" * 30, **a))  # the one repeat
        self.assertEqual(longhaul.inspect_transcript(self.path)["rereads"],
                         [{"path": "/app/a.py", "repeats": 1, "chars": 30, "images": 0, "scope": "main"}])

    def test_a_later_line_without_usage_keeps_the_usage_and_a_synthetic_line_is_no_request(self):
        lines = self.reply({"type": "text", "text": "a"}, {"type": "text", "text": "b"}, out=308)
        del lines[1]["message"]["usage"]
        synthetic = self.reply(model="<synthetic>")  # 18 such lines on this Mac, none an API request
        self.write(lines + synthetic)
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual((r["requests"]["main"], r["tokens"]["main"]["output"]), (1, 308))

    def test_a_short_id_several_conversations_start_with_says_so(self):
        for tail in ("0000-4000-8000-000000000000", "1111-4000-8000-000000000000"):
            self.write(self.reply(), self.claude / "projects" / "-code-app" / f"9f0e1d2c-{tail}.jsonl")
        out = self.cli("9f0e")
        self.assertEqual(out.returncode, 1)
        self.assertIn("2 conversations start with 9f0e", out.stderr)

    def test_one_conversation_in_two_project_folders_says_which_one_it_read(self):
        self.write(self.reply())
        self.write(self.reply(model="claude-haiku-5-5"), self.claude / "projects" / "-code-old" / "s1.jsonl")
        out = self.cli("s1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("2 transcripts", out.stderr)

    def test_odd_ids_and_deeply_nested_lines_do_not_lose_the_report(self):
        odd = self.reply({"type": "tool_use", "id": ["x"], "name": "Read", "input": {"file_path": "/a"}})
        odd += [{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": {"a": 1},
                                                           "content": "x"}]}}]
        self.path.parent.mkdir(parents=True)
        self.path.write_text("".join(json.dumps(l) + "\n" for l in odd + self.reply())
                             + "[" * 100000 + "]" * 100000 + "\n")
        r = longhaul.inspect_transcript(self.path)
        self.assertEqual((r["requests"]["main"], r["unreadable"]), (2, 1))

    def test_control_characters_from_a_transcript_never_reach_the_terminal(self):
        lines = self.turn("t1", "Read", "x", file_path="/a\x1b[2J.py") + self.reply(model="m\x1b]0;title\x07")
        lines[0]["message"]["content"][0]["name"] = "Re\x07ad"
        self.write(lines)
        out = self.cli("s1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse({"\x1b", "\x07"} & set(out.stdout + self.cli("s1", "--json").stdout))

    def test_the_cache_line_survives_a_later_reading_without_cache_numbers(self):
        # After longhaul reopens a conversation, the first readings come before
        # any reply, with no prompt_cache, and they still write a row.
        self.write(self.reply())
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1},
                         "prompt_cache": {"hit_ratio": 0.91, "misses": 2, "miss_recache_tokens": 9}}, None, now=1000.0)
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.0}}, None, now=2000.0)
        self.assertEqual(longhaul.inspect_transcript(self.path)["cache"],
                         {"hit": 0.91, "misses": 2, "recache": 9, "at": 1000.0})

    def test_a_number_json_cannot_hold_is_not_passed_on(self):
        self.assertIsNone(longhaul.as_number(float("nan")))
        self.assertIsNone(longhaul.as_number(float("inf")))

    def test_rows_the_ledger_cannot_use_are_passed_over(self):
        self.write(self.reply())
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1}}, None, now=1000.0)
        with longhaul.LEDGER.open("a") as f:
            f.write('[1, 2]\n{"at": "late", "session": "bad"}\n{"session": "nameless"}\n')
        self.assertEqual([r["session"] for r in longhaul.ledger_rows()], ["s1", "nameless"])
        out = self.cli()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Conversation s1", out.stdout)

    def records(self, *args):
        out = self.cli(*args, "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        return [json.loads(l) for l in out.stdout.splitlines()]

    def test_json_gives_every_number_its_source_and_confidence(self):
        lines = self.reply(self.call("t1", "Read", file_path="/a.py"), inp=3, write=100, read=5000, out=40)
        lines += [{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                             "content": "x" * 400}]}}]
        self.write(lines + self.turn("t2", "Read", "x" * 400, file_path="/a.py")
                   + self.turn("t3", "Glob", pattern="*") + self.turn("t4", "Glob", pattern="*")
                   + self.turn("t5", "Bash", "no", error=True, command="make") + [self.compaction()]
                   + [{"type": "assistant", "isApiErrorMessage": True, "error": "server_error", "message": {}}])
        self.agent(self.reply(out=308))
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1},
                         "prompt_cache": {"hit_ratio": 0.91, "misses": 2, "miss_recache_tokens": 310200}}, None)
        records = self.records("s1")
        allowed = {("transcript", "measured"), ("transcript", "counted"), ("transcript", "inferred"),
                   ("statusline", "measured"), ("estimate", "estimated")}
        for r in records:
            # Sprint 8 gained `kind`, the token kind of a record by model, None elsewhere: --json only gains keys.
            self.assertEqual(set(r), {"session", "metric", "scope", "item", "kind", "value", "unit", "source",
                                      "confidence"}, r)
            self.assertIn((r["source"], r["confidence"]), allowed, r)
            self.assertIsInstance(r["value"], (int, float), r)
            self.assertEqual(r["session"], "s1")
            if r["metric"] != "model_tokens":
                self.assertIsNone(r["kind"], r)
        by_model = {(r["scope"], r["item"], r["kind"]): (r["value"], r["unit"], r["source"], r["confidence"])
                    for r in records if r["metric"] == "model_tokens"}
        self.assertEqual(len(by_model), 8, "four kinds for each model in each scope")
        self.assertEqual(by_model[("main", "claude-opus-5-5", "input")], (3 + 2 * 4, "tokens", "transcript", "measured"))
        self.assertEqual(by_model[("main", "claude-opus-5-5", "cache_read")], (5000, "tokens", "transcript", "measured"))
        self.assertEqual(by_model[("subagents", "claude-opus-5-5", "output")], (308, "tokens", "transcript", "measured"))
        got = {(r["metric"], r["scope"], r["item"]): (r["value"], r["source"], r["confidence"]) for r in records}
        self.assertEqual(got[("input_tokens", "main", None)], (3 + 2 * 4, "transcript", "measured"))
        self.assertEqual(got[("output_tokens", "subagents", None)], (308, "transcript", "measured"))
        self.assertEqual(got[("requests", "main", None)], (5, "transcript", "counted"))
        self.assertEqual(got[("context_peak", "main", None)], (5103, "transcript", "measured"))
        self.assertEqual(got[("compaction_before", "main", "1 auto")], (967957, "transcript", "measured"))
        self.assertEqual(got[("cache_hit_ratio", "main", None)], (0.91, "statusline", "measured"))
        self.assertEqual(got[("tool_output_chars", "main", "Read /a.py")], (400, "transcript", "counted"))
        self.assertEqual(got[("tool_output_tokens", "main", "Read /a.py")], (100, "estimate", "estimated"))
        self.assertEqual(got[("repeated_reads", "main", "/a.py")], (1, "transcript", "inferred"))
        self.assertEqual(got[("repeated_searches", "main", "Glob")], (1, "transcript", "inferred"))
        self.assertEqual(got[("tool_errors", "all", "Bash")], (1, "transcript", "counted"))
        self.assertEqual(got[("api_errors", "all", "server_error")], (1, "transcript", "counted"))
        self.assertEqual(got[("unreadable_lines", "all", None)], (0, "transcript", "counted"))
        self.assertEqual(got[("transcript_lines", "all", None)], (13, "transcript", "counted"))

    def test_json_sums_by_metric_without_counting_anything_twice(self):
        # Totals are left to the reader: a "total" row next to main and
        # subagents would be added in a second time by anyone summing a metric.
        self.write(self.reply(out=40) + self.reply(out=2))
        self.agent(self.reply(out=308))
        out = [r for r in self.records("s1") if r["metric"] == "output_tokens"]
        self.assertEqual(sum(r["value"] for r in out), 350)

    def test_no_message_text_tool_output_or_tool_input_reaches_the_output_or_the_ledger(self):
        secret = "sk-ant-oat01-SECRET" + "Q" * 80
        self.write(self.reply({"type": "text", "text": f"the key is {secret}"})
                   + self.turn("t1", "Bash", f"token={secret}",
                               command=f"curl -H 'Authorization: Bearer {secret}' https://x")
                   + self.turn("t2", "Bash", "ok", command=f"{secret} --help")  # a secret as the first word
                   + self.turn("t3", "Grep", secret, pattern=secret, path="/app")
                   + self.turn("t4", "Grep", secret, pattern=secret, path="/app")
                   + self.turn("t5", "WebFetch", secret, url=f"https://x/?k={secret}", prompt=secret)
                   + self.turn("t6", "Agent", secret, error=True, prompt=secret, subagent_type=secret)
                   # Sprint 7: a Trail call, through its tool and its CLI, with the secret as its argument; both
                   # failed, so that no map is seen and the advice line still runs over the searches below.
                   # The tool's answer is the biggest output, so its row in the report names the tool.
                   + self.turn("t7", "mcp__trail__trail_find_code", secret * 2, error=True, query=secret)
                   + self.turn("t8", "Bash", secret, error=True, command=f"trail find-code {secret}")
                   + sum((self.turn(f"q{i}", "Bash", secret, command=f"grep -rn {secret} /app")  # the pattern is the
                          for i in range(18)), [])  # secret, and with t3 and t4 that is 20 searches: the advice runs
                   + [{"type": "user", "message": {"role": "user", "content": f"remember {secret}"}},
                      {"type": "assistant", "isApiErrorMessage": True, "error": secret, "message": {}}])
        longhaul.record_usage({"session_id": "s1", "cost": {"total_cost_usd": 0.1}, "session_name": secret,
                         "workspace": {"current_dir": secret}, "model": {"id": "claude-opus-5-5",
                                                                         "display_name": secret}}, None)
        # Sprint 8: a consultation whose trace lines hold the secret in every field, those the hooks write a code
        # in and a brief and a reply they never write; the advisor's transcript and meta.json hold it as text.
        self.trace({**self.decision(secret, secret, trigger=secret), "kind": secret, "executor": secret,
                    "advisor": secret, "brief_hash": secret, "prompt_id": secret, "brief": secret},
                   {**self.outcome(secret, verdict=secret, outcome=secret), "confidence": secret, "reply": secret})
        self.agent(self.reply({"type": "text", "text": f"Verdict: proceed\n{secret}"}), "agent-adv")
        self.meta("agent-adv", agentType="longhaul:advisor", description=secret, toolUseId=secret)
        out, records = self.cli("s1"), self.cli("s1", "--json")
        self.assertEqual((out.returncode, records.returncode), (0, 0), out.stderr + records.stderr)
        self.assertIn("denied by reason: other 1", out.stdout, "the Consultations section ran over the trace")
        self.assertIn("own tokens, from 1 advisor transcript:", out.stdout)
        self.assertIn("curl", out.stdout)  # t1's program, which the report may name
        self.assertIn("curl", records.stdout)
        self.assertIn("Search-heavy", out.stdout, "the Search section and its advice line ran over those searches")
        self.assertIn("Trail: 0 calls", out.stdout, "the Trail line ran over t7 and t8")
        self.assertNotIn(longhaul.CODEGRAPH_INSTALL["sh"], out.stdout, "the advice carries no pipe-to-shell line")
        self.assertNotIn("| sh", out.stdout)
        self.assertNotIn("SECRET", out.stdout + out.stderr + records.stdout + records.stderr)
        self.assertNotIn("SECRET", longhaul.LEDGER.read_text() + (longhaul.USAGE_DIR / "sessions" / "s1.json").read_text())


class Plugin(unittest.TestCase):
    """The repository is also a Claude Code plugin (docs/adr/0001-plugin-layout.md)."""

    def manifest(self):
        return json.loads((HERE / ".claude-plugin" / "plugin.json").read_text())

    def test_the_manifest_names_longhaul_its_version_author_and_licence(self):
        m = self.manifest()
        self.assertEqual((m["name"], m["license"]), ("longhaul", "MIT"))
        self.assertRegex(m["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(m["description"] and m["author"]["name"])
        self.assertNotRegex(m["name"], r"^(claude-|anthropic-|anthropics-|cc-plugin-)", "a reserved name")

    def test_every_component_path_the_manifest_names_is_inside_the_plugin_and_exists(self):
        m = self.manifest()
        for key in ("skills", "commands", "agents", "hooks", "mcpServers", "outputStyles", "lspServers"):
            value = m.get(key)  # a path, a list of paths, or (hooks, mcpServers) an inline object
            for path in [value] if isinstance(value, str) else value if isinstance(value, list) else []:
                if isinstance(path, str):
                    self.assertTrue(path.startswith("./") and ".." not in path, path)
                    self.assertTrue((HERE / path).exists(), path)

    def test_the_marketplace_lists_this_plugin_once_with_the_manifests_name_version_and_description(self):
        # P8 (Sprint 5), ADR-0001 decision 1: the root is its own marketplace,
        # so `/plugin install longhaul@longhaul` works. The entry repeats the
        # manifest's name, version and description, so a bump changes both
        # files or fails here (and `validate --strict`, which warns when an
        # entry's version differs from plugin.json's).
        m = self.manifest()
        market = json.loads((HERE / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(market["name"], "longhaul")
        self.assertTrue(market["description"], "validate --strict fails without one")
        self.assertEqual(market["owner"], {"name": m["author"]["name"]})  # no email here
        (entry,) = market["plugins"]
        self.assertEqual(entry["source"], "./")
        for key in ("name", "version", "description"):
            self.assertEqual(entry[key], m[key], key)

    def test_the_changelog_top_entry_is_the_manifests_version(self):
        # Review, Sprint 5 (G2): the newest entry is the version a user
        # installs, so a bump that touched the manifests and not the
        # CHANGELOG (or the other way round) fails here.
        heading = re.search(r"^## (\S+)", (HERE / "CHANGELOG.md").read_text(), re.M)
        self.assertIsNotNone(heading, "a version heading")
        self.assertEqual(heading.group(1), self.manifest()["version"], "the CHANGELOG's first ## heading")

    def test_git_tracks_the_plugin_files_and_the_licence(self):
        # .gitignore lets files in by name, so a new one stays untracked
        # while every test here still passes.
        requires_git(self)
        # Unignored is not tracked (review, Sprint 5, G3): a file let in by
        # name and never added would pass check-ignore and still not ship,
        # so each is looked for in the index too.
        tracked = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True, timeout=10).stdout.split()
        for path in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "LICENSE", "README.md",
                     "CHANGELOG.md", "hooks/hooks.json", "bin/longhaul", ".github/workflows/tests.yml"):
            self.assertEqual(subprocess.run(["git", "check-ignore", "-q", path], cwd=HERE, timeout=10).returncode, 1,
                             f"{path} is ignored by .gitignore")
            self.assertIn(path, tracked, f"{path} is not in git's index")
        # The owner's own tooling never ships (decided 2026-10-20): the
        # planning docs, the build-time agents and skills, their attribution.
        for path in ("docs/ONBOARDING.md", ".claude/agents/planner.md", "ATTRIBUTION.md"):
            self.assertEqual(subprocess.run(["git", "check-ignore", "-q", path], cwd=HERE, timeout=10).returncode, 0,
                             f"{path} would be shipped")
            self.assertNotIn(path, tracked, f"{path} is in git's index, ignored or not")

    def test_the_licence_is_mit_and_names_the_author(self):
        text = (HERE / "LICENSE").read_text()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn(f"Copyright (c) 2026 {self.manifest()['author']['name']}", text)

    # The onboarding's command: the whole suite against scratch folders,
    # never the owner's accounts. CI runs it as it is, on every push.
    SUITE = ("S=$(mktemp -d); LONGHAUL_HOME=$S/home LONGHAUL_DEFAULT_CONFIG_DIR=$S/claude "
             "LONGHAUL_DEFAULT_GLOBAL_JSON=$S/claude.json LONGHAUL_NO_NOTIFY=1 python3 -m unittest test_longhaul -q")

    def workflow(self):
        """The workflow's text and each job's lines by name. The stdlib has no
        YAML parser, so the file's shape is read with regexes."""
        text = (HERE / ".github" / "workflows" / "tests.yml").read_text()
        return text, dict(re.findall(r"^  (\w+):\n((?:(?:    .*)?\n)+)", text.split("\njobs:\n", 1)[1], re.M))

    def runs(self, body: str) -> list:
        return re.findall(r"^[ \t]+(?:- )?run: (.*)$", body, re.M)

    def floor(self) -> str:
        """The oldest Python any job's matrix runs the suite on, as written there."""
        text, _ = self.workflow()
        listed = {v for row in re.findall(r"python-version: \[(.*)\]", text) for v in re.findall(r'"(3\.\d+)"', row)}
        return min(listed, key=lambda v: tuple(map(int, v.split("."))))

    def test_the_workflow_runs_the_suite_on_both_platforms_with_the_onboarding_command(self):
        # R3 (Sprint 5).
        text, jobs = self.workflow()
        self.assertIn("\non:\n  push:\n  pull_request:\n", text)
        self.assertLessEqual({"macos", "linux"}, set(jobs), "both platforms the README names")
        for job, runner in (("macos", "macos-latest"), ("linux", "ubuntu-latest")):
            body = jobs[job]
            self.assertIn(f"runs-on: {runner}\n", body, job)
            self.assertIn("timeout-minutes: 30\n", body, job)
            self.assertIn("uses: actions/checkout@v4\n", body, job)
            self.assertIn("fetch-depth: 0\n", body, (job, "the history tests read every commit"))
            self.assertIn("uses: actions/setup-python@v5\n", body, job)
            # The floor and the latest (review, Sprint 5, G4): the matrix
            # starts at the Python the README names, and setup-python takes
            # the matrix's entry, or both runs would be on the runner's own.
            matrix = re.search(r'python-version: \["(3\.\d+)", "3\.x"\]', body)
            self.assertIsNotNone(matrix, (job, "a matrix of the floor and the latest"))
            self.assertEqual(matrix.group(1), "3.10", (job, "the floor; the README's floor sentence follows it"))
            self.assertIn("python-version: ${{ matrix.python-version }}\n", body, (job, "setup-python takes the matrix's entry"))
            self.assertEqual(self.runs(body), [self.SUITE], (job, "the onboarding command, verbatim, and no other step"))
            self.assertNotIn("claude-code", body, (job, "no claude is installed: the suite runs on the fakes"))

    def test_the_workflow_validates_the_plugin_with_todays_claude_code_and_a_refusal_fails_the_run(self):
        # R3's smoke test: Claude Code itself reads the plugin, on the version
        # npm gives that day. The first run (2026-10-21) showed `validate` runs
        # signed out on the runner, so a refusal fails the run.
        _, jobs = self.workflow()
        self.assertIn("validate", jobs)
        self.assertIn("runs-on: ubuntu-latest\n", jobs["validate"])
        # The first run showed `validate` runs signed out on the runner, so a
        # refusal must fail the run: no continue-on-error any more.
        self.assertNotIn("continue-on-error", jobs["validate"])
        self.assertEqual(self.runs(jobs["validate"]),
                         ["npm install -g @anthropic-ai/claude-code", "claude plugin validate --strict ."])

    def test_the_readme_matches_the_code(self):
        # Review, Sprint 5 (G1): the README is read against what it
        # describes, so a placeholder left for a later pass, a Python floor
        # or a Claude Code range the code has moved past, a test command
        # that drifted from CI's, or a hook described with another matcher
        # or message fails here, not in a reader's hands. One subTest per
        # claim, so one run names every gap.
        readme = (HERE / "README.md").read_text()
        changelog = (HERE / "CHANGELOG.md").read_text()

        def lines(text, needle):  # where a phrase is, so a failure names lines, not the whole file
            return [n for n, line in enumerate(text.splitlines(), 1) if needle in line]

        with self.subTest("no placeholders"):
            for placeholder in ("(verify", "(R3 decides", "3.X", "YYYY-MM-DD"):
                self.assertEqual(lines(readme, placeholder), [], f"README.md lines holding {placeholder!r}")
            for placeholder in ("(verify", "(R3 decides", "3.X"):  # the release date stays until the tag
                self.assertEqual(lines(changelog, placeholder), [], f"CHANGELOG.md lines holding {placeholder!r}")
        floor = self.floor()
        with self.subTest("the Python floor", floor=floor):
            self.assertTrue(lines(readme, f"Python {floor} or later"), "no sentence names the oldest Python CI runs as the floor")
            self.assertEqual(set(re.findall(r"Python (3\.\d+)", readme)), {floor},
                             "every Python version the README names is the floor")
        first, last = (".".join(map(str, version)) for version in longhaul.TESTED_VERSIONS)
        with self.subTest("the Claude Code range", first=first, last=last):
            self.assertEqual(set(re.findall(r"\b(\d+\.\d+\.\d+) to (\d+\.\d+\.\d+)\b", readme)), {(first, last)},
                             "the one version range the README gives is TESTED_VERSIONS")
        with self.subTest("the CodeGraph install"):  # Sprint 6, G3: the paragraph repeats the advice line's constant
            for key, text in longhaul.CODEGRAPH_INSTALL.items():
                self.assertTrue(lines(readme, text), f"no README.md line holds CODEGRAPH_INSTALL[{key!r}]: {text}")
        with self.subTest("the index commands"):  # Sprint 7, G4: the README quotes each init command as the code runs it
            # The headings the two sections are found by, said as such when
            # one is renamed, not an IndexError (review, Sprint 7, F15).
            headings = re.findall(r"^## .*$", readme, re.M)
            for heading in ("## A code index, built for you", "## Pairs well with CodeGraph and Trail-Code"):
                self.assertIn(heading, headings, "the README's heading the subtest splits on")
            section = readme.split("\n## A code index, built for you", 1)[1].split("\n## ", 1)[0]
            for program, command in longhaul.INDEX_COMMANDS.items():
                self.assertTrue(lines(section, f"`{' '.join(command)}`"), f"the section quotes {program}'s command verbatim")
                self.assertTrue(lines(section, f"`{longhaul.INDEX_MAPS[program]}/`"), f"the section names {program}'s map folder")
            self.assertTrue(lines(section, "`LONGHAUL_INDEX=on`"), "the section names the switch")
            self.assertTrue(lines(section, "24 hours"), "the section states the retry rule")
            self.assertTrue(lines(readme, "| `index/<key>.json`, `index/<key>.<program>.log` |"), "the files table has the index row")
            self.assertTrue(lines(readme, "`.index.lock`"), "the files table names the index lock")
            pairs = readme.split("\n## Pairs well with CodeGraph and Trail-Code", 1)[1].split("\n## ", 1)[0]
            for name in ("CodeGraph", "Trail-Code", "self-reported"):
                self.assertTrue(lines(pairs, name), f"the Pairs well section names {name}")
        # Sprint 7, A2: an account's repositories, the field and the flag, where a reader looks.
        with self.subTest("the files table names the repositories field"):
            rows = [line for line in readme.splitlines() if line.startswith("| `accounts.json`")]
            self.assertEqual(len(rows), 1, "one files-table row for accounts.json")
            self.assertIn("repositories", rows[0], "the files table's accounts.json row names the field")
        with self.subTest("the advanced section gives --repo"):
            advanced = readme.split("\n## Advanced, experimental", 1)[1].split("\n## ", 1)[0]
            self.assertTrue(lines(advanced, "--repo"), "the advanced section says how to restrict an account")
        _, jobs = self.workflow()
        with self.subTest("the test command"):
            fenced = re.findall(r"^```[^\n]*\n(.*?)\n```$", readme, re.M | re.S)
            commands = [block for block in fenced if "unittest test_longhaul" in block]
            self.assertEqual(len(commands), 1, "one fenced test command")
            self.assertEqual(commands[0], self.SUITE, "the README's test command is the onboarding's")
            for job in ("macos", "linux"):
                self.assertEqual(self.runs(jobs[job]), commands, (job, "CI runs the command the README gives"))
        hooks = json.loads((HERE / "hooks" / "hooks.json").read_text())
        section = readme.split("\n## What the hooks do", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("| `")]
        with self.subTest("the hooks table"):
            entries = [(event, entry) for event, entries in hooks["hooks"].items() for entry in entries]
            self.assertEqual(len(entries), 6, "the six hooks the README describes, and nothing else")
            self.assertEqual(len(rows), len(entries), "one row per hook")
            timeouts = set()
            for event, entry in entries:
                [handler] = entry["hooks"]
                timeouts.add(handler["timeout"])
                *rest, end = [f"`{matcher}`" for matcher in entry["matcher"].split("|")]
                # "on `a`, `b` and `c`", or "on `a`" for a hook with one matcher
                named = f"`{event}` on " + (f"{', '.join(rest)} and {end}" if rest else end)
                matching = [row for row in rows if named in row]
                self.assertEqual(len(matching), 1, (named, "one row names this hook by its event and matchers"))
                self.assertIn(f'"{handler["statusMessage"]}"', matching[0], (named, "its row quotes its status message"))
            # Sprint 8, F1: the consultation check has 30 s, every other hook 5.
            self.assertEqual(set(re.findall(r"(\d+)[- ]second", section)), {str(t) for t in timeouts},
                             "each hook timeout, wherever the section gives it")


class Guard(unittest.TestCase):
    """`longhaul guard`, the plugin's PreToolUse hook (Sprint 3, W1): it
    warns about, or blocks, a wasteful tool call before it runs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved = longhaul.LONGHAUL_HOME
        longhaul.LONGHAUL_HOME = self.home
        self.big = self.tmp / "big.py"
        self.big.write_text("x" * 30_000)
        self.small = self.tmp / "small.py"
        self.small.write_text("y" * 300)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LONGHAUL_")}
        # fake_stream.py as claude: the bad-size test runs status, which asks
        # it for auth status and the version (R3, Sprint 5: the real claude
        # answered here; Settings.test_the_suite_never_runs_the_real_claude).
        self.env.update(LONGHAUL_HOME=str(self.home), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "claude"),
                        LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "claude.json"), LONGHAUL_NO_NOTIFY="1",
                        LONGHAUL_CLAUDE_BIN=str(HERE / "fake_stream.py"))

    def tearDown(self):
        longhaul.LONGHAUL_HOME = self.saved

    # The hook runs the module as an import, as hooks/hooks.json does: the
    # cached bytecode starts faster than the script (87-94 ms against
    # 100-124 ms, measured); with -I, so the folder it runs in (the
    # project) is not on sys.path (review, Sprint 7, F10).
    HOOK = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import longhaul; "
                                        "sys.exit(longhaul.main(['guard']))", str(HERE)]

    def hook(self, tool, agent=None, session="s1", **tool_input):
        data = {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                "tool_use_id": "t1", "cwd": str(self.tmp), "transcript_path": "/t/s1.jsonl"}
        if agent:
            data["agent_id"], data["agent_type"] = agent, "Explore"
        return data

    def start(self, source="startup", agent=None, session="s1", cwd=None):
        data = {"session_id": session, "hook_event_name": "SessionStart", "source": source,
                "cwd": str(self.tmp) if cwd is None else cwd, "transcript_path": "/t/s1.jsonl"}
        if agent:
            data["agent_id"], data["agent_type"] = agent, "Explore"
        return data

    def check(self, data, mode="warn"):
        return longhaul.guard_check(data, mode)

    def context(self, answer):
        return (answer or {}).get("hookSpecificOutput", {}).get("additionalContext", "")

    def record(self, session="s1", agent=None):
        name = session + (f"+{agent}" if agent else "")
        return json.loads((self.home / "guard" / f"{name}.json").read_text())

    def test_a_whole_read_of_an_unchanged_file_read_before_is_a_repeat_and_the_call_still_runs(self):
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small))), "the first read says nothing")
        answer = self.check(self.hook("Read", file_path=str(self.small)))
        self.assertIn("already in the context", self.context(answer))
        self.assertIn("small.py", self.context(answer))
        self.assertNotIn("permissionDecision", answer["hookSpecificOutput"], "warn mode lets the call run")
        self.assertEqual(answer["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_a_read_after_the_file_changed_or_of_a_part_is_not_a_repeat(self):
        self.check(self.hook("Read", file_path=str(self.small)))
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small), offset=10, limit=5)))
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small), offset=10)), "from the middle")
        self.small.write_text("y" * 301)  # changed by any means, Bash included: the file says so
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small))))
        # Review, Sprint 3: a same-size rewrite shows only in the time.
        self.small.write_text("z" * 301)
        os.utime(self.small, ns=(self.small.stat().st_atime_ns, self.small.stat().st_mtime_ns + 2_000_000_000))
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small))))
        self.assertIsInstance(self.record()["reads"][str(self.small)]["mtime_ns"], int)
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.tmp / "other.py"))), "a missing file")

    def test_a_file_rewritten_with_its_old_time_put_back_is_not_a_repeat_so_block_mode_never_denies_it(self):
        # Sprint 4, W2: cp -p, rsync -a and touch -r keep a file's size and
        # mtime while changing its content. The record took that for
        # unchanged, and block mode denied the read of the new content.
        self.check(self.hook("Read", file_path=str(self.small)))
        before = self.small.stat()
        self.small.write_text("z" * 300)
        os.utime(self.small, ns=(before.st_atime_ns, before.st_mtime_ns))
        # Review, Sprint 4: what the rest stands on, said when it fails.
        self.assertNotEqual(self.small.stat().st_ctime_ns, before.st_ctime_ns,
                            "the clock did not move between the two writes, so the ctime cannot tell them apart")
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small)), mode="block"))
        self.assertEqual(self.record()["fired"]["repeat"], 0, "not counted either")
        self.assertIsInstance(self.record()["reads"][str(self.small)]["ctime_ns"], int)

    def test_a_record_from_before_the_ctime_was_kept_is_not_a_repeat_once_and_then_is(self):
        # Review, Sprint 4: a Sprint 3 record holds size, mtime_ns and at
        # for each read and no ctime_ns. Taking the missing time for a
        # match would deny, in block mode, the first read after the upgrade.
        st = self.small.stat()
        folder = self.home / "guard"
        folder.mkdir()
        (folder / "s1.json").write_text(json.dumps(
            {"reads": {str(self.small): {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "at": time.time() - 5}},
             "fired": dict.fromkeys(longhaul.GUARD_RULES, 0), "bytes": dict.fromkeys(longhaul.GUARD_RULES, 0)}))
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small)), mode="block"), "not a repeat once")
        self.assertEqual(self.record()["fired"]["repeat"], 0)
        self.assertIsInstance(self.record()["reads"][str(self.small)]["ctime_ns"], int, "brought up to date")
        self.assertIn("already", self.context(self.check(self.hook("Read", file_path=str(self.small)))), "then it is")
        self.assertEqual(self.record()["fired"]["repeat"], 1)

    def test_a_chmod_after_a_read_makes_the_next_whole_read_not_a_repeat(self):
        # The trade the ctime buys: a change of mode moves it as a rewrite
        # does, so the file is read once more. Cheaper than a denial of
        # content the context does not hold.
        self.check(self.hook("Read", file_path=str(self.small)))
        self.small.chmod(0o600)
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small)), mode="block"))
        self.assertEqual(self.record()["fired"]["repeat"], 0)

    def test_a_whole_read_of_a_big_file_warns_with_a_cheaper_call(self):
        answer = self.check(self.hook("Read", file_path=str(self.big)))
        self.assertIn("30,000 bytes", self.context(answer))
        self.assertIn("offset and limit", self.context(answer))
        self.assertTrue(self.context(answer).startswith("longhaul guard (advice; the call went ahead): "))
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.big), limit=200)), "a part is fine")
        # Review, Sprint 3: offset and limit are no advice for an image, and
        # a PDF read by pages is a part.
        png = self.tmp / "shot.png"
        png.write_bytes(b"\x89PNG" + b"\0" * 40_000)
        self.assertIsNone(self.check(self.hook("Read", file_path=str(png))))
        pdf = self.tmp / "paper.pdf"
        pdf.write_bytes(b"%PDF" + b"\0" * 40_000)
        self.assertIsNone(self.check(self.hook("Read", file_path=str(pdf), pages="1-5")))

    def test_a_cat_of_a_big_file_warns_and_the_record_keeps_only_the_program_and_the_path(self):
        secret = "sk-ant-SECRET" + "Q" * 40
        answer = self.check(self.hook("Bash", command=f"TOKEN={secret} cat {self.big}"))
        self.assertIn("cat", self.context(answer))
        self.assertIn("big.py", self.context(answer))
        self.assertNotIn("SECRET", json.dumps(answer))
        self.assertNotIn("SECRET", (self.home / "guard" / "s1.json").read_text())
        self.assertEqual(self.record()["fired"]["cat"], 1)
        self.assertIsNone(self.check(self.hook("Bash", command=f"wc -l {self.big}")), "not a dump")
        self.assertIsNone(self.check(self.hook("Bash", command=f"cat {self.small}")), "a small file is fine")

    def test_a_cat_that_is_filtered_redirected_or_chained_is_not_a_dump(self):
        # Review, Sprint 3: `cat big | head` is cheap, and `cat > big` is a
        # write; both were taken for whole-file dumps, and in block mode denied.
        for command in (f"cat {self.big} | head -50", f"cat {self.big} | grep x", f"cat > {self.big} <<'EOF'\nx\nEOF",
                        f"cat {self.big} > /dev/null", f"cat {self.big} && echo done", f"cat {self.big}; ls"):
            self.assertIsNone(self.check(self.hook("Bash", command=command)), command)
        self.assertFalse((self.home / "guard" / "s1.json").exists(), "nothing was recorded either")

    def test_block_mode_denies_with_the_reason_and_records_no_read_for_a_denied_call(self):
        self.check(self.hook("Read", file_path=str(self.small)))
        answer = self.check(self.hook("Read", file_path=str(self.small)), mode="block")
        out = answer["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertTrue(out["permissionDecisionReason"].startswith("longhaul guard blocked this call. "))
        self.assertIn("already in the context", out["permissionDecisionReason"])
        self.assertIn("Make the narrower call instead", out["permissionDecisionReason"])
        # Review, Sprint 3: the way to turn the guard off is for the user,
        # not the model; and a denied call never happened, so it is not a read.
        self.assertNotIn("LONGHAUL_GUARD", out["permissionDecisionReason"])
        self.assertIsNotNone(self.check(self.hook("Bash", command=f"cat {self.big}"), mode="block"))
        self.assertNotIn(str(self.big), self.record()["reads"])
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small)), mode="off"))

    def test_a_compaction_or_a_clear_forgets_what_was_read(self):
        # Review, Sprint 3: after /compact the file is no longer in the
        # context, and the record said it was. The plugin's SessionStart
        # hook for compact and clear runs the guard too.
        self.check(self.hook("Read", file_path=str(self.small)))
        self.check(self.hook("Read", agent="agent-1", file_path=str(self.small)))
        self.assertIsNone(self.check({"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"}))
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small))), "read afresh")
        self.assertIsNone(self.check(self.hook("Read", agent="agent-1", file_path=str(self.small))))
        self.assertEqual(self.record()["fired"]["repeat"], 0, "the counts stay")
        self.assertIsNone(self.check({"session_id": "s1", "hook_event_name": "SessionStart", "source": "resume"}))
        self.assertIn("already", self.context(self.check(self.hook("Read", file_path=str(self.small)))), "a resume keeps it")

    def test_a_compaction_keeps_the_counts_and_the_bytes_and_forgets_the_reads_and_the_search_count(self):
        # Review, Sprint 6: guard_forget() rewrites the record whole, so the
        # counts it writes back must be the ones it read, or a compaction
        # would take out of inspect's Guard section what the context cost.
        self.assertIn("30,000 bytes", self.context(self.check(self.hook("Read", file_path=str(self.big)))))
        self.check(self.hook("Bash", command="grep x a"))
        before = self.record()
        self.assertEqual((before["fired"]["big"], before["bytes"]["big"], before["searches"]), (1, 30_000, 1))
        self.assertIsNone(self.check(self.start(source="compact")))
        r = self.record()
        self.assertEqual(r["fired"], before["fired"])
        self.assertEqual(r["bytes"], before["bytes"])
        self.assertEqual((r["reads"], r["searches"], r["nudged"]), ({}, 0, False))

    def test_at_startup_the_main_conversation_is_told_of_the_projects_codegraph_index(self):
        # Sprint 6, G3 (2): the plugin's third SessionStart entry runs the
        # guard at startup. The count found the installed index's tool called
        # 0 times in five weeks. Without an index there is nothing to say
        # here: the install advice is the inspect report's, not every
        # session's; and a subagent's start is not the session's.
        self.assertIsNone(self.check(self.start()), "no .codegraph/ under cwd")
        (self.tmp / ".codegraph").mkdir()
        answer = self.check(self.start())
        self.assertEqual(answer["hookSpecificOutput"]["hookEventName"], "SessionStart")
        text = self.context(answer)
        self.assertTrue(text.startswith("This project has a CodeGraph index (.codegraph/). "), text)
        self.assertIn('`codegraph explore "<question>"` or the codegraph_explore MCP tool (`mcp__codegraph__codegraph_explore`)',
                      text, "the tool named in full: the model knows it by that name")
        self.assertTrue(text.endswith("keep grep and rg for plain text."), text)
        self.assertNotIn("permissionDecision", answer["hookSpecificOutput"])
        self.assertIsNone(self.check(self.start(agent="agent-1")), "a subagent's start")
        self.assertIsNone(self.check(self.start(source="resume")), "a resume is not a start")
        self.assertIsNone(self.check(self.start(), mode="off"))
        self.assertFalse((self.home / "guard").exists(), "nothing to record")
        # The index is looked for at the cwd and above it, where CodeGraph
        # itself looks: a session opened in a subfolder is told too. A
        # relative or missing cwd is no index.
        (self.tmp / "src").mkdir()
        self.assertIsNotNone(self.check(self.start(cwd=str(self.tmp / "src"))), "a subfolder of the indexed project")
        self.assertIsNone(self.check(self.start(cwd="src")), "a relative cwd")
        self.assertIsNone(self.check({k: v for k, v in self.start().items() if k != "cwd"}), "no cwd")
        (self.tmp / ".codegraph").rmdir()
        (self.tmp / ".codegraph").write_text("not a folder")
        self.assertIsNone(self.check(self.start()), "a file of that name is no index")

    def test_the_twentieth_search_of_a_context_in_an_indexed_project_is_nudged_once_and_never_denied(self):
        # Sprint 6, G3 (3): the count found nearly all search is Bash grep,
        # in chains, 87% of it inside subagents, and the installed index's
        # tool called 0 times. The rule is model-facing and only where the
        # index is: one note at a context's twentieth search, in any mode,
        # and never a denial.
        (self.tmp / ".codegraph").mkdir()
        secret = "sk-ant-SECRET" + "Q" * 40
        grep = self.hook("Bash", command=f"grep -rn {secret} src/")
        for n in range(1, 20):
            self.assertIsNone(self.check(grep, mode="block"), f"search {n}")
        answer = self.check(grep, mode="block")
        out = answer["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", out, "block mode never denies a search")
        self.assertTrue(out["additionalContext"].startswith("This project has a CodeGraph index (.codegraph/). "), out)
        # The count is a neutral parenthetical at the end, not a reproach
        # that could send the model off its chain (review, Sprint 6).
        self.assertTrue(out["additionalContext"].endswith("keep grep and rg for plain text. "
                                                          "(This context has made 20 searches so far.)"), out)
        self.assertIsNone(self.check(grep, mode="block"), "the 21st: once")
        self.assertIsNone(self.check(self.hook("Bash", command="rg -n x | head -5")), "the program is the search, piped or not")
        r = self.record()
        self.assertEqual((r["searches"], r["nudged"]), (22, True))
        self.assertEqual(r["fired"], {"repeat": 0, "big": 0, "cat": 0}, "a search fires no rule")
        self.assertEqual(r["reads"], {}, "and is no read")
        # The record holds the count, never the pattern; nor does the log or the answer.
        self.assertNotIn("SECRET", json.dumps(answer))
        for file in self.home.rglob("*"):
            self.assertNotIn("SECRET", file.read_text() if file.is_file() else "", file.name)

    def test_without_an_index_a_search_is_counted_and_never_nudged(self):
        grep = self.hook("Bash", command="grep -rn x src/")
        for n in range(1, 201):
            self.assertIsNone(self.check(grep), f"search {n}")
        self.assertEqual((self.record()["searches"], self.record()["nudged"]), (200, False))

    def test_a_subagents_searches_are_counted_in_its_own_record(self):
        (self.tmp / ".codegraph").mkdir()
        self.check(self.hook("Bash", command="grep x a"))
        for n in range(1, 20):
            self.assertIsNone(self.check(self.hook("Bash", agent="agent-1", command="grep x a")), f"search {n}")
        self.assertIn("(This context has made 20 searches so far.)",
                      self.context(self.check(self.hook("Bash", agent="agent-1", command="grep x a"))))
        self.assertEqual(self.record()["searches"], 1, "the main conversation's own count")
        self.assertEqual(self.record(agent="agent-1")["searches"], 20)

    def test_a_piped_cat_into_grep_is_neither_a_dump_nor_a_search(self):
        # The operator rule: a filtered cat is cheap, and its program is cat.
        (self.tmp / ".codegraph").mkdir()
        for n in range(1, 26):
            self.assertIsNone(self.check(self.hook("Bash", command=f"cat {self.big} | grep x"), mode="block"), n)
        self.assertFalse((self.home / "guard" / "s1.json").exists(), "nothing recorded")

    def test_a_compaction_resets_the_search_count_with_the_reads(self):
        (self.tmp / ".codegraph").mkdir()
        grep = self.hook("Bash", command="grep x a")
        for _ in range(19):
            self.check(grep)
        self.assertIn("20 searches", self.context(self.check(grep)))
        self.assertIsNone(self.check({"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"}))
        self.assertEqual((self.record()["searches"], self.record()["nudged"]), (0, False))
        for n in range(1, 20):
            self.assertIsNone(self.check(grep), f"search {n}")
        self.assertIn("20 searches", self.context(self.check(grep)), "once per context, and a compaction starts one")

    def test_an_index_made_mid_session_is_named_at_the_next_search(self):
        # The count is kept without an index, and the rule is "twenty or
        # more", not "the twentieth": a `codegraph init` run in the middle
        # of a session is named at the next search, not never.
        grep = self.hook("Bash", command="grep -rn x src/")
        for n in range(1, 26):
            self.assertIsNone(self.check(grep), f"search {n}")
        (self.tmp / ".codegraph").mkdir()
        self.assertIn("26 searches", self.context(self.check(grep)))
        self.assertIsNone(self.check(grep), "the 27th: once")
        self.assertEqual((self.record()["searches"], self.record()["nudged"]), (27, True))

    def test_a_subagents_record_is_reset_at_the_sessions_compaction_too(self):
        # The engine sends the SessionStart for the session alone, and
        # guard_forget() walks guard_records(sid), the session's record and
        # its subagents': a subagent's count and note go with the session's.
        (self.tmp / ".codegraph").mkdir()
        grep = self.hook("Bash", agent="agent-1", command="grep x a")
        for _ in range(19):
            self.check(grep)
        self.assertIn("20 searches", self.context(self.check(grep)))
        self.assertIsNone(self.check(self.start(source="compact")))
        r = self.record(agent="agent-1")
        self.assertEqual((r["searches"], r["nudged"]), (0, False))
        for n in range(1, 20):
            self.assertIsNone(self.check(grep), f"search {n}")
        self.assertIn("20 searches", self.context(self.check(grep)), "named again in the new context")

    def test_a_corrupt_search_count_or_note_flag_in_the_record_reads_as_none_yet(self):
        # A record another version wrote, or a hand edit: a count that is
        # no number starts again at the next search, and a flag that is
        # not True has the note still to come. Neither stops the hook.
        (self.tmp / ".codegraph").mkdir()
        folder = self.home / "guard"
        folder.mkdir()
        (folder / "s1.json").write_text(json.dumps(
            {"reads": {}, "fired": dict.fromkeys(longhaul.GUARD_RULES, 0), "bytes": dict.fromkeys(longhaul.GUARD_RULES, 0),
             "searches": "many", "nudged": "yes"}))
        grep = self.hook("Bash", command="grep x a")
        self.assertIsNone(self.check(grep))
        self.assertEqual((self.record()["searches"], self.record()["nudged"]), (1, False))
        for n in range(2, 20):
            self.assertIsNone(self.check(grep), f"search {n}")
        self.assertIn("20 searches", self.context(self.check(grep)))

    def test_a_record_that_cannot_be_rewritten_at_a_compaction_is_removed_so_block_mode_does_not_deny_wrongly(self):
        # Review, Sprint 4 (SF 6): a rewrite that failed left the reads in
        # place, and block mode then denied a read of a file the compaction
        # had taken out of the context. A disk with no room for the new
        # file can still lose the old one; the counts go with it.
        self.check(self.hook("Read", file_path=str(self.small)))
        compact = {"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"}
        with mock.patch.object(longhaul, "write_json", side_effect=OSError(28, "No space left on device")):
            self.assertIsNone(self.check(compact))
        self.assertFalse((self.home / "guard" / "s1.json").exists(), "removed")
        self.assertIn("could not be rewritten", (self.home / "longhaul.log").read_text())
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small)), mode="block"), "read afresh")

    def test_a_record_that_cannot_be_cleared_at_all_is_said_in_the_log_and_the_session_still_starts(self):
        self.check(self.hook("Read", file_path=str(self.small)))
        folder = self.home / "guard"
        folder.chmod(0o500)  # neither a new file nor an unlink
        self.addCleanup(folder.chmod, 0o700)
        self.assertIsNone(self.check({"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"}))
        log = (self.home / "longhaul.log").read_text()
        self.assertTrue(not (folder / "s1.json").exists()
                        or "the guard's record could not be cleared; block mode may deny a read wrongly until it is" in log,
                        log)

    def test_a_corrupt_time_in_the_record_does_not_switch_the_guard_off(self):
        self.check(self.hook("Read", file_path=str(self.small)))
        r = self.record()
        r["reads"][str(self.small)]["at"] = None
        (self.home / "guard" / "s1.json").write_text(json.dumps(r))
        self.assertIn("already", self.context(self.check(self.hook("Read", file_path=str(self.small)))))

    def test_two_sessions_keep_separate_records(self):
        self.check(self.hook("Read", file_path=str(self.small)))
        self.assertIsNone(self.check(self.hook("Read", session="s2", file_path=str(self.small))))
        self.assertTrue((self.home / "guard" / "s2.json").exists())

    def test_parallel_hooks_never_lose_a_count(self):
        # Review, Sprint 3: Claude sends tool calls in parallel, so hooks run
        # at once; read-modify-write without a lock dropped counts.
        self.check(self.hook("Read", file_path=str(self.small)))
        data = json.dumps(self.hook("Read", file_path=str(self.small)))
        procs = [subprocess.Popen(self.HOOK, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=self.env, text=True) for _ in range(6)]
        for p in procs:
            p.communicate(data, timeout=30)
        self.assertEqual([p.returncode for p in procs], [0] * 6)
        self.assertEqual(self.record()["fired"]["repeat"], 6)

    def test_a_bad_size_setting_never_breaks_the_hook_or_anything_else(self):
        # Review, Sprint 3: int() of the setting ran at import, so a typo
        # crashed every command, the hook included.
        for value in ("abc", "", "0", "-5"):
            out = subprocess.run(self.HOOK, input=json.dumps(self.hook("Read", file_path=str(self.big))),
                                 env={**self.env, "LONGHAUL_GUARD_BIG": value}, capture_output=True, text=True, timeout=10)
            self.assertEqual(out.returncode, 0, (value, out.stderr))
            self.assertIn("30,000 bytes", out.stdout, value)
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "status"], env={**self.env, "LONGHAUL_GUARD_BIG": "x"},
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_an_unknown_guard_mode_warns_rather_than_blocks(self):
        with mock.patch.dict(os.environ, {"LONGHAUL_GUARD": "Block"}):
            self.assertEqual(longhaul.guard_mode(), "warn")

    def test_the_record_does_not_grow_without_bound(self):
        many = self.tmp / "many"
        many.mkdir()
        for i in range(620):
            (many / f"f{i}.txt").write_text("x")
            self.check(self.hook("Read", file_path=str(many / f"f{i}.txt")))
        self.assertLessEqual(len(self.record()["reads"]), 500)
        self.assertIn(str(many / "f619.txt"), self.record()["reads"], "the newest stay")

    def test_odd_input_an_unknown_tool_or_a_corrupt_record_never_stops_a_call(self):
        self.assertIsNone(self.check({"tool_name": "Edit", "tool_input": {"file_path": "/x"}, "session_id": "s1"}))
        self.assertIsNone(self.check({"tool_name": "Read", "tool_input": {"file_path": str(self.small)}}), "no session")
        self.assertIsNone(self.check(self.hook("Read", session="../../etc", file_path=str(self.small))))
        self.assertIsNone(self.check(self.hook("Read", file_path=["not", "a", "path"])))
        self.check(self.hook("Read", file_path=str(self.small)))
        (self.home / "guard" / "s1.json").write_text("{not json")
        self.assertIsNone(self.check(self.hook("Read", file_path=str(self.small))), "a corrupt record is a fresh one")
        self.assertIn("already in the context", self.context(self.check(self.hook("Read", file_path=str(self.small)))))

    def test_a_subagents_reads_are_its_own_context(self):
        self.check(self.hook("Read", file_path=str(self.small)))
        self.assertIsNone(self.check(self.hook("Read", agent="agent-1", file_path=str(self.small))))
        self.assertIn("already", self.context(self.check(self.hook("Read", agent="agent-1", file_path=str(self.small)))))
        self.assertTrue((self.home / "guard" / "s1+agent-1.json").exists(), "a separator no id can hold")

    def test_the_record_is_private_and_counts_what_the_call_would_have_cost(self):
        for _ in range(3):
            self.check(self.hook("Read", file_path=str(self.small)))
        self.check(self.hook("Read", file_path=str(self.big)))
        self.check(self.hook("Bash", command=f"cat {self.big}"))
        # Two repeats of the small file, one whole read of the big one, and a
        # cat of the big one, which was read in full and unchanged: a repeat,
        # the surer of the two wastes, not a cat.
        r = self.record()
        self.assertEqual(r["fired"], {"repeat": 3, "big": 1, "cat": 0}, r)
        self.assertEqual(r["bytes"], {"repeat": 30_600, "big": 30_000, "cat": 0}, r)
        self.assertEqual(oct((self.home / "guard" / "s1.json").stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(set(r["reads"]), {str(self.small), str(self.big)})

    def test_the_hook_exits_0_whatever_happens_and_is_not_slow(self):
        # A hook that fails must let the call run (the engine does, on a
        # non-2 exit). Its speed is measured and recorded in the work log;
        # this only catches a hook that hangs or crawls (review, Sprint 3:
        # a tight wall-clock bound is flaky under load).
        data = json.dumps(self.hook("Read", file_path=str(self.small)))
        (self.tmp / ".codegraph").mkdir()
        start = json.dumps(self.start())  # Sprint 6, G3: the startup entry runs the same command
        took = []
        for raw in (data, data, data, start, "not json", "", json.dumps({"tool_name": "Read"})):
            started = time.time()
            out = subprocess.run(self.HOOK, input=raw, env=self.env, capture_output=True, text=True, timeout=10)
            took.append(time.time() - started)
            self.assertEqual(out.returncode, 0, out.stderr)
            if out.stdout.strip():
                json.loads(out.stdout)  # anything it prints is JSON the engine can read
        self.assertIn("already in the context", subprocess.run(
            self.HOOK, input=data, env=self.env, capture_output=True, text=True, timeout=10).stdout)
        self.assertIn("CodeGraph index", subprocess.run(
            self.HOOK, input=start, env=self.env, capture_output=True, text=True, timeout=10).stdout)
        self.assertLess(sorted(took)[len(took) // 2], 0.5, f"median of {[round(t, 3) for t in took]}")

    def test_inspect_reports_what_the_guard_counted(self):
        for _ in range(2):
            self.check(self.hook("Read", file_path=str(self.small)))
        self.check(self.hook("Read", agent="agent-1", file_path=str(self.big)))
        transcript = self.tmp / "projects" / "p" / "s1.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5",
                                          "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n")
        r = longhaul.inspect_transcript(transcript)
        self.assertEqual(r["guard"], {"repeat": 1, "big": 1, "cat": 0, "bytes": {"repeat": 300, "big": 30_000, "cat": 0}})
        lines = "\n".join(longhaul.inspect_lines(r))
        self.assertIn("Guard [longhaul guard, counted", lines)
        self.assertIn("read again unchanged: 1 (300 bytes)", lines)
        records = {(x["metric"], x["item"]): x["value"] for x in longhaul.inspect_records(r)}
        self.assertEqual(records[("guard_fired", "repeat")], 1)
        self.assertEqual(records[("guard_bytes", "big")], 30_000)
        # Review, Sprint 3: no record does not mean the hook was off.
        other = transcript.with_name("s7.jsonl")
        other.write_text(transcript.read_text())
        self.assertIn("no record: the hook was off, or saw no whole Read, cat or search",
                      "\n".join(longhaul.inspect_lines(longhaul.inspect_transcript(other))))


class Index(unittest.TestCase):
    """The auto-init (Sprint 7, G4): at a new session's start in a git work
    tree with no code map, the guard's startup hook starts `codegraph init`
    or `trail init`, or both, in the top level, detached, and returns at
    once. Two fakes stand for the programs; the real ones never run
    (Settings.test_the_suite_never_runs_the_real_claude marks them)."""

    MAPS = {"codegraph": ".codegraph", "trail": ".trail"}  # what each real program writes in the top level

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved = longhaul.LONGHAUL_HOME
        longhaul.LONGHAUL_HOME = self.home
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in self.MAPS:
            self.fake(self.bin / name, name)
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)  # a work tree, and no git needed
        (self.repo / "src").mkdir()
        # The fakes alone are on PATH, in this process and in a hook's; the
        # auto-init is opt-in, so the switch is on here and off where a
        # test says so; the runner is its own process and reads
        # LONGHAUL_HOME at its import, so the environment names this home
        # too; the variable a `-p` run sets is each test's.
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.bin), "LONGHAUL_INDEX": "on",
                                               "LONGHAUL_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LONGHAUL_")}
        self.env.update(LONGHAUL_HOME=str(self.home), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "claude"),
                        LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "claude.json"), LONGHAUL_NO_NOTIFY="1",
                        LONGHAUL_CLAUDE_BIN=str(HERE / "fake_stream.py"), LONGHAUL_INDEX="on")
        for name in self.TELEMETRY:  # what the programs read of their own telemetry is each test's
            self.env.pop(name, None)

    def tearDown(self):
        longhaul.LONGHAUL_HOME = self.saved

    # What the fakes record of their environment, one line each: the two
    # telemetry switches, passed as they are, and two of the session's own
    # variables (SESSION_ENV), which the runner strips.
    TELEMETRY = ("DO_NOT_TRACK", "CODEGRAPH_TELEMETRY")
    SEEN = TELEMETRY + ("CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ID")

    def fake(self, path: Path, name: str):
        """A program that records where and how it was called and what it
        saw of the environment, talks on both streams, makes its map, and
        is slow or fails when a marker file says so: slow before the map
        is made (a run in progress has none yet), failing after it (a
        failed init leaves a half-built map); with `.child`, it leaves a
        child of its own running first and records its pid. Its own PATH:
        sh has no mkdir or sleep of its own, and the test's PATH holds the
        fakes alone."""
        seen = " ".join(f'"${{{name}-unset}}"' for name in self.SEEN)
        path.write_text("#!/bin/sh\n"
                        "PATH=/bin:/usr/bin\n"
                        f"printf '%s %s\\n' \"$(pwd -P)\" \"$*\" >> '{self.tmp}/{name}.calls'\n"
                        f"printf '%s\\n' {seen} > '{self.tmp}/{name}.env'\n"
                        f"echo SECRET-OUTPUT-{name}\n"
                        f"echo SECRET-OUTPUT-{name} >&2\n"
                        f"[ -e '{self.tmp}/{name}.child' ] && {{ sleep 30 & echo $! > '{self.tmp}/{name}.child.pid'; }}\n"
                        f"[ -e '{self.tmp}/{name}.slow' ] && sleep 3\n"
                        f"mkdir -p {self.MAPS[name]}\n"
                        f"[ -e '{self.tmp}/{name}.fail' ] && exit 1\n"
                        "exit 0\n")
        path.chmod(0o755)

    def start(self, source="startup", agent=None, session="s1", cwd=None):
        data = {"session_id": session, "hook_event_name": "SessionStart", "source": source,
                "cwd": str(self.repo / "src") if cwd is None else cwd, "transcript_path": "/t/s1.jsonl"}
        if agent:
            data["agent_id"], data["agent_type"] = agent, "Explore"
        return data

    def plan(self, data, index="on"):
        return longhaul.index_plan(data, index)

    def planned(self, *programs, top=None):
        """The plan for these programs in the top (repo/ by default), each
        with the fake index_program() found for it in bin/."""
        return [(program, str(self.repo) if top is None else top, str(self.bin / program)) for program in programs]

    def check(self, data, mode="warn", index="on"):
        return longhaul.guard_check(data, mode, index=index)

    def context(self, answer):
        return (answer or {}).get("hookSpecificOutput", {}).get("additionalContext", "")

    def note(self, program, top=None):
        return longhaul.INDEX_NOTE.format(command=" ".join(longhaul.INDEX_COMMANDS[program]),
                                          where=longhaul.home_path(str(self.repo) if top is None else top))

    def calls(self, program):
        path = self.tmp / f"{program}.calls"
        return path.read_text().splitlines() if path.exists() else []

    def begin(self, data):
        """What a session's start does of the index: the pairs started."""
        return longhaul.index_start(data, "on")[0]

    def said(self, answer):
        """What the user is told: the answer's systemMessage, or None."""
        return (answer or {}).get("systemMessage")

    def told_started(self, program, top=None):
        top = str(self.repo) if top is None else top
        log = longhaul.home_path(str(self.home / "index" / f"{longhaul.index_key(top)}.{program}.log"))
        return (f"longhaul: started `{' '.join(longhaul.INDEX_COMMANDS[program])}` in {longhaul.home_path(top)} "
                f"in the background; the index is used from the next session (log: {log})")

    def told_failed(self, program, reason, top=None, see=None):
        top = str(self.repo) if top is None else top
        entry = self.state(top)["runs"][program]
        day = dt.datetime.fromtimestamp(entry["ended"]).strftime("%Y-%m-%d")
        see = see or self.home / "index" / f"{longhaul.index_key(top)}.{program}.log"
        return (f"longhaul: `{' '.join(longhaul.INDEX_COMMANDS[program])}` in {longhaul.home_path(top)} failed on "
                f"{day} ({reason}); see {longhaul.home_path(str(see))}; delete "
                f"{longhaul.home_path(str(self.state_path(top)))} and {self.MAPS[program]}/ together to retry")

    def state_path(self, top=None):
        return self.home / "index" / f"{longhaul.index_key(top or str(self.repo))}.json"

    def state(self, top=None):
        return json.loads(self.state_path(top).read_text())

    def wait(self, program, top=None):
        """The program's entry once its runner has written `ended`, within 10 s."""
        for _ in range(200):
            entry = dict((longhaul.read_json(self.state_path(top)) or {}).get("runs", {}).get(program) or {})
            if entry.get("ended") is not None:
                return entry
            time.sleep(0.05)
        self.fail(f"{program}'s run did not end within 10 s")

    def test_nothing_starts_without_the_switch_on_in_the_home_folder_outside_a_work_tree_at_the_root_or_for_a_subagent(self):
        both = self.planned("codegraph", "trail")
        self.assertEqual(self.plan(self.start()), both, "the control: a work tree with no map, both programs found")
        # Opt-in (the owner, 2026-10-21, after the outside review): "on" is
        # on; unset, "off" or anything else is off.
        self.assertEqual(longhaul.index_mode(), "on", "the fixture's environment")
        for value in (None, "off", "", "1", "ON", "yes"):
            with mock.patch.dict(os.environ):
                if value is None:
                    os.environ.pop("LONGHAUL_INDEX")
                else:
                    os.environ["LONGHAUL_INDEX"] = value
                self.assertEqual(longhaul.index_mode(), "off", f"LONGHAUL_INDEX={value}")
                self.assertEqual(self.plan(self.start(), index=longhaul.index_mode()), [], f"LONGHAUL_INDEX={value}")
        self.assertEqual(self.plan(self.start(), index="off"), [], "the mode as passed down")
        # Never in the home folder, in a subfolder of it, or in a work tree
        # the home folder is inside (a dotfiles repository at ~ makes every
        # folder one, and there Trail's "repo" settings file is the user's own).
        home = self.tmp / "h"
        (home / ".git").mkdir(parents=True)
        (home / "sub").mkdir()
        with mock.patch.object(longhaul, "HOME", home):
            self.assertEqual(self.plan(self.start(cwd=str(home))), [], "the home folder")
            self.assertEqual(self.plan(self.start(cwd=str(home / "sub"))), [], "a subfolder of the home folder")
            self.assertEqual(self.plan(self.start()), both, "another work tree is fine")
            (home / ".git").rmdir()
            (self.tmp / ".git").mkdir()  # the home folder is now inside this work tree
            self.assertEqual(self.plan(self.start(cwd=str(home / "sub"))), [], "a parent of the home folder")
            (self.tmp / ".git").rmdir()
            real_exists = os.path.exists
            with mock.patch.object(os.path, "exists", lambda p: p == os.path.join(os.sep, ".git") or real_exists(p)):
                self.assertIsNone(longhaul.index_top(os.sep), "the root, were it a work tree")
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(self.plan(self.start(cwd=str(plain))), [], "no .git at or above the cwd")
        self.assertEqual(self.plan(self.start(cwd=os.sep)), [], "the root")
        self.assertEqual(self.plan(self.start(cwd="repo/src")), [], "a relative cwd")
        self.assertEqual(self.plan({k: v for k, v in self.start().items() if k != "cwd"}), [], "no cwd")
        self.assertEqual(self.plan(self.start(agent="agent-1")), [], "a subagent")
        self.assertEqual(self.calls("codegraph") + self.calls("trail"), [], "nothing ran")
        self.assertFalse((self.home / "index").exists(), "nothing written")
        # A worktree's `.git` is a file; the folder holding it is a top level of its own.
        (self.tmp / "wt" / "src").mkdir(parents=True)
        (self.tmp / "wt" / ".git").write_text("gitdir: elsewhere\n")
        self.assertEqual(longhaul.index_top(str(self.tmp / "wt" / "src")), str(self.tmp / "wt"))

    def test_the_switch_unset_or_off_starts_nothing_through_the_hook_or_guard_check(self):
        # Review, Sprint 7 (F11): the switch as the hook reads it from its
        # own environment, not only as index_plan() is handed it; both
        # programs on PATH and a work tree with no map, so with the switch
        # on both would start (the tests above).
        self.assertIsNone(self.check(self.start(), index="off"))
        for value in (None, "off"):
            env = {k: v for k, v in self.env.items() if k != "LONGHAUL_INDEX"}
            if value is not None:
                env["LONGHAUL_INDEX"] = value
            out = subprocess.run(Guard.HOOK, input=json.dumps(self.start()), env=env, cwd=str(self.repo / "src"),
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout, "", f"LONGHAUL_INDEX={value}")
        self.assertEqual(self.calls("codegraph") + self.calls("trail"), [], "nothing ran")
        self.assertFalse((self.home / "index").exists(), "nothing written")

    def test_a_resume_compact_or_clear_never_starts_one(self):
        for source in ("resume", "compact", "clear"):
            self.assertEqual(self.plan(self.start(source=source)), [], source)
        self.assertEqual(self.plan({**self.start(), "hook_event_name": "PreToolUse"}), [], "not a SessionStart")
        self.assertEqual(self.plan({**self.start(), "source": None}), [], "no source")

    def test_an_unattended_run_never_starts_one(self):
        # A `claude -p` run's hook sees CLAUDE_CODE_SESSION_ATTENDED=0 (the
        # lead's probe, 2026-10-21): a script's or a loop's run should not
        # index a repository on its own.
        both = self.planned("codegraph", "trail")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ATTENDED": "0"}):
            self.assertEqual(self.plan(self.start()), [], "a -p run")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ATTENDED": "1"}):
            self.assertEqual(self.plan(self.start()), both, "an interactive session")
        self.assertEqual(self.plan(self.start()), both, "unset, as an older Claude Code leaves it")
        # The same through the hook, which reads its own environment.
        data = json.dumps(self.start())
        out = subprocess.run(Guard.HOOK, input=data, env={**self.env, "CLAUDE_CODE_SESSION_ATTENDED": "0"},
                             capture_output=True, text=True, timeout=10)
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        self.assertEqual(self.calls("codegraph") + self.calls("trail"), [], "nothing ran")
        self.assertFalse((self.home / "index").exists(), "nothing written")
        # The one refusal said in the log (review, Sprint 7, F9): the live
        # check and a user's "why not" read it there; once for the plan
        # above, once for the hook.
        log = self.home / "longhaul.log"
        self.assertEqual(log.read_text().count("index: skipped, unattended session\n"), 2)
        out = subprocess.run(Guard.HOOK, input=data, env={**self.env, "CLAUDE_CODE_SESSION_ATTENDED": "1"},
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(log.read_text().count("index: skipped, unattended session\n"), 2, "an attended one: not again")
        self.assertEqual(self.context(json.loads(out.stdout)), f"{self.note('codegraph')} {self.note('trail')}")
        for program in self.MAPS:
            self.assertEqual(self.wait(program)["exit"], 0, program)

    def test_a_mapped_project_is_left_alone_per_program(self):
        top, real = str(self.repo), os.path.realpath(self.repo)
        self.assertEqual(self.plan(self.start()), self.planned("codegraph", "trail"),
                         "the plan names every program found; a map present is the state's question")
        (self.repo / ".codegraph").mkdir()  # a map Longhaul did not make
        self.assertEqual(self.begin(self.start()), [("trail", top)], ".codegraph/ present: trail alone")
        self.assertEqual(self.wait("trail")["exit"], 0)
        self.assertNotIn("codegraph", self.state()["runs"], "nothing written for a map that is not Longhaul's")
        (self.repo / ".codegraph").rmdir()
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], ".trail/ present: codegraph alone")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(self.begin(self.start()), [], "both present")
        self.assertEqual(self.calls("codegraph"), [f"{real} init -y"])
        self.assertEqual(self.calls("trail"), [f"{real} init --yes --no-global --no-statusline --no-agents"])
        # The map is looked for at the cwd and above it, where the programs
        # themselves look: one in the cwd below the top level counts.
        other = self.tmp / "other"
        (other / ".git").mkdir(parents=True)
        (other / "src" / ".codegraph").mkdir(parents=True)
        self.assertEqual(self.begin(self.start(cwd=str(other / "src"))), [("trail", str(other))], "a map in the cwd")
        self.assertEqual(self.wait("trail", str(other))["exit"], 0)
        self.assertFalse((other / ".codegraph").exists())
        # Both present and no state: nothing, and no state file.
        third = self.tmp / "third"
        for folder in (".git", ".codegraph", ".trail"):
            (third / folder).mkdir(parents=True)
        self.assertEqual(self.begin(self.start(cwd=str(third))), [], "a repository that ships both maps")
        self.assertFalse(self.state_path(str(third)).exists(), "no state file for a project with nothing to do")

    def test_a_missing_program_is_skipped_and_the_other_still_runs(self):
        (self.bin / "trail").unlink()
        self.assertEqual(self.plan(self.start()), self.planned("codegraph"))
        # On PATH but not executable: npm's trail link on the owner's Mac
        # (2026-10-21), which which() passes over as the shell would.
        (self.bin / "codegraph").chmod(0o644)
        self.assertEqual(self.plan(self.start()), [], "a program that cannot be run is not found")
        (self.bin / "codegraph").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": ""}):
            self.assertEqual(self.plan(self.start()), [], "no PATH at all")

    def test_a_new_session_in_an_unmapped_work_tree_starts_both_inits_in_the_top_level_and_answers_at_once(self):
        top, real = str(self.repo), os.path.realpath(self.repo)
        # The guard off does not stop the auto-init (the owner's question
        # 1): LONGHAUL_INDEX alone governs it. The answer is one sentence
        # per program started, and the hook has returned before either
        # finished (the timing test measures that).
        answer = self.check(self.start(), mode="off")
        self.assertEqual(answer["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(self.context(answer), f"{self.note('codegraph')} {self.note('trail')}")
        self.assertNotIn("permissionDecision", answer["hookSpecificOutput"])
        for program in self.MAPS:
            entry = self.wait(program)
            self.assertEqual(entry["exit"], 0, program)
            self.assertIsInstance(entry["seconds"], float, program)
            self.assertTrue((self.repo / self.MAPS[program]).is_dir(), (program, "its map, in the top level"))
        # Each ran once, in the top level (pwd -P: the real path), with the
        # one command line the README gives for it.
        self.assertEqual(self.calls("codegraph"), [f"{real} init -y"])
        self.assertEqual(self.calls("trail"), [f"{real} init --yes --no-global --no-statusline --no-agents"])
        log = (self.home / "longhaul.log").read_text()
        where = longhaul.home_path(top)  # as the log names it: ~/... were the scratch tree under the home folder
        self.assertIn(f"index: started codegraph init in {where} (pid ", log)
        self.assertIn(f"index: started trail init in {where} (pid ", log)
        self.assertRegex(log, rf"index: codegraph init in {re.escape(where)} exit 0 after \d+\.\d s\n")
        self.assertRegex(log, rf"index: trail init in {re.escape(where)} exit 0 after \d+\.\d s\n")
        self.assertEqual(self.begin(self.start()), [], "the next session finds both maps")
        self.assertEqual(self.context(self.check(self.start())), longhaul.GUARD_CODEGRAPH,
                         "and the guard's own note of the CodeGraph index takes over")
        self.assertIsNone(self.check(self.start(), mode="off"), "the guard off stays silent, with nothing to start")

    def test_a_second_start_while_the_first_runs_is_skipped(self):
        (self.tmp / "codegraph.slow").touch()
        top = str(self.repo)
        self.assertEqual(self.begin(self.start()), [("codegraph", top), ("trail", top)])
        self.wait("trail")
        entry = self.state()["runs"]["codegraph"]
        self.assertIsNone(entry["exit"], "still running")
        self.assertTrue(longhaul.pid_alive(entry["pid"]), "the runner's pid")
        self.assertEqual(self.begin(self.start()), [], "a second session while it runs")
        self.assertEqual(self.begin(self.start(session="s2")), [], "any session: the state is the project's")
        # Started up to its timeout ago, with its pid alive: a run in
        # progress, never doubled (past the timeout it is a dead runner
        # whose pid was reused: the next test).
        with longhaul.locked("index"):
            state = self.state()
            state["runs"]["codegraph"]["started"] -= longhaul.INDEX_TIMEOUT - 10
            longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [], "a run in progress, started within its timeout")
        # The runs are counted once the first has ended: before it, its
        # fake may not have written its line yet (seen in the child run of
        # Settings.test_the_suite_never_runs_the_real_claude, under load).
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(self.calls("codegraph"), [f"{os.path.realpath(self.repo)} init -y"], "one run")

    def test_six_starts_at_once_start_one_run(self):
        # Review, Sprint 7 (F13): sessions opened together (a restored
        # window of tabs) each run the hook at the same moment; the index
        # lock makes them decide one at a time, so one starts the run and
        # the rest find it running. Threads, each with a lock file of its
        # own open, as six hooks would have.
        (self.tmp / "codegraph.slow").touch()
        (self.bin / "trail").unlink()
        gate, started, errors = threading.Barrier(6), [], []

        def session(n):
            try:
                gate.wait(timeout=10)
                started.extend(self.begin(self.start(session=f"s{n}")))
            except Exception as e:  # said below, not lost in the thread
                errors.append(e)

        threads = [threading.Thread(target=session, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(errors, [])
        self.assertEqual(started, [("codegraph", str(self.repo))], "one session started it")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(self.calls("codegraph"), [f"{os.path.realpath(self.repo)} init -y"], "one run")
        self.assertEqual(list(self.state()["runs"]), ["codegraph"], "one entry")
        self.assertEqual((self.home / "longhaul.log").read_text().count("index: started codegraph init"), 1)

    def plant(self, program="codegraph", **entry):
        """A state entry for the program in repo/, as a runner would have left it."""
        path = self.state_path()
        state = longhaul.read_json(path) or {"top": os.path.realpath(self.repo), "runs": {}}
        state["runs"][program] = entry
        longhaul.write_json(path, state)

    def test_a_runner_past_its_timeout_or_started_in_the_future_is_not_taken_for_running(self):
        # Review, Sprint 7 (F3): "exit null and the pid alive" was running
        # for ever, and a pid is reused; past INDEX_TIMEOUT (and a minute)
        # the runner is dead, and the failure's day counts from its start.
        (self.bin / "trail").unlink()
        top, now, alive = str(self.repo), time.time(), os.getpid()  # this process stands for the pid's new owner
        self.plant(started=now - longhaul.INDEX_TIMEOUT - 120, pid=alive, exit=None, ended=None, seconds=None)
        # Told apart from a run in progress, which starts nothing either
        # (final review, Sprint 7, G8): a failure is in index_start()'s
        # failures for the user, a run in progress is not.
        started, failed = longhaul.index_start(self.start(), "on")
        self.assertEqual((started, [(program, where) for program, where, _ in failed]), ([], [("codegraph", top)]),
                         "past its timeout: a failure that stands, under a day old")
        self.plant(started=now - 86_401, pid=alive, exit=None, ended=None, seconds=None)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "a day on: once more, the pid alive or not")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        shutil.rmtree(self.repo / ".codegraph")
        # A start more than a minute ahead of the clock is a clock that
        # moved: a failure, due now, running or not.
        for entry in ({"exit": 1, "pid": None}, {"exit": None, "pid": alive}):
            self.plant(started=time.time() + 3600, ended=None, seconds=None, **entry)
            self.assertEqual(self.begin(self.start()), [("codegraph", top)], entry)
            self.assertEqual(self.wait("codegraph")["exit"], 0, entry)
            shutil.rmtree(self.repo / ".codegraph")
        self.assertEqual(len(self.calls("codegraph")), 3)

    def test_a_map_gone_after_a_good_build_is_rebuilt_at_the_next_start(self):
        # Review, Sprint 7 (F2): `git clean -fdx`, a clone again at the
        # same path or a user's rm left an exit 0 and no map, for ever.
        (self.bin / "trail").unlink()
        top = str(self.repo)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)])
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(self.begin(self.start()), [], "the map there: nothing")
        shutil.rmtree(self.repo / ".codegraph")
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "the map gone: at once")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(len(self.calls("codegraph")), 2)
        self.assertIn(f"index: codegraph's map in {longhaul.home_path(top)} is gone; rebuilding\n",
                      (self.home / "longhaul.log").read_text())

    def test_a_failed_init_is_not_retried_for_a_day_and_is_after(self):
        (self.tmp / "codegraph.fail").touch()
        top = str(self.repo)
        self.assertEqual(self.begin(self.start()), [("codegraph", top), ("trail", top)])
        self.wait("trail")
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        self.assertNotIn("why", self.state()["runs"]["codegraph"], "the program's own exit: no why of Longhaul's")
        self.assertEqual(self.begin(self.start()), [], "a failure under a day old is left alone")
        self.assertEqual(len(self.calls("codegraph")), 1)
        state = self.state()
        state["runs"]["codegraph"]["started"] -= 86_401
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "a day on: once more")
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        self.assertEqual(len(self.calls("codegraph")), 2)
        # A runner that never wrote its exit (the Mac slept, the runner was
        # killed) is a failure once its pid is dead, on the same clock.
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        state = self.state()
        state["runs"]["codegraph"].update(exit=None, ended=None, seconds=None, pid=gone.pid)
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [], "a dead runner under a day old")
        state["runs"]["codegraph"]["started"] -= 86_401
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "and a day on")
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        self.assertEqual(len(self.calls("codegraph")), 3)
        # The file is deletable: a user who has fixed the cause removes it,
        # and the half-built map with it (a map with no entry is someone
        # else's: the half-built test), to try now.
        self.state_path().unlink()
        shutil.rmtree(self.repo / ".codegraph")
        (self.tmp / "codegraph.fail").unlink()
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "the state removed: now")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertTrue((self.repo / ".codegraph").is_dir())

    def test_a_run_over_the_timeout_is_killed_and_counts_as_a_failure(self):
        (self.tmp / "codegraph.slow").touch()
        (self.bin / "trail").unlink()
        with mock.patch.dict(os.environ, {"LONGHAUL_INDEX_TIMEOUT": "1"}):  # the runner reads it at its import
            self.assertEqual(self.begin(self.start()), [("codegraph", str(self.repo))])
        entry = self.wait("codegraph")
        self.assertEqual(entry["exit"], -1)
        self.assertLess(entry["seconds"], 3, "killed at the timeout, not waited for")
        self.assertFalse((self.repo / ".codegraph").exists())
        self.assertRegex((self.home / "longhaul.log").read_text(),
                         r"index: codegraph init in \S+ exit -1 after \d\.\d s \(killed at the 1 s timeout\)\n")
        self.assertEqual(entry["why"], "timeout")
        self.assertEqual(entry["timeout"], 1, "the timeout it was killed at, recorded at the kill")
        self.assertEqual(self.begin(self.start()), [], "a failure: left alone")
        # The user is told the timeout, not the time the run took (final
        # review, Sprint 7, G3): a loaded machine's 2.7 s says 1 s still.
        state = self.state()
        state["runs"]["codegraph"]["seconds"] = 2.7
        longhaul.write_json(self.state_path(), state)
        answer = self.check(self.start())
        self.assertEqual(self.said(answer), self.told_failed("codegraph", "exit -1, killed at the 1 s timeout"))
        self.assertNotIn("hookSpecificOutput", answer, "nothing started, nothing for the model")
        del state["runs"]["codegraph"]["timeout"]  # an entry written before the timeout was recorded
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.said(self.check(self.start())), self.told_failed("codegraph", "exit -1, killed at its timeout"))
        # A timeout is no transient failure (review, Sprint 7, F17): a day
        # on it is not tried again; the user's deletion of the state (and
        # of a half-built map, were there one) is what retries it.
        state = self.state()
        state["runs"]["codegraph"]["started"] -= 86_401
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [], "a day on, a timeout still")
        self.assertEqual(len(self.calls("codegraph")), 1)
        self.state_path().unlink()
        (self.tmp / "codegraph.slow").unlink()
        self.assertEqual(self.begin(self.start()), [("codegraph", str(self.repo))], "the state deleted: now")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertNotIn("why", self.state()["runs"]["codegraph"], "a clean exit has no why")

    def test_the_timeout_kills_the_programs_children_too(self):
        # Review, Sprint 7 (F6): the kill reached the program alone, and
        # what it had started ran on past the timeout, holding its log.
        for marker in ("slow", "child"):
            (self.tmp / f"codegraph.{marker}").touch()
        (self.bin / "trail").unlink()
        with mock.patch.dict(os.environ, {"LONGHAUL_INDEX_TIMEOUT": "1"}):
            self.assertEqual(self.begin(self.start()), [("codegraph", str(self.repo))])
        entry = self.wait("codegraph")
        self.assertEqual((entry["exit"], entry["why"]), (-1, "timeout"))
        child = int((self.tmp / "codegraph.child.pid").read_text())

        def kill():  # were it left running (the old kill), not for 30 s
            with contextlib.suppress(OSError):
                os.kill(child, signal.SIGKILL)

        self.addCleanup(kill)

        def gone():
            for _ in range(100):  # a killed child is a zombie until its new parent reaps it
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    return True
                time.sleep(0.05)
            return False

        self.assertTrue(gone(), "the program's child was killed with it")

    def test_a_half_built_map_of_longhauls_own_is_rebuilt_at_the_retry_and_a_strangers_map_never(self):
        # The review's "stale or incomplete index" (the owner, 2026-10-21):
        # the retry keys on Longhaul's own state, not on the map folder. A
        # map an init of Longhaul's left half-built is rebuilt at the
        # retry; a map the state knows nothing of is someone else's.
        (self.tmp / "codegraph.fail").touch()  # the fake makes its map, then fails
        (self.bin / "trail").unlink()
        top = str(self.repo)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)])
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        self.assertTrue((self.repo / ".codegraph").is_dir(), "half-built, and present")
        self.assertEqual(self.begin(self.start()), [], "under a day old")
        state = self.state()
        state["runs"]["codegraph"]["started"] -= 86_401
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "a day on, the folder present: Longhaul's own")
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        self.assertEqual(len(self.calls("codegraph")), 2)
        # The same folder with no entry of Longhaul's is someone else's.
        self.state_path().unlink()
        self.assertEqual(self.begin(self.start()), [], "a stranger's map")
        self.assertFalse(self.state_path().exists(), "and nothing is written for it")
        self.assertEqual(len(self.calls("codegraph")), 2)

    def test_a_failed_entry_follows_its_retry_rule_whatever_its_map_folder_shows(self):
        # Final review, Sprint 7 (G1): a map folder written after a failed
        # run ended read as a rebuild by hand, but CodeGraph's own
        # processes (its MCP server, `codegraph explore`) write in
        # .codegraph/ when they open its SQLite store, so a half-built map
        # looked rebuilt: never rebuilt, and named to the model. The entry
        # alone decides now; a user who rebuilt the map deletes the state.
        (self.tmp / "codegraph.fail").touch()
        (self.bin / "trail").unlink()
        top, folder = str(self.repo), self.repo / ".codegraph"
        self.assertEqual(self.begin(self.start()), [("codegraph", top)])
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        (self.tmp / "codegraph.fail").unlink()
        later = self.state()["runs"]["codegraph"]["ended"] + 30
        os.utime(folder, (later, later))  # written after the run ended, as a store opened by CodeGraph is
        answer = self.check(self.start())
        self.assertNotIn(longhaul.GUARD_CODEGRAPH, self.context(answer), "the half-built map is not named")
        self.assertEqual(self.said(answer), self.told_failed("codegraph", "exit 1"), "the failure stands")
        self.assertEqual(len(self.calls("codegraph")), 1, "under a day old: not yet")
        state = self.state()
        for key in ("started", "ended"):  # the failure a day old, the folder still newer
            state["runs"]["codegraph"][key] -= 86_401
        longhaul.write_json(self.state_path(), state)
        self.assertEqual(self.begin(self.start()), [("codegraph", top)], "a day on: retried")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(len(self.calls("codegraph")), 2)
        self.assertEqual(self.context(self.check(self.start())), longhaul.GUARD_CODEGRAPH, "built: named")

    def test_a_map_whose_state_file_was_deleted_is_someone_elses_untouched_and_named(self):
        # Final review, Sprint 7 (G1): what a user who rebuilt a map by
        # hand does to tell Longhaul. The map stays, nothing is started or
        # written for it, and the guard names it.
        (self.tmp / "codegraph.fail").touch()
        (self.bin / "trail").unlink()
        self.assertEqual(self.begin(self.start()), [("codegraph", str(self.repo))])
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        (self.tmp / "codegraph.fail").unlink()
        self.state_path().unlink()
        answer = self.check(self.start())
        self.assertEqual(self.context(answer), longhaul.GUARD_CODEGRAPH, "named, and no init's note")
        self.assertIsNone(self.said(answer), "nothing to tell the user")
        self.assertEqual(len(self.calls("codegraph")), 1, "never run again")
        self.assertFalse(self.state_path().exists(), "and nothing is written for it")
        self.assertTrue((self.repo / ".codegraph").is_dir())

    def test_one_answer_per_entry_shape_to_a_failure_that_stands(self):
        # Final review, Sprint 7 (G2): index_due(), index_start()'s
        # failures for the user and index_unfinished() each spelled "a
        # failure" their own way, and an exit of 0 beside a why of
        # Longhaul's was a failure to one and a clean exit to the other
        # two. index_failed() is the one answer. The map is there
        # throughout, and nothing runs: the spawn is a stand-in.
        (self.bin / "trail").unlink()
        (self.repo / ".codegraph").mkdir()
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        now, alive, dead = time.time(), os.getpid(), gone.pid
        top, folder, recent, old = str(self.repo), str(self.repo / ".codegraph"), now - 5, now - 86_460
        # (shape, entry, a failure that stands, started now, told the user)
        table = (
            ("running", dict(started=recent, pid=alive, exit=None), False, False, False),
            ("its pid alive past its timeout", dict(started=old, pid=alive, exit=None), True, True, False),
            ("a dead pid", dict(started=recent, pid=dead, exit=None), True, False, True),
            ("exit 0", dict(started=recent, pid=dead, exit=0, ended=recent), False, False, False),
            ("exit 1, a day old", dict(started=old, pid=dead, exit=1, ended=old), True, True, False),
            ("a timeout, a day old", dict(started=old, pid=dead, exit=-1, ended=old, why="timeout"), True, False, True),
            ("an error", dict(started=recent, pid=None, exit=-1, ended=recent, why="error"), True, False, True),
            ("corrupt", dict(pid="abc", exit="0", started=None), True, True, False),
            ("exit 0 and a why", dict(started=recent, pid=dead, exit=0, ended=recent, why="error"), True, False, True),
            ("exit 0 and a why, a day old", dict(started=old, pid=dead, exit=0, ended=old, why=5), True, True, False),
        )
        for shape, entry, failure, due, told in table:
            with self.subTest(shape):
                self.plant(**entry)
                self.assertIs(longhaul.index_failed(entry, now), failure)
                self.assertIs(longhaul.index_due("codegraph", top, entry, folder, now), due)
                self.assertIs(longhaul.index_unfinished(str(self.repo / "src")), failure or shape == "running")
                with mock.patch.object(longhaul, "index_spawn", return_value=dead):
                    started, failed = longhaul.index_start(self.start(), "on")
                self.assertEqual(started, [("codegraph", top)] if due else [])
                self.assertEqual([(program, where) for program, where, _ in failed], [("codegraph", top)] if told else [])
        self.assertEqual(self.calls("codegraph"), [], "nothing ran")

    def test_a_corrupt_state_file_is_said_once_per_read_and_a_corrupt_entry_is_the_retry_path(self):
        # Review, Sprint 7 (F8): as load_state() does, one line that names
        # the file, never its content, and a fresh state.
        (self.bin / "trail").unlink()
        top = str(self.repo)
        said = f"index: {longhaul.home_path(str(self.state_path()))} could not be read; a fresh state stands in until the next write\n"
        self.state_path().parent.mkdir(parents=True)
        for n, junk in enumerate(("not json {", "[1, 2]", '{"top": "x", "runs": [1]}'), 1):
            self.state_path().write_text(junk)
            self.assertEqual(self.begin(self.start()), [("codegraph", top)], junk)
            self.assertEqual(self.wait("codegraph")["exit"], 0, junk)
            self.assertEqual((self.home / "longhaul.log").read_text().count(said), n, junk)
            shutil.rmtree(self.repo / ".codegraph")
        # An entry of junk values reads as a dead runner of unknown age,
        # the retry path, and nothing raises on the way.
        for entry in ({"pid": "abc", "exit": "0", "started": None},
                      {"pid": True, "exit": [], "started": "x", "ended": {}, "seconds": "x", "why": 5},
                      {"pid": 0, "exit": None, "started": float("nan")}):
            self.plant(**entry)
            self.assertEqual(self.begin(self.start()), [("codegraph", top)], entry)
            self.assertEqual(self.wait("codegraph")["exit"], 0, entry)
            shutil.rmtree(self.repo / ".codegraph")
        self.assertEqual(len(self.calls("codegraph")), 6)
        self.assertNotIn("not json", (self.home / "longhaul.log").read_text(), "never the file's content")

    def test_a_state_that_cannot_be_written_starts_no_runner_and_the_guards_note_still_goes(self):
        # Review, Sprint 7 (F4): the runner was started before its entry
        # was written, and whatever the index raised cost the guard's note.
        (self.repo / ".codegraph").mkdir()  # a map of someone's: the note is due; trail is the one to start
        full = OSError(28, "No space left on device")
        with mock.patch.object(longhaul, "write_json", side_effect=full):
            answer = self.check(self.start())
        self.assertEqual(self.context(answer), longhaul.GUARD_CODEGRAPH)
        self.assertIn(f"index: not started ({full!r})\n", (self.home / "longhaul.log").read_text())
        time.sleep(0.5)
        self.assertEqual(self.calls("trail"), [], "no entry written, no runner started")
        # The entry written and the pid not: the runner runs, once, and says so.
        real, writes = longhaul.write_json, []

        def second_fails(path, data):
            writes.append(path)
            if len(writes) == 2:
                raise full
            real(path, data)

        with mock.patch.object(longhaul, "write_json", side_effect=second_fails):
            self.assertEqual(self.context(self.check(self.start())),
                             f"{longhaul.GUARD_CODEGRAPH} {self.note('trail')}")
        self.assertEqual(self.wait("trail")["exit"], 0)
        self.assertEqual(len(self.calls("trail")), 1)
        self.assertIsNone(self.state()["runs"]["trail"]["pid"])
        # A runner that cannot be started is a failed run, entry and line.
        shutil.rmtree(self.repo / ".trail")
        self.state_path().unlink()
        with mock.patch.object(longhaul, "index_spawn", side_effect=OSError(35, "Resource temporarily unavailable")):
            self.assertEqual(self.begin(self.start()), [])
        entry = self.state()["runs"]["trail"]
        self.assertEqual((entry["exit"], entry["why"], entry["seconds"]), (-1, "error", 0.0))
        self.assertIn(f"index: trail init in {longhaul.home_path(str(self.repo))} exit -1 after 0.0 s "
                      "(could not run: Resource temporarily unavailable)\n", (self.home / "longhaul.log").read_text())

    def test_a_home_that_cannot_be_written_never_breaks_the_hook(self):
        if os.geteuid() == 0:
            self.skipTest("root writes where the mode says no")
        self.home.chmod(0o500)
        self.addCleanup(self.home.chmod, 0o700)
        data = json.dumps(self.start())
        out = subprocess.run(Guard.HOOK, input=data, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        (self.repo / ".codegraph").mkdir()
        out = subprocess.run(Guard.HOOK, input=data, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.context(json.loads(out.stdout)), longhaul.GUARD_CODEGRAPH, "the guard's note still goes")
        self.assertEqual(self.calls("codegraph") + self.calls("trail"), [], "nothing ran")

    def test_a_runner_that_fails_anywhere_leaves_its_line(self):
        # Review, Sprint 7 (F5): the runner raised before its exit was
        # written, and the run left no trace at all.
        top = str(self.repo)
        (self.home / "index").write_text("a file where the folder belongs")
        longhaul.index_run("codegraph", top, str(self.bin / "codegraph"))  # raises nothing
        self.assertRegex((self.home / "longhaul.log").read_text(),
                         rf"index: codegraph init in {re.escape(longhaul.home_path(top))} exit -1 after \d+\.\d s "
                         r"\(FileExistsError\(17, 'File exists'\)\)\n")
        self.assertEqual(self.calls("codegraph"), [])
        # The program gone between the hook's look and the run (it was on
        # PATH, then uninstalled): could not run, exit -1.
        (self.home / "index").unlink()
        (self.bin / "codegraph").unlink()
        longhaul.index_run("codegraph", top, str(self.bin / "codegraph"))
        entry = self.state()["runs"]["codegraph"]
        self.assertEqual((entry["exit"], entry["why"]), (-1, "error"))
        self.assertIn(f"index: codegraph init in {longhaul.home_path(top)} exit -1 after ", (self.home / "longhaul.log").read_text())
        self.assertRegex((self.home / "longhaul.log").read_text(), r"\(could not run: No such file or directory\)\n")

    def test_a_half_built_codegraph_map_of_longhauls_own_is_not_named_to_the_model(self):
        # Review, Sprint 7 (F17): with .codegraph/ there but Longhaul's own
        # run of it failed or still going, the map is half-built, and the
        # guard's note would send the model to it.
        (self.tmp / "codegraph.fail").touch()
        (self.bin / "trail").unlink()
        self.assertEqual(self.context(self.check(self.start())), self.note("codegraph"))
        self.assertEqual(self.wait("codegraph")["exit"], 1)
        self.assertTrue((self.repo / ".codegraph").is_dir(), "the half-built map")
        self.assertNotIn(longhaul.GUARD_CODEGRAPH, self.context(self.check(self.start())), "a failed run's map")
        # Running: the map appears before the build ends.
        self.state_path().unlink()
        shutil.rmtree(self.repo / ".codegraph")
        (self.tmp / "codegraph.fail").unlink()
        (self.tmp / "codegraph.slow").touch()
        self.assertEqual(self.begin(self.start()), [("codegraph", str(self.repo))])
        (self.repo / ".codegraph").mkdir()
        self.assertNotIn(longhaul.GUARD_CODEGRAPH, self.context(self.check(self.start())), "a run in progress")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(self.context(self.check(self.start())), longhaul.GUARD_CODEGRAPH, "built: named")

    def test_the_init_runs_in_the_top_level_and_writes_nothing_outside_it(self):
        # The review's boundary point: the init runs in the top level, not
        # in the session's cwd, and nothing of the run lands beside the
        # repository or anywhere but Longhaul's index/ folder. The home
        # folder is in the scratch tree too, for this process and for the
        # runner and the programs, so a write under it would be listed
        # (review, Sprint 7, F15).
        sibling, user = self.tmp / "sibling", self.tmp / "user"
        sibling.mkdir()
        (sibling / "keep.txt").write_text("x")
        user.mkdir()
        (user / "keep.txt").write_text("x")
        for name in self.MAPS:  # the fakes' own records, and Longhaul's log and lock: there before and after
            (self.tmp / f"{name}.calls").touch()
            (self.tmp / f"{name}.env").touch()
        (self.home / "longhaul.log").touch()
        (self.home / ".index.lock").touch()

        def listing():
            return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*")
                          if not str(p).startswith((str(self.repo) + os.sep, str(self.home / "index"))))

        before = listing()
        self.assertIn("sibling/keep.txt", before)
        self.assertIn("user/keep.txt", before)
        with mock.patch.object(longhaul, "HOME", user), mock.patch.dict(os.environ, {"HOME": str(user)}):
            self.assertEqual(self.begin(self.start(cwd=str(self.repo / "src"))),
                             [("codegraph", str(self.repo)), ("trail", str(self.repo))])
            for program in self.MAPS:
                self.assertEqual(self.wait(program)["exit"], 0, program)
        real = os.path.realpath(self.repo)
        self.assertEqual(self.calls("codegraph"), [f"{real} init -y"], "run in the top level, not in the cwd")
        self.assertEqual(self.calls("trail"), [f"{real} init --yes --no-global --no-statusline --no-agents"])
        self.assertFalse((self.repo / "src" / ".codegraph").exists())
        self.assertTrue((self.repo / ".codegraph").is_dir() and (self.repo / ".trail").is_dir())
        self.assertEqual(listing(), before, "nothing new outside repo/ and index/")

    def test_the_state_file_shape_mode_and_the_programs_output_never_reach_the_answer_or_the_log(self):
        # Through the hook as the engine runs it, so its stdout is seen too;
        # DO_NOT_TRACK stands for any variable: the environment goes as it is.
        out = subprocess.run(Guard.HOOK, input=json.dumps(self.start()),
                             env={**self.env, "DO_NOT_TRACK": "1", "CODEGRAPH_TELEMETRY": "0"},
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        for program in self.MAPS:
            self.wait(program)
        self.assertEqual(self.context(json.loads(out.stdout)), f"{self.note('codegraph')} {self.note('trail')}")
        self.assertNotIn("SECRET", out.stdout + out.stderr, "the hook's own streams")
        state = self.state()
        self.assertEqual(set(state), {"top", "runs"})
        self.assertEqual(state["top"], os.path.realpath(self.repo))
        self.assertEqual(set(state["runs"]), {"codegraph", "trail"})
        for program, entry in state["runs"].items():
            self.assertEqual(list(entry), ["started", "pid", "exit", "ended", "seconds"], program)
            self.assertIsInstance(entry["started"], float, program)
            self.assertIsInstance(entry["pid"], int, program)
            self.assertEqual(entry["exit"], 0, program)
            self.assertGreaterEqual(entry["ended"], entry["started"], program)
            self.assertIsInstance(entry["seconds"], float, program)
        key = longhaul.index_key(str(self.repo))
        self.assertRegex(key, r"^repo-[0-9a-f]{12}$")
        self.assertEqual(sorted(p.name for p in (self.home / "index").iterdir()),
                         [f"{key}.codegraph.log", f"{key}.json", f"{key}.trail.log"])
        for file in (self.home / "index").iterdir():
            self.assertEqual(oct(file.stat().st_mode & 0o777), oct(0o600), file.name)
        self.assertTrue((self.home / ".index.lock").exists(), "the README's lock row")
        # The program's output, both streams, goes to its own log and nowhere else.
        self.assertEqual((self.home / "index" / f"{key}.codegraph.log").read_text(),
                         "SECRET-OUTPUT-codegraph\nSECRET-OUTPUT-codegraph\n")
        self.assertNotIn("SECRET", (self.home / "longhaul.log").read_text())
        self.assertNotIn("SECRET", self.state_path().read_text())
        # The telemetry variables are passed as they are: none added or
        # removed, so the program saw what the hook's environment held.
        self.assertEqual((self.tmp / "codegraph.env").read_text(), "1\n0\nunset\nunset\n")

    def test_the_program_sees_no_telemetry_variable_added_and_none_of_the_sessions_own(self):
        # Review, Sprint 7 (F12, F18): the other way round from the test
        # above, neither telemetry variable set, so one added would show;
        # and the session's own Claude variables (SESSION_ENV: its id, its
        # messaging token) are not the program's, so the runner strips them.
        env = {**self.env, "CLAUDE_CODE_MESSAGING_TOKEN": "tok", "CLAUDE_CODE_SESSION_ID": "sid"}
        out = subprocess.run(Guard.HOOK, input=json.dumps(self.start()), env=env, capture_output=True, text=True,
                             timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        for program in self.MAPS:
            self.assertEqual(self.wait(program)["exit"], 0, program)
            self.assertEqual((self.tmp / f"{program}.env").read_text(), "unset\nunset\nunset\nunset\n", program)
        self.assertEqual(set(self.SEEN[2:]) - set(longhaul.SESSION_ENV), set(), "the names are SESSION_ENV's")

    def test_the_walk_up_and_the_inside_test_are_one_each(self):
        # Review, Sprint 7 (F14): map_at(), index_top() and index_map()
        # walked the parents each their own way, and index_top(),
        # index_program() and the runner each spelled "inside" their own
        # way (one without the rstrip, so a top of "/" read as "//").
        self.assertEqual(list(longhaul.above(f"{os.sep}a{os.sep}b")), [f"{os.sep}a{os.sep}b", f"{os.sep}a", os.sep])
        for cwd in ("a/b", "", None, 5):
            self.assertEqual(list(longhaul.above(cwd)), [], cwd)
        for path, folder, expected in (("/a/b/c", "/a/b", True), ("/a/b/c", "/a/b/", True), ("/a/bc", "/a/b", False),
                                       ("/a/b", "/a/b", False), ("/a/b", "/", True)):
            self.assertEqual(longhaul.inside(path, folder), expected, (path, folder))
        # And the callers ask these two, never a spelling of their own
        # (final review, Sprint 7, G4, G8): each helper counted per call.
        top, work = str(self.repo), {"name": "Work", "repositories": [str(self.repo)]}
        src = str(self.repo / "src")
        for caller, helper, call in (
                ("map_at", "above", lambda: longhaul.map_at(src, ".codegraph")),
                ("index_map", "above", lambda: longhaul.index_map(src, "codegraph")),
                ("index_top", "above", lambda: longhaul.index_top(src)),
                ("index_top", "inside", lambda: longhaul.index_top(src)),
                ("index_program", "inside", lambda: longhaul.index_program("codegraph", top)),
                ("account_allowed", "inside", lambda: longhaul.account_allowed(work, src))):
            seen = []
            real = getattr(longhaul, helper)
            with mock.patch.object(longhaul, helper, lambda *args, real=real: seen.append(args) or real(*args)):
                call()
            self.assertNotEqual(seen, [], f"{caller}() asks {helper}()")

    def test_the_hook_loads_no_hashlib_until_a_state_is_keyed(self):
        # Review, Sprint 7 (F14): the guard hook imports longhaul at every
        # tool call, and hashlib loads OpenSSL on 3.11 and later; only
        # index_key() needs it, so only index_key() imports it.
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import longhaul; "
                 "print(sorted({'hashlib', '_hashlib'} & set(sys.modules))); longhaul.index_key(sys.argv[1]); "
                 "print('hashlib' in sys.modules)")
        out = subprocess.run([sys.executable, "-I", "-c", probe, str(HERE)], env=self.env, capture_output=True,
                             text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "[]\nTrue\n")

    def test_a_symlinked_cwd_keys_one_state_and_runs_in_the_path_given(self):
        # S1: the key is the real path's, so a project opened through a link
        # and through its real path has one state, and a start through the
        # one sees a run in progress through the other.
        link = self.tmp / "link"
        link.symlink_to(self.repo)
        (self.tmp / "codegraph.slow").touch()
        self.assertEqual(self.begin(self.start(cwd=str(link / "src"))), [("codegraph", str(link)), ("trail", str(link))])
        self.wait("trail", str(link))
        self.assertEqual(self.begin(self.start()), [], "a start at the real path while the link's runs")
        self.assertEqual(self.wait("codegraph", str(link))["exit"], 0)
        self.assertEqual([p.name for p in (self.home / "index").glob("*.json")], [f"{longhaul.index_key(str(self.repo))}.json"])
        self.assertEqual(self.state()["top"], os.path.realpath(self.repo), "the state names the real path")
        self.assertEqual(self.calls("codegraph"), [f"{os.path.realpath(self.repo)} init -y"], "one run, in the physical folder")
        self.assertIn(f"index: started codegraph init in {longhaul.home_path(str(link))} (pid ",
                      (self.home / "longhaul.log").read_text(),
                      "the log names the path given")
        self.assertTrue((self.repo / ".codegraph").is_dir() and (self.repo / ".trail").is_dir())

    def test_the_note_is_one_sentence_per_program_and_names_no_install(self):
        top = str(self.repo)
        (self.bin / "trail").unlink()
        answer = self.check(self.start())
        self.assertEqual(answer["hookSpecificOutput"]["hookEventName"], "SessionStart")
        text = self.context(answer)
        self.assertEqual(text, self.note("codegraph"))
        # The folder appears before the build ends, so the note names no
        # folder to watch for (review, Sprint 7, F19).
        self.assertEqual(text, f"Longhaul started `codegraph init -y` in {longhaul.home_path(top)} in the background "
                               "for later sessions; in this session search as you would without an index.")
        self.assertNotIn("install", text.lower())
        self.assertNotIn("permissionDecision", answer["hookSpecificOutput"])
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        # With .codegraph/ there and trail started: the guard's note, then
        # the Trail sentence, one answer.
        self.fake(self.bin / "trail", "trail")
        other = self.tmp / "other"
        (other / ".git").mkdir(parents=True)
        (other / ".codegraph").mkdir()
        text = self.context(self.check(self.start(cwd=str(other))))
        self.assertEqual(text, f"{longhaul.GUARD_CODEGRAPH} {self.note('trail', str(other))}")
        self.assertEqual(self.wait("trail", str(other))["exit"], 0)
        self.assertNotIn("install", text.lower())
        self.assertIn(f"guard: named the CodeGraph index at {longhaul.home_path(str(other))} at startup",
                      (self.home / "longhaul.log").read_text())
        # `where` is by home_path: a project under the home folder shows as ~/...
        third = self.tmp / "third"
        (third / ".git").mkdir(parents=True)
        with mock.patch.object(longhaul, "HOME", self.tmp):
            text = self.context(self.check(self.start(cwd=str(third))))
        self.assertTrue(text.startswith("Longhaul started `codegraph init -y` in ~/third in the background "), text)
        for program in self.MAPS:
            self.assertEqual(self.wait(program, str(third))["exit"], 0, program)

    def test_the_hook_returns_well_inside_its_timeout_while_the_init_runs_on(self):
        # The hook never waits: Popen only, in its own session, none of
        # the hook's pipes inherited (a runner holding the hook's stdout
        # would hold the engine too, until the init ended).
        (self.tmp / "codegraph.slow").touch()
        started = time.time()
        out = subprocess.run(Guard.HOOK, input=json.dumps(self.start()), env=self.env, capture_output=True, text=True,
                             timeout=10)
        took = time.time() - started
        self.assertEqual(out.returncode, 0, out.stderr)
        # Under the fake's 3 s sleep, so the hook did not wait for it; the
        # proof is the exit still None below, not a tighter figure, which
        # a loaded machine's slow start would break (review, Sprint 7, F15).
        self.assertLess(took, 3, "the hook returned with the init running on")
        self.assertEqual(self.context(json.loads(out.stdout)), f"{self.note('codegraph')} {self.note('trail')}")
        self.assertIsNone(self.state()["runs"]["codegraph"]["exit"], "still running when the hook had returned")
        self.assertEqual(self.wait("codegraph")["exit"], 0)
        self.assertEqual(self.wait("trail")["exit"], 0)

    def test_a_program_found_through_a_relative_path_entry_or_inside_the_project_is_not_run(self):
        # S1: a repository must not supply the program it is indexed with,
        # and a relative PATH entry would resolve inside whatever folder
        # the hook's process runs in (the project: the lead's probe).
        top = str(self.repo)
        (self.repo / "bin").mkdir()
        self.fake(self.repo / "bin" / "codegraph", "codegraph")
        with mock.patch.dict(os.environ, {"PATH": str(self.repo / "bin")}):
            self.assertIsNone(longhaul.index_program("codegraph", top), "inside the project, by an absolute entry")
            self.assertEqual(self.plan(self.start()), [])
        (self.tmp / "links").mkdir()
        (self.tmp / "links" / "codegraph").symlink_to(self.repo / "bin" / "codegraph")
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "links")}):
            self.assertIsNone(longhaul.index_program("codegraph", top), "inside the project, through a link from outside")
        # A relative entry, the hook's process in the project and then in
        # the folder that holds the fake: neither is looked in.
        for cwd in (self.repo / "bin", self.bin):
            out = subprocess.run(Guard.HOOK, input=json.dumps(self.start()), env={**self.env, "PATH": "."}, cwd=str(cwd),
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual((out.returncode, out.stdout), (0, ""), (cwd, out.stderr))
        self.assertEqual(self.calls("codegraph") + self.calls("trail"), [], "nothing ran")
        self.assertFalse((self.home / "index").exists(), "nothing written")
        # The fake in bin/ behind a relative entry: found through the
        # absolute one, and run from there.
        with mock.patch.dict(os.environ, {"PATH": f".{os.pathsep}{self.bin}"}):
            self.assertEqual(longhaul.index_program("codegraph", top), str(self.bin / "codegraph"))
            self.assertEqual(self.begin(self.start()), [("codegraph", top), ("trail", top)])
        for program in self.MAPS:
            self.assertEqual(self.wait(program)["exit"], 0, program)
        self.assertEqual(self.calls("codegraph"), [f"{os.path.realpath(self.repo)} init -y"])

    def test_the_runner_runs_the_program_the_hook_found_by_its_path_and_never_one_in_the_project(self):
        # Review, Sprint 7 (F1, high): the runner ran the bare name in the
        # top level, and a relative or empty PATH entry resolves there, so
        # a repository shipping its own `codegraph` at the top ran it with
        # the user's privileges, though the hook had found the one in bin/.
        top = str(self.repo)
        for name in self.MAPS:  # a program of the repository's own under each name: never to be run
            (self.repo / name).write_text(f"#!/bin/sh\necho ran >> '{self.tmp}/repo-{name}.ran'\nexit 0\n")
            (self.repo / name).chmod(0o755)
        for path in (f".{os.pathsep}{self.bin}", f"{os.pathsep}{self.bin}"):  # "." and "" both name the cwd
            with mock.patch.dict(os.environ, {"PATH": path}):
                self.assertEqual(self.begin(self.start()), [("codegraph", top), ("trail", top)], path)
            for program in self.MAPS:
                self.assertEqual(self.wait(program)["exit"], 0, (path, program))
                self.assertEqual(len(self.calls(program)), 1, (path, program, "the fake bin/ holds ran, once"))
                self.assertFalse((self.tmp / f"repo-{program}.ran").exists(), (path, program, "the project's never"))
                shutil.rmtree(self.repo / self.MAPS[program])
                (self.tmp / f"{program}.calls").unlink()
            self.state_path().unlink()
        # The second belt: a path that is not absolute, or is inside the
        # top, is refused by the runner itself, whatever the hook passed.
        for found in (str(self.repo / "codegraph"), "codegraph", f".{os.sep}codegraph"):
            longhaul.index_run("codegraph", top, found)
            self.assertEqual(self.state()["runs"]["codegraph"]["exit"], -1, found)
        self.assertFalse((self.tmp / "repo-codegraph.ran").exists(), "refused, not run")
        self.assertEqual(self.calls("codegraph"), [], "nor the fake")
        self.assertRegex((self.home / "longhaul.log").read_text(),
                         rf"index: codegraph init in {re.escape(longhaul.home_path(top))} exit -1 after 0\.0 s "
                         r"\(could not run: not the program found\)\n")

    def test_a_module_the_project_ships_is_never_imported_by_the_hook_or_the_runner(self):
        # Review, Sprint 7 (F10): the hooks run `python3 -c` in the
        # project, so its folder came first on sys.path, and a json.py
        # there was imported ahead of the standard library's by every hook
        # and by the runner, which starts in the hook's folder. `-I` keeps
        # it off.
        cwd = self.repo / "src"
        (cwd / "json.py").write_text('raise ImportError("the json.py of the project was imported")\n')
        out = subprocess.run(Guard.HOOK, input=json.dumps(self.start()), env=self.env, cwd=str(cwd),
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.context(json.loads(out.stdout)), f"{self.note('codegraph')} {self.note('trail')}")
        for program in self.MAPS:
            self.assertEqual(self.wait(program)["exit"], 0, (program, "the runner too"))

    def test_the_user_is_told_of_a_start_and_of_a_failure_that_stands(self):
        # Review, Sprint 7 (F16): the model was told and the user was not.
        # The systemMessage names the command, where, and the log; at a
        # later start, a failure that stands (not tried again now) is said
        # with its day and its reason, never the program's output.
        answer = self.check(self.start())
        self.assertEqual(self.said(answer), f"{self.told_started('codegraph')} {self.told_started('trail')}")
        self.assertEqual(self.context(answer), f"{self.note('codegraph')} {self.note('trail')}", "the model's, unchanged")
        for program in self.MAPS:
            self.wait(program)
        answer = self.check(self.start())
        self.assertNotIn("systemMessage", answer, "built: nothing to say")
        self.assertEqual(self.context(answer), longhaul.GUARD_CODEGRAPH)
        # A failure.
        other = self.tmp / "other"
        (other / ".git").mkdir(parents=True)
        (self.tmp / "codegraph.fail").touch()
        (self.bin / "trail").unlink()
        self.assertEqual(self.said(self.check(self.start(cwd=str(other)))), self.told_started("codegraph", str(other)))
        self.assertEqual(self.wait("codegraph", str(other))["exit"], 1)
        answer = self.check(self.start(cwd=str(other)))
        self.assertEqual(self.said(answer), self.told_failed("codegraph", "exit 1", str(other)))
        self.assertNotIn("SECRET", self.said(answer))
        self.assertNotIn("hookSpecificOutput", answer, "the half-built map is not named, and nothing started")
        self.assertIsNone(self.check(self.start(cwd=str(other), agent="agent-1")), "a subagent: nothing")
        self.assertIsNone(self.check(self.start(cwd=str(other)), index="off"), "the index off: nothing")
        out = subprocess.run(Guard.HOOK, input=json.dumps(self.start(cwd=str(other))),
                             env={**self.env, "LONGHAUL_INDEX": "off"}, capture_output=True, text=True, timeout=10)
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        # A run that never ran says where its reason is: longhaul.log.
        third = self.tmp / "third"
        (third / ".git").mkdir(parents=True)
        with mock.patch.object(longhaul, "index_spawn", side_effect=OSError(35, "Resource temporarily unavailable")):
            self.assertIsNone(self.said(self.check(self.start(cwd=str(third)))), "no start, and no failure yet")
        self.assertEqual(self.said(self.check(self.start(cwd=str(third)))),
                         self.told_failed("codegraph", "exit -1, could not run", str(third), self.home / "longhaul.log"))

    def test_a_repository_that_ships_a_map_folder_gets_no_init_and_no_note(self):
        # A shipped .codegraph/ or .trail/ is a map; what its hooks do is
        # between the user and the repository (the README's Limitations).
        # The guard's own note of a CodeGraph index stays what it was.
        (self.repo / ".trail").mkdir()
        (self.bin / "codegraph").unlink()
        self.assertIsNone(self.check(self.start()), "trail's map shipped, trail the only program: nothing")
        (self.repo / ".codegraph").mkdir()
        self.fake(self.bin / "codegraph", "codegraph")
        self.assertEqual(self.context(self.check(self.start())), longhaul.GUARD_CODEGRAPH, "the guard's note alone")
        self.assertEqual(self.calls("codegraph") + self.calls("trail"), [], "nothing ran")
        self.assertFalse((self.home / "index").exists(), "nothing written")


class Advisor(unittest.TestCase):
    """The Advisor experiment's hooks (Sprint 8, region B; the decision
    package's D2.1-D2.8): `longhaul advisor-hook`, PreToolUse on Agent,
    admits or denies a consultation (a call of the longhaul:advisor agent
    type), and `longhaul advisor-result`, PostToolUse, traces how it went.
    Off unless LONGHAUL_ADVISOR=experiment and a passing probe is recorded
    for the running Claude Code. Every test turns it on in a scratch home
    alone, and none runs a real claude: the version comes from a fake path
    or a fake executable."""

    # A brief's or a reply's text: it belongs in the tool's input and in the
    # engine's answer, and never in the trace, the state, the log or a reason.
    S = "SENTINEL-OKAPI-4412"
    # The hooks as the plugin registers them (F1: the PreToolUse one reads its
    # input before it imports longhaul, and denies a consultation without it).
    HOOK = registered_hook("PreToolUse", "advisor-hook")
    RESULT = registered_hook("PostToolUse", "advisor-result")
    # Claude Code's variables that set a subagent's model over the hook's (F14).
    FORCED = ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_CODE_COORDINATOR_FORCE_WORKER_INHERIT_MODEL",
              "CLAUDE_CODE_SUBAGENT_MODEL")
    # What the hooks read of the session's environment: each test gives its own.
    VARS = ("CLAUDE_CODE_EXECPATH", "CLAUDE_CONFIG_DIR", *FORCED)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved = {k: getattr(longhaul, k) for k in ("LONGHAUL_HOME", "USAGE_DIR", "HOME")}
        # The user's home folder, whose ~/.longhaul A2 reads as well (F13): a
        # scratch one, never the real one, here and in every hook run.
        self.os_home = self.tmp / "os-home"
        self.os_home.mkdir()
        longhaul.LONGHAUL_HOME, longhaul.USAGE_DIR, longhaul.HOME = self.home, self.home / "usage", self.os_home
        # The session's folder, beside the home: a cwd the home is inside makes it untrusted (F13).
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.now = time.time()
        self.n = 0
        # Claude Code as the probe saw it on the owner's terminal: the version
        # is the last folder of the executable's path.
        self.exe = self.tmp / "claude" / "versions" / "2.1.307"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("")
        self.transcript = self.tmp / "s1.jsonl"
        self.executor("claude-sonnet-4-5")
        self.probe()
        self.enable()
        self.reading()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("LONGHAUL_") and k not in self.VARS + Checkpoint.SESSION_VARS}
        self.env.update(HOME=str(self.os_home), LONGHAUL_HOME=str(self.home),
                        LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "claude-config"),
                        LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "claude.json"), LONGHAUL_NO_NOTIFY="1",
                        LONGHAUL_CLAUDE_BIN=str(HERE / "fake_stream.py"),
                        LONGHAUL_ADVISOR="experiment", CLAUDE_CODE_EXECPATH=str(self.exe))

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(longhaul, k, v)

    def executor(self, model, path=None, before=b""):
        """The session's transcript, its last assistant line on `model`."""
        lines = [{"type": "user", "message": {"role": "user", "content": "go"}},
                 {"type": "assistant", "message": {"model": model, "role": "assistant", "content": []}}]
        (path or self.transcript).write_bytes(before + "".join(json.dumps(line) + "\n" for line in lines).encode())

    def probe(self, version="2.1.307", rewrites=("opus", "fable"), **checks):
        """A passing record whose probe saw both advisor models' rewrites
        take effect (F5); rewrites=None leaves the list out, as the
        2.1.307 record of 2026-10-22 does."""
        record = {"schema": 1, "claude_version": version, "passed_at": self.now,
                  "checks": {**dict.fromkeys(longhaul.ADVISOR_CHECKS, True), **checks}, "evidence": "a test"}
        if rewrites is not None:
            record["rewrite_models"] = list(rewrites) if isinstance(rewrites, tuple) else rewrites
        (self.home / "advisor").mkdir(exist_ok=True)
        (self.home / "advisor" / "probe.json").write_text(json.dumps(record))

    def enable(self, version="2.1.307", home=None):
        """The key `longhaul advisor enable` writes (F8), for the probe record's version."""
        path = (home or self.home) / "advisor" / "enabled.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": 1, "enabled_at": self.now, "probe_version": version}))

    def reading(self, five=40, at=None, name="main", week=10):
        """The status line's latest reading for account `name`."""
        (self.home / "usage").mkdir(exist_ok=True)
        (self.home / "usage" / f"{name}.json").write_text(json.dumps(
            {"at": self.now if at is None else at, "session": "s1",
             "five_hour": {"used_percentage": five, "resets_at": self.now + 3600},
             "seven_day": {"used_percentage": week, "resets_at": self.now + 86400}}))

    def brief(self, trigger="stuck", reconcile=False, drop=(), extra=""):
        """A brief in the documented shape, each one new unless a test means a repeat."""
        self.n += 1
        lines = [f"Trigger: {trigger}", f"Task: make the parser take tabs ({self.S}, brief {self.n})",
                 "Constraints: the standard library only", "Findings: longhaul.py:120-140",
                 f"Approach: a second pass over the tokens {self.S}", f"Question: is one pass enough?{extra}"]
        if reconcile:
            lines.insert(1, "Reconcile: yes")
        return "\n".join(line for line in lines if not line.startswith(tuple(drop)))

    def call(self, brief=None, session="s1", prompt="p1", tool_id=None, agent="longhaul:advisor"):
        """A PreToolUse input for an Agent call, as the probe saw one on 2.1.307: a
        brief for the advisor, an ordinary prompt for any other agent type."""
        if brief is None:
            brief = self.brief() if longhaul.advisor_named(agent) else f"Find where the parser reads tabs ({self.S})."
        data = {"session_id": session, "prompt_id": prompt, "tool_use_id": tool_id or f"toolu_{uuid.uuid4().hex[:12]}",
                "transcript_path": str(self.transcript), "cwd": str(self.work), "permission_mode": "default",
                "effort": "high", "hook_event_name": "PreToolUse", "tool_name": "Agent",
                "tool_input": {"description": "Consult the advisor", "prompt": brief,
                               "run_in_background": False, "subagent_type": agent}}
        if prompt is None:
            del data["prompt_id"]
        return data

    def decide(self, data, now=None, **env):
        """The hook's answer, in this process, in the session's environment
        with `env` over it (None unsets a name)."""
        values = {k: v for k, v in {**self.env, **env}.items() if v is not None}
        with mock.patch.dict(os.environ, values, clear=True):
            return longhaul.advisor_check(data, self.now if now is None else now)

    def denied(self, data, code, **kw):
        out = self.decide(data, **kw)["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"), out)
        self.assertIn(f"denied ({code})", out["permissionDecisionReason"])
        self.assertEqual(self.trace()[-1]["reason"], code)
        return out["permissionDecisionReason"]

    def admitted(self, data, **kw):
        out = self.decide(data, **kw)["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "allow"), out)
        self.assertEqual(self.trace()[-1]["decision"], "allow")
        return out

    def trace(self):
        path = self.home / "advisor" / "trace.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state(self):
        path = self.home / "advisor" / "state.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def log(self):
        path = self.home / "longhaul.log"
        return path.read_text() if path.exists() else ""

    def run_hook(self, cmd, data, **env):
        raw = data if isinstance(data, str) else json.dumps(data)
        return subprocess.run(cmd, input=raw, env={**self.env, **env}, capture_output=True, text=True, timeout=30)

    def fake_claude(self, name="claude", says="2.1.307 (Claude Code)", code=0):
        """An executable that answers --version and leaves a mark each time it runs."""
        exe, mark = self.tmp / "bin" / name, self.tmp / f"{name}.ran"
        exe.parent.mkdir(exist_ok=True)
        exe.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{mark}'\nprintf '%s\\n' '{says}'\nexit {code}\n")
        exe.chmod(0o755)
        return exe, mark

    def subagent(self, agent, tool_id, agent_type="longhaul:advisor", **fields):
        """A subagent's meta.json beside the session's transcript, as Claude
        Code writes it: its agent type, the call that started it and, when
        the call set one, its model."""
        path = self.transcript.with_suffix("") / "subagents" / f"agent-{agent}.meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"agentType": agent_type, "description": "Consult the advisor", "toolUseId": tool_id,
                                    "spawnDepth": 1, **fields}))
        return path

    def message(self, to, session="s1", **fields):
        """A PreToolUse input for a SendMessage call, as the second probe saw
        one on 2.1.307: tool_name SendMessage, and `to` the advisor's agent
        id. Its input held content, recipient, recipient_kind and type too,
        whose values it did not log; a test gives those it needs."""
        return {"session_id": session, "prompt_id": "p1", "tool_use_id": f"toolu_{uuid.uuid4().hex[:12]}",
                "transcript_path": str(self.transcript), "cwd": str(self.tmp), "hook_event_name": "PreToolUse",
                "tool_name": "SendMessage", "tool_input": {"to": to, "message": f"One more question ({self.S})",
                                                           "summary": "a follow-up", **fields}}

    def test_the_plugin_registers_both_hooks_on_agent_task_sendmessage_and_workflow(self):
        hooks = json.loads((HERE / "hooks" / "hooks.json").read_text())["hooks"]
        guard, pre = hooks["PreToolUse"]
        [post] = hooks["PostToolUse"]
        [guard] = guard["hooks"]
        for entry, command, cmd, says, timeout in (
                (pre, "advisor-hook", self.HOOK, "Checking for an advisor consultation", 30),
                (post, "advisor-result", self.RESULT, "Tracing an advisor consultation", 5)):
            # The subagent tool's name, its name before 2.1.63, the tool that
            # sends a running or finished agent a message (F2), and the one
            # that runs a script of agents (F17).
            self.assertEqual(entry["matcher"], "Agent|Task|SendMessage|Workflow")
            [handler] = entry["hooks"]
            self.assertEqual(cmd[1:-1], handler["args"][:-1], "the tests run the hook as the plugin does")
            self.assertEqual((handler["args"][:2], handler["args"][3]), (["-I", "-c"], "${CLAUDE_PLUGIN_ROOT}"))
            self.assertEqual((handler["type"], handler["command"]), ("command", "python3"))
            # A hook past its timeout lets the call run (F1): the consultation
            # check has 30 s, and its every wait is bounded well inside 10.
            self.assertEqual(handler["timeout"], timeout)
            self.assertEqual(handler["statusMessage"], f"{says} (longhaul advisor)")
        self.assertLess(longhaul.ADVISOR_VERSION_TIMEOUT + 2 * longhaul.ADVISOR_LOCK_WAIT, 10)
        # The result hook is the guard's command line with the command alone
        # changed, and fails open. The consultation check reads its input
        # first (at most 1 MB and a byte), so that a longhaul it cannot import
        # still denies a consultation.
        [result] = post["hooks"]
        self.assertEqual(result["args"], [*guard["args"][:2], guard["args"][2].replace("'guard'", "'advisor-result'"),
                                          "${CLAUDE_PLUGIN_ROOT}"])
        [check] = pre["hooks"]
        wrapper = check["args"][2]
        self.assertLess(wrapper.index("sys.stdin.buffer.read(1000001)"), wrapper.index("import longhaul"))
        self.assertIn("longhaul.main(['advisor-hook'])", wrapper)
        reason = longhaul.advisor_answer({}, {}, longhaul.advisor_denial("error"))["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(reason.replace("'", "\\'"), wrapper, "the wrapper's deny is the hook's own, word for word")

    def test_a_check_that_cannot_import_longhaul_still_denies_a_consultation(self):
        # F1: a plugin root whose longhaul.py cannot be imported, or whose
        # call raises, would otherwise exit 1, and the call would run.
        roots = {"broken": "raise ImportError('a half-installed plugin')\n",
                 "raises": "def main(argv):\n    raise RuntimeError('mid-update')\n", "empty": None}
        deny = longhaul.advisor_answer({}, {}, longhaul.advisor_denial("error"))
        huge = json.dumps(self.call(self.brief(extra="x" * 1_100_000)))
        cases = [(self.call(), deny), (self.call(agent=" Advisor"), deny), (self.call(agent="other:advisor"), None),
                 (self.call(agent="Explore"), None), ("{not json, an advisor", deny), ("{not json", None), ("", None),
                 ("[]", None), (huge, deny), (huge.replace("advisor", "helper"), deny),  # past the cap: all (F21)
                 ("{not json, an ADVISOR", deny), (huge.replace("advisor", "ADVISOR"), deny),  # any case (F20)
                 # F17: a Workflow call is read as text, as the hook reads it.
                 (self.workflow({"script": "agent({subagent_type: 'ADVISOR'})"}), deny),
                 (self.workflow({"script": "agent({subagent_type: 'Explore'})"}), None)]
        for name, source in roots.items():
            root = self.tmp / name
            root.mkdir()
            if source:
                (root / "longhaul.py").write_text(source)
            for data, answer in cases:
                raw = data if isinstance(data, str) else json.dumps(data)
                out = subprocess.run(self.HOOK[:-1] + [str(root)], input=raw, env=self.env, cwd=self.tmp,
                                     capture_output=True, text=True, timeout=30)
                self.assertEqual(out.returncode, 0, (name, raw[:60], out.stderr[-300:]))
                self.assertEqual(json.loads(out.stdout) if out.stdout else None, answer, (name, raw[:60]))
        self.assertEqual(self.trace(), [], "longhaul never ran")

    def test_an_admitted_consultation_carries_an_admission_line_the_executor_cannot_write(self):
        # F1: the advisor answers only a brief whose first line is the
        # hook's, so a consultation the hook never answered gets a refusal.
        data = self.call(tool_id="toolu_adm")
        out = self.admitted(data)
        prompt = out["updatedInput"]["prompt"]
        first, rest = prompt.split("\n", 1)
        self.assertRegex(first, r"^Admission: [0-9a-f]{16}$")
        self.assertEqual(rest, data["tool_input"]["prompt"], "the brief follows it unchanged")
        nonce = first.split(": ")[1]
        [kept] = self.state()["sessions"]["s1"]["admitted"]
        self.assertEqual(kept["nonce"], nonce, "kept in the state with the decision")
        import hashlib
        self.assertEqual(self.trace()[-1]["admission"], hashlib.sha256(nonce.encode()).hexdigest()[:12])
        self.assertNotIn(nonce, (self.home / "advisor" / "trace.jsonl").read_text() + self.log(), "the trace has its hash")
        other = self.admitted(self.call(prompt="p2"))["updatedInput"]["prompt"].split("\n", 1)[0]
        self.assertNotEqual(other, first, "a nonce per consultation")
        for line in ("Admission: 0123456789abcdef", "  admission : x", "ADMISSION:"):
            brief = self.brief().replace("\nTask:", f"\n{line}\nTask:")
            reason = self.denied(self.call(brief, prompt="p3"), "brief-sections")
            self.assertIn("Admission:", reason)
            self.assertIsNone(self.trace()[-1]["admission"])
        self.assertEqual(self.trace()[-1]["kind"], "fixable")
        self.assertIn("starting Task:", self.denied(self.call(self.brief(drop=["Task:"]), prompt="p3"), "brief-sections"))
        text = (HERE / "agents" / "advisor.md").read_text()
        self.assertIn("`Admission:`", text)
        for refusal in ("longhaul advisor: not admitted", "longhaul advisor: one brief per consultation"):
            self.assertIn(f"`{refusal}`", text, "the advisor's two refusals, word for word")

    def test_an_advisor_that_ran_with_no_admitted_decision_is_flagged(self):
        # F1: the PreToolUse hook did not answer (or was ignored): the
        # outcome line says so, the log says so in codes, and status counts it.
        self.result(self.call(tool_id="toolu_never"), self.reply("longhaul advisor: not admitted"))
        self.assertEqual({k: self.trace()[-1][k] for k in ("outcome", "verdict", "seconds")},
                         {"outcome": "unadmitted", "verdict": None, "seconds": None})
        refused = self.call(self.brief(drop=["Task:"]), tool_id="toolu_refused")
        self.denied(refused, "brief-sections")
        self.result(refused, self.reply(f"Verdict: proceed\nWhy: {self.S}"))
        self.assertEqual(self.trace()[-1]["outcome"], "unadmitted", "a denied consultation that ran anyway")
        admitted = self.call(tool_id="toolu_ok")
        self.admitted(admitted)
        self.result(admitted, self.reply("Verdict: proceed"))
        self.assertEqual(self.trace()[-1]["outcome"], "ok")
        self.assertEqual(self.log().count("advisor-result hook: an advisor ran with no admitted decision (unadmitted)"), 2)
        self.assertNotIn(self.S, self.log())
        self.assertIn("unadmitted", longhaul.CONSULTATION_OUTCOMES)
        lines, last = self.status()
        self.assertEqual((lines["Unadmitted advisor runs traced"], last), ("2", "enabled"))

    def test_off_by_default_and_admitted_only_with_experiment_and_a_probe_for_this_version(self):
        for value in (None, "", "off", "on", "true", "1", "Experiment", " experiment"):
            self.denied(self.call(), "off", LONGHAUL_ADVISOR=value)
        (self.home / "advisor" / "probe.json").unlink()
        self.denied(self.call(), "no-probe")
        self.probe("2.1.304")
        self.denied(self.call(), "not-enabled")  # the key is for 2.1.307's record
        self.enable("2.1.304")
        self.denied(self.call(), "version-mismatch")
        self.assertEqual(self.state(), {}, "nothing denied was counted")
        self.probe()
        self.enable()
        self.assertEqual(self.admitted(self.call())["updatedInput"]["model"], "opus")
        self.assertEqual(self.trace()[0]["kind"], "hard", "off is no fault of the brief")

    def test_a_probe_record_counts_only_with_schema_1_a_version_and_all_five_checks_true(self):
        path = self.home / "advisor" / "probe.json"
        good = json.loads(path.read_text())
        broken = ["not json", "[]", json.dumps({**good, "schema": 2}), json.dumps({**good, "schema": True}),
                  json.dumps({k: v for k, v in good.items() if k != "claude_version"}),
                  json.dumps({**good, "claude_version": "2.1"}), json.dumps({**good, "checks": None}),
                  json.dumps({**good, "checks": {**good["checks"], "deny": "true"}})]
        broken += [json.dumps({**good, "checks": {**good["checks"], check: False}}) for check in longhaul.ADVISOR_CHECKS]
        broken += [json.dumps({**good, "checks": {k: v for k, v in good["checks"].items() if k != check}})
                   for check in longhaul.ADVISOR_CHECKS]
        for text in broken:
            path.write_text(text)
            self.denied(self.call(), "no-probe")
        path.unlink()
        self.denied(self.call(), "no-probe")
        self.assertEqual(longhaul.ADVISOR_CHECKS, ("recognise", "deny", "prompt_id", "model_rewrite", "read_only"))

    def test_the_running_version_is_the_last_one_in_the_real_path_of_the_execpath(self):
        link = self.tmp / "bin" / "claude"
        link.parent.mkdir()
        link.symlink_to(self.exe)  # the terminal's ~/.local/bin/claude, a link to the version's file
        self.admitted(self.call(), CLAUDE_CODE_EXECPATH=str(link))
        panel = self.tmp / "ext" / "anthropic.claude-code-{}-darwin-arm64" / "resources" / "native-binary" / "claude"
        for version in ("2.1.307", "2.1.304"):
            path = Path(str(panel).format(version))
            path.parent.mkdir(parents=True)
            path.write_text("")
        self.admitted(self.call(), CLAUDE_CODE_EXECPATH=str(panel).format("2.1.307"))
        reason = self.denied(self.call(), "version-mismatch", CLAUDE_CODE_EXECPATH=str(panel).format("2.1.304"))
        self.assertIn("2.1.307", reason)
        self.assertIn("2.1.304", reason)
        self.assertEqual(longhaul.advisor_version_in("/a/claude-code-2.1.304-darwin/x/2.1.9/claude"), "2.1.9")
        self.assertIsNone(longhaul.advisor_version_in("/a/claude-2.1/claude"))

    def test_with_no_version_in_the_path_claude_is_asked_once_and_the_answer_cached(self):
        # F11, item 14: on a loaded machine the fake can take past 2 s to
        # start, and its call is then denied. The timeout is the slow
        # case's to test, so every other call here gets 10 s.
        patched = mock.patch.object(longhaul, "ADVISOR_VERSION_TIMEOUT", 10)
        patched.start()
        self.addCleanup(patched.stop)
        exe, mark = self.fake_claude()
        self.assertIsNone(longhaul.advisor_version_in(str(exe)), "the fixture's path names no version")
        self.admitted(self.call(prompt="p1"), CLAUDE_CODE_EXECPATH=str(exe))
        self.admitted(self.call(prompt="p2"), CLAUDE_CODE_EXECPATH=str(exe))
        self.assertEqual(mark.read_text(), "--version\n", "asked once")
        cache = self.home / "advisor" / "version-cache.json"
        self.assertEqual(json.loads(cache.read_text()), {"realpath": str(exe), "mtime_ns": exe.stat().st_mtime_ns,
                                                         "size": exe.stat().st_size, "version": "2.1.307"})
        self.assertEqual(oct(cache.stat().st_mode & 0o777), oct(0o600))
        os.utime(exe, (self.now - 60, self.now - 60))  # an update in place: asked again
        self.admitted(self.call(prompt="p3"), CLAUDE_CODE_EXECPATH=str(exe))
        self.assertEqual(mark.read_text(), "--version\n--version\n")
        same = exe.stat().st_mtime_ns  # F7: another build with the same time, a size apart: asked again
        with exe.open("a") as f:
            f.write("# another build\n")
        os.utime(exe, ns=(same, same))
        self.admitted(self.call(prompt="p4"), CLAUDE_CODE_EXECPATH=str(exe))
        self.assertEqual(mark.read_text(), "--version\n" * 3)
        for name, says, code in (("junk", "Claude Code", 0), ("fails", "2.1.307 (Claude Code)", 1)):
            other, ran = self.fake_claude(name, says, code)
            self.denied(self.call(), "version-unknown", CLAUDE_CODE_EXECPATH=str(other))
            self.denied(self.call(), "version-unknown", CLAUDE_CODE_EXECPATH=str(other))
            self.assertEqual(ran.read_text(), "--version\n--version\n", "a failure is not cached")
        slow, _ = self.fake_claude("slow", "2.1.307")
        slow.write_text("#!/bin/sh\nsleep 5\necho 2.1.307\n")
        started = time.time()
        with mock.patch.object(longhaul, "ADVISOR_VERSION_TIMEOUT", 0.3):
            self.denied(self.call(), "version-unknown", CLAUDE_CODE_EXECPATH=str(slow))
        self.assertLess(time.time() - started, 3, "the hook's own timeout is far off")

    def test_an_unknown_or_another_version_is_denied(self):
        for exe in (None, "", str(self.tmp / "nowhere" / "claude"), str(self.tmp / "s1.jsonl")):
            self.denied(self.call(), "version-unknown", CLAUDE_CODE_EXECPATH=exe)
        other = self.tmp / "claude" / "versions" / "2.1.308"
        other.write_text("")
        reason = self.denied(self.call(), "version-mismatch", CLAUDE_CODE_EXECPATH=str(other))
        self.assertIn("2.1.307", reason)
        self.assertIn("2.1.308", reason)

    def stamped(self, *versions, model="claude-sonnet-4-5"):
        """The session's transcript as Claude Code writes it, each line naming
        the version that wrote it: an assistant line on `model` per version
        given, None a line with no version."""
        lines = [{"type": "assistant", "message": {"model": model, "role": "assistant", "content": []},
                  **({} if version is None else {"version": version})} for version in versions]
        self.transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_the_version_is_the_transcripts_then_the_paths_then_what_claude_says(self):
        # F7: the process writing the transcript names its version on each
        # line, whatever binary is on disk now. The last line that names one
        # decides, read from the tail the executor check reads, and once.
        older = self.tmp / "claude" / "versions" / "2.1.304"
        older.write_text("")
        self.stamped("2.1.306", "2.1.307", None)
        with mock.patch("builtins.open", wraps=open) as opened:
            self.admitted(self.call(prompt="p1"), CLAUDE_CODE_EXECPATH=str(older))
        self.assertEqual(sum(1 for c in opened.call_args_list if c.args[:1] == (str(self.transcript),)), 1, "read once")
        exe, mark = self.fake_claude()
        self.stamped("2.1.307", "2.1.308")
        self.assertIn("2.1.308", self.denied(self.call(prompt="p2"), "version-mismatch", CLAUDE_CODE_EXECPATH=str(exe)))
        self.denied(self.call(prompt="p2"), "version-mismatch")  # the path's 2.1.307 does not outrank it
        self.assertFalse(mark.exists(), "the transcript named it: claude was not asked")
        for junk in ("2.1.307-dev", "2.1", 2107, ""):  # the last one named is no x.y.z: unknown, never an older line's
            self.stamped("2.1.307", junk)
            self.denied(self.call(prompt="p3"), "version-unknown")
        # No line names one: the path's, and when that is not the record's
        # (an npm or nvm path names node's), claude is asked before a denial.
        self.stamped(None, None)
        self.admitted(self.call(prompt="p4"))
        nvm = self.tmp / "nvm" / "versions" / "node" / "v20.11.1" / "bin" / "claude"
        nvm.parent.mkdir(parents=True)
        shutil.copy2(exe, nvm)
        with mock.patch.object(longhaul, "ADVISOR_VERSION_TIMEOUT", 10):
            self.admitted(self.call(prompt="p5"), CLAUDE_CODE_EXECPATH=str(nvm))
            self.admitted(self.call(prompt="p6"), CLAUDE_CODE_EXECPATH=str(nvm))
            self.assertEqual(mark.read_text(), "--version\n", "asked once, then cached")
            nvm.write_text("#!/bin/sh\nexit 1\n")  # one that cannot say: the path's version, and denied
            self.assertIn("20.11.1", self.denied(self.call(prompt="p7"), "version-mismatch", CLAUDE_CODE_EXECPATH=str(nvm)))
            match = self.tmp / "v" / "2.1.307" / "claude"  # a path naming the record's version is taken as it is
            match.parent.mkdir(parents=True)
            shutil.copy2(self.fake_claude("other", "2.1.308 (Claude Code)")[0], match)
            self.admitted(self.call(prompt="p7"), CLAUDE_CODE_EXECPATH=str(match))
        self.assertFalse((self.tmp / "other.ran").exists(), "not asked")

    def test_the_executor_is_the_last_assistant_line_in_the_transcripts_tail(self):
        self.executor("claude-opus-4-5")
        with self.transcript.open("a") as f:  # neither a subagent's line nor the engine's own is the executor
            f.write(json.dumps({"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-4-5"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"model": "<synthetic>"}}) + "\n")
            f.write("not json\n")
        self.assertEqual(self.admitted(self.call())["updatedInput"]["model"], "fable")
        self.assertEqual(self.trace()[-1]["executor"], "opus")
        # Past the last 256 KB the transcript is not read: the line there is
        # not the executor's latest.
        self.executor("claude-haiku-4-5")
        with self.transcript.open("a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "y" * 300_000}}) + "\n")
        self.denied(self.call(), "executor-unknown")
        self.executor("claude-haiku-4-5", before=json.dumps({"type": "user", "message": {"content": "y" * 300_000}}).encode() + b"\n")
        self.assertEqual(self.admitted(self.call())["updatedInput"]["model"], "opus")
        for path in (self.tmp / "missing.jsonl", None, 7):
            data = self.call()
            data["transcript_path"] = str(path) if isinstance(path, Path) else path
            self.denied(data, "executor-unknown")
        self.transcript.write_text(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        self.denied(self.call(), "executor-unknown")

    def test_a_subagent_model_forced_by_claude_codes_variables_is_denied(self):
        # F14: each of these makes Claude Code choose a subagent's model over
        # the one the hook sets, so with any of them set, even empty, the
        # advisor's model is not D3's and nothing is admitted.
        self.assertEqual(longhaul.ADVISOR_FORCED_MODEL_VARS, self.FORCED)
        for name in self.FORCED:
            for value in ("opus", "fable", ""):
                reason = self.denied(self.call(prompt="p1"), "model-forced", **{name: value})
                self.assertIn(name, reason)
                self.assertEqual(self.trace()[-1]["kind"], "hard")
        reason = self.denied(self.call(prompt="p1"), "model-forced", **dict.fromkeys(self.FORCED, "1"))
        self.assertIn(", ".join(self.FORCED), reason, "each one set is named")
        self.assertEqual(self.state(), {}, "nothing counted")
        self.admitted(self.call(prompt="p1"))
        # status says so, after the version and before the rewrites
        _, last = self.status(CLAUDE_CODE_SUBAGENT_MODEL="opus")
        self.assertEqual(last, "off: Claude Code is set to choose subagents' model itself (CLAUDE_CODE_SUBAGENT_MODEL)")
        _, last = self.status(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_EXECPATH=None)
        self.assertEqual(last, "off: the running Claude Code version is unknown")

    def test_d3_names_the_advisor_and_never_a_substitute(self):
        self.assertEqual(longhaul.ADVISOR_FOR, {"haiku": "opus", "sonnet": "opus", "opus": "fable"})
        for n, (model, family, advisor) in enumerate((
                ("claude-haiku-4-5", "haiku", "opus"), ("claude-sonnet-4-5", "sonnet", "opus"),
                ("claude-opus-4-5", "opus", "fable"), ("claude-opus-5-5", "opus", "fable"),
                ("claude-fable-5", "fable", None), ("claude-mythos-1", "mythos", None), ("gpt-5", "other", None))):
            self.executor(model)
            data = self.call(session=f"s{n}")
            if advisor:
                self.assertEqual(self.admitted(data)["updatedInput"]["model"], advisor, model)
            else:
                self.assertIn(f"({family})", self.denied(data, "no-stronger-advisor"), model)
            self.assertEqual((self.trace()[-1]["executor"], self.trace()[-1]["advisor"]), (family, advisor), model)

    def test_only_an_advisor_model_whose_rewrite_the_probe_saw_is_admitted(self):
        # F5: the record lists the aliases a probe saw take effect (the
        # subagent's meta.json model). The 2.1.307 record has no list, so
        # it admits nothing until a new probe passes: the closed state.
        self.assertEqual(longhaul.ADVISOR_REWRITES, ("haiku", "sonnet", "opus", "fable"))
        self.probe(rewrites=None)
        reason = self.denied(self.call(), "model-unprobed")
        self.assertIn("the opus advisor", reason)
        self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.probe(rewrites=["opus"])
        self.admitted(self.call())
        self.executor("claude-opus-4-5")
        self.assertIn("the fable advisor", self.denied(self.call(prompt="p2"), "model-unprobed"))
        self.executor("claude-sonnet-4-5")
        path = self.home / "advisor" / "probe.json"
        good = json.loads(path.read_text())
        for junk in ("opus", None, ["opus", "gpt"], ["Opus"], [["opus"]], {"opus": True}, [7]):
            path.write_text(json.dumps({**good, "rewrite_models": junk}))  # a list not in its shape is none
            self.denied(self.call(prompt="p3"), "model-unprobed")
        self.assertEqual(self.counted(), [("opus", "p1", False)], "nothing denied was counted")

    def test_a_call_of_another_agent_type_gets_no_answer_and_nothing_is_written(self):
        others = [self.call(agent="Explore"), self.call(agent="general-purpose"), self.call(agent=None),
                  {**self.call(), "tool_input": "x"}, {"tool_name": "Agent"}, [], "x", {}]
        for data in others:
            self.assertFalse(longhaul.advisor_consultation(data), data)
            out = self.run_hook(self.HOOK, json.dumps(data))
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""), data)
        out = self.run_hook(self.HOOK, "")
        self.assertEqual((out.returncode, out.stdout), (0, ""))
        written = sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob("*") if p.is_file())
        self.assertEqual(written, ["advisor/enabled.json", "advisor/probe.json", "usage/main.json"], "the fixture's own files alone")
        # The agent type's name, however it is cased or spaced, is a consultation.
        self.assertTrue(longhaul.advisor_consultation(self.call(agent=" Longhaul:Advisor ")))

    def test_a_consultation_is_the_advisors_name_or_the_bare_advisor_however_it_is_spaced(self):
        # F6: lowercased with every space taken out. Another plugin's
        # other:advisor is not ours and passes through as any agent type.
        for agent in ("longhaul:advisor", " Longhaul : Advisor ", "LONGHAUL:ADVISOR", "advisor", "Advisor", "\tad visor\n"):
            self.assertTrue(longhaul.advisor_consultation(self.call(agent=agent)), agent)
        for agent in ("other:advisor", "advisors", "longhaul:advisor2", "longhaul-advisor", "Explore", "", None, 7):
            self.assertFalse(longhaul.advisor_consultation(self.call(agent=agent)), agent)
        self.assertEqual(longhaul.ADVISOR_NAMES, ("longhaul:advisor", "advisor"))
        # A bare advisor goes through the whole gate, and the call admitted names the plugin's agent type.
        self.denied(self.call(agent="Advisor", prompt=None) | {"transcript_path": None}, "executor-unknown")
        out = self.admitted(self.call(agent=" advisor"))
        self.assertEqual(out["updatedInput"]["subagent_type"], "longhaul:advisor")
        other = self.call("Look at the parser's tests.", agent="other:advisor")
        self.assertIsNone(self.decide(other), "another plugin's advisor: no answer")

    def test_a_brief_sent_to_any_other_agent_type_is_denied_whether_or_not_the_experiment_is_on(self):
        # F6: a stand-in. The prompt's first non-empty line begins Trigger:,
        # the brief's shape; a call that only mentions one is left alone.
        for agent, brief in (("Explore", self.brief()), ("general-purpose", "\n\n  Trigger: stuck\nTask: x"),
                             (None, "trigger : done\nTask: y"), ("other:advisor", self.brief("early"))):
            for env in ({}, {"LONGHAUL_ADVISOR": None}):
                reason = self.denied(self.call(brief, agent=agent), "stand-in", **env)
                self.assertIn("longhaul:advisor", reason)
                self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.assertEqual(self.state(), {}, "nothing counted")
        for prompt in ("Find the parser. Trigger: stuck", "Note: first\nTrigger: stuck", "", None, 7):
            data = self.call(agent="Explore")
            data["tool_input"]["prompt"] = prompt
            self.assertIsNone(self.decide(data), prompt)
        out = self.run_hook(self.HOOK, self.call(self.brief(), agent="Explore"), LONGHAUL_ADVISOR="off")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("denied (stand-in)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])

    def workflow(self, tool_input, session="s1"):
        """A PreToolUse input for a Workflow call. Its hook input is not yet
        probed (F17): the fields an Agent call's input comes with, and the
        tool's name."""
        return {"session_id": session, "prompt_id": "p1", "tool_use_id": f"toolu_{uuid.uuid4().hex[:12]}",
                "transcript_path": str(self.transcript), "cwd": str(self.tmp), "hook_event_name": "PreToolUse",
                "tool_name": "Workflow", "tool_input": tool_input}

    def test_a_workflow_that_mentions_an_advisor_is_denied_and_any_other_passes(self):
        # F17: a Workflow's script may start any agent, the advisor among
        # them, past the gate. Its input is not probed, so it is read as
        # text: advisor in any case, anywhere in its JSON, is a stand-in,
        # whether or not the experiment is on. It is never a consultation,
        # even in an Agent call's shape, so nothing is counted or admitted.
        script = "const a = await agent({subagent_type: 'longhaul:advisor', prompt: brief})"
        for tool_input in ({"script": script}, {"name": "review", "args": {"agents": ["Explore", "ADVISOR"]}},
                           {"Advisor": True}, "ask the advisor", ["x", {"y": "an advisory note"}],
                           {"description": "Consult the advisor", "prompt": self.brief(), "subagent_type": "longhaul:advisor"}):
            data = self.workflow(tool_input)
            self.assertFalse(longhaul.advisor_consultation(data), tool_input)
            for env in ({}, {"LONGHAUL_ADVISOR": None}):
                reason = self.denied(data, "stand-in", **env)
                self.assertIn("/longhaul:advise", reason)
                self.assertIn("workflow", reason)
                self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.assertEqual(self.state(), {}, "nothing counted")
        before = self.trace()
        for tool_input in ({"script": "await agent({subagent_type: 'Explore', prompt: 'find the parser'})"}, {}, None,
                           "x", [], 7):
            self.assertIsNone(self.decide(self.workflow(tool_input)), tool_input)
        # Its result is not a consultation's either: nothing is traced.
        self.assertIsNone(self.result(self.workflow({"script": script}), self.reply("Verdict: proceed")))
        self.assertEqual(self.trace(), before, "nothing written for a workflow that passes, nor for a result")
        # Through the hook as the plugin runs it: the name is read once the
        # JSON's escapes are, so an escaped letter does not hide it.
        raw = json.dumps(self.workflow({"script": "agent({subagent_type: 'ADVISOR'})"})).replace("ADVISOR", "ADV\\u0049SOR")
        self.assertNotIn("ADVISOR", raw)
        out = self.run_hook(self.HOOK, raw)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("denied (stand-in)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.run_hook(self.HOOK, self.workflow({"script": "agent({subagent_type: 'Explore'})"}))
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_a_message_to_an_advisor_is_denied_and_any_other_passes(self):
        # F2: a SendMessage resumes the advisor past every check. advisor-result
        # keeps its agent id: the Agent tool's response names it (agentId, on
        # 2.1.307), and so does the subagent's meta.json whose toolUseId is
        # the call's; a meta.json of the advisor's agent type names one too.
        # Its to and its recipient are checked (their other shapes: F19).
        # Outside the order, and whether or not it is on.
        data = self.call(tool_id="toolu_f1")
        self.admitted(data)
        meta = self.subagent("a1f2e3", "toolu_f1", model="opus")
        self.subagent("b77", "toolu_other", agent_type="Explore")
        self.result(data, self.reply("Verdict: proceed\nConfidence: high"))
        self.assertEqual(self.state()["sessions"]["s1"]["agents"], ["a1f2e3"])
        for agent in ("abeef7f4ab1007404", "../x", 7, None):
            self.result(data, self.reply("Verdict: proceed\nConfidence: high", agent=agent))
        self.assertEqual(self.state()["sessions"]["s1"]["agents"], ["a1f2e3", "abeef7f4ab1007404"], "each kept once")
        meta.unlink()  # the record alone names them now
        for to, session in (("a1f2e3", "s1"), (" a1f2e3 ", "s1"), ("a1f2e3 [3fa9c1]", "s1"), ("a1f2e3", "s2"),
                            ("abeef7f4ab1007404", "s1")):
            reason = self.denied(self.message(to, session), "follow-up", LONGHAUL_ADVISOR=None)
            self.assertIn("/longhaul:advise", reason)
            self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.denied(self.message("main", recipient="abeef7f4ab1007404"), "follow-up")
        self.subagent("c9", "toolu_never")  # an advisor whose result was never traced
        self.denied(self.message("c9"), "follow-up")
        for to in ("longhaul:advisor", " Advisor "):  # the agent type's own name, as a consultation is named
            self.denied(self.message(to), "follow-up")
        before = self.trace()
        for to in ("b77", "main", "researcher", "worker [3fa9c1]", "zz", "../c9", "", None, 7, "other:advisor"):
            self.assertIsNone(self.decide(self.message(to)), to)
        self.assertIsNone(self.decide(self.message("main", recipient={"id": "b77"}, recipient_kind="agent")),
                          "an object's values are read, and b77 is no advisor")
        self.assertIsNone(self.decide({**self.message("c9"), "transcript_path": None}), "no folder to look in")
        self.assertEqual(self.trace(), before, "nothing written for a message that passes")
        self.assertIsNone(self.result(self.message("a1f2e3"), self.reply("ok")))
        self.assertEqual(self.trace(), before, "nor for its result")
        out = self.run_hook(self.HOOK, self.message("a1f2e3"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("denied (follow-up)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.run_hook(self.HOOK, self.message("b77"))
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        written = "".join(p.read_text(errors="replace") for p in self.home.rglob("*") if p.is_file())
        self.assertNotIn(self.S, written)

    def test_a_state_that_cannot_be_read_still_denies_a_message_to_an_advisor_and_passes_any_other(self):
        # F18: a junk state.json no longer blocks every message. The advisor's
        # name and a meta.json of its agent type still deny one; an id the
        # record alone names cannot be told from any other, so it passes, as
        # every other message does. A consultation is still denied (error).
        data = self.call(tool_id="toolu_f18")
        self.admitted(data)
        self.subagent("a1f2e3", "toolu_f18", model="opus")
        self.subagent("b77", "toolu_other", agent_type="Explore")
        self.result(data, self.reply("Verdict: proceed\nConfidence: high", agent="abeef7f4ab1007404"))
        self.assertEqual(self.state()["sessions"]["s1"]["agents"], ["abeef7f4ab1007404", "a1f2e3"])
        path = self.home / "advisor" / "state.json"
        for text in ("not json", "[]", '{"sessions": []}', self.DEEP):
            path.write_text(text)
            for to in ("a1f2e3", "longhaul:advisor", " Advisor "):
                self.denied(self.message(to), "follow-up")
            before = self.trace()
            for to in ("abeef7f4ab1007404", "zz", "b77", "main"):
                self.assertIsNone(self.decide(self.message(to)), (text[:20], to))
            self.assertEqual(self.trace(), before, "nothing written for a message that passes")
        path.write_text("not json")
        out = self.run_hook(self.HOOK, self.message("zz"))
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        out = self.run_hook(self.HOOK, self.message("a1f2e3"))
        self.assertIn("denied (follow-up)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.run_hook(self.HOOK, self.call())
        self.assertIn("denied (error)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_message_names_its_target_in_any_of_its_shapes(self):
        # F19: to as a list (each text in it), recipient as an object (its
        # values that are text), in any case, with agent- in front or a
        # [ref] after. The record names one advisor and a meta.json another.
        data = self.call(tool_id="toolu_f19")
        self.admitted(data)
        self.result(data, self.reply("Verdict: proceed\nConfidence: high", agent="abeef7f4ab1007404"))
        self.subagent("c9d8", "toolu_never")
        self.subagent("b77", "toolu_other", agent_type="Explore")
        for to, fields in ((["main", "abeef7f4ab1007404"], {}), (["b77", " C9D8 [3fa9c1]"], {}), ([7, None, "c9d8"], {}),
                           (["ADVISOR"], {}), ("main", {"recipient": {"id": "abeef7f4ab1007404", "kind": "agent"}}),
                           ("main", {"recipient": {"id": "b77", "name": "Longhaul:Advisor"}}),
                           ("ABEEF7F4AB1007404", {}), ("agent-abeef7f4ab1007404", {}), ("Agent-C9D8", {}),
                           ("AGENT-c9d8 [3fa9c1]", {}), ("main", {"recipient": "Agent-ABEEF7F4AB1007404"})):
            self.denied(self.message(to, **fields), "follow-up", LONGHAUL_ADVISOR=None)
        before = self.trace()
        for to, fields in ((["main", "b77"], {}), ([], {}), ({"x": 7}, {}), ("agent-b77", {}), ("agent-", {}),
                           ("main", {"recipient": {"id": "B77", "n": 7}}), ("main", {"recipient": ["worker", None]})):
            self.assertIsNone(self.decide(self.message(to, **fields)), (to, fields))
        self.assertEqual(self.trace(), before, "nothing written for a message that passes")

    def test_a_brief_in_markdown_or_after_a_byte_order_mark_is_still_a_stand_in(self):
        # F19: a byte-order mark and any #, *, -, >, backtick or space before
        # the first non-empty line's Trigger: are set aside, and a line of
        # those alone (a fence, a rule) is passed over. The fail-closed
        # choice: Markdown's emphasis between the word and its colon too.
        for first in ("﻿Trigger: stuck", "## Trigger: stuck", "**Trigger:** stuck", "**Trigger**: stuck",
                      "- Trigger: done", "> Trigger: early", "`Trigger: change`", "```\nTrigger: stuck",
                      "---\n\n> **Trigger:** stuck", "﻿\n  # trigger : done", "*`-> Trigger:"):
            brief = first + "\nTask: x\nQuestion: y"
            for env in ({}, {"LONGHAUL_ADVISOR": None}):
                self.denied(self.call(brief, agent="Explore"), "stand-in", **env)
        before = self.trace()
        for prompt in ("## Notes\nTrigger: stuck", "Triggers: stuck", "`code` Trigger: stuck", "#Trigger stuck", "```"):
            self.assertIsNone(self.decide(self.call(prompt, agent="Explore")), prompt)
        self.assertEqual(self.trace(), before)

    def test_only_the_calls_known_fields_reach_claude_code(self):
        # F4: any other field (a background run, an isolation, a name) would
        # reach the engine unchecked. Fixable: drop them and call once more.
        self.assertEqual(longhaul.ADVISOR_CALL_FIELDS, ("description", "prompt", "subagent_type", "run_in_background", "model"))
        for change in ({"run_in_background": True}, {"run_in_background": "false"}, {"run_in_background": None},
                       {"isolation": "worktree"}, {"name": "helper"}, {"description": 7}):
            data = self.call()
            data["tool_input"].update(change)
            reason = self.denied(data, "call-fields")
            self.assertIn("Drop every other field", reason)
            self.assertEqual(self.trace()[-1]["kind"], "fixable", change)
            self.assertNotIn("worktree", reason + json.dumps(self.trace()[-1]), "a field's value is never echoed")
        self.assertIn("call-fields", longhaul.ADVISOR_FIXABLE)
        self.assertEqual(self.state(), {}, "nothing counted")
        # Built from scratch: the allowed keys alone, run_in_background false, the model overwritten.
        data = self.call()
        data["tool_input"]["model"] = "haiku"
        out = self.admitted(data)
        self.assertEqual(list(out["updatedInput"]), ["description", "prompt", "subagent_type", "run_in_background", "model"])
        self.assertEqual((out["updatedInput"]["run_in_background"], out["updatedInput"]["model"]), (False, "opus"))
        data = self.call()
        del data["tool_input"]["run_in_background"], data["tool_input"]["description"]
        out = self.admitted(data)
        self.assertEqual(list(out["updatedInput"]), ["prompt", "subagent_type", "run_in_background", "model"])
        self.assertIs(out["updatedInput"]["run_in_background"], False, "set false where the call left it out")

    def test_one_agent_type_constant_and_region_bs_patterns_are_built_from_region_as_codes(self):
        # F6: the two regions cannot drift apart.
        self.assertFalse(hasattr(longhaul, "CONSULTATION_AGENT"), "ADVISOR_TYPE alone names the agent type")
        self.assertEqual(longhaul.ADVISOR_TRIGGER.pattern, r"Trigger:\s*(" + "|".join(longhaul.CONSULTATION_TRIGGERS) + ")")
        self.assertEqual(longhaul.ADVISOR_VERDICT.pattern, r"verdict:\s*(" + "|".join(longhaul.CONSULTATION_VERDICTS) + r")\b")
        self.assertTrue(longhaul.ADVISOR_VERDICT.flags & re.IGNORECASE)
        with mock.patch.object(longhaul, "advisor_path", return_value=self.tmp / "elsewhere.jsonl"):
            (self.tmp / "elsewhere.jsonl").write_text(json.dumps({"session": "s9", "event": "decision", "decision": "deny",
                                                                  "reason": "off", "trigger": None}) + "\n")
            self.assertEqual(longhaul.consultations("s9")["denied"], {"off": 1}, "inspect reads the trace advisor_path names")

    def test_an_error_while_checking_a_consultation_denies_it(self):
        # Fail closed (D2.1, the owner's boundary): a consultation the hook
        # cannot finish checking never runs; run_hook's silent exit would let it.
        (self.home / "advisor" / "trace.jsonl").mkdir()
        for raw in (json.dumps(self.call()), "{not json", json.dumps(self.call())[:-5]):
            out = self.run_hook(self.HOOK, raw)
            self.assertEqual(out.returncode, 0, out.stderr)
            answer = json.loads(out.stdout)["hookSpecificOutput"]
            self.assertEqual(answer["permissionDecision"], "deny", raw[:40])
            self.assertIn("denied (error)", answer["permissionDecisionReason"])
        self.assertIn("advisor hook: a consultation denied on an error (IsADirectoryError)", self.log())
        self.assertNotIn(self.S, self.log())
        # A trace that cannot be read leaves the counts unknown (F9): denied
        # before anything is counted. F10: the state is written before the
        # trace (conservative), so a consultation admitted and then denied
        # when its trace could not be written counts.
        self.assertEqual(self.state(), {}, "the trace could not be read, so nothing was counted")
        (self.home / "advisor" / "trace.jsonl").rmdir()
        with mock.patch.object(longhaul, "advisor_trace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.decide(self.call())
        self.assertEqual(len(self.counted()), 1, "a consultation whose trace failed still counts")
        out = self.run_hook(self.HOOK, self.call(agent="Explore"))
        self.assertEqual((out.returncode, out.stdout), (0, ""), "another agent type still runs: fail open")

    DEEP = "[" * 100_000 + "]" * 100_000  # RecursionError in json.loads on 3.9; 3.14 parses it

    def test_json_nested_past_the_recursion_limit_is_junk_wherever_the_advisor_reads_it(self):
        # F10: as Region A does, a RecursionError is a line that cannot be
        # read, never the hook's error: the transcript's tail, the trace
        # (its trim and the decision's lookup) and the probe's evidence.
        with self.transcript.open("a") as f:
            f.write('{"type": "assistant", "x": ' + self.DEEP + "}\n")
        data = self.call(tool_id="toolu_deep")
        path = self.home / "advisor" / "trace.jsonl"
        path.write_text(self.DEEP + "\nnot json\n" + '{"id": "toolu_deep", "x": ' + self.DEEP + "}\n")
        self.admitted(data)  # a trim at this write: the unreadable lines count as old
        self.assertEqual(len(path.read_text().splitlines()), 1, "the junk at the front went")
        path.write_text(self.DEEP + "\n" + path.read_text() + '{"id": "toolu_deep", "x": ' + self.DEEP + "}\n")
        self.result(data, self.reply("Verdict: proceed"), now=self.now + 5)
        self.assertEqual((self.trace_lines()[-1]["outcome"], self.trace_lines()[-1]["seconds"]), ("ok", 5.0))
        evidence = self.tmp / "deep.json"
        evidence.write_text(self.DEEP)
        out = self.command("record-probe", str(evidence))
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertRegex(out.stderr, r"not recorded: it is not (JSON|a JSON object)\n$", "3.9 cannot parse it; 3.14 can")

    def trace_lines(self):
        """The trace's lines that are JSON objects: a test may leave junk in it."""
        found = []
        for line in (self.home / "advisor" / "trace.jsonl").read_text().splitlines():
            with contextlib.suppress(ValueError, RecursionError):
                found.append(json.loads(line))
        return [line for line in found if isinstance(line, dict)]

    def test_the_tail_read_for_the_executor_is_at_least_200_kb(self):
        self.assertGreaterEqual(longhaul.ADVISOR_TAIL, 200 * 1024, "an assistant line can be long; a short tail misses it")

    def test_a_junk_version_cache_is_asked_past_and_written_over(self):
        # F11, item 13: a cache that cannot be trusted is no answer; claude
        # is asked, and the cache written whole again.
        exe, mark = self.fake_claude()
        cache = self.home / "advisor" / "version-cache.json"
        st = exe.stat()
        key = {"realpath": str(exe), "mtime_ns": st.st_mtime_ns, "size": st.st_size}
        junk = [b"not json", b"\xff\xfe", b"[]", b"null", self.DEEP.encode(),
                json.dumps({**key, "version": "2.1"}).encode(), json.dumps({**key, "version": None}).encode(),
                json.dumps({**key, "mtime_ns": str(st.st_mtime_ns), "version": "2.1.307"}).encode(),
                json.dumps({**key, "size": float(st.st_size), "version": "2.1.307"}).encode(),
                json.dumps({**key, "size": st.st_size + 1, "version": "2.1.307"}).encode(),
                json.dumps({**key, "realpath": str(self.exe), "version": "2.1.307"}).encode(),
                json.dumps({"realpath": str(exe), "mtime": st.st_mtime, "version": "2.1.307"}).encode()]  # before F7
        with mock.patch.object(longhaul, "ADVISOR_VERSION_TIMEOUT", 10):
            for n, raw in enumerate(junk, 1):
                cache.write_bytes(raw)
                self.admitted(self.call(session=f"s{n}"), CLAUDE_CODE_EXECPATH=str(exe))
                self.assertEqual(mark.read_text(), "--version\n" * n, raw[:40])
                self.assertEqual(json.loads(cache.read_text()), {**key, "version": "2.1.307"})
            self.admitted(self.call(session="s0"), CLAUDE_CODE_EXECPATH=str(exe))
        self.assertEqual(mark.read_text(), "--version\n" * len(junk), "the cache written over is the answer")

    def hold(self, name="advisor"):
        """The lock `name` held by this process, as another hook would hold it, until the file is closed."""
        f = open(self.home / f".{name}.lock", "a")
        fcntl.flock(f, fcntl.LOCK_EX)
        self.addCleanup(f.close)
        return f

    def test_the_lock_waits_at_most_its_deadline_and_others_wait_as_before(self):
        # Sprint 8, F3: a hook past its timeout lets the call run, so the
        # Advisor's waits are bounded; every other caller blocks as before.
        held = self.hold("x")
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            with longhaul.locked("x", wait=0.2):
                self.fail("the lock was had while another held it")
        self.assertLess(time.monotonic() - started, 1)
        threading.Timer(0.3, held.close).start()
        started = time.monotonic()
        with longhaul.locked("x"):  # no deadline: it waits for the other to let go
            self.assertGreaterEqual(time.monotonic() - started, 0.25)
        with longhaul.locked("x", wait=0.2):
            pass  # free again: had at once
        self.assertEqual(longhaul.ADVISOR_LOCK_WAIT, 2)

    def test_a_held_lock_denies_the_consultation_within_the_deadline_and_the_trace_failure_is_logged(self):
        self.hold()
        started = time.monotonic()
        out = self.run_hook(self.HOOK, self.call())
        took = time.monotonic() - started
        self.assertEqual(out.returncode, 0, out.stderr)
        [answer] = [json.loads(line) for line in out.stdout.splitlines()]  # one answer, never two
        self.assertIn("denied (error)", answer["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertLess(took, 2 * longhaul.ADVISOR_LOCK_WAIT + 3, "the lock for the count, then the one for the trace")
        self.assertIn("advisor hook: a consultation denied on an error (TimeoutError)", self.log())
        self.assertIn("advisor hook: the error's denial was not traced (TimeoutError)", self.log())
        self.assertEqual(self.trace(), [])

    def test_off_is_decided_from_the_environment_before_any_file_is_read(self):
        with mock.patch.object(longhaul, "read_json", side_effect=AssertionError("a file was read")), \
                mock.patch.object(longhaul, "advisor_running_version", side_effect=AssertionError("the version was asked")):
            self.denied(self.call(), "off", LONGHAUL_ADVISOR=None)

    def test_stdin_is_read_up_to_1_mb_and_past_it_every_call_is_denied(self):
        self.assertEqual(longhaul.ADVISOR_STDIN_MAX, 1_000_000, "run_hook's cap")
        big = self.call(self.brief(extra="x" * 1_000_000))
        out = self.run_hook(self.HOOK, big)
        self.assertEqual(out.returncode, 0, out.stderr)
        reason = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("denied (brief-too-long)", reason)
        self.assertEqual(self.trace()[-1]["reason"], "brief-too-long")
        other = self.call("y" * 1_000_000, agent="Explore")
        other["tool_input"]["description"] = "Explore the parser"
        out = self.run_hook(self.HOOK, other)
        # F21: past the cap the part the hook did not read could name the
        # advisor, so another agent type is denied too; no real call is
        # that large.
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("denied (brief-too-long)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        late = json.dumps({"session_id": "s1", "tool_use_id": "toolu_f21", "hook_event_name": "PreToolUse",
                           "tool_name": "Agent", "tool_input": {"description": "d", "prompt": "z" * 1_000_100,
                                                                "subagent_type": "longhaul:advisor"}})
        self.assertNotIn("advisor", late[:1_000_001], "the advisor is named only past the part read")
        out = self.run_hook(self.HOOK, late)
        self.assertIn("denied (brief-too-long)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.run_hook(self.RESULT, {**big, "hook_event_name": "PostToolUse", "tool_response": self.reply("x")})
        self.assertEqual((out.returncode, out.stdout), (0, ""))
        self.assertIn("advisor-result hook: no outcome traced (over 1,000,000 characters)", self.log())

    def test_past_the_cap_the_advisor_is_named_in_any_case(self):
        # F20: the over-cap check reads the input lowercased, so an advisor
        # named in capitals alone is denied too; a check that read it as
        # written would let this call run. The name is in the first 1 MB,
        # the part the hook reads.
        for agent, description in (("Longhaul:Advisor", "Consult the ADVISOR"), ("ADVISOR", "Ask it")):
            big = {"session_id": "s1", "tool_use_id": "toolu_f20", "hook_event_name": "PreToolUse", "tool_name": "Agent",
                   "tool_input": {"subagent_type": agent, "description": description, "prompt": "x" * 1_000_001}}
            raw = json.dumps(big)
            self.assertNotIn("advisor", raw)
            out = self.run_hook(self.HOOK, raw)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("denied (brief-too-long)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
            self.assertEqual(self.trace()[-1]["reason"], "brief-too-long")

    def test_the_checks_run_in_the_documented_order(self):
        # Every check failing at once, then put right one at a time: each
        # answer names the next check down the list, never a later one.
        said = []

        def step(data, **env):
            answer = self.decide(data, **env)["hookSpecificOutput"]
            said.append(self.trace()[-1]["reason"] if answer["permissionDecision"] == "deny" else "allow")

        def call(brief, prompt="p9", extra=True, **kw):
            data = self.call(brief, prompt=prompt, **kw)
            data["tool_input"].update({"isolation": "worktree"} if extra else {})
            return data

        first = self.brief()
        self.admitted(self.call(first, prompt="p0"))  # in the session's hashes from now on
        state = self.state()
        state["sessions"]["s1"]["admitted"] = [{"id": None, "advisor": "opus", "prompt_id": p, "reconcile": False}
                                               for p in ("p1", "p1", "p2", "p3", "p4", "p0")]
        (self.home / "advisor" / "state.json").write_text(json.dumps(state))
        (self.home / "advisor" / "probe.json").unlink()
        (self.home / "advisor" / "enabled.json").unlink()
        self.transcript.unlink()
        self.reading(five=97)
        (self.home / "accounts.json").write_text(json.dumps([{"name": "main", "repositories": [str(self.tmp / "elsewhere")]}]))
        bad = "Reconcile: yes\nfindings in sk-ant-x\n" + "x" * 9_000
        around = call(bad, None)
        around["cwd"] = str(self.tmp)  # the home is inside it (F13)
        forced = {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}  # F14
        marked = json.loads(json.dumps(state))
        marked["sessions"]["s1"]["mismatch"] = {"decided": "opus", "seen": "sonnet"}
        (self.home / "advisor" / "state.json").write_text(json.dumps(marked))
        step(around, LONGHAUL_ADVISOR=None, CLAUDE_CODE_EXECPATH=None, **forced)
        step(around, CLAUDE_CODE_EXECPATH=None, **forced)
        step(call(bad, None), CLAUDE_CODE_EXECPATH=None, **forced)
        self.enable()
        step(call(bad, None), CLAUDE_CODE_EXECPATH=None, **forced)
        self.probe()
        step(call(bad, None), CLAUDE_CODE_EXECPATH=None, **forced)
        step(call(bad, None), CLAUDE_CODE_EXECPATH=str(self.tmp / "versions" / "2.1.304" / "claude"), **forced)
        step(call(bad, None), **forced)
        step(call(bad, None))
        (self.home / "advisor" / "state.json").write_text(json.dumps(state))
        step(call(bad, None))
        self.executor("claude-fable-5")
        step(call(bad, None))
        self.executor("claude-sonnet-4-5")
        self.probe(rewrites=["fable"])
        step(call(bad, None))
        self.probe()
        step(call(bad, None))
        (self.home / "accounts.json").unlink()
        step(call(bad, None))
        step(call(bad, None, extra=False))
        bad = "Reconcile: yes\nfindings in sk-ant-x"
        step(call(bad, None, extra=False))
        bad = "Reconcile: yes\nTask: t\nConstraints: c\nFindings: sk-ant-x\nApproach: a\nQuestion: q"
        step(call(bad, None, extra=False))
        bad = "Trigger: stuck\n" + bad
        step(call(bad, None, extra=False))
        bad = bad.replace("sk-ant-x", "f.py:1-9")
        step(call(bad, None, extra=False))
        step(call(bad, extra=False))
        step(call(first, extra=False))
        step(call(self.brief(), "p1", extra=False))
        step(call(self.brief(), extra=False))
        step(call(self.brief(), session="s2", extra=False))
        self.reading(five=40, at=self.now - 3_600)
        step(call(self.brief(), session="s2", extra=False))
        self.reading(five=40)
        step(call(self.brief(), session="s2", extra=False))
        self.assertEqual(said, ["off", "home-untrusted", "not-enabled", "no-probe", "version-unknown", "version-mismatch",
                                "model-forced", "model-mismatch",
                                "executor-unknown", "no-stronger-advisor", "model-unprobed", "repository", "call-fields",
                                "brief-too-long", "brief-sections", "trigger-missing", "credential", "task-unknown",
                                "reconcile-invalid", "repeat", "cap-task", "cap-session", "near-full", "allowance-unknown",
                                "allow"])
        self.assertEqual([line["kind"] for line in self.trace()[1:-1]],
                         ["hard"] * 12 + ["fixable"] * 5 + ["hard"] + ["fixable"] + ["hard"] * 5, "D2.6's two kinds of denial")

    def test_the_brief_is_at_most_8000_characters(self):
        base = self.brief()
        exact = base + "x" * (8_000 - len(base))
        self.admitted(self.call(exact))
        self.assertEqual(self.trace()[-1]["brief_chars"], 8_000)
        reason = self.denied(self.call(exact + "y"), "brief-too-long")
        self.assertIn("8,001", reason)
        self.assertIn("8,000", reason)
        self.assertEqual(self.trace()[-1]["kind"], "fixable")

    def test_each_section_must_have_its_line(self):
        for section in longhaul.ADVISOR_SECTIONS:
            self.assertIn(f"starting {section}", self.denied(self.call(self.brief(drop=[section])), "brief-sections"))
        self.assertIn("starting Task: or Question:",
                      self.denied(self.call(self.brief(drop=["Task:", "Question:"])), "brief-sections"))
        self.assertEqual(longhaul.ADVISOR_SECTIONS, ("Task:", "Constraints:", "Findings:", "Approach:", "Question:"))
        self.admitted(self.call("  " + self.brief().replace("\n", "\n  ")))  # an indented line counts

    def test_the_first_line_names_the_trigger(self):
        for trigger in ("early", "stuck", "change", "done"):
            self.admitted(self.call(self.brief(trigger), prompt=trigger))
            self.assertEqual(self.trace()[-1]["trigger"], trigger)
        body = self.brief().split("\n", 1)[1]
        for brief in (body, f"{body}\nTrigger: stuck", f"Trigger: later\n{body}", f"trigger: stuck\n{body}",
                      f"Trigger: stuck now\n{body}", f"Note: first\nTrigger: stuck\n{body}"):
            self.assertIn("first line", self.denied(self.call(brief), "trigger-missing"))
            self.assertIsNone(self.trace()[-1]["trigger"])
        self.admitted(self.call("\n\n" + self.brief("done"), prompt="blank"))  # a blank line before it is no line

    def test_a_credential_in_the_brief_is_denied(self):
        for shape in longhaul.CHECKPOINT_SECRET_SHAPES:
            reason = self.denied(self.call(self.brief(extra=f" the key is {shape}0123456789abcdef")), "credential")
            self.assertIn("Remove the credential from the brief", reason)
            self.assertNotIn(shape, reason)
            self.assertNotIn(shape, json.dumps(self.trace()[-1]))
            self.assertEqual(self.trace()[-1]["kind"], "fixable")
        self.assertEqual(self.state(), {}, "the advisor never ran: nothing counted")

    def test_a_reconcile_call_only_after_a_consultation_in_the_same_task(self):
        self.assertIn("same task", self.denied(self.call(self.brief(reconcile=True)), "reconcile-invalid"))
        self.assertEqual(self.trace()[-1]["kind"], "fixable")
        self.admitted(self.call())
        self.denied(self.call(self.brief(reconcile=True), prompt="p2"), "reconcile-invalid")
        self.admitted(self.call(self.brief(reconcile=True)))
        self.assertTrue(self.trace()[-1]["reconcile"])
        self.assertEqual(self.counted(), [("opus", "p1", False), ("opus", "p1", True)])
        # With no prompt_id there is no task for it to follow: since F9 the
        # call is denied before the reconcile rule is reached.
        self.denied(self.call(self.brief(reconcile=True), prompt=None), "task-unknown")
        self.assertEqual(len(self.counted()), 2)
        self.assertTrue(longhaul.advisor_reconcile("Trigger: done\n  reconcile:  YES \n"))
        self.assertFalse(longhaul.advisor_reconcile("Trigger: done\nReconcile: no\nTask: reconcile: yes"))

    def test_the_same_brief_twice_in_a_session_is_a_repeat(self):
        first = self.brief()
        self.admitted(self.call(first))
        self.assertIn("earlier advice", self.denied(self.call(first, prompt="p2"), "repeat"))
        self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.admitted(self.call(first, session="s2"))  # another session's hashes are its own
        entry = self.state()["sessions"]["s1"]
        self.assertEqual(entry["hashes"], [self.trace()[0]["brief_hash"]])
        self.assertRegex(entry["hashes"][0], r"^[0-9a-f]{12}$")
        self.assertEqual(len(entry["admitted"]), 1, "the repeat did not count")

    def test_the_caps_per_task_for_both_advisors(self):
        # D2.4: per task (one user prompt), an Opus advisor 2 and a Fable
        # advisor 1, each with one reconcile call.
        for session, model, advisor, cap in (("s1", "claude-sonnet-4-5", "opus", 2), ("s2", "claude-opus-4-5", "fable", 1)):
            self.executor(model)
            for _ in range(cap):
                self.assertEqual(self.admitted(self.call(session=session))["updatedInput"]["model"], advisor)
            reason = self.denied(self.call(session=session), "cap-task")
            self.assertIn(f"its {cap} consultation", reason)
            self.assertIn(f"the {advisor} advisor", reason)
            self.admitted(self.call(self.brief(reconcile=True), session=session))
            self.assertIn("its 1 reconcile call", self.denied(self.call(self.brief(reconcile=True), session=session), "cap-task"))
            self.admitted(self.call(session=session, prompt="p2"))  # the next task starts afresh
            self.assertEqual([c for c in self.counted(session) if c[1] == "p1"],
                             [(advisor, "p1", False)] * cap + [(advisor, "p1", True)])

    def test_the_caps_per_session_for_both_advisors(self):
        for session, model, cap, per_task in (("s1", "claude-sonnet-4-5", 6, 2), ("s2", "claude-opus-4-5", 3, 1)):
            self.executor(model)
            for n in range(cap):
                self.admitted(self.call(session=session, prompt=f"p{n // per_task}"))
            self.assertIn(f"its {cap} consultations", self.denied(self.call(session=session, prompt="new"), "cap-session"))
            # A reconcile call is a consultation too.
            self.denied(self.call(self.brief(reconcile=True), session=session, prompt="p0"), "cap-session")
        self.assertEqual([a for a, _, _ in self.counted("s2")], ["fable"] * 3)
        # Each advisor model's apart (F9): a session that moved from Sonnet to
        # Opus after four Opus consultations still has its three on Fable.
        self.executor("claude-sonnet-4-5")
        for n in range(4):
            self.admitted(self.call(session="s4", prompt=f"p{n // 2}"))
        self.executor("claude-opus-4-5")
        for n in range(3):
            self.admitted(self.call(session="s4", prompt=f"q{n}"))
        self.denied(self.call(session="s4", prompt="q9"), "cap-session")

    def counted(self, session="s1"):
        """The state's admitted consultations of `session`, as (advisor, prompt_id, reconcile), in order."""
        return [(a["advisor"], a["prompt_id"], a["reconcile"]) for a in self.state()["sessions"][session]["admitted"]]

    def test_a_call_that_names_no_task_is_denied(self):
        # F9: D2.4's caps are per task because the probe showed prompt_id
        # names it; on a probed version an absent or junk one is a fault.
        for prompt in (None, "", "p\n1", "p 1", 7, "x" * 101):
            reason = self.denied(self.call(prompt=prompt), "task-unknown")
            self.assertIn("prompt_id", reason)
            self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.assertEqual(self.state(), {}, "nothing counted")

    def test_a_call_with_no_call_id_is_denied_before_anything_is_counted(self):
        # F16: the call's id joins the decision to its outcome and the
        # admitted consultation to its advisor (F2, F5); with none, or one
        # not in the id's shape, the hook could not follow it, so it is
        # denied as an error, as a call with no session is.
        absent = self.call()
        del absent["tool_use_id"]
        runs = [absent] + [{**self.call(), "tool_use_id": junk}
                           for junk in (None, "", "toolu 2", "toolu\n1", "../toolu_1", 7, ["toolu_1"], "x" * 101)]
        for data in runs:
            self.denied(data, "error")
            self.assertIsNone(self.trace()[-1]["id"])
            self.assertIsNone(self.trace()[-1]["admission"])
        self.assertEqual(self.state(), {}, "nothing counted")
        # The hook as the plugin runs it denies it too.
        out = self.run_hook(self.HOOK, absent)
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        answer = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(answer["permissionDecision"], "deny")
        self.assertIn("denied (error)", answer["permissionDecisionReason"])
        self.assertEqual(self.state(), {}, "nothing counted")
        # A call with an id is admitted and counted, the denials before it costing nothing.
        self.admitted(self.call(tool_id="toolu_ok"))
        self.assertEqual(self.counted(), [("opus", "p1", False)])

    def test_the_sessions_account_must_be_allowed_in_the_calls_repository(self):
        # F9 (A2): the launch chose an account allowed here; the hook checks
        # that again, by the expression the allowance check names it with. A
        # cwd that is not an absolute path is home-untrusted before it (F13).
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "src").mkdir()
        accounts = self.home / "accounts.json"
        for n, (repositories, cwd, verdict) in enumerate((
                ([str(self.tmp / "elsewhere")], repo / "src", "repository"), ([str(repo)], repo / "src", "allow"),
                ([str(repo / "src")], repo / "src", "repository"),  # the repository is the work tree's top
                ("~/work", repo, "repository"), (None, repo, "allow"), ([], repo / "src", "allow"))):
            accounts.write_text(json.dumps([{"name": "main", "config_dir": None, "repositories": repositories}]))
            data = self.call(prompt=f"p{n}")
            data["cwd"] = str(cwd) if isinstance(cwd, Path) else cwd
            if verdict == "allow":
                self.admitted(data)
            else:
                self.assertIn("this repository", self.denied(data, "repository"), (repositories, cwd))
                self.assertEqual(self.trace()[-1]["kind"], "hard")
        # One helper names the account for the status line and for the hook.
        with mock.patch.object(longhaul, "session_account", return_value=("Zed", {"name": "Zed"})), \
                mock.patch.object(longhaul, "LEDGER", self.home / "ledger.jsonl"):
            longhaul.record_usage({"session_id": "s1", "rate_limits": {"five_hour": {"used_percentage": 1}}}, None)
        self.assertTrue((self.home / "usage" / "Zed.json").exists(), "record_usage names it through session_account")
        accounts.write_text(json.dumps([{"name": "Max", "config_dir": None, "repositories": [str(repo)]}]))
        self.assertEqual(longhaul.session_account(None), ("Max", {"name": "Max", "config_dir": None, "repositories": [str(repo)]}))
        self.assertEqual(longhaul.session_account(str(self.tmp / "pro")), ("pro", {"name": "pro"}), "not in the file: no list")

    def test_a_home_that_is_relative_or_inside_the_repository_is_untrusted(self):
        # F13: a repository's own settings could point LONGHAUL_HOME at files
        # it ships, a probe record and an enabled key among them. A home that
        # is relative, or by real path the call's cwd, inside it or inside the
        # repository above it, is denied before any file is read; a cwd that
        # is not an absolute path rules nothing out. The tests' homes are
        # absolute scratch folders beside the session's, so they are trusted.
        (self.tmp / "alias").symlink_to(self.tmp, target_is_directory=True)
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "src").mkdir()

        def untrusted(data, home=None):
            with mock.patch.object(longhaul, "LONGHAUL_HOME", home or self.home), \
                    mock.patch.object(longhaul, "read_json", side_effect=AssertionError("a file was read")), \
                    mock.patch.object(longhaul, "advisor_running_version", side_effect=AssertionError("version asked")):
                out = self.decide(data)["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny", data.get("cwd"))
            self.assertIn("denied (home-untrusted)", out["permissionDecisionReason"], data.get("cwd"))
            return out["permissionDecisionReason"]

        for cwd in (self.tmp, self.home, self.tmp / "alias", f"{self.tmp}/alias/work/..", "relative/path", "", None):
            data = self.call(prompt="p1")
            data["cwd"] = str(cwd) if isinstance(cwd, Path) else cwd
            if cwd is None:
                del data["cwd"]
            reason = untrusted(data)
            self.assertEqual((self.trace()[-1]["reason"], self.trace()[-1]["kind"]), ("home-untrusted", "hard"), cwd)
        self.assertNotIn("LONGHAUL_HOME", reason, "the executor is told why, not what to change")
        inner = self.call(prompt="p1")
        inner["cwd"] = str(repo / "src")
        untrusted(inner, repo / "lh")  # the repository above the cwd holds it
        untrusted(inner, repo / "src" / "lh")
        (self.tmp / "lh").symlink_to(repo / "src", target_is_directory=True)
        untrusted(inner, self.tmp / "lh" / "home")  # a link that resolves into it
        untrusted(self.call(prompt="p1"), Path(os.path.relpath(self.home)))  # relative, wherever it leads
        self.assertEqual(self.state(), {}, "nothing counted")
        # The hook as the plugin runs it, given a relative home.
        out = subprocess.run(self.HOOK, input=json.dumps(self.call(prompt="p1")), env={**self.env, "LONGHAUL_HOME": "home"},
                             cwd=self.tmp, capture_output=True, text=True, timeout=30)
        self.assertIn("denied (home-untrusted)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        # A home beside the session's folder is trusted, and so is one the
        # session's folder is inside: no repository's files are in it.
        self.admitted(self.call(prompt="p1"))
        nested = self.call(prompt="p2")
        nested["cwd"] = str(self.home / "advisor")
        self.admitted(nested)

    def test_a_scratch_home_cannot_lift_a_restriction_the_default_home_keeps(self):
        # F13: with LONGHAUL_HOME not the default home, A2 reads the default
        # home's accounts.json too, the account named there by its config
        # folder as the status line names it; a restriction in either file
        # that does not admit the repository denies the call.
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        pro, elsewhere = self.tmp / "pro", str(self.tmp / "elsewhere")
        default = self.os_home / ".longhaul"
        default.mkdir()
        self.reading(name="Pro")
        for n, (scratch, real, config, verdict) in enumerate((
                (None, [{"name": "main", "repositories": [elsewhere]}], None, "repository"),
                (None, [{"name": "main", "repositories": [str(repo)]}], None, "allow"),
                ([{"name": "main", "repositories": [elsewhere]}], [{"name": "main", "repositories": [str(repo)]}], None,
                 "repository"),
                ([{"name": "Pro", "config_dir": str(pro)}],
                 [{"name": "Work", "config_dir": str(pro), "repositories": [elsewhere]}], str(pro), "repository"),
                ([{"name": "Pro", "config_dir": str(pro)}], [{"name": "Work", "config_dir": str(pro)}], str(pro), "allow"),
                (None, [{"name": "main", "repositories": "~/work"}], None, "repository"),  # junk there fails closed
                (None, None, None, "allow"))):  # no file there: no restriction
            for home, accounts in ((self.home, scratch), (default, real)):
                path = home / "accounts.json"
                if accounts is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(json.dumps(accounts))
            data = self.call(prompt=f"p{n}")
            data["cwd"] = str(repo)
            if verdict == "allow":
                self.admitted(data, CLAUDE_CONFIG_DIR=config)
            else:
                self.assertIn("this repository", self.denied(data, "repository", CLAUDE_CONFIG_DIR=config), n)

    def test_the_caps_are_counted_apart_for_each_advisor_model(self):
        # F9, D2.4 as worded: an Opus advisor 2 per task plus 1 reconcile and
        # 6 per session; a Fable advisor 1 plus 1 and 3; each model apart, so
        # a session that moved from Sonnet to Opus may hold 6 Opus and 3 Fable.
        self.assertEqual((longhaul.ADVISOR_TASK_CAP_PER_MODEL, longhaul.ADVISOR_TASK_RECONCILES_PER_MODEL,
                          longhaul.ADVISOR_SESSION_CAP_PER_MODEL), ({"opus": 2, "fable": 1}, 1, {"opus": 6, "fable": 3}))
        for old in ("ADVISOR_TASK", "ADVISOR_RECONCILES", "ADVISOR_SESSION"):
            self.assertFalse(hasattr(longhaul, old), old)
        self.executor("claude-opus-4-5")
        self.admitted(self.call(prompt="p1"))
        self.assertIn("the fable advisor", self.denied(self.call(prompt="p1"), "cap-task"))
        self.admitted(self.call(self.brief(reconcile=True), prompt="p1"))
        self.executor("claude-sonnet-4-5")  # the same task, the other advisor: its own caps
        self.denied(self.call(self.brief(reconcile=True), prompt="p1"), "reconcile-invalid")
        for _ in range(2):
            self.admitted(self.call(prompt="p1"))
        self.admitted(self.call(self.brief(reconcile=True), prompt="p1"))
        self.assertIn("the opus advisor", self.denied(self.call(prompt="p1"), "cap-task"))
        self.executor("claude-opus-4-5")
        self.admitted(self.call(prompt="p2"))
        self.assertIn("its 3 consultations with the fable advisor", self.denied(self.call(prompt="p3"), "cap-session"))
        self.executor("claude-sonnet-4-5")  # the Fable cap is reached, and the Opus advisor still has room
        for n in range(3):
            self.admitted(self.call(prompt=f"q{n}"))
        self.assertIn("its 6 consultations with the opus advisor", self.denied(self.call(prompt="q9"), "cap-session"))
        self.assertEqual(collections.Counter(a for a, _, _ in self.counted()), {"opus": 6, "fable": 3})

    def test_the_counts_are_the_larger_of_the_states_and_the_traces(self):
        # F9: a deleted state.json lifts no cap; the trace's admitted
        # decisions count, a consultation that then failed among them.
        state, trace = self.home / "advisor" / "state.json", self.home / "advisor" / "trace.jsonl"
        first = self.call(prompt="p1")
        self.admitted(first)
        self.subagent("f1", first["tool_use_id"], model="opus")  # on the model admitted: F15 marks no mismatch
        self.result(first, None)  # it failed: counted all the same
        self.admitted(self.call(prompt="p1"))
        state.unlink()
        self.denied(self.call(prompt="p1"), "cap-task")
        self.admitted(self.call(self.brief(reconcile=True), prompt="p1"))  # valid: the trace has p1's consultations
        for n in range(3):
            self.admitted(self.call(prompt=f"p{n + 2}"))
        state.unlink()
        self.denied(self.call(prompt="p9"), "cap-session")
        # And the other way: the trace gone, the state's counts hold.
        for _ in range(2):
            self.admitted(self.call(session="s2", prompt="p1"))
        trace.unlink()
        self.denied(self.call(session="s2", prompt="p1"), "cap-task")
        self.admitted(self.call(session="s3", prompt="p1"))  # another session's are its own

    def test_two_hooks_at_once_with_one_consultation_left_admit_exactly_one(self):
        # F11, item 1: parallel Agent calls run their hooks at once. The
        # counts are read and written under the lock, so the session's last
        # consultation goes to one of them alone.
        for n in range(5):
            self.admitted(self.call(prompt=f"p{n // 2}"))
        hooks = []
        for n in range(2):  # two tasks: the session's cap alone stands between them
            given = self.tmp / f"input{n}.json"
            given.write_text(json.dumps(self.call(prompt=f"q{n}")))
            with given.open() as f:
                hooks.append(subprocess.Popen(self.HOOK, stdin=f, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              env=self.env, text=True))
        outs = [hook.communicate(timeout=30) for hook in hooks]
        self.assertEqual([hook.returncode for hook in hooks], [0, 0], [err for _, err in outs])
        answers = sorted((json.loads(out)["hookSpecificOutput"] for out, _ in outs), key=lambda a: a["permissionDecision"])
        self.assertEqual([a["permissionDecision"] for a in answers], ["allow", "deny"])
        self.assertIn("denied (cap-session)", answers[1]["permissionDecisionReason"])
        self.assertEqual(len(self.counted()), 6)
        self.assertEqual([line["decision"] for line in self.trace()].count("allow"), 6)

    def test_a_denied_attempt_counts_for_nothing_and_an_admitted_one_counts_at_once(self):
        self.denied(self.call("x" * 9_000), "brief-too-long")
        self.denied(self.call(self.brief(drop=["Task:"])), "brief-sections")
        self.reading(five=99)
        self.denied(self.call(), "near-full")
        self.assertEqual(self.state(), {})
        self.reading()
        self.admitted(self.call())
        # Counted when admitted, before the advisor runs: a consultation that
        # then fails counts all the same (D2.4).
        self.assertEqual(self.counted(), [("opus", "p1", False)])
        self.assertEqual(oct((self.home / "advisor" / "state.json").stat().st_mode & 0o777), oct(0o600))

    def test_the_allowance_guard_and_its_benchmark_opt_out(self):
        self.reading(five=95)
        self.assertIn("95%", self.denied(self.call(), "near-full"))
        self.reading(five=40, week=96)
        self.denied(self.call(), "near-full")
        self.reading(five=94.9)
        self.admitted(self.call())
        for at in (self.now - longhaul.ADVISOR_FRESH - 1, self.now + 3_600, "x"):
            self.reading(at=at)
            self.denied(self.call(), "allowance-unknown")
        self.reading()
        reset = json.loads((self.home / "usage" / "main.json").read_text())
        reset["five_hour"]["resets_at"] = self.now - 1  # the window this reading was in has reset since
        (self.home / "usage" / "main.json").write_text(json.dumps(reset))
        self.denied(self.call(), "allowance-unknown")
        (self.home / "usage" / "main.json").unlink()
        self.denied(self.call(), "allowance-unknown")
        self.assertEqual(self.trace()[-1]["allowance"], "checked")
        for value in ("yes", "UNCHECKED", ""):
            self.denied(self.call(), "allowance-unknown", LONGHAUL_ADVISOR_ALLOWANCE=value)
        # The benchmark's scratch runs (claude -p) have no status line, so no
        # reading: the opt-out admits them, and the trace says so.
        self.admitted(self.call(prompt="p2"), LONGHAUL_ADVISOR_ALLOWANCE="unchecked")
        self.assertEqual(self.trace()[-1]["allowance"], "unchecked")
        self.reading(five=99)  # a fresh reading still counts
        self.denied(self.call(prompt="p3"), "near-full", LONGHAUL_ADVISOR_ALLOWANCE="unchecked")

    def test_the_reading_is_the_sessions_own_account_and_the_hook_never_chooses_one(self):
        # A2 decided the account at launch and the subagent runs on it: the
        # hook names the session's account only to find its reading.
        pro = self.tmp / "pro"
        accounts = [{"name": "Max", "config_dir": None},
                    {"name": "Pro", "config_dir": str(pro), "repositories": [str(self.tmp)]}]
        (self.home / "accounts.json").write_text(json.dumps(accounts))
        self.reading(five=40, name="Max")
        self.reading(five=99, name="Pro")
        self.denied(self.call(), "near-full", CLAUDE_CONFIG_DIR=str(pro))
        data = self.call()
        out = self.admitted(data)  # no CLAUDE_CONFIG_DIR: ~/.claude, Max's folder
        given = out["updatedInput"]
        self.assertEqual({**given, "prompt": given["prompt"].split("\n", 1)[1]}, {**data["tool_input"], "model": "opus"},
                         "the model and the admission line (F1), nothing else")
        self.assertEqual(set(out), {"hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput"})
        # Pro may be used elsewhere only. Before F9 the hook let this session
        # consult on it all the same; now it checks A2 again, and fails closed.
        accounts[1]["repositories"] = [str(self.tmp / "elsewhere")]
        (self.home / "accounts.json").write_text(json.dumps(accounts))
        before = (self.home / "accounts.json").read_bytes()
        self.reading(five=40, name="Pro")
        self.denied(self.call(), "repository", CLAUDE_CONFIG_DIR=str(pro))
        self.assertEqual((self.home / "accounts.json").read_bytes(), before)
        self.assertFalse((self.home / "state.json").exists(), "the supervisor's state is untouched")
        self.assertEqual(sorted(p.name for p in (self.home / "advisor").iterdir()), ["enabled.json", "probe.json", "state.json", "trace.jsonl"])

    DECISION = ("t", "id", "session", "prompt_id", "event", "decision", "reason", "kind", "trigger", "reconcile",
                "executor", "advisor", "brief_chars", "brief_hash", "allowance", "admission")

    def test_the_trace_has_the_documented_shape(self):
        import hashlib
        data = self.call(tool_id="toolu_1")
        self.admitted(data)
        self.denied(self.call(self.brief(drop=["Task:"]), prompt=None), "brief-sections")
        allow, deny = self.trace()
        self.assertEqual(list(allow), list(self.DECISION))
        brief = data["tool_input"]["prompt"]
        self.assertEqual({k: v for k, v in allow.items() if k != "t"},
                         {"id": "toolu_1", "session": "s1", "prompt_id": "p1", "event": "decision", "decision": "allow",
                          "reason": None, "kind": None, "trigger": "stuck", "reconcile": False, "executor": "sonnet",
                          "advisor": "opus", "brief_chars": len(brief),
                          "brief_hash": hashlib.sha256(brief.encode()).hexdigest()[:12], "allowance": "checked",
                          "admission": hashlib.sha256(self.state()["sessions"]["s1"]["admitted"][0]["nonce"].encode())
                          .hexdigest()[:12]})
        self.assertAlmostEqual(allow["t"], self.now, places=2)
        self.assertEqual((deny["decision"], deny["reason"], deny["kind"], deny["prompt_id"], deny["admission"]),
                         ("deny", "brief-sections", "fixable", None, None))
        # What is not a plain id is not kept; with no session there is no
        # cap to keep, so the call is denied.
        self.denied(self.call(session="../s1", tool_id="toolu 2", prompt="p\n1"), "error")
        self.assertEqual([self.trace()[-1][k] for k in ("id", "session", "prompt_id")], [None, None, None])

    def test_no_brief_text_reaches_the_trace_the_state_the_log_or_a_reason(self):
        admitted = self.call(tool_id="toolu_a")
        runs = [admitted, self.call(self.brief(extra=" sk-ant-" + self.S)), self.call(self.brief(extra="x" * 9_000)),
                self.call(admitted["tool_input"]["prompt"], prompt="p2"), self.call(self.brief(reconcile=True), prompt="p3"),
                self.call(self.brief(trigger="soon"))]
        for data in runs:
            out = self.run_hook(self.HOOK, data)
            self.assertEqual((out.returncode, out.stderr), (0, ""))
            answer = json.loads(out.stdout)["hookSpecificOutput"]
            given = answer.pop("updatedInput", None)
            if given is not None:  # the subagent's prompt: the brief belongs there
                self.assertEqual({**given, "prompt": given["prompt"].split("\n", 1)[1]}, {**data["tool_input"], "model": "opus"})
            self.assertNotIn(self.S, json.dumps(answer))
        self.assertEqual([line["decision"] for line in self.trace()], ["allow"] + ["deny"] * 5)
        written = "".join(p.read_text(errors="replace") for p in self.home.rglob("*") if p.is_file())
        self.assertIn('"brief_hash"', written)
        self.assertNotIn(self.S, written)

    def test_the_trace_keeps_30_days_and_2000_lines_and_the_state_30_days(self):
        path = self.home / "advisor" / "trace.jsonl"
        old = self.now - 31 * 86400
        lines = [{"t": old, "n": n} for n in range(10)] + [{"t": self.now - 60, "n": n} for n in range(10, 2_005)]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        path.chmod(0o644)  # made before Longhaul kept it private
        self.denied(self.call(self.brief(drop=["Task:"])), "brief-sections")
        kept = self.trace()
        self.assertEqual((len(kept), kept[0]["n"]), (1_996, 10), "the month-old lines went")
        self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o600))
        path.write_text("".join(json.dumps({"t": self.now - 60, "n": n}) + "\n" for n in range(2_000)))
        self.admitted(self.call())
        kept = self.trace()
        self.assertEqual((len(kept), kept[0]["n"], kept[-1]["decision"]), (2_000, 1, "allow"), "the oldest went")
        state = self.state()
        for sid, at in (("old", old), ("recent", self.now - 86400)):
            state["sessions"][sid] = {"admitted": [{"id": None, "advisor": "opus", "prompt_id": "p0", "reconcile": False}],
                                      "hashes": [], "at": at}
        (self.home / "advisor" / "state.json").write_text(json.dumps(state))
        self.admitted(self.call(session="s2"))
        self.assertEqual(set(self.state()["sessions"]), {"s1", "recent", "s2"})

    def test_a_state_that_cannot_be_read_denies_every_consultation(self):
        # Counts that were lost would lift the caps: fail closed, said in the
        # log, until the file is deleted.
        path = self.home / "advisor" / "state.json"
        good = {"id": "toolu_1", "advisor": "opus", "prompt_id": "p0", "reconcile": False}
        entry = {"admitted": [good], "hashes": []}
        junk = [{**entry, "admitted": None}, {**entry, "admitted": {}}, {**entry, "hashes": None}, {"hashes": []},
                {**entry, "admitted": ["x"]}, {"count": 1, "fable": 0, "prompts": {}, "hashes": []},  # the shape before F9
                {**entry, "agents": "a1"}, {**entry, "agents": ["../a1"]}, {**entry, "agents": None},  # F2's ids
                {**entry, "mismatch": True}, {**entry, "mismatch": {"decided": "opus"}},  # F5's mark
                {**entry, "mismatch": {"decided": "opus", "seen": "gpt"}}, {**entry, "mismatch": {"decided": "x", "seen": "sonnet"}}]
        junk += [{**entry, "admitted": [{**good, key: value}]} for key, value in (
            ("advisor", "haiku"), ("advisor", ["opus"]), ("prompt_id", None), ("prompt_id", "p 1"), ("reconcile", 0))]
        for text in ["not json", "[]", '{"sessions": []}'] + [json.dumps({"sessions": {"s1": e}}) for e in junk]:
            path.write_text(text)
            with self.assertRaises(ValueError):
                self.decide(self.call())
        out = self.run_hook(self.HOOK, self.call())
        self.assertIn("denied (error)", json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("could not be read; consultations are denied until it is deleted", self.log())
        self.assertNotIn(self.S, self.log())
        path.write_text(json.dumps({"sessions": {"other": "junk"}}))  # another session's entry is not this one's
        self.admitted(self.call())
        path.unlink()
        self.admitted(self.call())

    def test_the_hook_is_fast(self):
        # Claude Code lets a call run when its hook overruns the timeout (5 s
        # here), so the hook must stay fast; its speed is in the work log.
        # This catches one that hangs or crawls, on a 6 MB transcript.
        # Left out of Settings' child run of this class (F11, item 14): a
        # timing under that run's load says nothing, and the child is there
        # for the programs run, which this test runs none of beyond its fake.
        if os.environ.get("LONGHAUL_TEST_CHILD"):
            self.skipTest("the child run of Settings")
        self.executor("claude-sonnet-4-5", before=(json.dumps({"type": "user", "message": {"content": "y" * 2_000}}) + "\n").encode() * 3_000)
        exe, mark = self.fake_claude()
        took = []
        for data, env in ((self.call(), {}), (self.call(self.brief(drop=["Task:"])), {}), (self.call(agent="Explore"), {}),
                          (self.call(prompt="p2"), {"CLAUDE_CODE_EXECPATH": str(exe)}),
                          (self.call(prompt="p3"), {"CLAUDE_CODE_EXECPATH": str(exe)}), (self.call(), {"LONGHAUL_ADVISOR": "off"}),
                          ("not json", {})):
            started = time.time()
            out = self.run_hook(self.HOOK, data, **env)
            took.append(time.time() - started)
            self.assertEqual(out.returncode, 0, out.stderr)
            if out.stdout.strip():
                json.loads(out.stdout)  # anything it prints is JSON the engine can read
        # The decisions are the other tests' to check: under a heavy load the
        # fake claude's --version can overrun its 2 s, and that call is then
        # denied, as it should be.
        self.assertTrue(mark.exists(), "the --version path was timed")
        self.assertLess(sorted(took)[len(took) // 2], 0.5, f"median of {[round(t, 3) for t in took]}")

    OUTCOME = ("t", "id", "session", "event", "outcome", "verdict", "confidence", "reply_chars", "seconds", "model_seen")

    def reply(self, text, agent=None):
        """The Agent tool's response to the executor, as PostToolUse gets it;
        on 2.1.307 it names the subagent's id (agentId) too."""
        response = {"status": "completed", "content": [{"type": "text", "text": text}], "totalDurationMs": 1_000}
        return response if agent is None else {"agentId": agent, "agentType": "longhaul:advisor", **response}

    def result(self, data, response, now=None):
        return longhaul.advisor_outcome({**data, "hook_event_name": "PostToolUse", "tool_response": response}, now)

    def test_advisor_result_traces_the_outcome_and_never_the_reply(self):
        data = self.call(tool_id="toolu_r1")
        self.admitted(data)
        self.subagent("r1", "toolu_r1", model="opus")  # it ran on the model admitted (F15: else the session is marked)
        text = f"Verdict: adjust\nWhy: the fixture is stale {self.S}\nRisks: none\nNext step: rebuild it\nConfidence: medium"
        self.assertIsNone(self.result(data, self.reply(text), now=self.now + 12.34))
        line = self.trace()[-1]
        self.assertEqual(list(line), list(self.OUTCOME))
        self.assertEqual({k: v for k, v in line.items() if k != "t"},
                         {"id": "toolu_r1", "session": "s1", "event": "outcome", "outcome": "ok", "verdict": "adjust",
                          "confidence": "medium", "reply_chars": len(text), "seconds": 12.3, "model_seen": "opus"})
        for response, outcome, verdict, confidence in (
                (self.reply("**Verdict:** stop\nConfidence: high"), "ok", "stop", "high"),
                (self.reply("\nVerdict: Proceed\n"), "ok", "proceed", None),
                ("Verdict: proceed\nConfidence: low", "ok", "proceed", "low"),
                (self.reply("Here is my view.\nVerdict: proceed\nConfidence: high"), "malformed", None, "high"),
                (self.reply("Verdict: maybe"), "malformed", None, None),
                (self.reply(""), "malformed", None, None),
                ({"status": "completed", "content": []}, "failed", None, None), ({}, "failed", None, None),
                (None, "failed", None, None)):
            self.result(data, response)
            line = self.trace()[-1]
            self.assertEqual((line["outcome"], line["verdict"], line["confidence"]), (outcome, verdict, confidence), response)
        self.assertIsNone(self.trace()[-1]["reply_chars"], "no reply, no size")
        self.result(self.call(tool_id="toolu_none"), self.reply("Verdict: proceed"))
        self.assertEqual((self.trace()[-1]["outcome"], self.trace()[-1]["seconds"]), ("unadmitted", None), "never admitted")
        refused = self.call(self.brief(drop=["Task:"]), tool_id="toolu_d")
        self.denied(refused, "brief-sections")
        self.result(refused, self.reply("Verdict: proceed"))
        self.assertEqual((self.trace()[-1]["outcome"], self.trace()[-1]["seconds"]), ("unadmitted", None), "denied")
        before = self.trace()
        self.assertIsNone(self.result(self.call(agent="Explore"), self.reply("Verdict: stop")))
        self.assertEqual(self.trace(), before, "another agent type: nothing written")
        self.assertEqual(len(self.counted()), 1, "a failed consultation still counts")

    def test_a_consultation_that_failed_before_any_model_answered_marks_nothing(self):
        # F15 keeps D2.6: a consultation that failed with no model to read
        # (no transcript line, no model in a meta.json) is unknown but marks
        # nothing, so the one retry after a rate limit stays possible; the
        # same unknown on a reply is a mismatch, as the next test says.
        data = self.call(prompt="p1", tool_id="toolu_x1")
        self.admitted(data)
        self.result(data, {"status": "error", "content": []})
        self.assertEqual((self.trace()[-1]["outcome"], self.trace()[-1]["model_seen"]), ("failed", "unknown"))
        self.assertNotIn("mismatch", self.state()["sessions"]["s1"])
        data = self.call(prompt="p2", tool_id="toolu_x2")
        self.admitted(data)
        self.result(data, self.reply("Verdict: proceed\nConfidence: high"))
        self.assertEqual(self.state()["sessions"]["s1"]["mismatch"], {"decided": "opus", "seen": "unknown"})
        self.denied(self.call(prompt="p3"), "model-mismatch")

    def test_an_advisor_that_ran_on_another_family_blocks_the_rest_of_the_session(self):
        # F5, closed by F15: advisor-result reads the model the advisor ran
        # on, as a family (its own transcript first, the next test's; here
        # its meta.json alone). Another family than the one admitted marks
        # the session, in the state and in the trace, so either one alone
        # keeps the mark; so does unknown (no meta.json, or no model in it).
        def ran(tool_id, prompt, session="s1", **meta):
            data = self.call(session=session, prompt=prompt, tool_id=tool_id)
            self.admitted(data)
            if meta:
                self.subagent(tool_id[-2:], tool_id, **meta)
            self.result(data, self.reply("Verdict: proceed\nConfidence: high"))
            return self.trace()[-1]["model_seen"]
        self.assertEqual(ran("toolu_m1", "p1", model="opus"), "opus")
        self.assertEqual([longhaul.advisor_seen({"model": junk}) for junk in (7, "", " ", ["opus"])], ["unknown"] * 4)
        self.assertNotIn("mismatch", self.state()["sessions"]["s1"])
        self.assertEqual(ran("toolu_m5", "p5", model="claude-sonnet-4-5"), "sonnet")
        self.assertEqual(self.state()["sessions"]["s1"]["mismatch"], {"decided": "opus", "seen": "sonnet"})
        reason = self.denied(self.call(prompt="p6"), "model-mismatch")
        self.assertIn("sonnet", reason)
        self.assertIn("the opus advisor", reason)
        self.assertEqual(self.trace()[-1]["kind"], "hard")
        self.denied(self.call(self.brief(reconcile=True), prompt="p5"), "model-mismatch")
        self.admitted(self.call(session="s2"))  # another session's are its own
        state, trace = self.home / "advisor" / "state.json", self.home / "advisor" / "trace.jsonl"
        saved = state.read_text()
        state.unlink()
        self.denied(self.call(prompt="p7"), "model-mismatch")  # the trace alone
        state.write_text(saved)
        trace.write_text("")
        self.denied(self.call(prompt="p8"), "model-mismatch")  # the state alone
        state.unlink()
        trace.write_text("")
        self.admitted(self.call(prompt="p9"))  # both gone: nothing left to say it
        # A model that names no family, as an alias the rewrite did not take
        # would (inherit), is another family: other.
        self.assertEqual(ran("toolu_n1", "p1", session="s3", model="inherit"), "other")
        self.denied(self.call(session="s3", prompt="p2"), "model-mismatch")
        # F15: unknown on an admitted consultation is a mismatch too (fail
        # closed), each case in a session of its own.
        for session, meta, why in (("u1", {}, "no meta.json"), ("u2", {"spawnDepth": 1}, "no model in it"),
                                   ("u3", {"model": None}, "null, as the probe saw for a call not rewritten")):
            self.assertEqual(ran(f"toolu_{session}", "p1", session=session, **meta), "unknown", why)
            self.assertEqual(self.state()["sessions"][session]["mismatch"], {"decided": "opus", "seen": "unknown"}, why)
            reason = self.denied(self.call(session=session, prompt="p2"), "model-mismatch")
            self.assertIn("a model that could not be read", reason)
            self.assertIn("the opus advisor", reason)
        state.unlink()
        self.denied(self.call(session="u1", prompt="p3"), "model-mismatch")  # the trace alone keeps unknown's mark
        # An advisor that ran with no admitted decision has no model to be
        # matched: it is flagged unadmitted (F1), and its unknown marks nothing.
        self.result(self.call(session="u4", tool_id="toolu_u4"), self.reply("Verdict: proceed"))
        self.assertEqual((self.trace()[-1]["outcome"], self.trace()[-1]["model_seen"]), ("unadmitted", "unknown"))
        self.admitted(self.call(session="u4", prompt="p2"))
        self.executor("claude-opus-4-5")
        self.assertEqual(ran("toolu_f1", "p1", session="s4", model="fable"), "fable")
        self.admitted(self.call(session="s4", prompt="p2"))

    def test_the_model_seen_is_the_one_the_advisor_s_own_transcript_ran_on(self):
        # F15: the advisor's own transcript (agent-<id>.jsonl beside its
        # meta.json, every line a sidechain one) names the model that ran,
        # as a full id: its last assistant line decides, over the model the
        # call set in meta.json, which is read only when the transcript
        # names none. Only the transcript's tail is read, as the executor's is.
        subagents = self.transcript.with_suffix("") / "subagents"

        def line(model, sidechain=True):
            return {"type": "assistant", **({"isSidechain": True} if sidechain else {}),
                    "message": {"model": model, "role": "assistant", "content": [{"type": "text", "text": self.S}]}}

        def ran(session, lines, meta_model="opus", head=b""):
            tool_id = f"toolu_{session}"
            data = self.call(session=session, tool_id=tool_id)
            self.admitted(data)
            self.subagent(session, tool_id, **({} if meta_model is None else {"model": meta_model}))
            (subagents / f"agent-{session}.jsonl").write_bytes(
                head + "".join((raw if isinstance(raw, str) else json.dumps(raw)) + "\n" for raw in lines).encode())
            self.result(data, self.reply("Verdict: proceed\nConfidence: high"))
            return self.trace()[-1]["model_seen"], self.state()["sessions"][session].get("mismatch")

        self.assertEqual(ran("t1", [line("claude-fable-5-1")]), ("fable", {"decided": "opus", "seen": "fable"}),
                         "the call set opus; the advisor ran on Fable")
        self.assertIn("ran on fable", self.denied(self.call(session="t1", prompt="p2"), "model-mismatch"))
        self.assertEqual(ran("t2", [line("claude-sonnet-4-5"), line("claude-opus-4-5")], meta_model="fable"),
                         ("opus", None), "the transcript's last line beats meta.json")
        self.admitted(self.call(session="t2", prompt="p2"))
        self.assertEqual(ran("t3", [line("claude-opus-4-5"), line("claude-sonnet-4-5", sidechain=False)]),
                         ("sonnet", {"decided": "opus", "seen": "sonnet"}), "the last one, not the first")
        no_model = [{"type": "user", "isSidechain": True, "message": {"role": "user", "content": "go"}},
                    line("<synthetic>"), "not json {", {"type": "assistant", "isSidechain": True, "message": {"model": 7}}]
        self.assertEqual(ran("t4", no_model), ("opus", None), "no model in the transcript: meta.json's")
        self.assertEqual(ran("t5", no_model, meta_model=None), ("unknown", {"decided": "opus", "seen": "unknown"}))
        self.assertEqual(ran("t6", []), ("opus", None), "an empty transcript: meta.json's")
        # A model line beyond the tail is never read: meta.json's model decides.
        head = (json.dumps(line("claude-sonnet-4-5")) + "\n").encode() + b" " * longhaul.ADVISOR_TAIL + b"\n"
        self.assertEqual(ran("t7", [], head=head), ("opus", None), "the head of a long transcript is not read")
        written = "".join(p.read_text(errors="replace") for p in self.home.rglob("*") if p.is_file())
        self.assertNotIn(self.S, written, "the advisor's text is never kept")

    def test_one_round_trip_across_the_regions(self):
        # F11, item 2: what Region B writes is what Region A reads. A
        # consultation admitted by advisor_decide and its outcome traced by
        # advisor_outcome are in inspect_transcript's report, with the
        # advisor's own tokens from its subagent's transcript.
        data = self.call(tool_id="toolu_trip")
        with mock.patch.dict(os.environ, self.env, clear=True):
            answer = longhaul.advisor_decide(data, self.now)
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "allow")
        # The advisor's transcript and meta.json, there when PostToolUse runs (F12 d).
        subagents = self.transcript.with_suffix("") / "subagents"
        subagents.mkdir(parents=True)
        usage = {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 40}
        (subagents / "agent-a1.jsonl").write_text(json.dumps(
            {"type": "assistant", "isSidechain": True,
             "message": {"id": "msg_a1", "model": "claude-opus-4-5", "role": "assistant", "usage": usage,
                         "content": [{"type": "text", "text": f"Verdict: adjust {self.S}"}]}}) + "\n")
        (subagents / "agent-a1.meta.json").write_text(json.dumps(
            {"agentType": longhaul.ADVISOR_TYPE, "toolUseId": "toolu_trip", "model": "opus"}))
        self.assertIsNone(longhaul.advisor_outcome({**data, "hook_event_name": "PostToolUse",
                                                    "tool_response": self.reply(f"Verdict: adjust\nWhy: {self.S}")},
                                                   self.now + 7))
        self.assertEqual(self.trace()[-1]["model_seen"], "opus")
        self.denied(self.call(self.brief(drop=["Task:"])), "brief-sections")
        r = longhaul.inspect_transcript(self.transcript)
        c = r["consultations"]
        self.assertEqual({k: c[k] for k in ("attempts", "admitted", "denied", "outcomes", "verdicts", "transcripts")},
                         {"attempts": 2, "admitted": 1, "denied": {"brief-sections": 1}, "outcomes": {"ok": 1},
                          "verdicts": {"adjust": 1}, "transcripts": 1})
        self.assertEqual(c["tokens"], [{"model": "claude-opus-4-5", "input": 3, "cache_write": 100, "cache_read": 50,
                                        "output": 40}])
        lines = longhaul.inspect_lines(r)
        self.assertIn("  2 consultations asked for: 1 admitted, 1 denied", lines)
        self.assertNotIn(self.S, "\n".join(lines))

    def test_the_result_hook_prints_nothing_and_never_keeps_the_reply(self):
        data = self.call(tool_id="toolu_r2")
        self.assertEqual(self.run_hook(self.HOOK, data).returncode, 0)
        reply = {**data, "hook_event_name": "PostToolUse",
                 "tool_response": self.reply(f"Verdict: proceed\nWhy: {self.S}\nConfidence: high")}
        out = self.run_hook(self.RESULT, reply)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        self.assertEqual(self.trace()[-1]["outcome"], "ok")
        written = "".join(p.read_text(errors="replace") for p in self.home.rglob("*") if p.is_file())
        self.assertIn('"outcome"', written)
        self.assertNotIn(self.S, written)
        (self.home / "advisor" / "trace.jsonl").unlink()
        (self.home / "advisor" / "trace.jsonl").mkdir()  # nothing can be traced: the call has run, so exit 0 all the same
        for raw in (json.dumps(reply), "not json", ""):
            out = self.run_hook(self.RESULT, raw)
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        self.assertIn("advisor-result hook: no outcome traced (IsADirectoryError)", self.log())
        written = "".join(p.read_text(errors="replace") for p in self.home.rglob("*") if p.is_file())
        self.assertNotIn(self.S, written)

    def command(self, *args, cwd=None, **env):
        """`longhaul advisor ...` from a terminal in the session's folder (or
        `cwd`), the session's variables over it (None unsets a name)."""
        values = {k: v for k, v in {**self.env, **env}.items() if v is not None}
        return subprocess.run([sys.executable, str(HERE / "longhaul.py"), "advisor", *args], env=values, cwd=cwd or self.work,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def default_home(self):
        """A HOME whose ~/.longhaul is this test's home, by a link: the default home, in scratch."""
        fake = self.tmp / "user"
        fake.mkdir(exist_ok=True)
        if not (fake / ".longhaul").exists():
            (fake / ".longhaul").symlink_to(self.home)
        return fake

    def test_the_gate_needs_the_enabled_key_for_the_probe_records_version(self):
        # F8: the switch alone is not enough; the key names the version.
        key = self.home / "advisor" / "enabled.json"
        key.unlink()
        reason = self.denied(self.call(prompt="p1"), "not-enabled")
        self.assertNotIn("LONGHAUL_ADVISOR", reason, "the denial never names the switch")
        for text in ("not json", "[]", json.dumps({"schema": 2, "probe_version": "2.1.307"}),
                     json.dumps({"schema": True, "probe_version": "2.1.307"}), json.dumps({"schema": 1}),
                     json.dumps({"schema": 1, "probe_version": "latest"})):
            key.write_text(text)
            self.denied(self.call(prompt="p1"), "not-enabled")
        self.enable()
        self.admitted(self.call(prompt="p1"))
        # A probe record for another version needs a new enable.
        self.probe("2.1.308")
        other = self.tmp / "claude" / "versions" / "2.1.308"
        other.write_text("")
        self.denied(self.call(prompt="p2"), "not-enabled", CLAUDE_CODE_EXECPATH=str(other))
        self.enable("2.1.308")
        self.admitted(self.call(prompt="p2"), CLAUDE_CODE_EXECPATH=str(other))
        self.assertEqual(self.trace()[0]["kind"], "hard")

    def test_enable_is_the_owners_and_disable_always_works(self):
        # F8: refused, with nothing written, inside a session, without a
        # passing probe record, and for the default home without a terminal.
        key = self.home / "advisor" / "enabled.json"
        key.unlink()
        terminal = dict.fromkeys(longhaul.SESSION_ENV + list(self.VARS))  # a terminal of the owner's: no session's variables
        for name in longhaul.SESSION_ENV:
            out = self.command("enable", **{**terminal, name: "1"})
            self.assertEqual((out.returncode, out.stdout), (2, ""), name)
            self.assertIn(f"not enabled: it runs inside a Claude Code session ({name} set)", out.stderr)
            self.assertFalse(key.exists(), name)
        self.assertIn("CLAUDECODE", longhaul.SESSION_ENV)
        self.probe(read_only=False)
        out = self.command("enable", **terminal)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not enabled: no passing probe record", out.stderr)
        self.probe()
        fake = self.default_home()
        out = self.command("enable", **terminal, HOME=str(fake), LONGHAUL_HOME=None)  # stdin is no terminal here
        self.assertEqual(out.returncode, 2)
        self.assertIn("not enabled: stdin is not a terminal", out.stderr)
        self.assertFalse(key.exists())
        primary, secondary = pty.openpty()
        self.addCleanup(os.close, primary)
        with open(secondary, "rb") as tty:  # a person at a terminal: the default home is enabled
            out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "advisor", "enable"], stdin=tty,
                                 env={k: v for k, v in {**self.env, **terminal, "HOME": str(fake)}.items()
                                      if v is not None and k != "LONGHAUL_HOME"},
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        key.unlink()
        out = self.command("enable", **terminal)  # a scratch home: no terminal needed
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        self.assertIn("Enabled for Claude Code 2.1.307", out.stdout)
        written = json.loads(key.read_text())
        self.assertEqual((set(written), written["schema"], written["probe_version"]),
                         ({"schema", "enabled_at", "probe_version"}, 1, "2.1.307"))
        self.assertAlmostEqual(written["enabled_at"], time.time(), delta=60)
        self.assertEqual(oct(key.stat().st_mode & 0o777), oct(0o600))
        for env in ({"CLAUDECODE": "1"}, {}):  # disable: inside a session too, and with no key
            out = self.command("disable", **env)
            self.assertEqual((out.returncode, out.stderr), (0, ""), env)
            self.assertFalse(key.exists())
        self.denied(self.call(), "not-enabled")

    def test_the_allowance_opt_out_is_ignored_in_the_default_home_and_near_full_only_stricter(self):
        # F8: LONGHAUL_ADVISOR_ALLOWANCE=unchecked counts in a scratch home
        # alone; LONGHAUL_NEAR_FULL can lower the Advisor's 95 and never raise it.
        (self.home / "usage" / "main.json").unlink()
        self.admitted(self.call(prompt="p1"), LONGHAUL_ADVISOR_ALLOWANCE="unchecked")
        with mock.patch.object(longhaul, "HOME", self.default_home()):
            self.denied(self.call(prompt="p2"), "allowance-unknown", LONGHAUL_ADVISOR_ALLOWANCE="unchecked")
            self.assertEqual(self.trace()[-1]["allowance"], "checked")
        self.assertEqual(longhaul.ADVISOR_NEAR_FULL, 95)
        for n, (setting, five, verdict, limit) in enumerate(((99, 96, "near-full", "95%"), (99, 94.9, "allow", None),
                                                             (80, 85, "near-full", "80%"), (80, 79, "allow", None))):
            self.reading(five=five)
            with mock.patch.object(longhaul, "NEAR_FULL", setting):
                if verdict == "allow":
                    self.admitted(self.call(prompt=f"q{n}"))
                else:
                    self.assertIn(limit, self.denied(self.call(prompt=f"q{n}"), "near-full"))
        # unchecked in the trace only when no fresh reading was checked
        self.admitted(self.call(prompt="r1", session="s2"), LONGHAUL_ADVISOR_ALLOWANCE="unchecked")
        self.assertEqual(self.trace()[-1]["allowance"], "checked", "a fresh reading was checked")

    def status(self, cwd=None, **env):
        """`longhaul advisor status`: its lines but the last, by what each names (the text before its colon), and the last."""
        out = self.command("status", cwd=cwd, **env)
        self.assertEqual((out.returncode, out.stderr), (0, ""), env)
        lines = out.stdout.splitlines()
        return dict(line.split(": ", 1) for line in lines[:-1]), lines[-1]

    def test_advisor_status_names_the_switch_both_versions_and_whether_it_is_enabled(self):
        day = dt.datetime.fromtimestamp(self.now).strftime("%Y-%m-%d")
        lines, last = self.status()
        self.assertEqual(lines, {"LONGHAUL_ADVISOR": "experiment",
                                 "Probe record": f"Claude Code 2.1.307, passed {day}; rewrites seen: opus, fable",
                                 "Running Claude Code": "2.1.307 (from the path)",
                                 "Enabled key": "present, for Claude Code 2.1.307",
                                 "Allowance": "checked: a fresh status-line reading of the session's account is needed",
                                 "Home": str(self.home), "Unadmitted advisor runs traced": "0"})
        self.assertEqual(last, "enabled", "the skill's contract: the last line")
        self.probe(rewrites=None)  # F5: the list, none when the record has none
        lines, last = self.status()
        self.assertEqual(lines["Probe record"], f"Claude Code 2.1.307, passed {day}; rewrites seen: none")
        self.assertEqual(last, "off: the probe record names no advisor model whose rewrite it saw (rewrite_models); "
                               "record a probe that does", "no model the gate could admit: not enabled")
        self.probe()
        other = self.tmp / "claude" / "versions" / "2.1.308"
        other.write_text("")
        for env, named, why in (
                ({"LONGHAUL_ADVISOR": None}, {"LONGHAUL_ADVISOR": "off"}, "LONGHAUL_ADVISOR is not experiment"),
                ({"LONGHAUL_ADVISOR": "on"}, {"LONGHAUL_ADVISOR": "off"}, "LONGHAUL_ADVISOR is not experiment"),
                ({"CLAUDE_CODE_EXECPATH": None}, {"Running Claude Code": "unknown"}, "the running Claude Code version is unknown"),
                ({"CLAUDE_CODE_EXECPATH": str(other)}, {"Running Claude Code": "2.1.308 (from the path)"},
                 "the probe passed on 2.1.307, not on 2.1.308, the version running"),
                ({"LONGHAUL_ADVISOR_ALLOWANCE": "unchecked"},
                 {"Allowance": "unchecked when no fresh reading exists (LONGHAUL_ADVISOR_ALLOWANCE=unchecked, a scratch home)"},
                 None)):
            lines, last = self.status(**env)
            self.assertEqual({k: lines[k] for k in named}, named, env)
            self.assertEqual(last, f"off: {why}" if why else "enabled", env)
        # The default home: the opt-out is ignored there, and status says so.
        lines, _ = self.status(LONGHAUL_ADVISOR_ALLOWANCE="unchecked", HOME=str(self.default_home()), LONGHAUL_HOME=None)
        self.assertEqual((lines["Allowance"], lines["Home"]),
                         ("checked: LONGHAUL_ADVISOR_ALLOWANCE=unchecked is ignored in the default home", "~/.longhaul (the default)"))
        self.enable("2.1.306")
        lines, last = self.status()
        self.assertEqual((lines["Enabled key"], last), ("present, for Claude Code 2.1.306, not the probe record's 2.1.307",
                                                        "off: the enabled key is for 2.1.306, not the probe record's 2.1.307; "
                                                        "run longhaul advisor enable again"))
        (self.home / "advisor" / "enabled.json").unlink()
        lines, last = self.status()
        self.assertEqual((lines["Enabled key"], last), ("missing", "off: not enabled (longhaul advisor enable, in a terminal)"))
        self.enable()
        self.probe(read_only=False)
        lines, last = self.status()
        self.assertEqual((lines["Probe record"], last), ("none", "off: no passing probe record"))
        written = sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob("*") if p.is_file())
        self.assertEqual(written, ["advisor/enabled.json", "advisor/probe.json", "usage/main.json"], "status writes nothing")

    def test_advisor_status_names_an_untrusted_home(self):
        # F13: status runs in the session's folder, so it checks the home
        # against that folder and the repository above it, as the hook does.
        inside = "inside the working folder or its repository"
        lines, last = self.status(cwd=self.tmp)
        self.assertEqual((lines["Home"], last), (f"{self.home} (untrusted: {inside})", f"off: Longhaul's home is untrusted ({inside})"))
        lines, last = self.status(cwd=self.tmp, LONGHAUL_HOME="home")
        self.assertEqual((lines["Home"], last), ("home (untrusted: a relative path)", "off: Longhaul's home is untrusted (a relative path)"))
        self.assertEqual(lines["Probe record"][:22], "Claude Code 2.1.307, p", "the relative home is read all the same")
        _, last = self.status(cwd=self.tmp, LONGHAUL_ADVISOR=None)
        self.assertEqual(last, "off: LONGHAUL_ADVISOR is not experiment", "the switch first, as in the hook")
        lines, last = self.status()
        self.assertEqual((lines["Home"], last), (str(self.home), "enabled"))

    def test_advisor_status_names_where_the_running_version_came_from(self):
        # F7: status reads what the hook reads: the session's transcript
        # (by CLAUDE_CODE_SESSION_ID, as inspect finds one), then the path,
        # then what claude says to --version.
        self.transcript = self.tmp / "claude-config" / "projects" / "-code-app" / "s1.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.stamped("2.1.307")
        older = self.tmp / "claude" / "versions" / "2.1.304"
        older.write_text("")
        exe, _ = self.fake_claude()
        for env, running, last in (
                ({"CLAUDE_CODE_SESSION_ID": "s1", "CLAUDE_CODE_EXECPATH": str(older)}, "2.1.307 (from the transcript)",
                 "enabled"),
                ({"CLAUDE_CODE_SESSION_ID": "s2"}, "2.1.307 (from the path)", "enabled"),
                ({"CLAUDE_CODE_SESSION_ID": "../s1"}, "2.1.307 (from the path)", "enabled"),
                ({"CLAUDE_CODE_EXECPATH": str(exe)}, "2.1.307 (from --version)", "enabled"),
                ({"CLAUDE_CODE_EXECPATH": None}, "unknown", "off: the running Claude Code version is unknown")):
            lines, said = self.status(**env)
            self.assertEqual((lines["Running Claude Code"], said), (running, last), env)
        self.stamped("2.1.307", "2.1.307-dev")
        lines, said = self.status(CLAUDE_CODE_SESSION_ID="s1")
        self.assertEqual((lines["Running Claude Code"], said), ("unknown (from the transcript)",
                                                               "off: the running Claude Code version is unknown"))

    def test_record_probe_writes_a_passing_record_and_refuses_anything_else(self):
        path = self.home / "advisor" / "probe.json"
        path.unlink()
        evidence = self.tmp / "evidence.json"
        good = {"claude_version": "2.1.307", "checks": dict.fromkeys(longhaul.ADVISOR_CHECKS, True),
                "rewrite_models": ["opus", "fable", "opus"],
                "evidence": "the probe's transcripts of 2026-10-22", "passed_at": 1_792_600_000}
        rewrites = "rewrite_models must be a list of haiku, sonnet, opus, fable"
        refusals = [("not JSON", "it is not JSON"), ([], "it is not a JSON object"),
                    ({**good, "rewrite_models": "opus"}, rewrites), ({**good, "rewrite_models": None}, rewrites),
                    ({**good, "rewrite_models": ["opus", "gpt"]}, rewrites), ({**good, "rewrite_models": ["Opus"]}, rewrites),
                    ({**good, "rewrite_models": [7]}, rewrites),
                    ({**good, "claude_version": None}, "it names no claude_version"),
                    ({**good, "claude_version": "latest"}, "it names no claude_version"),
                    ({**good, "checks": {**good["checks"], "deny": "true"}}, "these checks are not true: deny"),
                    ({**good, "checks": None}, "these checks are not true: " + ", ".join(longhaul.ADVISOR_CHECKS))]
        refusals += [({**good, "checks": {**good["checks"], check: False}}, f"these checks are not true: {check}")
                     for check in longhaul.ADVISOR_CHECKS]
        for found, why in refusals:
            evidence.write_text(found if isinstance(found, str) else json.dumps(found))
            out = self.command("record-probe", str(evidence))
            self.assertEqual((out.returncode, out.stdout), (2, ""), why)
            self.assertIn(f"longhaul advisor record-probe: not recorded: {why}", out.stderr)
            self.assertFalse(path.exists(), why)
        for args, why in (((), "name the evidence file"), ((str(self.tmp / "missing.json"),), "could not read")):
            out = self.command("record-probe", *args)
            self.assertEqual(out.returncode, 2)
            self.assertIn(why, out.stderr)
        evidence.write_text(json.dumps({**good, "checks": {**good["checks"], "extra": False}}))
        out = self.command("record-probe", str(evidence))
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        record = {"schema": 1, "claude_version": "2.1.307", "passed_at": 1_792_600_000,
                  "checks": dict.fromkeys(longhaul.ADVISOR_CHECKS, True), "rewrite_models": ["opus", "fable"],
                  "evidence": "the probe's transcripts of 2026-10-22"}
        self.assertEqual(json.loads(path.read_text()), record)
        self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o600))
        self.assertTrue(out.stdout.startswith(json.dumps(record, indent=2) + "\n"), out.stdout)
        self.assertIn("This turns nothing on", out.stdout)
        # The record is the gate's half; the switch is the user's.
        self.denied(self.call(), "off", LONGHAUL_ADVISOR=None)
        self.admitted(self.call())
        # With no evidence named in it, the evidence is the file itself; with
        # no rewrite_models, the probe saw none, and nothing is admitted (F5).
        evidence.write_text(json.dumps({k: v for k, v in good.items() if k not in ("evidence", "passed_at", "rewrite_models")}))
        self.assertEqual(self.command("record-probe", str(evidence)).returncode, 0)
        kept = json.loads(path.read_text())
        self.assertEqual(kept["evidence"], str(evidence))
        self.assertAlmostEqual(kept["passed_at"], time.time(), delta=60)
        self.assertEqual(kept["rewrite_models"], [])
        self.denied(self.call(), "model-unprobed")


class Settings(unittest.TestCase):
    """The LONGHAUL_* numbers read at import (Sprint 4, T5): a typo in any
    one is ignored and said, never a stop."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "home").mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LONGHAUL_")}
        # fake_stream.py as claude: status asks it for auth status and the
        # version (review, Sprint 4: the real claude answered here, twelve times).
        self.env.update(LONGHAUL_HOME=str(self.tmp / "home"), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "claude"),
                        LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "claude.json"), LONGHAUL_NO_NOTIFY="1",
                        LONGHAUL_CLAUDE_BIN=str(HERE / "fake_stream.py"))

    def test_a_poll_of_zero_is_under_its_floor_where_a_reopen_delay_of_zero_is_not(self):
        # Review, Sprint 4 (TA): the test below gives every name "-1", so
        # it cannot tell POLL's 10 ms floor from 0, which spun the loop;
        # LONGHAUL_REOPEN_DELAY=0 is what the loop's tests set.
        env = {**self.env, "LONGHAUL_POLL_SECONDS": "0", "LONGHAUL_REOPEN_DELAY": "0"}
        out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import longhaul; "
                                                    "print(longhaul.POLL, longhaul.REOPEN_DELAY)", str(HERE)],
                             env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.split(), ["2.0", "0"], out.stderr)
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "status"], env=env, capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([l for l in out.stdout.splitlines() if l.startswith("Ignored")],
                         ["Ignored (not a number, or under its minimum; the default is in force): LONGHAUL_POLL_SECONDS"])

    def test_pipe_says_in_the_log_which_settings_it_ignored(self):
        # Review, Sprint 4 (SF 3): only `status` named them; run, pipe and
        # the hooks ran on the default without a word. The hooks stay
        # silent (nothing may be printed there), so run and pipe say it
        # in the log, at their start, in status's own words. pipe is run
        # with no accounts file: it exits 1 before any claude, after it.
        pipe = [sys.executable, str(HERE / "longhaul.py"), "pipe", "--", "-p"]
        log = self.tmp / "home" / "longhaul.log"
        out = subprocess.run(pipe, env=self.env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertNotIn("Ignored", log.read_text(), "nothing to say when every setting is good")
        out = subprocess.run(pipe, env={**self.env, "LONGHAUL_MAX_REOPENS": "abc"}, capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("Ignored (not a number, or under its minimum; the default is in force): LONGHAUL_MAX_REOPENS",
                      log.read_text())
        self.assertNotIn("abc", log.read_text(), "the names, never the values")

    def test_a_bad_value_in_any_setting_never_stops_a_command(self):
        # Sprint 4, T5: int() or float() of seventeen settings ran at import,
        # so a typo in any one stopped every command, the hooks included.
        # The names come from the source, so a new setting is covered
        # without editing this test, and one read the old way is caught.
        source = (HERE / "longhaul.py").read_text()
        names = sorted(set(re.findall(r'(?:setting\(|(?:int|float)\(os\.(?:environ\.get\(|getenv\(|environ\[))'
                                      r'"(LONGHAUL_[A-Z_]+)"', source)))
        self.assertLessEqual({"LONGHAUL_POLL_SECONDS", "LONGHAUL_MAX_REOPENS", "LONGHAUL_GUARD_BIG"}, set(names), names)
        status = [sys.executable, str(HERE / "longhaul.py"), "status"]
        for value in ("abc", "nan", "", "-1"):
            out = subprocess.run(status, env={**self.env, **dict.fromkeys(names, value)},
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, (value, out.stderr))
            self.assertNotIn("Traceback", out.stderr, value)
            if value:  # an empty one is unset, not a typo, so there is nothing to say
                self.assertIn("LONGHAUL_POLL_SECONDS", out.stdout, (value, "status names what it ignored"))
            else:
                self.assertNotIn("Ignored", out.stdout, "an unset name is never reported")
        out = subprocess.run(status, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("Ignored", out.stdout, "nothing to say when every setting is good")
        out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import longhaul; "
                                                    "print(longhaul.POLL, longhaul.MAX_REOPENS, longhaul.GUARD_BIG)", str(HERE)],
                             env={**self.env, **dict.fromkeys(names, "abc")}, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["2.0", "5", "20000"], "the defaults are in force")

    def test_the_suite_never_runs_the_real_claude(self):
        # R3 (Sprint 5): a test that runs `status` with no LONGHAUL_CLAUDE_BIN
        # runs the real claude, against ~/.claude's sign-in and the onboarding
        # rule; this class did until Sprint 4's review, the guard's bad-size
        # test after it. A claude that leaves a mark goes first on PATH, the
        # classes that run commands whole go through unittest in a child
        # with no LONGHAUL_CLAUDE_BIN, and the mark must not appear. The
        # child skips this test, or it would nest without end. Sprint 7,
        # G4: the auto-init runs `codegraph` and `trail` from PATH, so the
        # two are marked the same way and the Index class runs in the child.
        if os.environ.get("LONGHAUL_TEST_CHILD"):
            self.skipTest("the child run of this test")
        bin_dir, mark = self.tmp / "bin", self.tmp / "was-run"
        bin_dir.mkdir()
        for name in ("claude", "codegraph", "trail"):
            (bin_dir / name).write_text("#!/bin/sh\n" f"printf '%s\\n' \"{name} $*\" >> '{mark}'\n")
            (bin_dir / name).chmod(0o755)
        env = {k: v for k, v in self.env.items() if k != "LONGHAUL_CLAUDE_BIN"}
        # Sprint 8: the Advisor's hook asks CLAUDE_CODE_EXECPATH for its version
        # when the path names none; the suite runs inside a session, whose
        # variable would name the real claude, so the marker stands there.
        env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', os.defpath)}", LONGHAUL_TEST_CHILD="1",
                   CLAUDE_CODE_EXECPATH=str(bin_dir / "claude"))
        # The positive control (review, Sprint 5, G5): the marker programs
        # are the ones this PATH gives, and running each leaves the mark;
        # without this, a mark that stayed empty proved nothing. Run by
        # path, never by name: a lookup that missed one (seen, with the
        # marker not yet executable) resolves to the real program on the
        # owner's PATH.
        for name in ("claude", "codegraph", "trail"):
            self.assertEqual(shutil.which(name, path=env["PATH"]), str(bin_dir / name), f"the marker {name} is first on PATH")
            control = subprocess.run([str(bin_dir / name), "--version"], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(control.returncode, 0, name)
        self.assertEqual(mark.read_text(), "claude --version\ncodegraph --version\ntrail --version\n",
                         "each marker records its name and arguments")
        mark.write_text("")
        out = subprocess.run([sys.executable, "-m", "unittest", "test_longhaul.Settings", "test_longhaul.Guard",
                              "test_longhaul.Index", "test_longhaul.Advisor", "-q"],
                             cwd=HERE, env=env, capture_output=True, text=True, timeout=300)  # slow CI runners
        self.assertEqual(mark.read_text(), "", "a test ran a program on PATH, with these arguments")
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])

    def test_status_goes_on_with_the_version_unknown_when_claude_cannot_be_run(self):
        # The fail-safe the test above leans on: a claude that cannot start
        # is a word on each line that asked it, never a stop. (fake_stream.py
        # answers --version with a line that is no version, so "(version
        # unknown)" alone would not tell the two apart.)
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "status"],
                             env={**self.env, "LONGHAUL_CLAUDE_BIN": "/nonexistent"}, capture_output=True, text=True,
                             timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("could not check", out.stdout, "the account's line")
        self.assertIn("(version unknown)", out.stdout)
        self.assertNotIn("Traceback", out.stderr)


class Checkpoint(unittest.TestCase):
    """`longhaul checkpoint` (Sprint 4, C1): the model's own structured
    state, saved by the skill through `checkpoint save` and put back by the
    plugin's SessionStart hook after a compaction or a resume (a /clear
    gives the session a new id, so until the live check says otherwise
    nothing promises a return after one; the hook answers a clear with
    the same id all the same)."""

    # The model's text. It belongs in the file and in the hook's stdout, which
    # the engine hands to the model, and nowhere else: not in `save`'s lines,
    # the log, a refusal or inspect. A string no path or message would hold.
    S = "SENTINEL-ZEBRA-7731"
    # What names the session in the Bash tool's environment (and a skill's
    # ${CLAUDE_SESSION_ID}, a substitution, in case it is ever a variable too).
    SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")

    HOOK = [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import longhaul; "
                                  "sys.exit(longhaul.main(['checkpoint']))", str(HERE)]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()  # /var is /private/var: what getcwd() will say
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved = longhaul.LONGHAUL_HOME
        longhaul.LONGHAUL_HOME = self.home
        self.work = self.tmp / "work"  # where the model runs the save: the files it names are relative to it
        self.work.mkdir()
        for name in ("a.py", "b.py", "c.py"):
            (self.work / name).write_text(name)
        # The suite runs from inside a session too, whose variables would
        # name it: the save and the hook under test get a terminal's
        # environment unless a test says otherwise.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LONGHAUL_") and k not in Checkpoint.SESSION_VARS}
        self.env.update(LONGHAUL_HOME=str(self.home), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "claude"),
                        LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "claude.json"), LONGHAUL_NO_NOTIFY="1")

    def tearDown(self):
        longhaul.LONGHAUL_HOME = self.saved

    def state(self, **over):
        """A state as the skill would write it, every text carrying the sentinel."""
        s = {"goal": f"finish the parser {self.S}", "next_action": f"run the tests {self.S}",
             "decisions": [f"argparse stays {self.S}"], "done": [f"the plan was read {self.S}"],
             "in_progress": [f"the save command {self.S}"], "blocked": [],
             "changed_files": ["a.py", "b.py", "c.py"], "verification": [f"python3 -m unittest {self.S}"]}
        s.update(over)
        return s

    def save(self, state, *args, cwd=None, env=None):
        """`longhaul checkpoint save [session] [--file PATH]` as the shim runs
        it: the state on stdin (or in the file), from the folder the model
        works in, with `env` added to a terminal's environment."""
        raw = state if isinstance(state, str) else json.dumps(state)
        return subprocess.run([sys.executable, str(HERE / "longhaul.py"), "checkpoint", "save", *args],
                              input=None if "--file" in args else raw, cwd=cwd or self.work, env={**self.env, **(env or {})},
                              capture_output=True, text=True, timeout=30)

    def start(self, source="compact", session="s1", agent=None):
        # The hook's cwd is not the save's: the files a checkpoint names are
        # relative to the folder the save ran in, which the file records.
        data = {"session_id": session, "hook_event_name": "SessionStart", "source": source,
                "cwd": str(self.tmp / "elsewhere"), "transcript_path": "/t/s1.jsonl"}
        if agent:
            data["agent_id"], data["agent_type"] = agent, "Explore"
        return data

    def file(self, session="s1"):
        return self.home / "checkpoints" / f"{session}.json"

    def log(self):
        path = self.home / "longhaul.log"
        return path.read_text() if path.exists() else ""

    def check(self, data, now=None):
        return longhaul.checkpoint_check(data, now)

    def context(self, answer):
        return (answer or {}).get("hookSpecificOutput", {}).get("additionalContext", "")

    def process(self, raw, env=None):
        """The hook as the engine runs it, with `env` added to its environment."""
        return subprocess.run(self.HOOK, input=raw, env={**self.env, **(env or {})}, capture_output=True, text=True,
                              timeout=10)

    def test_a_compaction_a_clear_or_a_resume_puts_the_checkpoint_back_with_its_age_and_what_changed_since(self):
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        at = json.loads(self.file().read_text())["written_at"]
        (self.work / "b.py").write_text("b, again")
        os.utime(self.work / "b.py", (at + 5, at + 5))
        (self.work / "c.py").unlink()
        for source in ("compact", "clear", "resume"):
            answer = self.check(self.start(source), now=at + 125)
            self.assertEqual(set(answer), {"hookSpecificOutput"}, source)
            self.assertEqual(answer["hookSpecificOutput"]["hookEventName"], "SessionStart")
            text = self.context(answer)
            when = dt.datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")
            self.assertTrue(text.startswith(f"longhaul checkpoint, written {when} (2 min ago) in "
                                            f"{longhaul.home_path(str(self.work))}:\n"), text)
            for key in ("goal", "next_action", "decisions", "done", "in_progress", "blocked", "changed_files", "verification"):
                self.assertIn(f"\n{key}:", text, "every field, by its name in the schema")
            self.assertIn(f"\ngoal: finish the parser {self.S}\n", text)
            self.assertIn(f"\ndecisions:\n- argparse stays {self.S}\n", text)
            self.assertIn("\nblocked: (none)\n", text)
            self.assertIn("\nchanged_files:\n- a.py\n- b.py\n- c.py\n", text)
            tail = text.split("\nChanged since the checkpoint: ")[1]
            self.assertEqual(tail, "b.py (changed), c.py (gone); check them before trusting this.")
        # The age is shown whatever it is: staleness is not withheld.
        self.assertIn("(3 h ago)", self.context(self.check(self.start(), now=at + 3 * 3600 + 10)))
        self.assertIn("(2 days ago)", self.context(self.check(self.start(), now=at + 2 * 86400 + 10)))
        self.assertIn("(0 s ago)", self.context(self.check(self.start(), now=at - 100)), "a clock set back")
        # Nothing changed since: it says so, and so does a checkpoint naming no file.
        os.utime(self.work / "b.py", (at - 5, at - 5))
        self.assertEqual(self.save(self.state(changed_files=["a.py", "b.py"]), "s1").returncode, 0)
        text = self.context(self.check(self.start()))
        self.assertTrue(text.endswith("\nChanged since the checkpoint: none of the 2 files named."), text)
        self.assertEqual(self.save(self.state(changed_files=[]), "s1").returncode, 0)
        text = self.context(self.check(self.start()))
        self.assertTrue(text.endswith("\nChanged since the checkpoint: no files were named."), text)
        # As a process, the way the engine runs it: the text on stdout, nothing on stderr.
        out = self.process(json.dumps(self.start("resume")))
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        self.assertIn(self.S, json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("checkpoint for s1 put back after resume (", self.log(), "the log says so, by size")
        self.assertNotIn(self.S, self.log())

    def test_the_largest_checkpoint_the_save_takes_comes_back_whole(self):
        # Sprint 4 review, A11: the engine caps a hook's additionalContext
        # at 10,000 characters; past it the model gets a file path and a
        # preview instead of the text. So the file's cap is 8,000 bytes,
        # and the text of the largest checkpoint the save takes, with every
        # one of its 20 files changed since, fits with room for the header.
        names = [f"f{i:02}.py" for i in range(20)]
        for name in names:
            (self.work / name).write_text(name)

        def state(n):
            lists = {key: ["x" * n] * 20 for key in ("decisions", "done", "in_progress", "blocked", "verification")}
            return self.state(goal="g" * 1000, next_action="n" * 1000, changed_files=names, **lists)

        def size(n):  # what the save measures
            record = {"schema_version": 1, "session_id": "s1", "written_at": time.time(), "cwd": str(self.work), **state(n)}
            return len(json.dumps(record, indent=2)) + 1

        n = next(n for n in range(1, 1000) if size(n + 1) > longhaul.CHECKPOINT_BYTES_MAX)
        self.assertGreater(size(n), longhaul.CHECKPOINT_BYTES_MAX - 200, "just under the cap")
        out = self.save(state(n), "s1")
        self.assertEqual(out.returncode, 0, out.stderr)
        at = json.loads(self.file().read_text())["written_at"]
        for name in names:
            os.utime(self.work / name, (at + 5, at + 5))
        text = self.context(self.check(self.start(), now=at + 10))
        self.assertEqual(text.count(" (changed)"), 20)
        self.assertLess(len(text), 10_000, len(text))

    def test_a_startup_or_a_subagent_gets_nothing_back(self):
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        self.assertIsNone(self.check(self.start("startup")), "a new session has nothing to put back")
        self.assertIsNone(self.check(self.start("compact", agent="agent-1")), "a subagent has no checkpoint")
        self.assertIsNone(self.check(self.start("compact", session="s2")), "another session: no file")
        self.assertIsNone(self.check(self.start("compact", session="../s1")))
        self.assertIsNone(self.check({"hook_event_name": "SessionStart", "source": "compact"}), "no session")
        self.assertIsNone(self.check({"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Read"}))
        self.assertIsNone(self.check({}))
        self.assertIsNone(self.check("not an object"))
        self.assertNotIn("not put back", self.log(), "no file is the usual case, not worth a line")
        self.assertIsNotNone(self.check(self.start("compact")), "the checkpoint is still there for its own session")

    def test_a_corrupt_stale_half_written_or_foreign_checkpoint_never_stops_a_session_start(self):
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        good = self.file().read_text()
        kept = json.loads(good)
        cases = [
            ("{not json", "not JSON (9 bytes)"),
            # Cut halfway through a string: a write that never finished.
            (good[:good.index(self.S) + 6], f"not JSON ({good.index(self.S) + 6} bytes)"),
            ("", "not JSON (0 bytes)"),
            ("[]", "not an object"),
            ("null", "not an object"),
            (json.dumps({**kept, "session_id": "s2"}), "the session_id inside is another session's"),
            (json.dumps({**kept, "schema_version": 2}), "schema_version is not 1"),
            (json.dumps({**kept, "schema_version": True}), "schema_version is not 1"),
            (json.dumps({k: v for k, v in kept.items() if k != "goal"}), "goal is missing"),
            (json.dumps({**kept, "written_at": "yesterday"}), "written_at is not a number"),
            (json.dumps({**kept, "done": f"not a list {self.S}"}), "done is not a list"),
            # A hand-edited file (review, A3, A4, A6): a time fromtimestamp()
            # would not take, a cwd os.stat() would not take (a NUL raises
            # ValueError, not OSError), a nest deeper than json reads
            # (RecursionError, not ValueError, on a Python whose limit is
            # 1,000 levels, 3.11; 3.14 reads 30,000 and says not JSON), a
            # file far over the cap, which is not even read, and an unknown
            # key, whose name is the editor's text and stays out of the log.
            (json.dumps({**kept, "written_at": 1e300}), "written_at is out of range"),
            (json.dumps({**kept, "written_at": -1}), "written_at is out of range"),
            (json.dumps({**kept, "written_at": kept["written_at"] + 2 * 86400}), "written_at is out of range"),
            (json.dumps({**kept, "cwd": str(self.work) + "\x00"}), "cwd is not a plain path"),
            (json.dumps({**kept, "cwd": 7}), "cwd is not a plain path"),
            (json.dumps({k: v for k, v in kept.items() if k != "cwd"}), "cwd is not a plain path"),
            ("[" * 30_000, "not JSON (30000 bytes)"),
            ("[" * 70_000, f"over {longhaul.CHECKPOINT_FILE_MAX:,} bytes"),
            (json.dumps({**kept, "extra": self.S}), "unknown key (5 characters)"),
        ]
        for i, (raw, reason) in enumerate(cases):
            self.file().write_text(raw)
            self.assertIsNone(self.check(self.start("compact")), reason)
            out = self.process(json.dumps(self.start("resume")))
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""), reason)
            # Each run logs its one line and nothing before it: not "put
            # back" first, which a text that then raised left as a lie
            # (A3); and a case that logs nothing cannot pass on an earlier
            # case's line (A20).
            lines = self.log().splitlines()
            self.assertEqual(len(lines), 1 + 2 * (i + 1), (reason, lines[-3:]))
            self.assertEqual([line.split(" ", 2)[2] for line in lines[-2:]], [f"checkpoint for s1 not put back: {reason}"] * 2)
        self.assertNotIn(self.S, self.log())
        self.assertNotIn("extra", self.log(), "an unknown key's name is the editor's text")
        # The hook's own input can be junk too.
        for raw in ("{not json", "[]", json.dumps({"session_id": ["s1"], "hook_event_name": "SessionStart", "source": "compact"})):
            out = self.process(raw)
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""), raw)

    def test_the_checkpoint_hook_exits_0_whatever_happens_and_is_not_slow(self):
        # The guard's timing test, for the second hook: it runs at every
        # compaction, clear and resume, and must never stop one.
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        data = json.dumps(self.start("compact"))
        took = []
        for raw in (data, data, data, "not json", "", json.dumps({"hook_event_name": "SessionStart"})):
            started = time.time()
            out = self.process(raw)
            took.append(time.time() - started)
            self.assertEqual(out.returncode, 0, out.stderr)
            if out.stdout.strip():
                json.loads(out.stdout)  # anything it prints is JSON the engine can read
        self.assertIn(self.S, self.process(data).stdout)
        self.assertLess(sorted(took)[len(took) // 2], 0.5, f"median of {[round(t, 3) for t in took]}")

    def test_the_plugin_registers_the_guard_for_startup_and_compaction_and_the_checkpoint_for_compaction_clear_and_resume(self):
        hooks = json.loads((HERE / "hooks" / "hooks.json").read_text())
        self.assertEqual(set(hooks["hooks"]), {"PreToolUse", "SessionStart", "PostToolUse"}, "these three events and nothing else")
        # The Advisor's two entries on Agent, Task, SendMessage and Workflow are pinned in Advisor (Sprint 8).
        self.assertEqual([entry["matcher"] for entry in hooks["hooks"]["PreToolUse"]],
                         ["Read|Bash", "Agent|Task|SendMessage|Workflow"])
        calls = hooks["hooks"]["PreToolUse"][0]
        self.assertEqual(len(hooks["hooks"]["SessionStart"]), 3)
        starts = {entry["matcher"]: entry for entry in hooks["hooks"]["SessionStart"]}
        self.assertEqual(calls["matcher"], "Read|Bash")
        # The guard names the project's code index at startup (Sprint 6, G3)
        # and forgets its reads after a compaction or a clear; the
        # checkpoint comes back after a compaction or a resume (the clear
        # in its matcher is kept: it is harmless, and the live check will
        # say whether a /clear keeps the id).
        self.assertEqual(set(starts), {"startup", "compact|clear", "compact|clear|resume"})
        handlers = {}
        for entry, command in ((calls, "guard"), (starts["startup"], "guard"), (starts["compact|clear"], "guard"),
                               (starts["compact|clear|resume"], "checkpoint")):
            [handler] = entry["hooks"]
            self.assertEqual((handler["type"], handler["command"]), ("command", "python3"))
            # -I: the hook runs in the project, and without it the project's
            # folder is first on sys.path (a json.py there would be imported).
            self.assertEqual(handler["args"][:2], ["-I", "-c"])
            self.assertIn("import longhaul", handler["args"][2])
            self.assertIn(f"main(['{command}'])", handler["args"][2])
            self.assertEqual(handler["args"][3], "${CLAUDE_PLUGIN_ROOT}")
            self.assertLessEqual(handler["timeout"], 10, "a slow hook is a tax on every call")
            self.assertIn(f"(longhaul {command})", handler["statusMessage"])
            handlers[command, entry["matcher"]] = handler
        # One command line run four ways: the checkpoint's differs from the guard's in the command alone.
        self.assertEqual(handlers["guard", "Read|Bash"]["args"], handlers["guard", "compact|clear"]["args"])
        self.assertEqual(handlers["guard", "Read|Bash"]["args"], handlers["guard", "startup"]["args"])
        self.assertEqual(handlers["checkpoint", "compact|clear|resume"]["args"][2],
                         handlers["guard", "Read|Bash"]["args"][2].replace("'guard'", "'checkpoint'"))
        # The tests run the hook as the plugin does: Guard.HOOK is the guard's entry, the root aside.
        self.assertEqual(Guard.HOOK[1:-1], handlers["guard", "Read|Bash"]["args"][:-1])
        # Each says what it is doing: a compaction runs three of them, and the
        # guard's SessionStart entry used to repeat the PreToolUse message.
        self.assertEqual(len({h["statusMessage"] for h in handlers.values()}), 4)
        self.assertIn("Looking for a code index", handlers["guard", "startup"]["statusMessage"])
        self.assertIn("Forgetting this session's reads", handlers["guard", "compact|clear"]["statusMessage"])
        # It flashes at every compaction and resume, most often with nothing
        # to put back (review, A17).
        self.assertIn("Looking for a checkpoint", handlers["checkpoint", "compact|clear|resume"]["statusMessage"])
        requires_git(self)
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "hooks/hooks.json"], cwd=HERE).returncode, 1)

    def test_checkpoint_save_validates_the_state_and_writes_it_whole_and_private(self):
        before = time.time()
        out = self.save(self.state(next_action=f"run\tthe tests\x1b[0m {self.S}"), "s1")
        self.assertEqual(out.returncode, 0, out.stderr)
        size = self.file().stat().st_size
        self.assertEqual(out.stdout, f"longhaul checkpoint: saved for s1 ({size:,} bytes); "
                                     "it comes back after a compaction or a resume\n")
        self.assertEqual(out.stderr, "")
        self.assertEqual(oct(self.file().stat().st_mode & 0o777), oct(0o600))
        self.assertEqual([p.name for p in self.file().parent.iterdir()], ["s1.json"], "whole or not at all: no .tmp")
        kept = json.loads(self.file().read_text())
        self.assertEqual((kept["schema_version"], kept["session_id"]), (1, "s1"))
        self.assertLessEqual(before, kept["written_at"])
        self.assertLessEqual(kept["written_at"], time.time())
        self.assertEqual(kept["cwd"], str(self.work), "the shim's working directory, which the files are relative to")
        self.assertEqual(kept["goal"], f"finish the parser {self.S}")
        self.assertEqual(kept["next_action"], f"run?the tests?[0m {self.S}", "control characters go, as shown() prints them")
        self.assertEqual(kept["blocked"], [], "every list is there, empty or not")
        self.assertEqual(set(kept), {"schema_version", "session_id", "written_at", "cwd", "goal", "next_action", "decisions",
                                     "done", "in_progress", "blocked", "changed_files", "verification"})
        # The model's text stays in the file: the line it reads and the log
        # name the session and the size only.
        self.assertNotIn(self.S, out.stdout)
        self.assertIn(f"checkpoint for s1 saved ({size:,} bytes)", self.log())
        self.assertNotIn(self.S, self.log())
        # Saved again: the newer state replaces the older, whole.
        out = self.save(self.state(goal=f"the second goal {self.S}"), "s1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(self.file().read_text())["goal"], f"the second goal {self.S}")
        self.assertEqual([p.name for p in self.file().parent.iterdir()], ["s1.json"])

    def test_checkpoint_save_refuses_bad_json_an_unknown_key_a_wrong_type_an_oversize_field_or_a_key_shape_and_names_only_the_field(self):
        long = "x" * 1_001
        cases = [
            ("{not json", "not JSON"),
            ("", "nothing on stdin"),
            ("[]", "the state is not a JSON object"),
            (self.state(extra=self.S), "unknown key 'extra'"),
            ({"goal": self.S}, "next_action is missing"),
            (self.state(goal=[self.S]), "goal is not text"),
            (self.state(goal="  "), "goal is empty"),
            (self.state(next_action=7), "next_action is not text"),
            (self.state(decisions=self.S), "decisions is not a list"),
            (self.state(decisions=[self.S, 5]), "decisions[1] is not text"),
            (self.state(done=[""]), "done[0] is empty"),
            (self.state(goal=long + self.S), f"goal is {1_001 + len(self.S):,} characters; the cap is 1,000"),
            (self.state(done=[self.S] * 21), "done has 21 items; the cap is 20"),
            (self.state(verification=[f"export ANTHROPIC_API_KEY=sk-ant-{self.S}"]), "verification[0] holds what looks like a credential"),
            (self.state(next_action=f"AKIA{self.S}"), "next_action holds what looks like a credential"),
            (self.state(blocked=[f"-----BEGIN PRIVATE KEY----- {self.S}"]), "blocked[0] holds what looks like a credential"),
            (self.state(goal=f"ghp_{self.S}"), "goal holds what looks like a credential"),
            (self.state(done=[f"{'d' * 900} {self.S}"] * 20), "bytes in all; the cap is 8,000"),
            # An unknown key's name is the model's text too (review, A6): the
            # refusal names it only when it is not shaped like a credential.
            (self.state(**{f"sk-ant-{self.S}": "x"}), "unknown key (looks like a credential)"),
            (self.state(**{f"AKIA{self.S}": "x"}), "unknown key (looks like a credential)"),
        ]
        for raw, reason in cases:
            out = self.save(raw, "s1")
            self.assertEqual(out.returncode, 2, (reason, out.stderr))
            self.assertEqual(out.stdout, "", reason)
            self.assertTrue(out.stderr.startswith("longhaul checkpoint: not saved: "), out.stderr)
            self.assertIn(reason, out.stderr)
            self.assertNotIn(self.S, out.stderr, reason)
        # The log has an unknown key's length, never its name.
        self.assertIn("checkpoint for s1 not saved: unknown key (5 characters)", self.log())
        self.assertIn(f"checkpoint for s1 not saved: unknown key ({7 + len(self.S)} characters)", self.log())
        self.assertNotIn("extra", self.log())
        self.assertNotIn("sk-ant", self.log())
        for args in (("../x",), ("a b",), ()):
            out = self.save(self.state(), *args)
            self.assertEqual(out.returncode, 2, (args, out.stderr))
            self.assertIn("session id", out.stderr, args)
            self.assertEqual(out.stdout, "", args)
            self.assertNotIn(self.S, out.stderr, args)
        self.assertFalse((self.home / "checkpoints").exists(), "a refusal writes nothing, not even a .tmp")
        self.assertIn("checkpoint for s1 not saved: done has 21 items; the cap is 20", self.log())
        self.assertNotIn(self.S, self.log())

    def test_ago_text_says_how_long_ago_in_the_largest_unit_that_fits(self):
        # Sprint 4 review, A8: the wording the checkpoint's header uses,
        # one place beside clock(); a clock set back is "0 s ago".
        self.assertEqual([longhaul.ago_text(s) for s in (5, 59, 60, 90, 3599, 3600 * 3, 86400 - 1, 86400, 86400 * 2, -1)],
                         ["5 s ago", "59 s ago", "1 min ago", "1 min ago", "59 min ago", "3 h ago", "23 h ago",
                          "1 day ago", "2 days ago", "0 s ago"])

    def test_checkpoint_save_says_when_the_file_could_not_be_written(self):
        # Sprint 4 review, A5: a full disk or an unwritable home gave a
        # traceback and exit 1; the model reads one line and exit 2.
        blocked = self.tmp / "not-a-folder"
        blocked.write_text("")
        out = self.save(self.state(), "s1", env={"LONGHAUL_HOME": str(blocked)})
        self.assertEqual((out.returncode, out.stdout), (2, ""), out.stderr)
        self.assertEqual(out.stderr.count("\n"), 1, out.stderr)
        self.assertTrue(out.stderr.startswith("longhaul checkpoint: not saved: could not write the file: "), out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        self.assertFalse(blocked.is_dir())
        # The folder the save runs in was deleted under the shell.
        gone = self.tmp / "gone"
        gone.mkdir()
        out = subprocess.run(["sh", "-c", 'cd "$1" && rmdir "$1" && exec "$2" "$3" checkpoint save s1', "sh", str(gone),
                              sys.executable, str(HERE / "longhaul.py")],
                             input=json.dumps(self.state()), env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual((out.returncode, out.stdout), (2, ""), out.stderr)
        self.assertEqual(out.stderr.count("\n"), 1, out.stderr)
        self.assertTrue(out.stderr.startswith("longhaul checkpoint: not saved: could not read the working directory: "), out.stderr)
        self.assertFalse(self.file().exists(), "nothing was written")

    def test_checkpoint_save_takes_this_sessions_id_from_the_environment_and_refuses_another_sessions(self):
        # Sprint 4 review, A2 and A13: the Bash tool's environment names the
        # session it runs in (CLAUDE_CODE_SESSION_ID; the ${CLAUDE_SESSION_ID}
        # of a skill's text is a substitution, not a variable). With no id
        # on the command line the save takes the environment's, and an id
        # that differs from it is refused, so a session can never plant a
        # state for another one. Only a terminal, which names no session,
        # takes any id.
        inside = {"CLAUDE_CODE_SESSION_ID": "s1"}
        out = self.save(self.state(), env=inside)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("saved for s1 (", out.stdout)
        self.assertEqual(json.loads(self.file("s1").read_text())["session_id"], "s1")
        out = self.save(self.state(), "s1", env=inside)
        self.assertEqual(out.returncode, 0, "this session's id, spelled out")
        out = self.save(self.state(), "s2", env=inside)
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertEqual(out.stderr, "longhaul checkpoint: not saved: the session id is not this session's: "
                                     "s2 was given and this session is s1\n")
        self.assertFalse(self.file("s2").exists(), "nothing was written for the other session")
        self.assertIn("checkpoint for s2 not saved: the session id is not this session's", self.log())
        out = self.save(self.state(), "s3")
        self.assertEqual(out.returncode, 0, "a terminal names no session: the id given is taken")
        self.assertTrue(self.file("s3").is_file())
        out = self.save(self.state())
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertEqual(out.stderr, "longhaul checkpoint: not saved: save needs the session id: pass it, or run from "
                                     "inside a Claude Code session (CLAUDE_CODE_SESSION_ID)\n")
        out = self.save(self.state(), env={"CLAUDE_CODE_SESSION_ID": "../x"})
        self.assertEqual(out.returncode, 2, "an environment id is held to the same shape")
        self.assertIn("the session id must be", out.stderr)
        self.assertEqual(sorted(p.name for p in self.file().parent.iterdir()), ["s1.json", "s3.json"])
        self.assertNotIn(self.S, self.log())

    def test_checkpoint_save_reads_the_state_from_a_file_when_asked(self):
        # Sprint 4 review, A14: --file is the way that needs nothing on
        # stdin, and the live check of 2026-10-20 made it the only way: the
        # skill's allowed-tools rule does not match a heredoc.
        path = self.work / "state.json"
        path.write_text(json.dumps(self.state(goal=f"from the file {self.S}")))
        out = self.save("", "s1", "--file", str(path))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(self.file().read_text())["goal"], f"from the file {self.S}")
        out = self.save("", "s1", "--file", str(self.work / "missing.json"))
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertEqual(out.stderr.count("\n"), 1, out.stderr)
        self.assertTrue(out.stderr.startswith("longhaul checkpoint: not saved: could not read "), out.stderr)
        self.assertIn("No such file", out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        path.write_bytes(b"\xff\xfe not text")
        out = self.save("", "s1", "--file", str(path))
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertEqual(out.stderr.count("\n"), 1, out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        self.assertEqual(json.loads(self.file().read_text())["goal"], f"from the file {self.S}", "the file kept is the good one")

    def test_checkpoint_save_removes_the_skills_state_file_once_saved_and_leaves_any_other(self):
        # Live check, 2026-10-20: the skill writes the state with the Write
        # tool as LONGHAUL_HOME/checkpoints/<session>.state.json (0644, the
        # model's text beside the 0600 checkpoint) and saves it with --file.
        # Saved or refused, that file goes, and a refusal says so: left after
        # a refusal (review, Sprint 5, A1) it kept the model's text, a
        # credential among it perhaps, for good, in a file nothing swept
        # and the README did not list. A file anywhere else, or named
        # otherwise, is the user's and is left, refused or not.
        folder = self.home / "checkpoints"
        folder.mkdir()
        state = folder / "s1.state.json"
        state.write_text(json.dumps(self.state(goal=f"from the state file {self.S}")))
        out = self.save("", "s1", "--file", str(state))
        self.assertEqual(out.returncode, 0, out.stderr)
        size = self.file().stat().st_size
        self.assertEqual(out.stdout, f"longhaul checkpoint: saved for s1 ({size:,} bytes); "
                                     "it comes back after a compaction or a resume\n")
        self.assertEqual(json.loads(self.file().read_text())["goal"], f"from the state file {self.S}")
        self.assertEqual([p.name for p in folder.iterdir()], ["s1.json"], "the state file went, the checkpoint stays")
        for refused in ("{not json", json.dumps(self.state(goal=f"sk-ant-{self.S}")), b"\xff\xfe not text"):
            state.write_bytes(refused if isinstance(refused, bytes) else refused.encode())
            out = self.save("", "s1", "--file", str(state))
            self.assertEqual((out.returncode, out.stdout), (2, ""), out.stderr)
            self.assertEqual(out.stderr.count("\n"), 1, out.stderr)
            self.assertTrue(out.stderr.endswith("; the state file was removed, write it again\n"), out.stderr)
            self.assertEqual([p.name for p in folder.iterdir()], ["s1.json"], "refused: the text goes too")
            self.assertEqual(json.loads(self.file().read_text())["goal"], f"from the state file {self.S}")
        for other in (self.work / "s1.state.json", folder / "draft.json"):
            other.write_text("{not json")
            out = self.save("", "s1", "--file", str(other))
            self.assertEqual(out.returncode, 2, out.stderr)
            self.assertNotIn("state file", out.stderr, f"{other.name} is not the skill's file: nothing to say")
            self.assertTrue(other.is_file(), f"{other.name} is not the skill's file: left, refused")
            other.write_text(json.dumps(self.state(goal=f"from {other.name} {self.S}")))
            out = self.save("", "s1", "--file", str(other))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("state file", out.stdout)
            self.assertTrue(other.is_file(), f"{other.name} is not the skill's file: left, saved")
            self.assertEqual(json.loads(self.file().read_text())["goal"], f"from {other.name} {self.S}")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["draft.json", "s1.json"])
        self.assertNotIn(self.S, self.log())

    def test_the_checkpoints_folder_is_private_when_the_save_makes_it(self):
        # Review, Sprint 5 (A1): write_json() made checkpoints/ with the
        # umask's mode (0755), a folder anyone could list beside its 0600
        # files. A folder that is there already (the Write tool makes it
        # first in the skill's flow) keeps its mode: it is the user's.
        folder = self.home / "checkpoints"
        self.assertFalse(folder.exists())
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        self.assertEqual(oct(folder.stat().st_mode & 0o777), oct(0o700))
        self.assertEqual(oct(self.file().stat().st_mode & 0o777), oct(0o600))
        shutil.rmtree(folder)
        folder.mkdir(mode=0o755)
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        self.assertEqual(oct(folder.stat().st_mode & 0o777), oct(0o755), "left as the user made it")

    def test_the_hook_run_by_hand_from_inside_a_session_gives_no_other_sessions_checkpoint(self):
        # Sprint 4 review, A1: the shim no longer runs the hook form, and
        # `python3 longhaul.py checkpoint` with hook-shaped JSON for another
        # session is refused too when the environment names the session it
        # runs in. The engine's environment can never name a different
        # session, so the real hook is not touched by this: with the same
        # id, or in a terminal, the checkpoint comes back as before.
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        out = self.process(json.dumps(self.start("compact")), env={"CLAUDE_CODE_SESSION_ID": "s2"})
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertEqual(out.stderr.count("\n"), 1, out.stderr)
        self.assertIn("s1", out.stderr)
        self.assertNotIn(self.S, out.stderr)
        self.assertIn("checkpoint for s1 not put back: run by hand from inside session s2", self.log())
        for env in ({"CLAUDE_CODE_SESSION_ID": "s1"}, {"CLAUDECODE": "1"}, {}):
            out = self.process(json.dumps(self.start("compact")), env=env)
            self.assertEqual((out.returncode, out.stderr), (0, ""), env)
            self.assertIn(self.S, out.stdout, env)
        # A terminal with nothing piped in has nothing to read (A18): say
        # so instead of waiting on it.
        master, slave = os.openpty()
        try:
            out = subprocess.run(self.HOOK, stdin=slave, env=self.env, capture_output=True, text=True, timeout=10)
        finally:
            os.close(master)
            os.close(slave)
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertEqual(out.stderr, "longhaul checkpoint: the state (save) or the hook's JSON goes on stdin; "
                                     "nothing to read from a terminal\n")

    def test_the_checkpoint_text_never_reaches_inspect_the_log_or_a_hooks_stderr(self):
        # The file holds the model's text on purpose. It is read only by the
        # hook, whose stdout the engine hands to the model; everything else
        # names the checkpoint by session, size, field and reason.
        self.assertEqual(self.save(self.state(), "s1").returncode, 0)
        self.assertEqual(self.save(self.state(decisions=[self.S] * 21), "s1").returncode, 2)
        self.assertIn(self.S, self.file().read_text())
        for raw in (json.dumps(self.start("compact")), json.dumps(self.start("resume")), "{not json"):
            out = subprocess.run(self.HOOK, input=raw, env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual((out.returncode, out.stderr), (0, ""), raw)
            if out.stdout:
                answer = json.loads(out.stdout)
                answer["hookSpecificOutput"].pop("additionalContext")  # the one place the text may be
                self.assertNotIn(self.S, json.dumps(answer))
        transcript = self.tmp / "projects" / "p" / "s1.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5",
                                          "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n")
        report = longhaul.inspect_transcript(transcript)
        self.assertNotIn(self.S, "\n".join(longhaul.inspect_lines(report)))
        self.assertNotIn(self.S, json.dumps(list(longhaul.inspect_records(report))))
        self.assertIn("checkpoint for s1", self.log(), "the log names the session")
        self.assertNotIn(self.S, self.log())


class Shim(unittest.TestCase):
    """bin/longhaul: what Claude may run of Longhaul inside a session
    (docs/adr/0001-plugin-layout.md, decision 5), and P5."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # A terminal's environment (the suite runs from inside a session too).
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LONGHAUL_") and k not in Checkpoint.SESSION_VARS}
        # Every data path in scratch: `usage` and `inspect` read the config
        # folder, which must never be the real one (review, Sprint 3).
        self.env.update(LONGHAUL_HOME=str(self.tmp / "home"), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.tmp / "claude"),
                        LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "claude.json"), LONGHAUL_NO_NOTIFY="1")

    def shim(self, *args, input=None, **env):
        return subprocess.run([str(HERE / "bin" / "longhaul"), *args], input=input, capture_output=True, text=True,
                              env={**self.env, **env}, timeout=30)

    def test_the_shim_runs_the_read_only_commands_of_the_checkout_it_sits_in(self):
        out = self.shim("-h")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("usage: longhaul", out.stdout)
        self.assertIn("longhaul inspect", out.stdout, "the Usage block reaches -h (review, Sprint 3)")
        out = self.shim("inspect", "nope")
        self.assertEqual(out.returncode, 1, "inspect ran, and found nothing: its own exit code")
        self.assertIn("longhaul inspect:", out.stderr)
        out = self.shim("usage")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Allowance", out.stdout)

    def test_the_shim_refuses_the_supervisor_and_anything_that_changes_state(self):
        # From the Bash tool a plain `longhaul` would start a second claude
        # under a session, with Remote Control and caffeinate.
        for args in ((), ("-c",), ("-r", "x"), ("setup",), ("add", "A", "a@b"), ("remove", "A"), ("clear",),
                     ("pipe", "--", "x"), ("wrap",), ("statusline",), ("guard",), ("--switch", "auto")):
            out = self.shim(*args)
            self.assertEqual(out.returncode, 2, args)
            self.assertIn("run it in a terminal", out.stderr, args)
            self.assertEqual(out.stdout, "", args)
        self.assertIn("nothing to run", self.shim().stderr)
        self.assertFalse((self.tmp / "home").exists(), "nothing was written")

    def test_the_shim_runs_advisor_status_and_nothing_else_of_the_advisor(self):
        # Sprint 8: `advisor status` is the advise skill's first step. A probe
        # record is half of the Advisor's gate, so recording one is the
        # user's, in a terminal; the hooks are the engine's.
        exe = str(self.tmp / "versions" / "2.1.307" / "claude")  # never the session's own claude
        out = self.shim("advisor", "status", CLAUDE_CODE_EXECPATH=exe)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[:3], ["LONGHAUL_ADVISOR: off", "Probe record: none",
                                     "Running Claude Code: 2.1.307 (from the path)"])
        self.assertIn("Enabled key: missing", lines)
        self.assertEqual(lines[-1], "off: LONGHAUL_ADVISOR is not experiment")
        # F8: enabling is the owner's, in a terminal; disable too goes there.
        for args in (("advisor",), ("advisor", "record-probe", "x.json"), ("advisor", "-h"), ("advisor-hook",),
                     ("advisor-result",), ("advisor", "enable"), ("advisor", "disable")):
            out = self.shim(*args, CLAUDE_CODE_EXECPATH=exe)
            self.assertEqual((out.returncode, out.stdout), (2, ""), args)
            self.assertIn("run it in a terminal", out.stderr, args)
        self.assertFalse((self.tmp / "home" / "advisor").exists(), "nothing was written")
        self.assertIn("advisor status", self.shim("nope").stderr, "the refusal names what does run")
        self.assertIn("longhaul advisor status", self.shim("-h").stdout, "the Usage block names it")

    def test_the_shim_runs_checkpoint_save_which_writes_one_file_under_the_home(self):
        # Sprint 4, C1: decision 5 of ADR-0001 amended by the owner. The model
        # saves its own state through the shim; that file is the one thing a
        # session may write through it.
        state = {"goal": "finish the parser", "next_action": "run the tests"}
        out = self.shim("checkpoint", "save", "s1", input=json.dumps(state))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("longhaul checkpoint: saved for s1 (", out.stdout)
        home = self.tmp / "home"
        written = {p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()}
        self.assertEqual(written - {"longhaul.log"}, {"checkpoints/s1.json"})
        out = self.shim("checkpoint", "save", "s1", input="{not json")
        self.assertEqual(out.returncode, 2, "the refusal's own exit code")
        self.assertIn("longhaul checkpoint: not saved", out.stderr)
        self.assertIn("longhaul checkpoint", self.shim("-h").stdout, "the Usage block names it")
        # The skill passes no id: the save takes the session's from the Bash
        # tool's environment (review, A13).
        out = self.shim("checkpoint", "save", input=json.dumps(state), CLAUDE_CODE_SESSION_ID="s9")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue((home / "checkpoints" / "s9.json").is_file())
        # Only `checkpoint save` runs from here (review, A1): the hook form
        # with hook-shaped JSON on stdin would print another session's text.
        hook_input = json.dumps({"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"})
        for args in (("checkpoint",), ("checkpoint", "s1"), ("checkpoint", "bogus"), ("checkpoint", "-h")):
            out = self.shim(*args, input=hook_input)
            self.assertEqual((out.returncode, out.stdout), (2, ""), args)
            self.assertIn("from here only `checkpoint save` runs", out.stderr, args)
            self.assertNotIn("finish the parser", out.stderr, args)
        # A checkpoint can never be committed, wherever the home is.
        requires_git(self)
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "checkpoints/s1.json"], cwd=HERE).returncode, 0)
        self.assertIn("\ncheckpoints/\n", (HERE / ".gitignore").read_text(), "in the never-tracked block, beside guard/")

    def test_the_shim_says_when_python3_is_not_on_the_path(self):
        # Review, A16: a Mac without the developer tools has no python3, and
        # the shim's `exec python3` said only "not found". The model reads
        # what to ask the user.
        out = subprocess.run(["/bin/sh", str(HERE / "bin" / "longhaul"), "inspect", "x"], env={**self.env, "PATH": "/nonexistent"},
                             capture_output=True, text=True, timeout=30)
        self.assertEqual((out.returncode, out.stdout), (2, ""))
        self.assertIn("longhaul: python3 was not found on PATH", out.stderr)
        self.assertIn("xcode-select --install", out.stderr)
        self.assertNotIn("docs/adr", (HERE / "bin" / "longhaul").read_text(), "the rule is stated inline: docs/ does not ship")
        self.assertNotIn("docs/adr", self.shim("-h").stdout)

    def test_the_shim_is_tracked_and_executable(self):
        requires_git(self)
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "bin/longhaul"], cwd=HERE).returncode, 1)
        self.assertTrue(os.access(HERE / "bin" / "longhaul", os.X_OK))

    def test_setup_never_links_the_command_into_a_plugin_cache(self):
        # P5: link_command() links ~/.local/bin/longhaul to wherever
        # longhaul.py is; from an installed plugin that is a folder that
        # changes at every update and is deleted on uninstall.
        cache = self.tmp / ".claude" / "plugins" / "cache" / "m" / "longhaul" / "0.1.0" / "longhaul.py"
        cache.parent.mkdir(parents=True)
        cache.write_text("")
        target = self.tmp / ".local" / "bin" / "longhaul"
        said = []
        with mock.patch.object(longhaul, "HOME", self.tmp), mock.patch.object(longhaul, "say", said.append), \
                mock.patch.object(longhaul, "__file__", str(cache)):
            linked = longhaul.link_command()
        self.assertFalse(target.exists(), "no link into the cache")
        self.assertFalse(linked, "setup must not say Ready then")
        self.assertTrue(any("python3 longhaul.py setup" in s for s in said), said)
        with mock.patch.object(longhaul, "HOME", self.tmp), mock.patch.object(longhaul, "say", said.append), \
                mock.patch.object(longhaul, "__file__", str(HERE / "longhaul.py")):
            longhaul.link_command()
        self.assertEqual(target.resolve(), (HERE / "longhaul.py").resolve())

    def test_setup_with_no_accounts_links_the_command_for_the_status_line(self):
        # R1: one subscription needs no accounts.json, but the status line
        # needs ~/.local/bin/longhaul, and setup stopped at "write
        # accounts.json first" before it linked. HOME is the scratch folder
        # (Path.home() reads it), so the link never lands in the real one.
        target = self.tmp / ".local" / "bin" / "longhaul"
        out = subprocess.run([sys.executable, str(HERE / "longhaul.py"), "setup"], capture_output=True, text=True,
                             env={**self.env, "HOME": str(self.tmp)}, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(target.resolve(), (HERE / "longhaul.py").resolve(), "linked, with no account to sign in")
        self.assertIn("longhaul statusline", out.stdout, "what to point the status line at")
        self.assertIn("longhaul add", out.stdout, "a second subscription is the advanced feature")
        self.assertFalse((self.tmp / "home" / "accounts.json").exists(), "no account was invented")

    def test_setup_with_no_accounts_refuses_a_name_and_says_when_it_could_not_link(self):
        # Review, Sprint 5 (F6d): the routes beside the happy one. A name
        # with no list to find it on is exit 2; a link refused because
        # something else sits at ~/.local/bin/longhaul is exit 1, said, and
        # the file is left alone.
        env = {**self.env, "HOME": str(self.tmp)}

        def setup(*args):
            return subprocess.run([sys.executable, str(HERE / "longhaul.py"), "setup", *args], capture_output=True,
                                  text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)

        target = self.tmp / ".local" / "bin" / "longhaul"
        out = setup("Max")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("no account called Max: none is on the list yet", out.stdout)
        self.assertFalse(target.exists(), "nothing linked on a refusal")
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\n")  # someone else's command
        out = setup()
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("already exists and is not longhaul; left it alone", out.stdout)
        self.assertIn("is not linked as a command", out.stdout)
        self.assertNotIn("Ready", out.stdout)
        self.assertEqual(target.read_text(), "#!/bin/sh\n")


class InspectSkill(unittest.TestCase):
    """/longhaul:inspect (Sprint 3, P3): a plugin skill whose shell step
    runs the shim for the current session and shows the report."""

    def test_the_skill_runs_the_shim_for_this_session_through_the_plugins_own_paths(self):
        text = (HERE / "skills" / "inspect" / "SKILL.md").read_text()
        head, body = text.split("---\n", 2)[1:]
        self.assertIn("name: inspect", head)
        self.assertIn("description:", head)
        # The docs leave open whether bin/ is on the shell step's PATH, so
        # the shim is named by its full path; the session id is the
        # documented substitution.
        # The path is unquoted so that the allowed-tools rule, which names
        # the plugin's own shim and nothing else, can match it (review,
        # Sprint 3: a `*` rule would pre-approve any file called bin/longhaul).
        self.assertIn('!`${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect "${CLAUDE_SESSION_ID}"`', body)
        self.assertIn("allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul inspect *)", head)
        self.assertNotIn("$ARGUMENTS`", body.replace('"$ARGUMENTS"', ""), "the argument is quoted")
        self.assertIn("numbers", body, "it says what the report holds and never message text")
        # Sprint 6 (G3): the description lists the Search section, and the
        # body says what to do with the install command the advice line may
        # name: the report is tool output in the model's context.
        self.assertIn("searches (their chains and big results, and whether a code index was seen)", head)
        self.assertIn("The report may name an install command; show it, take no install step.", body)
        # Sprint 8: the two new sections, and the trace among the sources the body names.
        self.assertIn("usage by kind and by model", head)
        self.assertIn("the Advisor's consultations when there were any", head)
        self.assertIn("the guard's record and the Advisor experiment's trace", " ".join(body.split()))
        requires_git(self)
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "skills/inspect/SKILL.md"], cwd=HERE).returncode, 1)


class CheckpointSkill(unittest.TestCase):
    """/longhaul:checkpoint (Sprint 4, C1): a plugin skill the model or the
    user invokes to save the conversation's state through the shim."""

    def test_the_skill_saves_this_sessions_state_through_the_plugins_own_shim(self):
        text = (HERE / "skills" / "checkpoint" / "SKILL.md").read_text()
        head, body = text.split("---\n", 2)[1:]
        self.assertIn("name: checkpoint", head)
        self.assertIn("description:", head)
        self.assertIn("compaction", head, "the description says when it comes back, so the model can judge when to save")
        self.assertIn("resume", head)
        # The rule names the plugin's own shim and nothing else, and the shim
        # takes only `checkpoint save` and what follows it (review, Sprint 3
        # and A1). The state goes in a file, never on stdin: the live check
        # of 2026-10-20 saw the rule deny the heredoc form and pass --file
        # (A14). The shell step prints where to write it, under Longhaul's
        # own folder and keyed to this session by the documented
        # substitution; the save command passes no id, as ${CLAUDE_SESSION_ID}
        # is a substitution in a skill's text, not a variable the Bash tool
        # has, so the save takes the session's id from the environment (A13).
        self.assertIn("allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/longhaul checkpoint *)", head)
        # The step runs `echo`: a `!` command that is not in Claude Code's
        # documented read-only set (ls, cat, echo, pwd, head, tail, grep,
        # find, wc, which, diff, stat, du, cd) stops the skill with no
        # prompt, and printf is not in it (review, Sprint 5, H1).
        step = '!`echo "${LONGHAUL_HOME:-$HOME/.longhaul}/checkpoints/${CLAUDE_SESSION_ID}.state.json"`'
        self.assertIn(step, body)
        self.assertNotIn("printf", body)
        self.assertIn("-p", body, "in a print-mode run the Write tool is denied, so no checkpoint is saved there")
        self.assertIn("Write tool", body, "the state is written as a file, not typed into a command")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/bin/longhaul checkpoint save --file", body)
        self.assertNotIn("CLAUDE_SESSION_ID", body.replace(step, ""), "no id on the command line")
        self.assertNotIn("<<", text, "no heredoc anywhere: the allowed-tools rule does not match one")
        self.assertIn("removed", body, "it says the state file goes once saved")
        for key in longhaul.CHECKPOINT_TEXTS + longhaul.CHECKPOINT_LISTS:
            self.assertIn(f'"{key}"', body, "the schema's eight fields, as the save takes them")
        for cap in ("1,000 characters", "20 entries", "8,000 bytes"):
            self.assertIn(cap, body, "the caps, so the model keeps under them")
        self.assertIn("secret", body.lower())
        self.assertIn("no tabs", body, "a newline or a tab in a text comes back as ? (review, A9)")
        # A subagent shares the session id and would write over the main
        # conversation's checkpoint (A15); the note the user may add is
        # named as the inspect skill names its argument.
        self.assertIn("subagent", body.lower())
        self.assertIn("argument-hint:", head)
        self.assertIn("the Bash tool's current directory", body, "the folder the files are relative to, which a cd changes")
        # The body's example is a state the save would take as it is.
        example = json.loads(body.split("```json\n", 1)[1].split("```", 1)[0])
        state, problem, _ = longhaul.checkpoint_validate(example)
        self.assertIsNone(problem)
        self.assertEqual(set(state), set(longhaul.CHECKPOINT_TEXTS + longhaul.CHECKPOINT_LISTS))
        requires_git(self)
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "skills/checkpoint/SKILL.md"], cwd=HERE).returncode, 1)


OLD_NAME = "c" + "w"  # spelled in pieces, so this file passes its own check
OLD_FOLDER = "claude-" + "relay"  # the owner's former private folder, in pieces for the same reason


class Name(unittest.TestCase):
    """docs/adr/0003-the-name.md: the name is Longhaul. The old working
    name survives only in history: the work log, the handover, the
    discovery report, the plans and the decision records."""

    HISTORY = ("docs/WORKLOG.md", "docs/CW_PROJECT_HANDOVER.md", "docs/discovery/", "docs/plans/", "docs/adr/")
    # In code and in the build team's files both spellings are banned; in
    # the present-tense docs the former name may be named in prose, in capitals.
    CODE = ("longhaul.py", "test_longhaul.py", "longhaul_vscode.py", "longhaul-vscode", "fake_claude.py",
            "fake_stream.py", ".gitignore", ".claude-plugin/plugin.json", ".claude/")
    # OLD_FOLDER is the owner's former private folder: the build team's
    # files name it as a folder never to touch, and this test names it to
    # look for it. The program itself must not know it.
    PROGRAM = ("longhaul.py", "longhaul_vscode.py", "longhaul-vscode", "fake_claude.py", "fake_stream.py",
               ".gitignore", ".claude-plugin/plugin.json")
    # Built from OLD_NAME, so this file does not match itself by accident.
    OLD = re.compile(rf"\b{OLD_NAME}\b|\b{OLD_NAME.upper()}_[A-Z]|\b{OLD_NAME}[._-]|\btest_{OLD_NAME}\b|"
                     rf"_{OLD_NAME}_|_{OLD_NAME}(?![A-Za-z0-9])|[a-z]{OLD_NAME.capitalize()}")
    OLD_IN_CODE = re.compile(rf"\b{OLD_NAME.upper()}\b")
    OLD_HOME = re.compile(OLD_FOLDER)
    # R2a: nothing that ships names the owner, their machine or their private
    # setup. Each spelled in pieces, so this file passes its own check.
    PRIVATE = (OLD_FOLDER, "/Us" + "ers/", "amr" + "adel", "no" + "ah", "her" + "mes")
    # An email that is not an example's (review, Sprint 5, D1): the owner's
    # is in the signed files alone.
    EMAIL = r"[a-z0-9._%+-]+@(?!exam" + r"ple\.com\b)[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}"
    # The owner chose to keep their name, email and GitHub handle in these.
    SIGNED = ("LICENSE", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")
    # The README is checked line by line (it passed whole before, so a
    # private path pasted into an example would have passed with it): its
    # one allowed match is the repository's own address, which since
    # 2026-10-22 is in the organisation the owner moved it to (the owner's
    # choice); any other mention of that name still fails. In pieces, as above.
    HANDLE = "No" + "ah-OS/longhaul"

    def test_nothing_private_ships(self):
        requires_git(self)
        tracked = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True, timeout=10).stdout.split()
        private = re.compile("|".join(re.escape(word) for word in self.PRIVATE) + "|" + self.EMAIL, re.IGNORECASE)
        wrong = []
        for name in tracked:
            if name in self.SIGNED:
                continue
            for n, line in enumerate((HERE / name).read_text(errors="replace").splitlines(), 1):
                for found in private.finditer(line):
                    if name == "README.md" and line.startswith(self.HANDLE, found.start()):
                        continue
                    wrong.append(f"{name}:{n}: {found.group()}")
        self.assertEqual(wrong[:40], [], f"{len(wrong)} shipped lines name something private")

    def test_the_old_name_survives_only_in_history(self):
        requires_git(self)
        tracked = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True, timeout=10).stdout.split()
        wrong = []
        for name in tracked:
            if name.startswith(self.HISTORY):
                continue
            if re.search(r"(^|/)(" + OLD_NAME + r"[._-]|test_" + OLD_NAME + r"\.)", name):
                wrong.append(f"{name}: the file's name")
            in_code, program = name.startswith(self.CODE), name.startswith(self.PROGRAM)
            for n, line in enumerate((HERE / name).read_text(errors="replace").splitlines(), 1):
                line = line.replace("CW_PROJECT_HANDOVER", "")  # a history file's name, allowed
                line = line.replace("code/" + OLD_NAME, "")  # the checkout's folder, still so named
                if (self.OLD.search(line) or (in_code and self.OLD_IN_CODE.search(line))
                        or (program and self.OLD_HOME.search(line))):
                    wrong.append(f"{name}:{n}: {line.strip()[:70]}")
                if program and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", line):
                    wrong.append(f"{name}:{n}: a control byte in the source (the rename left one once)")
        self.assertEqual(wrong[:40], [], f"{len(wrong)} places still carry the old name")

    def test_the_default_home_and_the_wrappers_are_longhauls_own(self):
        # P6: the wrappers were hard-coded to the owner's former private
        # folder; they must find the folder they are in.
        # Review, Sprint 3: the first version of this compared a literal to
        # itself under the official command; the default is read in a fresh
        # process with no LONGHAUL_HOME and a scratch HOME.
        env = {k: v for k, v in os.environ.items() if k != "LONGHAUL_HOME"}
        env["HOME"] = tempfile.mkdtemp()
        out = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(HERE)!r}); import longhaul; "
                              "print(longhaul.LONGHAUL_HOME)"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), str(Path(env["HOME"]) / ".longhaul"), out.stderr)
        shim = (HERE / "longhaul-vscode").read_text()
        self.assertIn('dirname "$0"', shim)
        self.assertNotIn(OLD_FOLDER, shim)


class Pieces(unittest.TestCase):
    ACCOUNTS = [{"name": "Max"}, {"name": "Pro"}]

    def ask(self, typed_before=b"", typed_after=b"", wait=3.0, **patches):
        """longhaul.ask_to_move at a pseudo-terminal: keys typed before it asks,
        and keys typed once it has asked."""
        return self.at_terminal(lambda: longhaul.ask_to_move("Max", "session", time.time() + wait, "Pro"),
                                typed_before, typed_after, **patches)

    def at_terminal(self, call, typed_before=b"", typed_after=b"", **patches):
        """call() with a pseudo-terminal as stdin: keys typed before, and
        0.3 s after it starts. Keeps what it printed and notified, and the
        terminal's mode before and after."""
        master, slave = pty.openpty()
        typist = None
        printed, self.notified = io.StringIO(), []

        def type_later():
            time.sleep(0.3)
            with contextlib.suppress(OSError):
                os.write(master, typed_after)

        try:
            os.write(master, typed_before)
            if typed_after:
                typist = threading.Thread(target=type_later, daemon=True)
                typist.start()
            with open(slave, "r", closefd=False) as tty_in, mock.patch.object(longhaul.sys, "stdin", tty_in), \
                    mock.patch.object(longhaul, "notify", self.notified.append), contextlib.redirect_stdout(printed):
                with contextlib.ExitStack() as stack:
                    for target, value in patches.items():
                        stack.enter_context(mock.patch.object(longhaul.select, target, value))
                    before = termios.tcgetattr(slave)
                    try:
                        return call()
                    finally:
                        self.modes = (before, termios.tcgetattr(slave))
                        self.printed = printed.getvalue()
        finally:
            if typist:
                typist.join(2)
            os.close(master)
            os.close(slave)

    def test_a_yes_typed_once_longhaul_asks_is_a_yes(self):
        self.assertEqual(self.ask(typed_after=b"y"), "yes")

    def test_n_or_enter_is_a_no_at_once(self):
        for key in (b"n", b"\r"):
            started = time.time()
            self.assertEqual(self.ask(typed_after=key, wait=10), "no", key)
            self.assertLess(time.time() - started, 3, key)

    def test_an_arrow_key_or_other_escape_sequence_never_stops_longhaul(self):
        # Review, Sprint 1: a lone ESC counted as q, so an arrow key, or a
        # terminal's focus report, ended longhaul at the limit.
        self.assertEqual(self.ask(typed_after=b"\x1b[A\x1b[I", wait=1), "no")

    def test_the_terminal_is_set_back_after_every_answer(self):
        def flags(mode):  # PENDIN is the kernel's own: set when line mode comes back
            return mode[:3] + [mode[3] & ~termios.PENDIN] + mode[4:]

        for key, patches in ((b"y", {}), (b"n", {}), (b"q", {}), (b"", {"select": mock.Mock(side_effect=KeyboardInterrupt)})):
            self.ask(typed_after=key, wait=1, **patches)
            self.assertEqual(flags(self.modes[0]), flags(self.modes[1]), key)

    def test_the_question_says_what_happens_with_no_answer_and_how_to_stop_asking(self):
        self.ask(typed_after=b"n")
        self.assertIn("With no answer longhaul waits and carries on with Max at", self.printed)
        self.assertEqual(len(self.notified), 1)
        self.assertIn("carries on with Max at", self.notified[0])
        self.assertIn("--switch auto", self.notified[0])

    def test_no_answer_by_the_reset_is_said_and_notified(self):
        # Review, Sprint 1: it was logged only; Claude just reopened.
        self.assertEqual(self.ask(wait=0.3), "no")
        self.assertIn("No answer by the reset", self.printed)
        self.assertIn("No answer", self.notified[-1])

    def test_a_reset_already_past_asks_nothing(self):
        # Review, Sprint 1: the question was shown and notified though no
        # one could answer it in time.
        self.assertEqual(self.ask(wait=-5), "no")
        self.assertNotIn("Move this conversation", self.printed)
        self.assertEqual(self.notified, [])

    # Sprint 2, T2: the crash countdown had the faults Sprint 1 fixed in the question.

    def test_the_crash_countdown_is_not_held_up_by_a_terminal_that_shows_nothing(self):
        result = []
        t = threading.Thread(target=lambda: result.append(
            self.at_terminal(lambda: longhaul.countdown(5, "Reopening"), typed_before=b"x", typed_after=b"\r")), daemon=True)
        t.start()
        t.join(4)
        self.assertEqual(result, [True], "Enter reopens at once")

    def test_an_arrow_key_never_stops_the_crash_countdown(self):
        self.assertTrue(self.at_terminal(lambda: longhaul.countdown(1, "Reopening"), typed_after=b"\x1b[A"))
        self.assertFalse(self.at_terminal(lambda: longhaul.countdown(5, "Reopening"), typed_after=b"q"))

    def test_the_crash_countdown_still_waits_when_its_terminal_goes_away(self):
        # A closed terminal reads empty at once: the loop ran out at full
        # speed and the reopen skipped the wait that grows with each crash.
        master, slave = pty.openpty()
        closer = threading.Timer(0.3, os.close, [master])
        closer.daemon = True
        closer.start()
        started = time.time()
        with open(slave, "r") as tty_in, mock.patch.object(longhaul.sys, "stdin", tty_in), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(longhaul.countdown(2, "Reopening"))
        self.assertGreaterEqual(time.time() - started, 1.5)

    # Sprint 2 review: two more faults in the countdown, and the rule for a move.

    def test_an_ignored_key_never_shortens_the_crash_countdown(self):
        # Each ignored byte used up one second of the wait: an arrow key (3
        # bytes) cut a 5 s pause to 2 s, and a burst of them ran it out.
        started = time.time()
        self.assertTrue(self.at_terminal(lambda: longhaul.countdown(3, "Reopening"), typed_after=b"\x1b[A\x1b[B"))
        self.assertGreaterEqual(time.time() - started, 2.5)

    def test_keys_typed_while_claude_was_dying_never_answer_the_crash_countdown(self):
        # Switching the terminal at once no longer threw pending input away,
        # so a stray q ended longhaul and a stray Enter skipped the wait.
        self.assertTrue(self.at_terminal(lambda: longhaul.countdown(1, "Reopening"), typed_before=b"q"))
        started = time.time()
        self.assertTrue(self.at_terminal(lambda: longhaul.countdown(2, "Reopening"), typed_before=b"\r"))
        self.assertGreaterEqual(time.time() - started, 1.5)

    def test_the_crash_countdown_waits_the_time_out_when_its_terminal_cannot_be_written(self):
        master, slave = pty.openpty()
        gone = mock.Mock()
        gone.write.side_effect = OSError(5, "Input/output error")
        gone.flush.side_effect = OSError(5, "Input/output error")
        started = time.time()
        try:
            with open(slave, "r", closefd=False) as tty_in, mock.patch.object(longhaul.sys, "stdin", tty_in), \
                    mock.patch.object(longhaul.sys, "stdout", gone):
                self.assertTrue(longhaul.countdown(1, "Reopening"))
        finally:
            os.close(master)
            os.close(slave)
        self.assertGreaterEqual(time.time() - started, 0.8)

    def test_decide_move_is_the_one_rule_for_every_front_end(self):
        # docs/adr/0002-switching-without-a-terminal.md, decision 1: a move
        # needs the user's mode and the front end's consent; ask needs
        # someone to ask. The whole table, so run() and Relay cannot drift.
        table = {}
        for mode in ("ask", "auto", "never"):
            for consent in (True, False):
                for can_ask in (True, False):
                    table[(mode, consent, can_ask)] = longhaul.decide_move(mode, consent, can_ask)
        self.assertEqual(table, {
            ("ask", True, True): "ask", ("ask", True, False): "stay",
            ("ask", False, True): "stay", ("ask", False, False): "stay",
            ("auto", True, True): "move", ("auto", True, False): "move",
            ("auto", False, True): "stay", ("auto", False, False): "stay",
            ("never", True, True): "stay", ("never", True, False): "stay",
            ("never", False, True): "stay", ("never", False, False): "stay",
        })

    def test_a_relay_never_moves_unless_told_the_mode(self):
        # Review, Sprint 2: the constructor defaulted to auto, the one mode
        # that is opt-in.
        self.assertEqual(longhaul.Relay("/bin/true", [], [{"name": "Max"}], 0).mode, "never")

    def test_the_crash_countdown_sets_the_terminal_back(self):
        self.at_terminal(lambda: longhaul.countdown(1, "Reopening"), typed_after=b"q")
        mask = lambda m: m[:3] + [m[3] & ~termios.PENDIN] + m[4:]  # noqa: E731
        self.assertEqual(mask(self.modes[0]), mask(self.modes[1]))

    def test_no_stdin_at_all_is_no_terminal(self):
        with mock.patch.object(longhaul.sys, "stdin", None), mock.patch.object(longhaul, "notify"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(longhaul.ask_to_move("Max", "session", time.time() + 5, "Pro"), "no")

    def test_keys_typed_before_longhaul_asks_never_count_as_an_answer(self):
        # Typed while Claude ran: a stray y must not move the conversation.
        self.assertEqual(self.ask(typed_before=b"y", wait=0.6), "no")

    def test_no_answer_by_the_reset_is_a_no(self):
        self.assertEqual(self.ask(wait=0.3), "no")

    def test_ctrl_c_at_the_question_stops_longhaul(self):
        self.assertEqual(self.ask(select=mock.Mock(side_effect=KeyboardInterrupt)), "stop")

    def test_a_terminal_that_shows_nothing_does_not_hold_the_question_up(self):
        # Sprint 1: switching the terminal waited for its output to be shown.
        # With output paused (Ctrl+S, a stuck pane) longhaul hung at the limit.
        # Here nothing reads the terminal, so the echo of keys typed before
        # the question is never shown.
        result = []
        t = threading.Thread(target=lambda: result.append(self.ask(typed_before=b"x", typed_after=b"y", wait=3)),
                             daemon=True)
        t.start()
        t.join(5)
        self.assertEqual(result, ["yes"])

    def test_a_terminal_that_goes_away_is_no_answer(self):
        # Closed, the terminal reads as empty for ever: longhaul spun at full speed.
        master, slave = pty.openpty()
        closer = threading.Timer(0.3, os.close, [master])
        closer.daemon = True
        closer.start()
        started = time.time()
        with open(slave, "r") as tty_in, mock.patch.object(longhaul.sys, "stdin", tty_in), \
                mock.patch.object(longhaul, "notify"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(longhaul.ask_to_move("Max", "session", time.time() + 30, "Pro"), "no")
        self.assertLess(time.time() - started, 5)

    def test_each_reopen_after_a_crash_waits_longer_up_to_a_ceiling(self):
        self.assertEqual([longhaul.reopen_delay(n, 10) for n in range(1, 6)], [10, 30, 90, 270, 300])
        self.assertEqual(longhaul.reopen_delay(3, 0), 0)

    def test_a_claude_code_version_is_read_from_its_version_line(self):
        self.assertEqual(longhaul.parse_version("2.1.304 (Claude Code)"), (2, 1, 304))
        self.assertIsNone(longhaul.parse_version(""))
        self.assertIsNone(longhaul.parse_version("command not found"))

    def test_pick_skips_an_account_at_its_limit(self):
        self.assertEqual(longhaul.pick(self.ACCOUNTS, {"limited_until": {"Max": 200}}, 0, now=100), (1, None))

    def test_pick_takes_an_account_back_once_its_limit_has_reset(self):
        self.assertEqual(longhaul.pick(self.ACCOUNTS, {"limited_until": {"Max": 50}}, 0, now=100), (0, None))

    def test_pick_returns_the_first_reset_when_every_account_is_limited(self):
        state = {"limited_until": {"Max": 300, "Pro": 200}}
        self.assertEqual(longhaul.pick(self.ACCOUNTS, state, 0, now=100), (1, 200))

    def seen(self, five=None, five_at=None, week=None, week_at=None):
        r = {}
        if five is not None:
            r["five_hour"] = {"used_percentage": five, "resets_at": five_at}
        if week is not None:
            r["seven_day"] = {"used_percentage": week, "resets_at": week_at}
        return r

    def test_pick_spends_the_allowance_that_expires_first(self):
        seen = {"Max": self.seen(five=77, five_at=120), "Pro": self.seen(five=23, five_at=500, week=51, week_at=300)}
        self.assertEqual(longhaul.pick(self.ACCOUNTS, {}, 1, now=100, seen=seen), (0, None))

    def test_pick_leaves_a_nearly_full_account_while_another_has_room(self):
        seen = {"Max": self.seen(five=96, five_at=120), "Pro": self.seen(week=51, week_at=900)}
        self.assertEqual(longhaul.pick(self.ACCOUNTS, {}, 0, now=100, seen=seen), (1, None))

    def test_pick_uses_a_nearly_full_account_when_the_other_is_at_its_limit(self):
        seen = {"Max": self.seen(five=96, five_at=120)}
        state = {"limited_until": {"Pro": 500}}
        self.assertEqual(longhaul.pick(self.ACCOUNTS, state, 1, now=100, seen=seen), (0, None))

    def test_an_account_with_no_open_window_loses_nothing_by_waiting(self):
        seen = {"Max": self.seen(five=10, five_at=50), "Pro": self.seen(week=20, week_at=900)}
        self.assertEqual(longhaul.pick(self.ACCOUNTS, {}, 0, now=100, seen=seen), (1, None),
                         "Max's window already reset; Pro's weekly room is the one that expires")

    def test_last_account_reads_the_sessions_record_and_ignores_junk_main_and_a_removed_account(self):
        usage = Path(tempfile.mkdtemp()) / "usage"
        (usage / "sessions").mkdir(parents=True)
        for sid, record in (("s-pro", json.dumps({"account": "Pro", "cost": 0.1})),
                            ("s-main", json.dumps({"account": "main"})),  # a session outside longhaul
                            ("s-gone", json.dumps({"account": "Team"})),  # an account since removed
                            ("s-list", json.dumps(["Pro"])), ("s-null", json.dumps({"account": None})),
                            ("s-junk", "{not json")):
            (usage / "sessions" / f"{sid}.json").write_text(record)
        # Where "../s-pro" would land: an id is never a path (review, Sprint 5, F1).
        (usage / "s-pro.json").write_text(json.dumps({"account": "Pro"}))
        with mock.patch.object(longhaul, "USAGE_DIR", usage):
            self.assertEqual(longhaul.last_account("s-pro", self.ACCOUNTS), "Pro")
            for sid in ("s-main", "s-gone", "s-list", "s-null", "s-junk", "s-none", "", None, "a/b", "../s-pro", 7):
                self.assertIsNone(longhaul.last_account(sid, self.ACCOUNTS), sid)

    def test_a_session_id_that_is_not_a_plain_id_is_never_a_path_and_a_note_that_cannot_be_kept_is_logged(self):
        # Review, Sprint 5 (F1, F6b): note_session_account(), last_account()
        # and record_usage() took the id straight into usage/sessions/
        # <id>.json, so a "/" or ".." in it would have written outside; and
        # a note the disk refuses is logged, never raised.
        tmp = Path(tempfile.mkdtemp())
        usage = tmp / "usage"
        (usage / "sessions").mkdir(parents=True)
        with mock.patch.object(longhaul, "LONGHAUL_HOME", tmp), mock.patch.object(longhaul, "USAGE_DIR", usage), \
                mock.patch.object(longhaul, "LEDGER", tmp / "ledger.jsonl"):
            longhaul.note_session_account("../outside", "Max")
            longhaul.note_session_account("a/b", "Max")
            longhaul.record_usage({"session_id": "../outside", "cost": {"total_cost_usd": 1}}, None)
            written = sorted(p.relative_to(tmp).as_posix() for p in tmp.rglob("*") if p.is_file())
            self.assertEqual(written, ["longhaul.log"], "nothing but the log")
            log = (tmp / "longhaul.log").read_text()
            self.assertIn("could not note where ../outside ran on Max: not a plain session id", log)
            self.assertIn("could not note where a/b ran on Max: not a plain session id", log)
            longhaul.record_usage({"session_id": "s-ok", "cost": {"total_cost_usd": 1}}, None)
            self.assertTrue((usage / "sessions" / "s-ok.json").is_file(), "a plain id is recorded as before")
            # usage/sessions a regular file: the note cannot be kept, and says so.
            shutil.rmtree(usage / "sessions")
            (usage / "sessions").write_text("")
            longhaul.note_session_account("s1", "Max")
            self.assertIn("could not note that s1 ran on Max: ", (tmp / "longhaul.log").read_text())

    def test_a_429_without_a_rejected_quota_is_not_a_limit(self):
        line = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.assertIsNone(longhaul.limit_stop(line, time.time()))

    def test_a_subagent_limit_does_not_switch_the_main_conversation(self):
        line = {"type": "assistant", "isSidechain": True, "isApiErrorMessage": True, "error": "rate_limit",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "quotaLimits": {"status": "rejected", "resetsAt": 1}}
        self.assertIsNone(longhaul.limit_stop(line, time.time()))

    def test_a_weekly_limit_is_named_weekly(self):
        line = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "apiErrorParams": {"rate_limit_info": {"status": "rejected", "resetsAt": 5, "rateLimitType": "seven_day"}},
                "uuid": "u1"}
        self.assertEqual(longhaul.limit_stop(line, time.time() - 1),
                         {"resets_at": 5.0, "kind": "weekly", "uuid": "u1", "type": "seven_day"})

    def test_a_limit_line_from_before_the_launch_is_not_counted(self):
        line = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "quotaLimits": {"status": "rejected", "resetsAt": 5}}
        self.assertIsNone(longhaul.limit_stop(line, time.time() + 1))

    def test_the_default_account_never_gets_claude_config_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = "/somewhere"
        try:
            self.assertNotIn("CLAUDE_CONFIG_DIR", longhaul.env_for({"name": "Max", "config_dir": None}))
            self.assertEqual(longhaul.env_for({"name": "Pro", "config_dir": "/x/pro"})["CLAUDE_CONFIG_DIR"], "/x/pro")
        finally:
            del os.environ["CLAUDE_CONFIG_DIR"]

    def test_a_connection_added_after_setup_reaches_the_second_account(self):
        tmp = Path(tempfile.mkdtemp())
        main_json, pro = tmp / "main.json", tmp / "pro"
        pro.mkdir()
        main_json.write_text(json.dumps({"oauthAccount": {"email": "first"},
                                         "mcpServers": {"codegraph": {"command": "cg"}, "linear": {"url": "https://l"}}}))
        (pro / ".claude.json").write_text(json.dumps({"oauthAccount": {"email": "second"},
                                                      "mcpServers": {"codegraph": {"command": "cg"}, "own": {"url": "https://o"}}}))
        old = longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME
        longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME = main_json, tmp / "relay"
        try:
            longhaul.sync_mcp({"name": "Pro", "config_dir": str(pro)})
        finally:
            longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME = old
        after = json.loads((pro / ".claude.json").read_text())
        self.assertEqual(sorted(after["mcpServers"]), ["codegraph", "linear", "own"])
        self.assertEqual(after["oauthAccount"], {"email": "second"}, "the sign-in is never copied")

    def test_a_projects_own_connection_added_after_setup_reaches_the_second_account(self):
        # `claude mcp add` (local scope, the default) writes under
        # projects[<folder>] in ~/.claude.json, which only `longhaul setup` copied:
        # after a switch the task's server was gone.
        tmp = Path(tempfile.mkdtemp())
        main_json, pro = tmp / "main.json", tmp / "pro"
        pro.mkdir()
        new_project = {"mcpServers": {"db": {"command": "pg"}}, "hasTrustDialogAccepted": True}
        main_json.write_text(json.dumps({"projects": {
            "/work/a": {"mcpServers": {"linear": {"url": "https://l"}}, "hasTrustDialogAccepted": True},
            "/work/b": new_project}}))
        (pro / ".claude.json").write_text(json.dumps({"oauthAccount": {"email": "second"}, "projects": {
            "/work/a": {"mcpServers": {"own": {"url": "https://o"}}, "hasTrustDialogAccepted": True,
                        "lastSessionId": "pro-session"}}}))
        old = longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME
        longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME = main_json, tmp / "relay"
        try:
            longhaul.sync_mcp({"name": "Pro", "config_dir": str(pro)})
        finally:
            longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME = old
        after = json.loads((pro / ".claude.json").read_text())
        a = after["projects"]["/work/a"]
        self.assertEqual(sorted(a["mcpServers"]), ["linear", "own"], "Pro's own server is kept")
        self.assertEqual(a["lastSessionId"], "pro-session", "only the servers change in a project Pro has")
        self.assertEqual(after["projects"]["/work/b"], new_project, "a project Pro never opened comes whole")
        self.assertEqual(after["oauthAccount"], {"email": "second"})

    def test_mcp_sync_writes_nothing_when_the_accounts_file_cannot_be_read_again(self):
        # Review, Phase 1c: Claude Code writes .claude.json often. A re-read
        # that found it half written put back the copy read a moment before.
        pro = Path(tempfile.mkdtemp())
        src = {"mcpServers": {"linear": {"url": "https://l"}}}
        with mock.patch.object(longhaul, "read_json", side_effect=[src, {"oauthAccount": {}}, None]), \
                mock.patch.object(longhaul, "write_json") as write, mock.patch.object(longhaul, "LONGHAUL_HOME", pro / "relay"):
            longhaul.sync_mcp({"name": "Pro", "config_dir": str(pro)})
        write.assert_not_called()
        self.assertIn("changed as longhaul read it", (pro / "relay" / "longhaul.log").read_text())

    def test_mcp_sync_leaves_an_accounts_file_that_is_not_an_object_alone(self):
        tmp = Path(tempfile.mkdtemp())
        main_json, pro = tmp / "main.json", tmp / "pro"
        pro.mkdir()
        main_json.write_text(json.dumps({"mcpServers": {"linear": {"url": "https://l"}}}))
        (pro / ".claude.json").write_text("[1, 2]")
        old = longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME
        longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME = main_json, tmp / "relay"
        try:
            longhaul.sync_mcp({"name": "Pro", "config_dir": str(pro)})
        finally:
            longhaul.DEFAULT_GLOBAL_JSON, longhaul.LONGHAUL_HOME = old
        self.assertEqual((pro / ".claude.json").read_text(), "[1, 2]")
        self.assertIn("could not be read as an object", (tmp / "relay" / "longhaul.log").read_text())

    def test_a_notification_that_hangs_or_fails_never_stops_longhaul(self):
        # notify() runs inside the watch loop now (a refused reconnect): an
        # osascript hung there would have stopped limit switching with it.
        with mock.patch.dict(os.environ), mock.patch.object(longhaul.shutil, "which", return_value="/usr/bin/osascript"):
            os.environ.pop("LONGHAUL_NO_NOTIFY", None)
            for failure in (subprocess.TimeoutExpired("osascript", 10), OSError("no osascript")):
                with mock.patch.object(longhaul.subprocess, "run", side_effect=failure) as run:
                    longhaul.notify("hello")
                self.assertIn("timeout", run.call_args.kwargs)

    def relink_with(self, main_text, copy_text, copy_newer):
        tmp = Path(tempfile.mkdtemp())
        main, pro = tmp / "main", tmp / "pro"
        main.mkdir()
        pro.mkdir()
        (main / "settings.json").write_text(main_text)
        (pro / "settings.json").write_text(copy_text)
        now = time.time()
        os.utime(pro / "settings.json", (now, now + (60 if copy_newer else -60)))
        old = longhaul.DEFAULT_CONFIG, longhaul.LONGHAUL_HOME
        longhaul.DEFAULT_CONFIG, longhaul.LONGHAUL_HOME = main, tmp / "relay"
        try:
            longhaul.relink({"name": "Pro", "config_dir": str(pro)})
        finally:
            longhaul.DEFAULT_CONFIG, longhaul.LONGHAUL_HOME = old
        self.assertTrue((pro / "settings.json").is_symlink())
        return main, pro

    def test_relink_sets_an_older_copy_aside_and_keeps_the_shared_file(self):
        main, pro = self.relink_with("{}", '{"copy": true}', copy_newer=False)
        kept = list(pro.glob("settings.json.pro-*"))
        self.assertEqual([k.read_text() for k in kept], ['{"copy": true}'])
        self.assertEqual((main / "settings.json").read_text(), "{}")

    def test_relink_makes_a_newer_settings_change_the_shared_one(self):
        # a setting changed in a chat on Pro was reverted at the next launch
        main, pro = self.relink_with("{}", '{"changed": true}', copy_newer=True)
        self.assertEqual((main / "settings.json").read_text(), '{"changed": true}')
        self.assertEqual([k.read_text() for k in main.glob("settings.json.before-pro-*")], ["{}"])

    def test_two_writers_recording_limits_at_once_both_keep_theirs(self):
        tmp = Path(tempfile.mkdtemp())
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import longhaul\n"
                "for i in range(60): longhaul.update_state(lambda s, i=i: s['limited_until'].update({f'{sys.argv[2]}-{i}': i}))\n")
        env = dict(os.environ, LONGHAUL_HOME=str(tmp))
        procs = [subprocess.Popen([sys.executable, "-c", code, str(HERE), f"w{n}"], env=env) for n in range(4)]
        self.assertEqual([p.wait(60) for p in procs], [0, 0, 0, 0])
        self.assertEqual(len(json.loads((tmp / "state.json").read_text())["limited_until"]), 240)

    def test_load_state_tolerates_a_state_file_that_is_a_list_or_a_string(self):
        # T6: `.setdefault` on a list or a string raised AttributeError, and
        # run() calls load_state() outside any try. A non-object is an empty
        # state; a limited_until that is not an object is empty too.
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(longhaul, "LONGHAUL_HOME", tmp):
            for junk in ([], ["junk"], "x", {"limited_until": []}):
                (tmp / "state.json").write_text(json.dumps(junk))
                self.assertEqual(longhaul.load_state(), {"limited_until": {}}, repr(junk))

    def test_load_state_says_in_the_log_when_the_state_file_is_not_an_object(self):
        # Review, Sprint 6 (A6): T6 made a list or a string an empty state
        # in silence (before T6, a traceback), and the next write replaced
        # the file with no one the wiser. One log line names the file, never
        # its content; a well-formed or a missing file gets none; pick()
        # runs on the fallback either way.
        tmp = Path(tempfile.mkdtemp())
        log, state_file = tmp / "longhaul.log", tmp / "state.json"
        with mock.patch.object(longhaul, "LONGHAUL_HOME", tmp):
            for junk in ([["Max", 1]], {"limited_until": [["Max", 1]]}):
                log.unlink(missing_ok=True)
                state_file.write_text(json.dumps(junk))
                state = longhaul.load_state()
                self.assertEqual(state["limited_until"], {}, repr(junk))
                self.assertEqual(longhaul.pick(self.ACCOUNTS, state, 1)[0], 1, repr(junk))
                text = log.read_text() if log.exists() else ""
                self.assertEqual(text.count("\n"), 1, text or f"nothing logged for {junk!r}")
                self.assertIn("state.json", text)
                self.assertNotIn("Max", text, "the file's name, never its content")
            log.unlink()
            state_file.write_text(json.dumps({"limited_until": {"Pro": 1}}))
            self.assertEqual(longhaul.load_state()["limited_until"], {"Pro": 1})
            state_file.unlink()
            self.assertEqual(longhaul.load_state(), {"limited_until": {}})
            self.assertFalse(log.exists(), "nothing to say of a well-formed or a missing file")

    def test_a_chat_back_on_an_earlier_account_counts_in_full(self):
        rows = [{"at": 1, "session": "A", "account": "Max", "cost": 0.5, "proc": 11},
                {"at": 2, "session": "A", "account": "Pro", "cost": 0.3, "proc": 12},
                {"at": 3, "session": "A", "account": "Max", "cost": 0.6, "proc": 13}]
        self.assertAlmostEqual(longhaul.summarize(rows)[("A", "Max")]["cost"], 1.1)

    def test_a_hold_set_while_a_swap_finishes_is_kept(self):
        r = longhaul.Relay("/bin/true", [], [{"name": "Max"}], 0)
        r.paused, r.swap_started, r.held_since = True, 100.0, 150.0
        r.end_swap()
        self.assertTrue(r.paused)
        r.held_since = 50.0
        r.end_swap()
        self.assertFalse(r.paused)

    # Sprint 7, A2: an account restricted to repositories ("repositories"
    # in accounts.json). The helpers are pure; realpath() is their only I/O,
    # on a scratch tree of the test's own.

    def tree(self):
        """work/.git; work/sub/.git (a repository nested in work); work/deep/er;
        work2/.git (a sibling whose name starts with work's); personal/.git;
        link -> work; and work/vendor -> personal, a link inside work to a
        repository elsewhere."""
        tmp = Path(tempfile.mkdtemp())
        for repo in ("work", "work/sub", "work2", "personal"):
            (tmp / repo / ".git").mkdir(parents=True)
        (tmp / "work" / "deep" / "er").mkdir(parents=True)
        (tmp / "link").symlink_to(tmp / "work")
        (tmp / "work" / "vendor").symlink_to(tmp / "personal")
        return tmp

    def allowed(self, top, *repos):
        """Whether an account restricted to `repos` may be used in `top`."""
        return longhaul.account_allowed({"name": "Pro", "repositories": [str(r) for r in repos]}, str(top))

    def test_account_allowed_with_no_repositories_field_or_an_empty_list_allows_everywhere(self):
        for acct in ({"name": "Max"}, {"name": "Max", "repositories": None}, {"name": "Max", "repositories": []}):
            for top in ("/anywhere/at/all", "/", str(Path.home())):
                self.assertTrue(longhaul.account_allowed(acct, top), (acct, top))

    def test_account_allowed_compares_real_paths_on_both_sides(self):
        tmp = self.tree()
        work = tmp / "work"
        with mock.patch.dict(os.environ, {"HOME": str(tmp)}):
            self.assertTrue(self.allowed(work, "~/work"), "a ~ entry, expanded as config_dir is")
        self.assertTrue(self.allowed(work, f"{work}/"), "a trailing slash")
        self.assertTrue(self.allowed(work, tmp / "link"), "an entry that is a link to the repository")
        self.assertTrue(self.allowed(tmp / "link", work), "a session folder reached through a link")
        # On a Mac mkdtemp's folder is under /var, itself a link to
        # /private/var: the real path on either side still agrees.
        self.assertTrue(self.allowed(os.path.realpath(work), work))
        self.assertTrue(self.allowed(work, os.path.realpath(work)))
        self.assertFalse(self.allowed(tmp / "personal", work), "another repository")

    def test_a_nested_repository_is_allowed_by_an_entry_for_its_parent_and_not_the_reverse(self):
        tmp = self.tree()
        work = tmp / "work"
        self.assertTrue(self.allowed(work / "sub", work), "work/sub/.git is inside work (Q2: nested ones inherit)")
        self.assertTrue(self.allowed(work / "deep" / "er", work), "any folder inside it")
        self.assertFalse(self.allowed(work, work / "sub"), "an entry for the nested one, a session at the outer root")
        self.assertFalse(self.allowed(tmp / "work2", work), "a sibling whose name starts with the entry's")

    def test_a_symlink_inside_an_allowed_repository_to_another_repository_is_denied(self):
        tmp = self.tree()
        work = tmp / "work"
        top = longhaul.repository_of(str(work / "vendor"))
        self.assertEqual(top, str(work / "vendor"), "the link shows personal's .git, so the walk stops at it")
        self.assertFalse(self.allowed(top, work), "its real top is personal, wherever you came from")
        self.assertTrue(self.allowed(top, tmp / "personal"))

    def test_repository_of_is_the_nearest_git_top_or_the_folder_itself(self):
        tmp = self.tree()
        work = tmp / "work"
        self.assertEqual(longhaul.repository_of(str(work / "deep" / "er")), str(work), "a subfolder")
        self.assertEqual(longhaul.repository_of(str(work / "sub")), str(work / "sub"), "the nearest .git wins")
        (tmp / "wt").mkdir()
        (tmp / "wt" / ".git").write_text(f"gitdir: {work}/.git/worktrees/wt\n")
        self.assertEqual(longhaul.repository_of(str(tmp / "wt")), str(tmp / "wt"), "a worktree's .git file")
        (tmp / "plain" / "deeper").mkdir(parents=True)
        self.assertEqual(longhaul.repository_of(str(tmp / "plain" / "deeper")), str(tmp / "plain" / "deeper"),
                         "no .git at or above it: the folder itself")
        home = tmp / "home"
        (home / ".git").mkdir(parents=True)
        (home / "proj").mkdir()
        with mock.patch.object(longhaul, "HOME", home):
            self.assertEqual(longhaul.repository_of(str(home / "proj")), str(home / "proj"),
                             "a dotfiles repository at ~ is declined by index_top(): the folder itself")

    def test_a_repositories_field_that_is_not_a_list_of_absolute_paths_allows_nothing(self):
        tmp = self.tree()
        work = str(tmp / "work")
        # A falsy junk value fails closed too (final review, Sprint 7): only an absent field, null or
        # an empty list means unrestricted; "", {}, 0 and false are typos, never "anywhere".
        for junk in (work, "~/work", 42, True, {"path": work}, [work, "relative/one"], [work, None], [work, 7], [""],
                     "", {}, 0, False):
            acct = {"name": "Pro", "repositories": junk}
            self.assertIsNone(longhaul.repositories_of(acct), repr(junk))
            self.assertFalse(longhaul.account_allowed(acct, work), f"{junk!r} must fail closed, even naming work")
            self.assertFalse(longhaul.account_allowed(acct, "/anywhere"), repr(junk))
        with mock.patch.dict(os.environ, {"HOME": str(tmp)}):
            self.assertEqual(longhaul.repositories_of({"name": "Pro", "repositories": ["~/work", "/x"]}),
                             [work, "/x"], "a list of absolute paths, ~ expanded")
        self.assertEqual(longhaul.repositories_of({"name": "Pro"}), [], "no field: unrestricted")
        self.assertEqual(longhaul.repositories_of({"name": "Pro", "repositories": None}), [], "null: unrestricted")
        self.assertEqual(longhaul.repositories_of({"name": "Pro", "repositories": []}), [], "[]: unrestricted")

    def test_eligible_keeps_the_files_order_and_the_denied_are_the_rest(self):
        tmp = self.tree()
        work, personal = str(tmp / "work"), str(tmp / "personal")
        accounts = [{"name": "Team", "repositories": [personal]}, {"name": "Max"},
                    {"name": "Pro", "repositories": [work]}, {"name": "Odd", "repositories": "junk"}]
        self.assertEqual(longhaul.eligible(accounts, work), ["Max", "Pro"])
        self.assertEqual(longhaul.eligible(accounts, personal), ["Team", "Max"])
        self.assertEqual(longhaul.eligible(accounts, str(tmp)), ["Max"])
        with mock.patch.object(longhaul, "LONGHAUL_HOME", tmp / "relay"):
            self.assertEqual(longhaul.denied_in(accounts, work), {"Team", "Odd"})
            log = (tmp / "relay" / "longhaul.log").read_text()
        self.assertEqual(log.count("\n"), 1, log)
        self.assertIn(f"Odd in {longhaul.home_path(str(tmp / 'relay' / 'accounts.json'))}: {longhaul.REPOSITORIES_JUNK}; "
                      "longhaul will not use this account anywhere\n", log)

    def test_a_repositories_field_that_is_not_a_list_of_paths_is_said_one_way(self):
        # Final review, Sprint 7 (G6): the log, `longhaul status`, the
        # --account refusal and the no-account line spelled it four ways
        # ("absolute" in two, "field" in two); REPOSITORIES_JUNK is the one.
        self.assertEqual(longhaul.REPOSITORIES_JUNK, "its repositories field is not a list of absolute paths")
        odd = {"name": "Odd", "repositories": "junk"}
        self.assertEqual(longhaul.where_allowed(odd), f"nowhere ({longhaul.REPOSITORIES_JUNK})")
        self.assertEqual(longhaul.may_not_text(odd, "/x"), f"Odd may not be used in /x ({longhaul.REPOSITORIES_JUNK})")
        self.assertIn(f"Odd nowhere ({longhaul.REPOSITORIES_JUNK}). ", longhaul.no_account_text("/x", [odd]))


class Links(unittest.TestCase):
    """A transcript link to nowhere under the projects folder (T6), and
    one into a folder that cannot be read (Sprint 6 review).

    Path.glob() with a literal last component lists a dangling link on
    Python 3.12 and later and passes it over on 3.9 to 3.11 (with a
    wildcard last component it is listed on every version), so each
    reader of `projects/*/<sid>.jsonl` and of a transcript's
    `subagents/*.jsonl` must leave it out itself: a dead link is no
    transcript, on every Python."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude = self.tmp / "claude"
        self.folder = self.claude / "projects" / "p"
        self.folder.mkdir(parents=True)
        (self.folder / "sid-dead.jsonl").symlink_to(self.tmp / "gone.jsonl")
        relay = self.tmp / "relay"
        for name, value in (("DEFAULT_CONFIG", self.claude), ("LONGHAUL_HOME", relay),
                            ("USAGE_DIR", relay / "usage"), ("LEDGER", relay / "ledger.jsonl")):
            patcher = mock.patch.object(longhaul, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    LINE = {"type": "user", "message": {"role": "user", "content": "x"}}  # one transcript line inspect counts

    def subagents(self, sid="sid-real"):
        """A real transcript and its subagents folder, holding a link to nowhere."""
        path = self.folder / f"{sid}.jsonl"
        path.write_text(json.dumps(self.LINE) + "\n")
        sub = path.with_suffix("") / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-dead.jsonl").symlink_to(self.tmp / "gone.jsonl")
        return path, sub

    def test_the_launch_measures_no_transcript_behind_a_dead_link(self):
        self.assertEqual(longhaul.transcript_size("sid-dead"), {})

    def test_the_watch_sees_no_transcript_behind_a_dead_link_and_its_poll_passes(self):
        watch = longhaul.Watch({"name": "Max"}, 0, time.time(), False)
        watch.sid = "sid-dead"
        self.assertIsNone(watch._transcript())
        watch._read_transcript()  # one poll of the supervisor: nothing to read, nothing raised

    def test_the_usage_records_list_no_transcript_behind_a_dead_link(self):
        self.assertEqual(longhaul.transcript_hits("sid-dead"), [])

    def test_a_short_id_is_not_expanded_to_a_dead_link(self):
        self.assertEqual(longhaul.full_session_id("sid-de"), "sid-de")

    def test_inspect_finds_no_transcript_behind_a_dead_link(self):
        self.assertIsNone(longhaul.find_transcript("sid-dead")[0])

    def test_inspect_expands_a_short_id_past_a_dead_link(self):
        # Review, Sprint 6 (A4): find_transcript()'s own files_of() site,
        # pinned. With it reverted, the dead link beside the live transcript
        # made "2 conversations start with sid-de" (transcript_hits(), fixed
        # upstream, never saw the short id).
        live = self.folder / "sid-de-live.jsonl"
        live.write_text("x\n")
        self.assertEqual(longhaul.find_transcript("sid-de"), (live, ""))

    def test_a_link_into_a_folder_that_cannot_be_read_is_no_transcript_and_stops_nothing(self):
        # Review, Sprint 6 (A1): is_file() raised the permission error
        # before Python 3.13 and answers False from it on. The wildcard
        # globs of full_session_id() and find_transcript() list the link on
        # every version, so a launch by short id stopped on 3.9 to 3.12;
        # the literal globs list it from 3.12 on (3.9 to 3.11 pass it over
        # in the glob itself). Root reads any folder: nothing to see there.
        if os.geteuid() == 0:
            self.skipTest("root reads any folder")
        locked = self.tmp / "locked"
        locked.mkdir()
        (locked / "x.jsonl").write_text("x\n")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o700)
        (self.folder / "sid-locked.jsonl").symlink_to(locked / "x.jsonl")
        self.assertEqual(longhaul.transcript_size("sid-locked"), {})
        self.assertEqual(longhaul.full_session_id("sid-lo"), "sid-lo")
        self.assertIsNone(longhaul.find_transcript("sid-lo")[0])
        self.assertEqual(longhaul.transcript_hits("sid-locked"), [])
        watch = longhaul.Watch({"name": "Max"}, 0, time.time(), False)
        watch.sid = "sid-locked"
        self.assertIsNone(watch._transcript())

    def test_a_dead_link_among_the_subagents_does_not_stop_the_watch(self):
        # Review, Sprint 6 (A2): agents_busy() globs subagents/*.jsonl, a
        # wildcard, so the link was listed on every Python and p.stat()
        # raised at every poll ("watch error" each POLL): no early move and
        # no reconnect was decided while it was there.
        _, sub = self.subagents()
        watch = longhaul.Watch({"name": "Max"}, 0, time.time(), False)
        watch.sid = "sid-real"
        self.assertFalse(watch.agents_busy())
        (sub / "agent-1.jsonl").write_text("{}\n")  # a live subagent beside it still counts
        self.assertTrue(watch.agents_busy())

    def test_inspect_passes_over_a_dead_link_among_the_subagents(self):
        # Review, Sprint 6 (A3): inspect_transcript() opened every
        # subagents/agent-*.jsonl hit, so `longhaul inspect` exited 1 with
        # "could not read" over a link to nowhere. The main file and the
        # live subagent beside it are read.
        path, sub = self.subagents()
        (sub / "agent-1.jsonl").write_text(json.dumps({**self.LINE, "isSidechain": True}) + "\n")
        r = longhaul.inspect_transcript(path)
        self.assertEqual((r["subagents"], r["lines"]), (1, 2))

    def test_a_link_to_a_transcript_that_exists_still_counts(self):
        # is_file() follows the link: a transcript reached through one, as
        # under a shared projects folder, is a transcript, at every site
        # (a pin, not failing-first: the sites were right before it).
        (self.tmp / "live.jsonl").write_text("x\n")
        link = self.folder / "sid-live.jsonl"
        link.symlink_to(self.tmp / "live.jsonl")
        self.assertEqual(list(longhaul.transcript_size("sid-live").values()), [2])
        self.assertEqual(longhaul.full_session_id("sid-li"), "sid-live")
        self.assertEqual(longhaul.transcript_hits("sid-live"), [link])
        self.assertEqual(longhaul.find_transcript("sid-live"), (link, ""))
        watch = longhaul.Watch({"name": "Max"}, 0, time.time(), False)
        watch.sid = "sid-live"
        self.assertEqual(watch._transcript(), (self.tmp / "live.jsonl").resolve())


class StreamHarness(unittest.TestCase):
    """What the panel's tests and longhaul pipe's share: a scratch relay folder
    with two accounts, fake_stream.py as claude, and a reader that parses
    every line coming back. It holds no test, so Pipe can use it without
    running Panel's 26 tests a second time."""

    EXT_ARGS = ["--output-format", "stream-json", "--verbose", "--input-format", "stream-json",
                "--permission-prompt-tool=stdio", "--permission-mode=auto", "--replay-user-messages"]
    HOOKS = {"PreToolUse": [{"matcher": "Edit|Write", "hookCallbackIds": ["hook_0"]}]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.relay, self.default = self.tmp / "relay", self.tmp / "default"
        self.max, self.pro = self.tmp / "max", self.tmp / "pro"
        for d in (self.relay, self.default / "projects", self.default / "sessions", self.max, self.pro):
            d.mkdir(parents=True)
        self.accounts([{"name": "Max", "config_dir": str(self.max), "email": "first@example.com"},
                       {"name": "Pro", "config_dir": str(self.pro), "email": "second@example.com"}])
        self.emails = {str(self.max): "first@example.com", str(self.pro): "second@example.com"}
        self.switch_mid_session(True)
        self.procs, self.readers = [], []

    def tearDown(self):
        # Every process ended, waited for and its pipes closed, so the run's
        # stderr holds no ResourceWarning and a new one stands out (T1).
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            with contextlib.suppress(OSError):
                p.stdin.close()
        for t in self.readers:
            t.join(5)

    def accounts(self, data):
        (self.relay / "accounts.json").write_text(json.dumps(data))

    def switch_mid_session(self, on, **more):
        (self.relay / "vscode.json").write_text(json.dumps({"switch_mid_session": on, **more}))

    def fake_notifications(self, delay=0):
        """The environment for an osascript of the test's own: each
        notification longhaul sends is a line in notes.txt, after `delay` seconds."""
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        fake = bin_dir / "osascript"
        fake.write_text(f'#!/bin/sh\nsleep {delay}\nprintf "%s\\n" "$2" >> "$LONGHAUL_TEST_NOTES"\n')
        fake.chmod(0o755)
        self.notes = self.tmp / "notes.txt"
        return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "LONGHAUL_NO_NOTIFY": "", "LONGHAUL_TEST_NOTES": str(self.notes)}

    def notified(self, wait=5.0):
        """The text of each notification longhaul sent, out of the AppleScript.
        The notification goes out after the held lines (review, Sprint 2),
        so it may land after the chat's next reply: wait for it."""
        end = time.time() + wait
        while not self.notes.exists() and time.time() < end:
            time.sleep(0.05)
        if not self.notes.exists():
            return []
        return [m.group(1) for m in (re.search(r'display notification "(.*)" with title', line)
                                     for line in self.notes.read_text().splitlines()) if m]

    def plan(self, steps):
        (self.relay / "stream_plan.json").write_text(json.dumps(steps))

    def start(self, args=None, launcher=None, command=None, cwd=None, **extra_env):
        """The panel wrapper as the extension runs it; `cwd`: the folder it
        runs in (default: the scratch folder)."""
        # LONGHAUL_SWITCH=auto: the panel tests written before Sprint 2 assert its
        # moves, which "auto" keeps (docs/adr/0002-switching-without-a-terminal.md).
        env = dict(os.environ, LONGHAUL_HOME=str(self.relay), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.default),
                   LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "global.json"), LONGHAUL_POLL_SECONDS="0.1",
                   LONGHAUL_NO_NOTIFY="1", LONGHAUL_AUTH_TTL="0", FAKE_EMAILS=json.dumps(self.emails),
                   CLAUDE_CODE_ENTRYPOINT="claude-vscode", ANTHROPIC_API_KEY="sk-test-not-real",
                   LONGHAUL_SWITCH="auto")
        env.update(extra_env)
        env.pop("CLAUDE_CONFIG_DIR", None)
        argv = [str(HERE / "fake_stream.py"), *(self.EXT_ARGS if args is None else args)]
        p = subprocess.Popen((command or [sys.executable, str(launcher or HERE / "longhaul_vscode.py")]) + argv,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                             cwd=cwd or self.tmp)
        return self.follow(p)

    def repositories(self):
        """work/.git and personal/.git under the scratch folder: (work, personal)."""
        for name in ("work", "personal"):
            (self.tmp / name / ".git").mkdir(parents=True)
        return self.tmp / "work", self.tmp / "personal"

    def restrict(self, name, *repos):
        """`name` restricted to `repos` in accounts.json; the rest as they were."""
        accounts = json.loads((self.relay / "accounts.json").read_text())
        for a in accounts:
            if a["name"] == name:
                a["repositories"] = [str(r) for r in repos]
        self.accounts(accounts)

    def shown(self, folder):
        """`folder` as longhaul names the session's: its real path, ~ for the home."""
        return longhaul.home_path(os.path.realpath(folder))

    def log_text(self):
        log = self.relay / "longhaul.log"
        return log.read_text() if log.exists() else ""

    def follow(self, p):
        """p's lines into self.lines (None at the end) and its stderr into
        self.stderr, each read on a thread that closes its pipe at the end."""
        def lines():
            with p.stdout:
                for l in p.stdout:
                    self.lines.put(l)
            self.lines.put(None)

        def stderr():
            with p.stderr:
                self.stderr = p.stderr.read()

        self.procs.append(p)
        self.lines, self.seen = __import__("queue").Queue(), []
        for target in (lines, stderr):
            self.readers.append(threading.Thread(target=target, daemon=True))
            self.readers[-1].start()
        return p

    def send(self, p, obj):
        p.stdin.write(json.dumps(obj).encode() + b"\n")
        p.stdin.flush()

    def initialize(self, p, rid="ext-init"):
        self.send(p, {"type": "control_request", "request_id": rid,
                      "request": {"subtype": "initialize", "hooks": self.HOOKS}})

    def user(self, p, text):
        u = str(uuid.uuid4())
        self.send(p, {"type": "user", "uuid": u, "session_id": "", "parent_tool_use_id": None,
                      "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        return u

    def wait_for(self, pred, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.lines.get(timeout=max(0.05, end - time.time()))
            except Exception:
                break
            if line is None:
                break
            msg = json.loads(line)  # anything that is not JSON fails the test here
            self.seen.append(msg)
            if pred(msg):
                return msg
        self.fail(f"not seen within {timeout}s; seen: {[m.get('type') for m in self.seen]}")

    def output(self, timeout=10):
        """Everything a short job printed (the reader thread has stdout)."""
        out, end = b"", time.time() + timeout
        while time.time() < end:
            line = self.lines.get(timeout=max(0.05, end - time.time()))
            if line is None:
                return out.strip()
            out += line
        return out.strip()

    def calls(self):
        return [json.loads(l) for l in (self.relay / "calls.jsonl").read_text().splitlines()]

    def received(self, pid):
        return [json.loads(l)["msg"] for l in (self.relay / "received.jsonl").read_text().splitlines()
                if json.loads(l)["pid"] == pid]

    def result_for(self, u):
        return lambda m: m.get("type") == "result" and m.get("user_message_uuid") == u


class Panel(StreamHarness):
    """longhaul as the VS Code extension's process wrapper, played against
    fake_stream.py, which speaks the panel's stream-json protocol."""

    def test_a_panel_job_runs_on_the_account_longhaul_picks(self):
        (self.relay / "usage").mkdir()
        (self.relay / "usage" / "Max.json").write_text(json.dumps(
            {"five_hour": {"used_percentage": 96, "resets_at": time.time() + 3600}}))
        p = self.start(["--version"])
        self.assertEqual(self.output(), b"fake job done")
        self.assertEqual(p.wait(10), 0)
        self.assertEqual(self.calls()[0]["config"], str(self.pro), "Max is nearly full")

    def test_without_the_switch_the_panel_talks_to_claude_itself(self):
        self.switch_mid_session(False)
        self.plan(["reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "hello")
        self.assertEqual(self.wait_for(self.result_for(u))["result"], "reply to: hello")
        call = self.calls()[0]
        self.assertEqual(call["pid"], p.pid, "longhaul handed over: the process is claude itself")
        self.assertEqual(call["config"], str(self.max))
        self.assertEqual(call["entrypoint"], "claude-vscode", "the extension's environment is kept")
        self.assertIsNone(call["api_key"])

    def test_the_relay_passes_a_chat_through_unchanged(self):
        self.plan(["reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "hello")
        self.wait_for(self.result_for(u))
        self.assertEqual([m["type"] for m in self.seen],
                         ["control_response", "auth_status", "system", "user", "assistant", "rate_limit_event", "result"])
        self.assertEqual(self.calls()[0]["args"], self.EXT_ARGS)

    def test_a_limit_moves_the_panel_chat_to_the_other_account(self):
        self.plan(["limit", "reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "build it")
        self.assertTrue(self.wait_for(self.result_for(u))["is_error"])
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)
        self.assertTrue(carry["message"]["content"][0]["text"].startswith("The Max account hit its session limit"))
        self.wait_for(self.result_for(carry["uuid"]))
        kinds = [(m["type"], m.get("subtype")) for m in self.seen]
        self.assertEqual(kinds.count(("system", "init")), 2, "one per turn, as claude sends them (2.1.300)")
        self.assertEqual(kinds.count(("auth_status", None)), 1, "the new claude's own is kept from the panel")
        self.assertEqual(sum(1 for m in self.seen if m["type"] == "control_response"), 1)
        first, second = self.calls()
        self.assertEqual(second["config"], str(self.pro))
        self.assertEqual([a for a in second["args"] if "resume" in a], ["--resume=sid-1"])
        got = self.received(second["pid"])
        self.assertEqual(got[0]["request"], {"subtype": "initialize", "hooks": self.HOOKS})
        self.assertEqual(got[0]["request_id"], "longhaul-initialize-1")
        self.assertEqual(got[1]["type"], "user")
        self.assertIn("the panel chat carries on with Pro", (self.relay / "longhaul.log").read_text())

    def test_a_panel_chat_records_its_usage_for_its_account(self):
        self.plan(["reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "hello")
        self.wait_for(self.result_for(u))
        p.stdin.close()
        p.wait(10)
        reading = json.loads((self.relay / "usage" / "Max.json").read_text())
        self.assertEqual(reading["five_hour"]["used_percentage"], 30)
        self.assertEqual(reading["seven_day"]["used_percentage"], 20)
        row = json.loads((self.relay / "ledger.jsonl").read_text().splitlines()[-1])
        self.assertEqual((row["session"], row["account"], row["cost"], row["five"]), ("sid-1", "Max", 0.25, 30))

    def test_a_message_sent_during_the_switch_reaches_the_new_claude(self):
        self.plan(["limit", "reply", "reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u1 = self.user(p, "build it")
        self.wait_for(self.result_for(u1))
        u2 = self.user(p, "and then this")
        self.assertEqual(self.wait_for(self.result_for(u2), 20)["result"], "reply to: and then this")
        second = self.calls()[1]
        texts = [m["message"]["content"][0]["text"] for m in self.received(second["pid"]) if m["type"] == "user"]
        self.assertTrue(texts[0].startswith("The Max account hit"))
        self.assertEqual(texts[1], "and then this")

    def test_a_changed_permission_mode_survives_the_switch(self):
        self.plan(["limit", "reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.send(p, {"type": "control_request", "request_id": "ext-mode",
                      "request": {"subtype": "set_permission_mode", "mode": "acceptEdits"}})
        self.wait_for(lambda m: (m.get("response") or {}).get("request_id") == "ext-mode")
        u = self.user(p, "build it")
        self.wait_for(self.result_for(u))
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)
        self.wait_for(self.result_for(carry["uuid"]))
        got = self.received(self.calls()[1]["pid"])
        self.assertEqual(got[1]["request"], {"subtype": "set_permission_mode", "mode": "acceptEdits"})
        self.assertFalse(any((m.get("response") or {}).get("request_id", "").startswith("longhaul-") for m in self.seen))

    def test_closing_the_panel_ends_the_chat(self):
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        p.stdin.close()
        self.assertEqual(p.wait(10), 0)

    def test_a_chat_stays_put_when_no_other_account_has_room(self):
        (self.relay / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3600}}))
        self.plan(["limit"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "build it")
        self.wait_for(self.result_for(u))
        time.sleep(1)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("no other account has room", (self.relay / "longhaul.log").read_text())

    def test_the_panel_sees_no_text_from_longhaul_when_an_account_is_wrong(self):
        self.emails[str(self.max)] = "stranger@example.com"
        self.plan(["reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "hello")
        self.wait_for(self.result_for(u))  # every line parsed as JSON on the way
        self.assertEqual(self.calls()[0]["config"], str(self.pro))
        self.assertIn("should be first@example.com", (self.relay / "longhaul.log").read_text())

    def test_if_longhaul_fails_while_choosing_the_panel_still_gets_claude(self):
        self.accounts([{"nam": "broken"}])
        p = self.start(["--version"])
        self.assertEqual(self.output(), b"fake job done")
        self.assertIsNone(self.calls()[0]["config"], "run as the extension asked")
        self.assertIn("running Claude unchanged", (self.relay / "longhaul.log").read_text())

    def test_if_longhaul_cannot_even_load_the_panel_still_gets_claude(self):
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "longhaul.py").write_text("this is not python (\n")
        (broken / "longhaul_vscode.py").write_text((HERE / "longhaul_vscode.py").read_text())
        p = self.start(["--version"], launcher=broken / "longhaul_vscode.py")
        self.assertEqual(self.output(), b"fake job done")
        self.assertEqual(p.wait(10), 0)


    # -- each of these failed before its fix

    def chat_to_limit(self, p, text="build it"):
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, text)
        self.wait_for(self.result_for(u))
        return u

    def test_the_wrapper_ends_with_claudes_own_code_when_claude_ends_first(self):
        self.plan(["exit:3"])
        p = self.start()
        self.chat_to_limit(p)
        self.assertEqual(p.wait(10), 3)
        time.sleep(0.2)
        self.assertNotIn(b"Fatal Python error", getattr(self, "stderr", b""))

    def test_a_claude_that_will_not_start_on_the_next_account_leaves_the_chat_on_this_one(self):
        self.plan(["limit", "reply"])
        p = self.start(FAKE_DIE_ON_RESUME_IN=str(self.pro))
        self.chat_to_limit(p)
        u2 = self.user(p, "and then this")
        self.assertEqual(self.wait_for(self.result_for(u2), 15)["result"], "reply to: and then this")
        configs = [c["config"] for c in self.calls()]
        self.assertEqual(configs, [str(self.max), str(self.pro), str(self.max)])
        self.assertIn("the chat stays on Max", (self.relay / "longhaul.log").read_text())

    def test_mcp_and_flag_changes_made_in_the_panel_survive_the_switch(self):
        self.plan(["limit", "reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        changes = [{"subtype": "mcp_set_servers", "servers": {"a": {"type": "sdk"}}},
                   {"subtype": "apply_flag_settings", "settings": {"viewMode": "focus"}},
                   {"subtype": "mcp_toggle", "serverName": "x", "enabled": False},
                   {"subtype": "mcp_toggle", "serverName": "x", "enabled": True},
                   {"subtype": "set_mcp_permission_mode_override", "serverName": "y", "mode": "auto"},
                   {"subtype": "apply_flag_settings", "settings": {"z": 1}}]
        for i, req in enumerate(changes):
            self.send(p, {"type": "control_request", "request_id": f"ext-{i}", "request": req})
            self.wait_for(lambda m, i=i: (m.get("response") or {}).get("request_id") == f"ext-{i}")
        u = self.user(p, "build it")
        self.wait_for(self.result_for(u))
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)
        self.wait_for(self.result_for(carry["uuid"]))
        got = [m["request"] for m in self.received(self.calls()[1]["pid"]) if m["type"] == "control_request"]
        self.assertEqual(got[0]["subtype"], "initialize")
        self.assertEqual(got[1:], [changes[0], changes[1], changes[3], changes[4], changes[5]])
        self.assertFalse(any((m.get("response") or {}).get("request_id", "").startswith("longhaul-") for m in self.seen))

    def test_signing_out_in_the_panel_does_not_touch_cws_accounts(self):
        p = self.start(["auth", "logout"])
        self.assertEqual(self.output(), b"fake job done")
        self.assertIsNone(self.calls()[0]["config"], "run as the extension asked, on ~/.claude")

    def test_a_sign_in_in_a_panel_chat_makes_longhaul_check_that_account_again(self):
        (self.relay / "auth-cache.json").write_text(json.dumps(
            {str(self.max): {"email": "first@example.com", "at": time.time()}}))
        p = self.start(LONGHAUL_AUTH_TTL="3600")
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.send(p, {"type": "control_request", "request_id": "ext-login",
                      "request": {"subtype": "claude_authenticate", "loginWithClaudeAi": True}})
        self.wait_for(lambda m: (m.get("response") or {}).get("request_id") == "ext-login")
        self.assertNotIn(str(self.max), json.loads((self.relay / "auth-cache.json").read_text()))

    def test_an_account_signed_in_as_someone_else_is_never_used_even_when_every_account_is_limited(self):
        (self.relay / "state.json").write_text(json.dumps(
            {"limited_until": {"Max": time.time() + 100, "Pro": time.time() + 200}}))
        self.emails[str(self.max)] = "stranger@example.com"
        p = self.start(["--version"])
        self.assertEqual(self.output(), b"fake job done")
        self.assertEqual(self.calls()[0]["config"], str(self.pro))

    def test_the_account_a_chat_moves_to_gets_its_links_first(self):
        (self.default / "settings.json").write_text("{}")
        self.plan(["limit", "reply"])
        p = self.start()
        u = self.chat_to_limit(p)
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)
        self.wait_for(self.result_for(carry["uuid"]))
        self.assertTrue((self.pro / "projects").is_symlink())
        self.assertTrue((self.pro / "settings.json").is_symlink())

    def test_a_hung_sign_in_check_does_not_hold_up_the_panel(self):
        t = time.time()
        p = self.start(["--version"], FAKE_AUTH_HANG="1", LONGHAUL_AUTH_TIMEOUT="1")
        self.assertEqual(self.output(15), b"fake job done")
        self.assertLess(time.time() - t, 8)
        self.assertIn("could not check who is signed in", (self.relay / "longhaul.log").read_text())

    def test_a_failed_sign_in_check_is_not_remembered(self):
        p = self.start(["--version"], FAKE_AUTH_GARBAGE="1", LONGHAUL_AUTH_TTL="3600")
        self.assertEqual(self.output(), b"fake job done")
        cache = self.relay / "auth-cache.json"
        self.assertFalse(cache.exists() and json.loads(cache.read_text()))

    def test_a_half_line_from_a_stopped_claude_never_reaches_the_panel(self):
        self.plan(["limit_partial", "reply"])
        p = self.start()
        u = self.chat_to_limit(p)
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)  # parses every line
        self.wait_for(self.result_for(carry["uuid"]))

    def test_after_a_switch_the_old_accounts_reading_is_not_put_under_the_new_one(self):
        self.plan(["limit", "reply"])
        p = self.start(FAKE_NO_EVENT_IN=str(self.pro))
        u = self.chat_to_limit(p)
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)
        self.wait_for(self.result_for(carry["uuid"]))
        time.sleep(0.3)
        self.assertFalse((self.relay / "usage" / "Pro.json").exists())

    def test_if_the_relay_cannot_start_the_panel_still_gets_claude(self):
        # junk where state.json keeps handled_limits fails start() in Watch on every Python
        # (a transcript link to nowhere did on 3.12+ alone: 3.11's Path.glob passes it over);
        # claude is spawned by then and killed, so the chat the panel got is the last call
        (self.relay / "state.json").write_text(json.dumps({"handled_limits": None}))
        self.plan(["reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "hello")
        self.assertEqual(self.wait_for(self.result_for(u))["result"], "reply to: hello")
        self.assertIsNone(self.calls()[-1]["config"])
        self.assertIn("running Claude unchanged", (self.relay / "longhaul.log").read_text())

    def test_a_log_that_cannot_be_written_does_not_stop_the_wrapper(self):
        (self.relay / "longhaul.log").mkdir()
        p = self.start(["--version"])
        self.assertEqual(self.output(), b"fake job done")

    def test_the_shell_launcher_runs_claude_unchanged_without_longhaul(self):
        empty_home = self.tmp / "home"
        empty_home.mkdir()
        p = self.start(["--version"], command=["/bin/sh", str(HERE / "longhaul-vscode")], HOME=str(empty_home))
        self.assertEqual(self.output(), b"fake job done")

    def test_a_reopened_panel_chat_starts_on_the_account_it_last_ran_on(self):
        # Sprint 5, D3d. With no readings yet, choose_for_panel() starts in
        # order, on Max; the record names Pro.
        (self.relay / "usage" / "sessions").mkdir(parents=True)
        record = self.relay / "usage" / "sessions" / "sid-c.json"
        record.write_text(json.dumps({"account": "Pro"}))
        self.plan(["reply"] * 4)

        def chat_starts_on(expect, mode):
            p = self.start([*self.EXT_ARGS, "--resume=sid-c"], LONGHAUL_SWITCH=mode)
            self.initialize(p)
            self.wait_for(lambda m: m.get("type") == "control_response")
            self.assertEqual(self.wait_for(self.result_for(self.user(p, "hello")))["result"], "reply to: hello")
            p.stdin.close()
            p.wait(10)
            self.assertEqual(self.calls()[-1]["config"], str(expect), mode)

        chat_starts_on(self.max, "auto")  # auto keeps the account longhaul picks
        self.assertEqual(json.loads(record.read_text())["account"], "Max", "the relay keeps the record current")
        record.write_text(json.dumps({"account": "Pro"}))
        chat_starts_on(self.pro, "ask")
        chat_starts_on(self.pro, "never")
        log = (self.relay / "longhaul.log").read_text
        self.assertIn("panel: sid-c last ran on Pro; with switching set to ask it starts there", log())
        # With the switch off too: the choice comes before the handover.
        self.switch_mid_session(False)
        chat_starts_on(self.pro, "ask")
        # Pro signed in as someone else: picked instead, and the log says
        # why (review, Sprint 5, B2: the chat moved with no line).
        record.write_text(json.dumps({"account": "Pro"}))
        self.emails[str(self.pro)] = "stranger@example.com"
        chat_starts_on(self.max, "never")
        self.assertIn("panel: sid-c last ran on Pro, which is not signed in as expected; picking an account", log())

    def test_with_the_switch_off_a_chat_reopened_by_id_is_recorded_where_it_ran(self):
        # Review, Sprint 5 (B1). With switch_mid_session off (the default)
        # the wrapper execs claude and nothing of longhaul sees the chat;
        # only the relay's note_cost() wrote the record, so a chat reopened
        # by id with no record was picked, ran, and could be picked again at
        # the next reopen, moved without a word in never. The wrapper now
        # notes the account before the handover. A new chat's id is not
        # known before the exec: the status line or the relay records it.
        self.switch_mid_session(False)
        self.plan(["reply"] * 3)
        record = self.relay / "usage" / "sessions" / "sid-c.json"

        def chat_runs_on(mode):
            p = self.start([*self.EXT_ARGS, "--resume=sid-c"], LONGHAUL_SWITCH=mode)
            self.initialize(p)
            self.wait_for(lambda m: m.get("type") == "control_response")
            self.assertEqual(self.wait_for(self.result_for(self.user(p, "hello")))["result"], "reply to: hello")
            p.stdin.close()
            p.wait(10)
            return self.calls()[-1]["config"]

        self.assertEqual(chat_runs_on("never"), str(self.max), "no record: picked, and Max is first in order")
        self.assertEqual(json.loads(record.read_text())["account"], "Max", "noted before the handover")
        self.assertIn("panel: no record of where sid-c last ran; picking an account",
                      (self.relay / "longhaul.log").read_text())
        # Pro's unused 5-hour allowance now expires soonest, so pick() alone
        # would start the chat there; reopened by id in never, it stays on Max.
        (self.relay / "usage" / "Pro.json").write_text(json.dumps(
            {"at": time.time(), "five_hour": {"used_percentage": 20, "resets_at": time.time() + 600}}))
        self.assertEqual(chat_runs_on("never"), str(self.max), "the record wins over pick()")
        self.assertEqual(chat_runs_on("auto"), str(self.pro), "auto leaves it to pick()")
        self.assertEqual(json.loads(record.read_text())["account"], "Pro", "and the record follows the chat")

    def test_a_chat_reopened_on_an_account_at_its_limit_is_told_so_by_notification(self):
        # Review, Sprint 5 (B4): with the switch off a chat reopened by id on
        # an account at its limit got Claude's own limit error and no word
        # of why. The terminal says it and names --account, which the panel
        # has not: a notification, once, before the handover.
        self.switch_mid_session(False)
        (self.relay / "usage" / "sessions").mkdir(parents=True)
        (self.relay / "usage" / "sessions" / "sid-c.json").write_text(json.dumps({"account": "Pro"}))
        (self.relay / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3600}}))
        self.plan(["reply"] * 2)

        def chat_runs_on(mode):
            p = self.start([*self.EXT_ARGS, "--resume=sid-c"], LONGHAUL_SWITCH=mode, **self.fake_notifications())
            self.initialize(p)
            self.wait_for(lambda m: m.get("type") == "control_response")
            self.assertEqual(self.wait_for(self.result_for(self.user(p, "hello")))["result"], "reply to: hello")
            p.stdin.close()
            p.wait(10)
            return self.calls()[-1]["config"]

        self.assertEqual(chat_runs_on("never"), str(self.pro), "it stays there, limited or not")
        said = self.notified()
        self.assertEqual(len(said), 1, said)
        self.assertIn("Pro is at its limit until", said[0])
        self.assertIn("the chat stays on it (switching is never)", said[0])
        self.notes.unlink()
        self.assertEqual(chat_runs_on("auto"), str(self.max), "auto picks an account with room")
        self.assertEqual(self.notified(wait=1), [], "nothing to say for a chat longhaul picked")

    # Sprint 7, A2: a panel chat runs only on an account allowed in the
    # folder the wrapper runs in, which is the folder its Claude runs in.

    def chat_runs_in(self, folder, args=None, **env):
        """A panel chat started in `folder`: the config folder its claude ran on."""
        p = self.start(args, cwd=folder, **env)
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.assertEqual(self.wait_for(self.result_for(self.user(p, "hello")))["result"], "reply to: hello")
        p.stdin.close()
        p.wait(10)
        return self.calls()[-1]["config"]

    def test_a_panel_chat_starts_only_on_an_account_allowed_in_its_folder(self):
        work, personal = self.repositories()
        self.restrict("Max", work)  # Max is first in order: choose_for_panel() alone picks it
        self.plan(["reply"] * 3)
        self.assertEqual(self.chat_runs_in(work), str(self.max))
        self.assertEqual(self.chat_runs_in(personal), str(self.pro), "Max may not be used in personal")
        self.switch_mid_session(False)
        self.assertEqual(self.chat_runs_in(personal), str(self.pro), "the choice comes before the handover")
        self.assertIn(f"panel chat on Pro in {self.shown(personal)}:", self.log_text(), "the log names the folder")

    def test_a_panel_chat_reopened_by_id_leaves_a_recorded_account_not_allowed_here(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        (self.relay / "usage" / "sessions").mkdir(parents=True)
        (self.relay / "usage" / "sessions" / "sid-c.json").write_text(json.dumps({"account": "Pro"}))
        self.plan(["reply"])
        self.assertEqual(self.chat_runs_in(personal, [*self.EXT_ARGS, "--resume=sid-c"], LONGHAUL_SWITCH="ask"),
                         str(self.max))
        self.assertIn(f"panel: sid-c last ran on Pro, which may not be used in {self.shown(personal)}; "
                      "picking an account", self.log_text())

    def test_a_panel_switch_never_lands_on_an_account_not_allowed_here(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        self.plan(["limit", "reply"])
        p = self.start(cwd=personal)
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.assertTrue(self.wait_for(self.result_for(self.user(p, "build it")))["is_error"])
        time.sleep(1.5)
        self.assertEqual(len(self.calls()), 1, "no second claude on Pro")
        self.assertFalse(self.wait_for(self.result_for(self.user(p, "and now")))["is_error"], "the chat goes on")
        self.assertIn("Max hit its session limit; no other account allowed here has room, so the chat stays as it is",
                      self.log_text())

    def test_with_no_allowed_account_the_panel_chat_is_refused_with_a_notification(self):
        work, personal = self.repositories()
        self.restrict("Max", work)
        self.restrict("Pro", work)
        p = self.start(cwd=personal, **self.fake_notifications())
        self.assertEqual(p.wait(10), 1)
        self.assertFalse((self.relay / "calls.jsonl").exists(), "no claude, not even the panel's own (Q3)")
        said = f"No account may be used in {self.shown(personal)}: Max only in {work}, Pro only in {work}."
        self.assertIn(f"panel: {said}", self.log_text())
        notes = self.notified()
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith(said), notes)
        # A short job is not a session: it runs as it does today.
        job = self.start(["--version"], cwd=personal)
        self.assertEqual(self.output(), b"fake job done")
        self.assertEqual(job.wait(10), 0)

    def test_with_the_allowed_accounts_signed_in_wrong_a_chat_is_refused_not_run_on_claude(self):
        # Final review, Sprint 7: with Max denied here and Pro, the one account
        # allowed, signed in as someone else, choose_for_panel() found nothing and
        # the wrapper ran Claude unchanged on ~/.claude, which can be the denied
        # account or signed in as it. With any account denied the chat is refused,
        # as run() and pipe() refuse; unchanged only when nothing is restricted.
        work, personal = self.repositories()
        self.restrict("Max", work)
        self.emails[str(self.pro)] = "stranger@example.com"
        p = self.start(cwd=personal, **self.fake_notifications())
        self.assertEqual(p.wait(10), 1)
        self.assertFalse((self.relay / "calls.jsonl").exists(), "no claude, not even the panel's own")
        said = f"No account allowed in {self.shown(personal)} is signed in as expected"
        self.assertIn(f"panel: {said}", self.log_text())
        notes = self.notified()
        self.assertEqual(len(notes), 2, notes)
        self.assertTrue(notes[0].startswith("Pro should be"), "the sign-in check's own notice, as before")
        self.assertTrue(notes[1].startswith(said), notes)
        self.assertIn("(Max may not be used here)", notes[1])


class PanelSwitching(StreamHarness):
    """Sprint 2, D3c: with switch_mid_session on, the panel moves a chat only
    in "auto" (docs/adr/0002-switching-without-a-terminal.md)."""

    def limit_in_chat(self, **env):
        """A chat that meets a limit: the result, after checking that no
        second claude started and that the chat goes on where it is."""
        self.plan(["limit", "reply"])
        p = self.start(**env)
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.assertTrue(self.wait_for(self.result_for(self.user(p, "build it")))["is_error"])
        time.sleep(1.5)
        self.assertEqual(len(self.calls()), 1, "no second claude")
        self.assertFalse(self.wait_for(self.result_for(self.user(p, "and now")))["is_error"])

    def limit_moves(self, **env):
        """A chat that meets a limit and moves: the second claude's launch."""
        self.plan(["limit", "reply"])
        p = self.start(**env)
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        u = self.user(p, "build it")
        self.assertTrue(self.wait_for(self.result_for(u))["is_error"])
        carry = self.wait_for(lambda m: m.get("type") == "user" and m.get("uuid") != u)
        self.wait_for(self.result_for(carry["uuid"]))
        self.assertEqual(len(self.calls()), 2)
        return self.calls()[1]

    def test_in_ask_mode_a_panel_chat_stays_on_its_account_and_says_so_once(self):
        self.limit_in_chat(LONGHAUL_SWITCH="ask", **self.fake_notifications())
        said = [n for n in self.notified() if "limit" in n]
        self.assertEqual(len(said), 1, said)
        # Review, Sprint 2: the fix is usable as written (notify() turns "
        # into ', so a JSON snippet came out wrong), and it names both
        # places the mode can come from.
        self.assertRegex(said[0], r"^Max hit its session limit")
        self.assertIn("chat stays on Max", said[0])
        self.assertIn("set the key switch to auto in", said[0])
        self.assertIn(str(self.relay / "vscode.json"), said[0])
        self.assertIn("LONGHAUL_SWITCH=auto", said[0])
        self.assertNotIn("'switch'", said[0])

    def test_in_never_mode_the_panels_notification_says_you_chose_it(self):
        self.limit_in_chat(LONGHAUL_SWITCH="never", **self.fake_notifications())
        said = [n for n in self.notified() if "limit" in n]
        self.assertEqual(len(said), 1, said)
        self.assertIn("switching is never", said[0])
        self.assertNotIn("cannot ask", said[0])

    def test_a_slow_notification_never_holds_the_panels_lines(self):
        # Review, Sprint 2: notify() ran before the held lines were let go,
        # so a slow osascript (up to 10 s) froze the panel.
        self.plan(["limit", "reply"])
        p = self.start(LONGHAUL_SWITCH="ask", **self.fake_notifications(delay=4))
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.assertTrue(self.wait_for(self.result_for(self.user(p, "build it")))["is_error"])
        started = time.time()
        self.assertFalse(self.wait_for(self.result_for(self.user(p, "and now")))["is_error"])
        self.assertLess(time.time() - started, 3)

    def test_the_panels_mode_comes_from_vscode_json_without_longhaul_switch(self):
        # Review, Sprint 2: the first version used "never", which a panel that
        # never read the file would also satisfy. "auto" cannot pass that way.
        self.switch_mid_session(True, switch="auto")
        self.assertEqual(self.limit_moves(LONGHAUL_SWITCH="")["config"], str(self.pro))

    def test_longhaul_switch_in_the_panels_environment_beats_vscode_json(self):
        self.switch_mid_session(True, switch="auto")
        self.limit_in_chat(LONGHAUL_SWITCH="never")
        self.switch_mid_session(True, switch="never")
        self.tearDown()  # the first chat's processes end here, not with the run
        self.setUp()  # a fresh scratch home for the second run
        self.switch_mid_session(True, switch="never")
        self.assertEqual(self.limit_moves(LONGHAUL_SWITCH="auto")["config"], str(self.pro))

    def test_an_empty_switch_key_is_the_default(self):
        self.switch_mid_session(True, switch="")
        self.limit_in_chat(LONGHAUL_SWITCH="")
        self.assertNotIn("is not ask, auto or never", (self.relay / "longhaul.log").read_text())

    def test_an_unknown_switch_in_vscode_json_runs_the_chat_and_never_moves_it(self):
        self.switch_mid_session(True, switch=["auto"])
        self.limit_in_chat(LONGHAUL_SWITCH="")
        lines = [l for l in (self.relay / "longhaul.log").read_text().splitlines() if "is not ask, auto or never" in l]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("vscode.json", lines[0], "the log says where the value came from")

    def test_switch_mid_session_must_be_true_itself_not_a_truthy_word(self):
        # Review, Sprint 2: "false" in vscode.json put longhaul in between.
        (self.relay / "vscode.json").write_text(json.dumps({"switch_mid_session": "false"}))
        self.plan(["reply"])
        p = self.start()
        self.initialize(p)
        self.wait_for(lambda m: m.get("type") == "control_response")
        self.assertEqual(self.wait_for(self.result_for(self.user(p, "hello")))["result"], "reply to: hello")
        self.assertEqual(self.calls()[0]["pid"], p.pid, "longhaul handed over: the process is claude itself")


class Pipe(StreamHarness):
    """longhaul pipe: the panel's relay with another program as the driver. The
    driver sends no initialize of its own here, only plain user lines;
    claude's arguments come after `--`."""

    HEAD = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    ARGS = [*HEAD, "--session-id", "sid-1", "--permission-mode", "default"]

    def pipe(self, *flags, args=None, cwd=None, **extra_env):
        """longhaul pipe as a driver runs it; `cwd`: the driver's folder
        (default: the scratch folder)."""
        env = dict(os.environ, LONGHAUL_HOME=str(self.relay), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.default),
                   LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "global.json"), LONGHAUL_POLL_SECONDS="0.1",
                   LONGHAUL_NO_NOTIFY="1", LONGHAUL_AUTH_TTL="0", FAKE_EMAILS=json.dumps(self.emails),
                   LONGHAUL_CLAUDE_BIN=str(HERE / "fake_stream.py"), ANTHROPIC_API_KEY="sk-test-not-real",
                   CLAUDE_CODE_ENTRYPOINT="claude-vscode", LONGHAUL_SWITCH="auto")  # as the panel's: ADR-0002
        env.update(extra_env)
        env.pop("CLAUDE_CONFIG_DIR", None)
        argv = [sys.executable, str(HERE / "longhaul.py"), "pipe", "--name", "api", *flags, "--",
                *(self.ARGS if args is None else args)]
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, cwd=cwd or self.tmp)
        return self.follow(p)

    def calls_or_none(self):
        log = self.relay / "calls.jsonl"
        return self.calls() if log.exists() else []

    def test_pipe_relays_the_drivers_lines_unchanged(self):
        self.plan(["reply"])
        p = self.pipe()
        u = self.user(p, "hello")
        self.assertEqual(self.wait_for(self.result_for(u))["result"], "reply to: hello")
        call = self.calls()[0]
        self.assertNotEqual(call["pid"], p.pid, "longhaul stays in between")
        self.assertEqual(call["args"], self.ARGS)
        self.assertEqual(call["config"], str(self.max))

    def test_pipe_never_passes_remote_control(self):
        self.plan(["reply"])
        p = self.pipe()
        self.wait_for(self.result_for(self.user(p, "hello")))
        self.assertNotIn("--remote-control", self.calls()[0]["args"])
        for asking in (["--remote-control", "api"], ["--remote-control=api"], ["--rc"], ["remote-control"]):
            with self.subTest(asking=asking):
                asked = self.pipe(args=[*self.ARGS[:-2], *asking, "--permission-mode", "default"])
                self.assertEqual(asked.wait(10), 2)
        self.assertEqual(len(self.calls()), 1, "no claude was started for it")

    def test_pipe_strips_an_api_key_and_an_outer_sessions_variables(self):
        self.plan(["reply"])
        p = self.pipe()
        self.wait_for(self.result_for(self.user(p, "hello")))
        call = self.calls()[0]
        self.assertIsNone(call["api_key"])
        self.assertIsNone(call["entrypoint"], "env_for's environment, not the panel's whole one")

    def test_pipe_records_its_usage_for_its_account(self):
        self.plan(["reply"])
        p = self.pipe()
        self.wait_for(self.result_for(self.user(p, "hello")))
        p.stdin.close()
        p.wait(10)
        reading = json.loads((self.relay / "usage" / "Max.json").read_text())
        self.assertEqual(reading["five_hour"]["used_percentage"], 30)

    def test_pipe_never_starts_claude_when_no_account_is_signed_in_as_expected(self):
        self.emails = {str(self.max): "stranger@example.com", str(self.pro): "stranger@example.com"}
        p = self.pipe()
        self.assertEqual(p.wait(10), 1)
        self.assertEqual(self.calls_or_none(), [])

    def test_pipe_writes_no_text_of_its_own_into_the_drivers_stream(self):
        # longhaul tidies an account's folder before a launch and says what it
        # tidied. Printed, that sentence would reach the driver between
        # claude's JSON lines and break its reading of the stream, as it
        # would the panel's (QUIET).
        (self.default / "settings.json").write_text("{}")
        (self.max / "settings.json").write_text("{}")
        self.plan(["reply"])
        p = self.pipe()
        self.wait_for(self.result_for(self.user(p, "hello")))  # every line parsed as JSON on the way
        self.assertIn("settings.json", (self.relay / "longhaul.log").read_text(), "longhaul did say something")

    def reply_to_carry_on(self, m):
        return m.get("type") == "result" and str(m.get("result", "")).startswith("reply to: The Max account")

    def test_pipe_moves_the_session_at_a_limit_and_says_so_in_the_stream(self):
        self.plan(["limit", "reply"])
        p = self.pipe("--swap")
        u = self.user(p, "build it")
        self.assertTrue(self.wait_for(self.result_for(u))["is_error"])
        self.wait_for(self.reply_to_carry_on, 20)
        said = [m for m in self.seen if m.get("type") == "longhaul"]
        self.assertEqual(said, [{"type": "longhaul", "subtype": "moved", "from": "Max", "to": "Pro", "why": "five_hour"}])
        self.assertEqual(self.calls()[1]["config"], str(self.pro))

    def test_pipe_makes_its_own_initialize_when_the_driver_sent_none(self):
        self.plan(["limit", "reply"])
        p = self.pipe("--swap")
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.wait_for(self.reply_to_carry_on, 20)
        first = self.received(self.calls()[1]["pid"])[0]
        self.assertEqual(first["request"], {"subtype": "initialize"})
        self.assertTrue(first["request_id"].startswith("longhaul-initialize"))
        self.assertFalse(any(m.get("type") == "control_response" for m in self.seen), "its answer is longhaul's own")

    def test_pipe_says_when_no_account_has_room(self):
        (self.relay / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3600}}))
        self.plan(["limit"])
        p = self.pipe("--swap")
        self.wait_for(self.result_for(self.user(p, "build it")))
        line = self.wait_for(lambda m: m.get("type") == "longhaul")
        self.assertEqual(line["subtype"], "all_limited")
        self.assertIsInstance(line["until"], (int, float))
        self.assertEqual(len(self.calls()), 1)

    def test_pipe_never_swaps_when_swapping_is_off(self):
        self.plan(["limit"])
        p = self.pipe()
        self.assertTrue(self.wait_for(self.result_for(self.user(p, "build it")))["is_error"])
        time.sleep(1)
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(any(m.get("type") == "longhaul" for m in self.seen))
        self.assertIn("swapping is off", (self.relay / "longhaul.log").read_text())

    def test_pipe_relays_and_swaps_whatever_the_panels_switch_says(self):
        self.switch_mid_session(False)
        self.plan(["limit", "reply"])
        p = self.pipe("--swap")
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.wait_for(self.reply_to_carry_on, 20)
        self.assertEqual(self.calls()[1]["config"], str(self.pro))

    # Sprint 2, D3c (docs/adr/0002-switching-without-a-terminal.md): a move
    # needs both the user's "auto" and the driver's --swap.

    def stays(self, *flags, **env):
        """A limit in a session started with --swap: the longhaul line written, after
        checking that no second claude started."""
        self.plan(["limit", "reply"])
        p = self.pipe("--swap", *flags, **env)
        self.assertTrue(self.wait_for(self.result_for(self.user(p, "build it")))["is_error"])
        line = self.wait_for(lambda m: m.get("type") == "longhaul")
        time.sleep(1)
        self.assertEqual(len(self.calls()), 1, "no second claude")
        # The session goes on where it is: the driver's next line reaches the same claude.
        self.assertFalse(self.wait_for(self.result_for(self.user(p, "and now")))["is_error"])
        return line

    def test_pipe_with_swap_in_ask_mode_stays_and_says_so_in_the_stream(self):
        line = self.stays(LONGHAUL_SWITCH="ask")
        self.assertEqual({k: line.get(k) for k in ("subtype", "on", "why", "switch")},
                         {"subtype": "stays", "on": "Max", "why": "five_hour", "switch": "ask"})
        self.assertIsInstance(line["until"], (int, float))

    def test_pipe_with_swap_stays_in_never_mode(self):
        self.assertEqual(self.stays(LONGHAUL_SWITCH="never")["switch"], "never")

    def test_pipe_with_swap_stays_by_default(self):
        self.assertEqual(self.stays(LONGHAUL_SWITCH="")["switch"], "ask")

    def test_pipe_switch_flag_never_wins_over_auto_in_the_environment(self):
        self.assertEqual(self.stays("--switch", "never", LONGHAUL_SWITCH="auto")["switch"], "never")

    def test_pipe_without_swap_writes_no_stays_line_in_any_mode(self):
        # Review, Sprint 2: only the auto cell of this row had a test.
        for mode in ("ask", "never", "auto"):
            self.setUp()
            self.plan(["limit"])
            p = self.pipe(LONGHAUL_SWITCH=mode)
            self.assertTrue(self.wait_for(self.result_for(self.user(p, "build it")))["is_error"])
            time.sleep(1)
            self.assertEqual(len(self.calls()), 1, mode)
            self.assertFalse(any(m.get("type") == "longhaul" for m in self.seen), mode)
            self.tearDown()

    def test_pipe_switch_auto_on_the_flag_moves_with_swap_and_beats_never_in_the_environment(self):
        self.plan(["limit", "reply"])
        p = self.pipe("--swap", "--switch", "auto", LONGHAUL_SWITCH="never")
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.wait_for(self.reply_to_carry_on, 20)
        self.assertEqual(self.calls()[1]["config"], str(self.pro))
        self.assertEqual([m["subtype"] for m in self.seen if m.get("type") == "longhaul"], ["moved"])

    def test_pipe_with_an_unknown_switch_mode_starts_no_claude(self):
        for flags, env in ((("--switch", "sometimes"), {}), ((), {"LONGHAUL_SWITCH": "sometimes"})):
            self.assertEqual(self.pipe("--swap", *flags, **env).wait(10), 2, (flags, env))
        self.assertFalse((self.relay / "calls.jsonl").exists())

    def test_pipe_refuses_an_abbreviation_that_could_be_swap_or_switch(self):
        # With two flags starting --sw, "--sw" must not quietly mean either.
        self.assertEqual(self.pipe("--sw").wait(10), 2)
        self.assertFalse((self.relay / "calls.jsonl").exists())

    # -- each of these failed before its fix, or, where it pins code
    # already there, failed with that code broken

    def test_pipe_never_starts_claude_when_the_accounts_file_is_missing_or_broken(self):
        # load_accounts() falls back to one account on ~/.claude with no
        # email, which the sign-in check lets through: a driven session
        # would run on the panel's own sign-in.
        path = self.relay / "accounts.json"
        for case, text in (("missing", None), ("empty", ""), ("invalid JSON", "[{not json"),
                           ("no accounts", "[]"), ("not a list", '{"name": "Max"}')):
            with self.subTest(case):
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(text)
                p = self.pipe()
                self.assertEqual(p.wait(10), 1)
                self.assertEqual(self.output(), b"")
                self.assertEqual(self.calls_or_none(), [])

    def test_pipe_never_runs_on_an_account_without_an_email_or_a_folder_of_its_own(self):
        self.emails["default"] = "first@example.com"  # ~/.claude is signed in as Max would be
        for case, acct in (("no email", {"name": "Max", "config_dir": str(self.max)}),
                           ("no folder", {"name": "Max", "config_dir": None, "email": "first@example.com"})):
            with self.subTest(case):
                self.accounts([acct])
                p = self.pipe()
                self.assertEqual(p.wait(10), 1)
                self.assertEqual(self.calls_or_none(), [])

    def test_pipe_never_moves_a_session_onto_an_account_without_an_email(self):
        self.accounts([{"name": "Max", "config_dir": str(self.max), "email": "first@example.com"},
                       {"name": "Pro", "config_dir": str(self.pro)}])
        self.plan(["limit"])
        p = self.pipe("--swap")
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.assertEqual(self.wait_for(lambda m: m.get("type") == "longhaul")["subtype"], "all_limited")
        self.assertEqual(len(self.calls()), 1)

    def test_pipe_hands_claude_no_github_sign_in_at_launch_or_after_a_move_when_asked(self):
        self.plan(["limit", "reply"])
        p = self.pipe("--swap", "--no-git-sign-in",
                      GITHUB_TOKEN="not-real", SSH_AUTH_SOCK="/tmp/agent.sock", GIT_CONFIG_COUNT="1")
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.wait_for(self.reply_to_carry_on, 20)
        self.assertEqual([c["sign_ins"] for c in self.calls()], [[], []])

    def test_pipe_leaves_the_git_sign_in_alone_unless_asked(self):
        self.plan(["reply"])
        p = self.pipe(GITHUB_TOKEN="not-real", SSH_AUTH_SOCK="/tmp/agent.sock")
        self.wait_for(self.result_for(self.user(p, "hello")))
        self.assertEqual(self.calls()[0]["sign_ins"], ["GITHUB_TOKEN", "SSH_AUTH_SOCK"])

    def test_pipe_starts_on_the_account_it_is_asked_for_while_that_one_is_not_at_its_limit(self):
        (self.relay / "usage").mkdir()
        (self.relay / "usage" / "Pro.json").write_text(json.dumps(
            {"five_hour": {"used_percentage": 96, "resets_at": time.time() + 3600}}))
        p = self.pipe("--account", "Pro")
        p.stdin.close()
        self.assertEqual(p.wait(10), 0)
        self.assertEqual(self.calls()[0]["config"], str(self.pro), "nearly full is not at its limit")
        (self.relay / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3600}}))
        p = self.pipe("--account", "Pro")
        p.stdin.close()
        self.assertEqual(p.wait(10), 0)
        self.assertEqual(self.calls()[1]["config"], str(self.max), "at its limit: the account longhaul picks")

    def test_pipe_refuses_an_account_it_does_not_know(self):
        p = self.pipe("--account", "Team")
        self.assertEqual(p.wait(10), 2)
        self.assertEqual(self.calls_or_none(), [])

    def test_pipe_leaves_telling_the_user_of_a_move_to_the_driver(self):
        # The driver says it from the `moved` line; a Mac notification as
        # well would say it twice. A stand-in osascript, first on PATH,
        # records any notification and shows none.
        bin_dir, told = self.tmp / "bin", self.tmp / "told.txt"
        bin_dir.mkdir()
        (bin_dir / "osascript").write_text(f'#!/bin/sh\necho "$@" >> "{told}"\n')
        (bin_dir / "osascript").chmod(0o755)
        self.plan(["limit", "reply"])
        p = self.pipe("--swap", LONGHAUL_NO_NOTIFY="", PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.wait_for(self.reply_to_carry_on, 20)
        p.stdin.close()  # longhaul ends only after switch(), which notifies, has returned
        self.assertEqual(p.wait(10), 0)
        self.assertTrue(any(m.get("subtype") == "moved" for m in self.seen))
        self.assertFalse(told.exists(), "no Mac notification for a move")

    def test_pipe_names_the_session_for_longhaul_usage_however_its_id_is_given(self):
        forms = {"sid-a": ["--session-id", "sid-a"], "sid-b": ["--session-id=sid-b"],
                 "sid-c": ["--resume", "sid-c"], "sid-d": ["--resume=sid-d"], "sid-e": ["-r", "sid-e"]}
        for sid, form in forms.items():
            p = self.pipe(args=[*self.HEAD, *form, "--permission-mode", "default"])
            p.stdin.close()
            self.assertEqual(p.wait(10), 0, sid)
        self.assertEqual(json.loads((self.relay / "labels.json").read_text()), {sid: "api" for sid in forms})

    def test_pipe_resumes_a_session_on_the_account_it_last_ran_on_unless_auto(self):
        # Sprint 5, D3d. Max is first in order, so choose_for_panel() alone
        # would start the session on Max; the record names Pro.
        (self.relay / "usage" / "sessions").mkdir(parents=True)
        record = self.relay / "usage" / "sessions" / "sid-c.json"
        record.write_text(json.dumps({"account": "Pro"}))
        resumed = [*self.HEAD, "--resume", "sid-c", "--permission-mode", "default"]

        def starts_on(expect, *flags, args=resumed):
            p = self.pipe(*flags, args=args)
            p.stdin.close()
            self.assertEqual(p.wait(10), 0, flags)
            self.assertEqual(self.calls()[-1]["config"], str(expect), flags)

        starts_on(self.pro, "--switch", "ask")
        starts_on(self.pro, "--switch", "never")
        starts_on(self.max, "--switch", "auto")  # auto keeps the account longhaul picks
        # Max nearly full: pick() would avoid it, the record names Pro, and
        # --account still wins over both (review, Sprint 5, F6c).
        (self.relay / "usage" / "Max.json").write_text(json.dumps(
            {"at": time.time(), "five_hour": {"used_percentage": 96, "resets_at": time.time() + 3600}}))
        starts_on(self.max, "--switch", "ask", "--account", "Max")
        (self.relay / "usage" / "Max.json").unlink()
        starts_on(self.max, "--switch", "ask", args=[*self.HEAD, "--session-id", "sid-c", "--permission-mode", "default"])
        log = (self.relay / "longhaul.log").read_text
        self.assertIn("sid-c last ran on Pro; with switching set to ask it starts there", log())
        # No record: picked, and the log says why (review, Sprint 5, F6a).
        starts_on(self.max, "--switch", "never", args=[*self.HEAD, "--resume", "sid-new", "--permission-mode", "default"])
        self.assertIn("pipe: no record of where sid-new last ran; picking an account", log())
        # Limited or not: in ask and never the turn ends at the limit anyway.
        (self.relay / "state.json").write_text(json.dumps({"limited_until": {"Pro": time.time() + 3600}}))
        starts_on(self.pro, "--switch", "never")
        # Never an account longhaul does not manage, whatever the record says,
        # and the log says why the session did not start there (review,
        # Sprint 5, B2: it moved with no line).
        self.accounts([{"name": "Max", "config_dir": str(self.max), "email": "first@example.com"},
                       {"name": "Pro", "config_dir": str(self.pro)}])
        (self.relay / "state.json").unlink()
        starts_on(self.max, "--switch", "never")
        self.assertIn("pipe: sid-c last ran on Pro, which is not managed; picking an account", log())
        # Nor one signed in as someone else.
        self.accounts([{"name": "Max", "config_dir": str(self.max), "email": "first@example.com"},
                       {"name": "Pro", "config_dir": str(self.pro), "email": "second@example.com"}])
        self.emails[str(self.pro)] = "stranger@example.com"
        starts_on(self.max, "--switch", "never")
        self.assertIn("pipe: sid-c last ran on Pro, which is not signed in as expected; picking an account", log())

    # Sprint 7, A2: a driven session runs only on an account allowed in the
    # driver's folder, which is the folder its Claude runs in.

    def runs_on(self, *flags, cwd, args=None):
        """The config folder a pipe session in `cwd` started on."""
        p = self.pipe(*flags, args=args, cwd=cwd)
        p.stdin.close()
        self.assertEqual(p.wait(10), 0, self.log_text())
        return self.calls()[-1]["config"]

    def test_pipe_picks_only_an_account_allowed_in_the_drivers_folder(self):
        work, personal = self.repositories()
        self.restrict("Max", work)  # Max is first in order: choose_for_panel() alone picks it
        self.assertEqual(self.runs_on(cwd=work), str(self.max))
        self.assertEqual(self.runs_on(cwd=personal), str(self.pro), "Max may not be used in personal")
        (work / "src").mkdir()
        self.assertEqual(self.runs_on(cwd=work / "src"), str(self.max), "a folder inside the repository is the repository")

    def test_pipe_exits_1_with_no_allowed_account(self):
        work, personal = self.repositories()
        self.restrict("Max", work)
        self.restrict("Pro", work)
        p = self.pipe(cwd=personal)
        self.assertEqual(p.wait(10), 1)
        self.assertEqual(self.calls_or_none(), [])
        self.assertIn(f"pipe: No account may be used in {self.shown(personal)}: Max only in {work}, Pro only in {work}. "
                      f'Edit "repositories" in {self.relay / "accounts.json"}, or run from one of those.',
                      self.log_text())

    def test_pipe_with_the_allowed_account_signed_in_wrong_never_launches_the_denied_one(self):
        # Final review, Sprint 7 (G5): Max, signed in as expected, may not be
        # used here, and Pro, the one allowed, is signed in as someone else.
        work, personal = self.repositories()
        self.restrict("Max", work)
        self.emails[str(self.pro)] = "stranger@example.com"
        p = self.pipe(cwd=personal)
        self.assertEqual(p.wait(10), 1)
        self.assertEqual(self.calls_or_none(), [], "Max is never launched")
        self.assertIn("pipe: no account allowed here is signed in as expected, so longhaul did not start Claude",
                      self.log_text())

    def test_pipe_refuses_account_for_a_folder_it_may_not_use(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        p = self.pipe("--account", "Pro", cwd=personal)
        self.assertEqual(p.wait(10), 2)
        self.assertEqual(self.calls_or_none(), [])
        self.assertIn(f"pipe: Pro may not be used in {self.shown(personal)} (its repositories: {work}); "
                      "longhaul did not start Claude", self.log_text())

    def test_pipe_swaps_only_to_an_allowed_account(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        self.plan(["limit"])
        p = self.pipe("--swap", cwd=personal)
        self.wait_for(self.result_for(self.user(p, "build it")))
        self.assertEqual(self.wait_for(lambda m: m.get("type") == "longhaul")["subtype"], "all_limited")
        self.assertEqual(len(self.calls()), 1, "no claude on Pro")
        self.assertIn("Max hit its session limit; no other account allowed here has room", self.log_text())

    def test_pipe_leaves_a_recorded_account_not_allowed_here(self):
        work, personal = self.repositories()
        self.restrict("Pro", work)
        (self.relay / "usage" / "sessions").mkdir(parents=True)
        (self.relay / "usage" / "sessions" / "sid-c.json").write_text(json.dumps({"account": "Pro"}))
        resumed = [*self.HEAD, "--resume", "sid-c", "--permission-mode", "default"]
        self.assertEqual(self.runs_on("--switch", "ask", args=resumed, cwd=personal), str(self.max))
        self.assertIn(f"pipe: sid-c last ran on Pro, which may not be used in {self.shown(personal)}; "
                      "picking an account", self.log_text())
        self.assertEqual(self.runs_on("--switch", "ask", args=resumed, cwd=work), str(self.pro), "allowed in work")


class PanelPieces(unittest.TestCase):
    def test_a_chat_is_told_apart_from_a_short_job(self):
        self.assertTrue(longhaul.is_chat(["--input-format", "stream-json"]))
        self.assertTrue(longhaul.is_chat(["--input-format=stream-json"]))
        self.assertFalse(longhaul.is_chat(["auth", "status", "--json"]))

    def test_every_way_of_choosing_a_conversation_is_taken_out(self):
        args = ["--verbose", "--resume=abc", "--session-id", "x", "--continue", "--fork-session", "-r", "y", "--debug"]
        self.assertEqual(longhaul.without_resume(args), ["--verbose", "--debug"])
        self.assertEqual(longhaul.resume_id(["--a", "--resume=abc"]), "abc")
        self.assertEqual(longhaul.resume_id(["--resume", "abc"]), "abc")
        self.assertIsNone(longhaul.resume_id(["--verbose"]))


class PipePieces(unittest.TestCase):
    """What `longhaul pipe --no-git-sign-in` hands claude: no GitHub or git
    sign-in, whatever the shell it came from had. Values are dummies."""

    ACCOUNT = {"name": "Max", "config_dir": None}
    SIGN_INS = {"GH_TOKEN": "x", "GITHUB_TOKEN": "x", "GH_ENTERPRISE_TOKEN": "x", "GITHUB_PAT_ANY": "x",
                "SSH_AUTH_SOCK": "/tmp/agent.sock", "GIT_ASKPASS": "/bin/echo", "SSH_ASKPASS": "/bin/echo",
                "GIT_SSH": "/usr/bin/ssh", "GIT_SSH_COMMAND": "ssh -i k"}
    INJECTED = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraheader",
                "GIT_CONFIG_VALUE_0": "Authorization: x", "GIT_CONFIG_PARAMETERS": "'http.extraheader'='x'"}

    def test_pipe_strips_every_gh_and_github_variable_and_the_ssh_agent(self):
        with mock.patch.dict(os.environ, self.SIGN_INS):
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual(sorted(set(self.SIGN_INS) & set(env)), [])

    def test_pipe_strips_the_variables_that_inject_git_configuration(self):
        with mock.patch.dict(os.environ, self.INJECTED):
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual(sorted(set(self.INJECTED) & set(env)), [])

    def test_pipe_keeps_the_drivers_git_configuration_variables(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/tmp/s/gitconfig", "GIT_CONFIG_NOSYSTEM": "1"}):
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual((env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_NOSYSTEM"]), ("/tmp/s/gitconfig", "1"))

    # -- found in review

    def test_pipe_gives_git_no_system_or_user_configuration_and_no_prompt_when_the_driver_named_none(self):
        with mock.patch.dict(os.environ):
            for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT"):
                os.environ.pop(k, None)
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual((env.get("GIT_CONFIG_NOSYSTEM"), env.get("GIT_CONFIG_GLOBAL"), env.get("GIT_TERMINAL_PROMPT")),
                         ("1", os.devnull, "0"))

    def test_pipe_drops_the_system_git_configuration_and_the_prompt_whatever_it_is_given(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "0", "GIT_TERMINAL_PROMPT": "1"}):
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual((env["GIT_CONFIG_NOSYSTEM"], env["GIT_TERMINAL_PROMPT"]), ("1", "0"))

    def test_pipe_strips_every_variable_whose_name_holds_github(self):
        named = {"HOMEBREW_GITHUB_API_TOKEN": "x", "MY_GITHUB_PAT": "x", "my_github_pat": "x"}
        with mock.patch.dict(os.environ, named):
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual(sorted(set(named) & set(env)), [])

    def test_pipe_strips_the_variables_that_point_git_at_other_files_or_repositories(self):
        elsewhere = {"GIT_CONFIG": "/tmp/c", "GIT_CONFIG_SYSTEM": "/tmp/s", "GIT_DIR": "/tmp/r/.git",
                     "GIT_WORK_TREE": "/tmp/r", "GIT_COMMON_DIR": "/tmp/r/.git"}
        with mock.patch.dict(os.environ, elsewhere):
            env = longhaul.no_git_env(self.ACCOUNT)
        self.assertEqual(sorted(set(elsewhere) & set(env)), [])


class Accounts(unittest.TestCase):
    """longhaul add and longhaul remove, against the stand-in claude (fake_stream.py)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.relay, self.default = self.tmp / "relay", self.tmp / "default"
        for d in (self.relay, self.default / "projects", self.default / "sessions"):
            d.mkdir(parents=True)
        (self.default / "settings.json").write_text("{}")
        self.max = self.tmp / "max"
        (self.relay / "accounts.json").write_text(json.dumps(
            [{"name": "Max", "config_dir": str(self.max), "email": "first@example.com"}]))
        self.emails = {str(self.max): "first@example.com"}

    def longhaul(self, *argv, **extra_env):
        # HOME is the scratch folder too: `add` makes `~/.claude-<name>` and
        # `setup` links `~/.local/bin/longhaul`, and a contributor's real home
        # must never get either from a test run.
        env = dict(os.environ, HOME=str(self.tmp), LONGHAUL_HOME=str(self.relay), LONGHAUL_DEFAULT_CONFIG_DIR=str(self.default),
                   LONGHAUL_DEFAULT_GLOBAL_JSON=str(self.tmp / "global.json"), LONGHAUL_CLAUDE_BIN=str(HERE / "fake_stream.py"),
                   LONGHAUL_NO_NOTIFY="1", FAKE_EMAILS=json.dumps(self.emails), FAKE_REQUIRE_LOGIN="1")
        env.update(extra_env)
        with (self.relay / "logins.jsonl").open("a") as f:
            f.write(json.dumps({"config": str(self.max)}) + "\n")
        return subprocess.run([sys.executable, str(HERE / "longhaul.py"), *argv], env=env, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)

    def accounts(self):
        return json.loads((self.relay / "accounts.json").read_text())

    def test_longhaul_add_puts_a_new_account_on_the_list_and_signs_it_in(self):
        team = self.tmp / "team"
        self.emails[str(team)] = "team@example.com"
        out = self.longhaul("add", "Team", "team@example.com", "--dir", str(team))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.accounts()[-1], {"name": "Team", "config_dir": str(team), "email": "team@example.com"})
        login = [json.loads(l) for l in (self.relay / "logins.jsonl").read_text().splitlines()
                 if json.loads(l).get("args")][0]
        self.assertEqual(login["config"], str(team))
        self.assertIn("team@example.com", login["args"], "the browser opens with the email filled in")
        self.assertTrue((team / "projects").is_symlink(), "linked to the shared history")

    def test_longhaul_add_refuses_a_name_email_or_folder_already_in_use(self):
        for argv in (("add", "max", "new@example.com", "--dir", str(self.tmp / "x")),
                     ("add", "New", "FIRST@example.com", "--dir", str(self.tmp / "y")),
                     ("add", "New", "new@example.com", "--dir", str(self.max))):
            out = self.longhaul(*argv)
            self.assertEqual(out.returncode, 2, argv)
        self.assertEqual(len(self.accounts()), 1)

    def test_longhaul_add_refuses_the_folder_the_panel_signs_in_to(self):
        out = self.longhaul("add", "New", "new@example.com", "--dir", str(self.default))
        self.assertEqual(out.returncode, 2)
        self.assertIn("its own folder", out.stdout)

    def test_an_added_account_signed_in_as_someone_else_stays_unused(self):
        team = self.tmp / "team"
        self.emails[str(team)] = "stranger@example.com"
        out = self.longhaul("add", "Team", "team@example.com", "--dir", str(team))
        self.assertEqual(out.returncode, 1)
        self.assertIn("not signed in, so longhaul will not use it yet", out.stdout)
        self.assertEqual(self.accounts()[-1]["email"], "team@example.com")

    def test_longhaul_remove_takes_an_account_off_the_list_and_keeps_its_folder(self):
        team = self.tmp / "team"
        team.mkdir()
        (self.relay / "accounts.json").write_text(json.dumps(self.accounts() + [
            {"name": "Team", "config_dir": str(team), "email": "team@example.com"}]))
        out = self.longhaul("remove", "team")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual([a["name"] for a in self.accounts()], ["Max"])
        self.assertTrue(team.exists())

    def test_longhaul_remove_keeps_the_last_account(self):
        self.assertEqual(self.longhaul("remove", "Max").returncode, 2)
        self.assertEqual(len(self.accounts()), 1)

    def test_an_accounts_file_that_cannot_be_read_stops_setup_add_and_remove_and_is_never_written_over(self):
        # Review, Sprint 5 (C1): setup took a file it could not read for "no
        # accounts", said Ready and exited 0, and add then wrote a
        # one-account list over it. Not there is no accounts; there and not
        # a list of accounts with names is an error, exit 2, nothing written.
        path = self.relay / "accounts.json"
        team = self.tmp / "team"
        for text in ("{not json", json.dumps({"name": "Max"}), json.dumps([{"config_dir": str(team)}]), b"\xff\xfe"):
            path.write_bytes(text if isinstance(text, bytes) else text.encode())
            for argv in (("setup",), ("setup", "Max"), ("add", "Team", "team@example.com", "--dir", str(team)),
                         ("remove", "Max")):
                out = self.longhaul(*argv, HOME=str(self.tmp))
                self.assertEqual(out.returncode, 2, (text, argv, out.stdout, out.stderr))
                self.assertIn("accounts.json is not readable: ", out.stdout, (text, argv))
                self.assertIn("; fix or remove it", out.stdout, (text, argv))
                self.assertEqual(path.read_bytes(), text if isinstance(text, bytes) else text.encode(), "nothing written")
                self.assertNotIn("Ready", out.stdout)
        self.assertFalse((self.tmp / ".local" / "bin" / "longhaul").exists(), "setup stopped before linking")
        # An empty list is no accounts, as no file is: setup links the command
        # for the status line, and add makes the first account.
        path.write_text("[]")
        out = self.longhaul("setup", HOME=str(self.tmp))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("longhaul add", out.stdout)
        self.assertEqual(path.read_text(), "[]")
        self.emails[str(team)] = "team@example.com"
        out = self.longhaul("add", "Team", "team@example.com", "--dir", str(team), HOME=str(self.tmp))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual([a["name"] for a in self.accounts()], ["Team"])

    # Sprint 7, A2: an account may be restricted to repositories.

    def test_longhaul_add_repo_stores_the_paths_and_refuses_a_relative_one(self):
        team, work = self.tmp / "team", self.tmp / "work"
        work.mkdir()
        self.emails[str(team)] = "team@example.com"
        before = (self.relay / "accounts.json").read_bytes()
        out = self.longhaul("add", "Team", "team@example.com", "--dir", str(team), "--repo", str(work), "--repo", "work")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("--repo work is not an absolute path; give the repository's full path, like ~/work/repo",
                      out.stdout)
        self.assertEqual((self.relay / "accounts.json").read_bytes(), before, "nothing written")
        # HOME is the scratch folder, so work is ~/work; ~/later is not there.
        out = self.longhaul("add", "Team", "team@example.com", "--dir", str(team), "--repo", str(work), "--repo", "~/later")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.accounts()[-1], {"name": "Team", "config_dir": str(team), "email": "team@example.com",
                                               "repositories": [str(work), "~/later"]}, "kept as given, as --dir is")
        self.assertIn("--repo ~/later does not exist yet; kept for when it does", out.stdout)
        self.assertIn(f"added Team (team@example.com), folder {team}, only in ~/work, ~/later; signing it in now",
                      out.stdout)

    def test_longhaul_status_shows_each_accounts_repositories(self):
        (self.relay / "accounts.json").write_text(json.dumps([
            {"name": "Max", "config_dir": str(self.max), "email": "first@example.com",
             "repositories": [str(self.tmp / "work"), "~/other"]},
            {"name": "Team", "config_dir": str(self.tmp / "team"), "email": "team@example.com", "repositories": "~/work"},
            {"name": "Pro", "config_dir": str(self.tmp / "pro"), "email": "pro@example.com"}]))
        out = self.longhaul("status")
        under, name = {}, None
        for line in out.stdout.splitlines():  # each account's line, then its own indented ones
            if not line.startswith(" "):
                name = line.split(" ", 1)[0]
            under.setdefault(name, []).append(line)
        self.assertIn("       only in: ~/work, ~/other", under["Max"], out.stdout)
        self.assertIn(f"       {longhaul.REPOSITORIES_JUNK}; longhaul will not use this account anywhere",
                      under["Team"], out.stdout)
        self.assertFalse([l for l in under["Pro"] if "only in" in l or "repositories" in l], "Pro is unrestricted")

    def test_remove_and_setup_keep_another_accounts_repositories(self):
        team, new = self.tmp / "team", self.tmp / "new"
        team.mkdir()
        self.emails[str(new)] = "new@example.com"
        restricted = {"name": "Team", "config_dir": str(team), "email": "team@example.com",
                      "repositories": ["~/work/repo"], "note": "a key longhaul does not know"}
        (self.relay / "accounts.json").write_text(json.dumps(self.accounts() + [restricted]))
        out = self.longhaul("setup", "Max")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.accounts()[1], restricted)
        out = self.longhaul("add", "New", "new@example.com", "--dir", str(new))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.accounts()[1:], [restricted, {"name": "New", "config_dir": str(new), "email": "new@example.com"}],
                         "add writes back the list it read, and the field only when given")
        self.assertEqual(self.longhaul("remove", "Max").returncode, 0)
        self.assertEqual(self.accounts()[0], restricted, "remove writes back the rest as they were")


class History(unittest.TestCase):
    """R2b: the public history never held the owner's own tooling. The
    never-tracked block of .gitignore says what must not be in the
    repository; this reads every path a commit on HEAD's ancestry ever
    named and matches each against that block. CI checks out the whole
    history (fetch-depth: 0), so there it reads every commit."""

    def git(self, *args) -> str:
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=60).stdout

    def never_tracked(self) -> list:
        """The patterns under `# Never tracked` in .gitignore, comments and
        blank lines left out."""
        lines = (HERE / ".gitignore").read_text().splitlines()
        start = [n for n, line in enumerate(lines) if line.startswith("# Never tracked")]
        self.assertTrue(start, ".gitignore has no never-tracked block")
        return [line.strip() for line in lines[start[0] + 1:] if line.strip() and not line.startswith("#")]

    @staticmethod
    def matches(path: str, pattern: str) -> bool:
        """gitignore's forms as the block uses them: `dir/` is a folder anywhere
        on the path, a bare name or glob is the file's name at any depth, and
        a pattern with a slash is the whole path (fnmatch's `*` crosses a
        slash where git's does not, so that form matches at least what git
        would; the block has none today)."""
        from fnmatch import fnmatchcase
        if pattern.endswith("/"):
            return pattern[:-1] in path.split("/")[:-1]
        if "/" not in pattern:
            return fnmatchcase(path.rsplit("/", 1)[-1], pattern)
        return fnmatchcase(path, pattern)

    def added_in(self, path: str) -> str:
        """The short hash of the first commit that added the path."""
        return (self.git("log", "HEAD", "--diff-filter=A", "--no-renames", "--format=%h", "--", path).split() or ["?"])[-1]

    def test_the_matcher_reads_the_three_forms_the_block_uses(self):
        for path, pattern, hit in (("docs/adr/0001-plugin-layout.md", "docs/", True), ("a/docs/x", "docs/", True),
                                   ("docs", "docs/", False), (".claude-plugin/plugin.json", ".claude/", False),
                                   ("ATTRIBUTION.md", "ATTRIBUTION.md", True), ("a/ATTRIBUTION.md", "ATTRIBUTION.md", True),
                                   ("guard/a.log", "*.log", True), ("a.log.txt", "*.log", False),
                                   ("hooks/hooks.json", "hooks/*.json", True), ("bin/hooks.json", "hooks/*.json", False)):
            self.assertEqual(self.matches(path, pattern), hit, (path, pattern))

    def test_no_never_tracked_path_was_ever_committed(self):
        requires_git(self)
        if self.git("rev-parse", "--is-shallow-repository").strip() == "true":
            self.skipTest("a shallow clone: the history is not here")
        patterns = self.never_tracked()
        # An emptied block must not pass this, and a form the matcher does
        # not read (anchored, negated) must not be read as something else.
        self.assertLessEqual({".claude/", "ATTRIBUTION.md", "docs/"}, set(patterns), "the owner's own tooling")
        for pattern in patterns:
            self.assertFalse(pattern.startswith(("/", "!")), (pattern, "a form this test does not read"))
        # Every path a commit on HEAD's ancestry ever named, whether added,
        # changed, deleted or renamed (a rename names the new path; the old
        # one was named when it was added). One call, well under a second.
        named = sorted(set(self.git("log", "HEAD", "--name-only", "--format=").splitlines()) - {""})
        self.assertTrue(named, "git log named no path")
        leaked = [path for path in named if any(self.matches(path, pattern) for pattern in patterns)]
        # Only on a hit: the commit that let each path in, so it can be found.
        hits = [f"{path} (added in {self.added_in(path)})" for path in leaked]
        self.maxDiff = None  # every hit by name, not the first 640 characters
        self.assertEqual(hits, [], f"{len(hits)} paths the never-tracked block of .gitignore names are in the history")


if __name__ == "__main__":
    unittest.main()
