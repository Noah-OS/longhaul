"""The VS Code extension's process wrapper, in front of longhaul.wrap().

The extension runs the claudeCode.claudeProcessWrapper program with the
bundled claude binary's path first, then claude's arguments. This file is
kept small and separate from longhaul.py on purpose: if longhaul.py cannot
be imported (a syntax error in an edit, a missing module), the panel must
still get its Claude, so the binary is run unchanged and the reason goes to
the log in LONGHAUL_HOME (~/.longhaul by default), as longhaul's own log
would.
"""
import datetime
import os
import sys


def run_unchanged(reason: str) -> None:
    home = os.environ.get("LONGHAUL_HOME") or os.path.expanduser("~/.longhaul")
    try:
        os.makedirs(home, exist_ok=True)
        # Created private, as longhaul's own log is: it names accounts and paths.
        fd = os.open(os.path.join(home, "longhaul.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} panel wrapper could not start longhaul "
                    f"({reason}); running Claude unchanged\n")
    except OSError:
        pass
    os.execv(sys.argv[1], sys.argv[1:])


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import longhaul
    except BaseException as e:  # SyntaxError too
        run_unchanged(repr(e))
    return longhaul.wrap(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
