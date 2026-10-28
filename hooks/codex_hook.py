"""Longhaul's hooks under OpenAI Codex.

Codex's hooks file runs `python3 -I "$PLUGIN_ROOT/hooks/codex_hook.py"
COMMAND`: a Codex hook is one command line for its shell, with no argument
list (docs/discovery/2026-10-28-codex-hooks.md). This runs `longhaul
COMMAND --agent codex` on the hook's stdin, for the hook commands alone
(longhaul.CODEX_HOOK_COMMANDS), and exits 0 whatever happens: Codex blocks
a tool when its hook exits 2, as argparse does on a bad argument. What
longhaul prints on stdout is the answer; nothing goes to stderr.
"""
import os
import sys

try:
    sys.stderr = open(os.devnull, "w")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import longhaul

    if len(sys.argv) > 1 and sys.argv[1] in longhaul.CODEX_HOOK_COMMANDS:
        longhaul.main([sys.argv[1], *sys.argv[2:], "--agent", "codex"])
except BaseException:
    pass
sys.exit(0)
