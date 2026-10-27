---
name: advise
description: "Consult a stronger model for an independent review at one of four decision points, then continue the task yourself. Use it: early, after orientation and before the first write, on a multi-file, architectural or unclear task; when stuck (the same failure twice, an approach not converging); before changing approach; and before declaring a long task done, once its files are written and its tests run. Not for trivial edits, docs edits or simple questions. Part of Longhaul's Advisor experiment: it runs only when the owner has enabled the experiment; otherwise it stops at once."
argument-hint: "[early | stuck | change | done]"
allowed-tools: Bash(longhaul advisor status)
---

First, whether the experiment is on:

!`longhaul advisor status`

If the last line above does not begin with `enabled`, stop here: tell the user
in one line that the Advisor is off and why (the line says), and carry on with
the task without a consultation. Do not try to call the advisor anyway.

If it is enabled, write a brief of at most 8,000 characters in exactly these
lines, the first line first:

    Trigger: early | stuck | change | done
    Task: what the user asked, in your words
    Constraints: what must hold (the user's rules, files that must not change, tests that must pass)
    Findings: what you have established, as file paths and line ranges, not pasted files
    Approach: what you intend to do next, or what you tried and how it failed
    Question: the one specific thing you want reviewed

Add a line `Reconcile: yes` only when this call follows an earlier
consultation in the same task whose advice conflicts with what you have
since seen; say what conflicts. Never put a credential in the brief, and
never an `Admission:` line: Longhaul's hook adds that one itself.

Then call the Agent tool once with `subagent_type` `longhaul-advisor:advisor`, a
`description` naming the trigger (for example "Longhaul advisor consultation
(stuck)"), and the brief as the `prompt`, and no other field: no
`run_in_background`, `isolation` or `name`. Never send a brief to any other
agent type: it is denied. Never send a message to an advisor after its
reply: that is denied too, and more advice is a new consultation. Longhaul's hook
checks the call, the brief, the caps and the model, and may deny the call
with a reason: if the reason is one you can fix (a field the call may not
have, the brief too long, a line missing, a credential in it), fix it and
try once more; otherwise carry on without advice and say why in one line.
If the advisor fails or its reply has no `Verdict:` line, carry on without
advice and say so in one line.

How to use the reply. Give the advice serious weight, and act on it unless
primary evidence (a test result, the code itself, an error you saw) points
the other way; if it does, and the caps allow, make one call with
`Reconcile: yes` that states the conflict, rather than switching silently.
You stay responsible for the task: the advisor changes nothing. If the
verdict is `stop`, pause: tell the user the advisor's reason in a sentence
and wait for them before continuing. After every consultation, tell the user
in one line the trigger and the verdict, for example "Consulted the advisor
(stuck): adjust, the test fixture is stale."
