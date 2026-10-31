// Longhaul's contract (plugin.json "types"): the record the accounts pane
// draws (hooks/accounts.tsx) and the values it keeps in $.state. A redraw
// needs $.state, and its values outlive a reload of the hooks module, which
// module variables do not. Each is kept under a shape tag in the pane, so a
// change here changes the tag and new code declines what old code stored.

// One usage window of an account: how much is used, in percent, and when it resets (epoch seconds).
export type LonghaulWindow = { used: number; resets_at: number }

// One account as `longhaul.py status --json` gives it. The email comes masked.
export type LonghaulAccount = {
  name: string
  email: string | null
  signed_in: boolean | null // null: it could not be checked
  as_expected: boolean | null // signed in with the email on Longhaul's list
  check_failed: string | null // why the check failed
  plan: string | null
  limited_until: number | null // epoch seconds
  five_hour: LonghaulWindow | null
  week: LonghaulWindow | null
  only_in: string[] | null // [] anywhere, null nowhere
  folder: string
  this_chat: boolean
}

// The whole answer of `longhaul.py status --json`.
export type LonghaulStatus = {
  accounts: LonghaulAccount[]
  listed: boolean
  here: { folder: string; may_use: string[] } | null
  switch: 'ask' | 'auto' | 'never' | null
  switch_problem: string | null
  vscode: boolean | null
  problem: string | null
}

// The pane's last read: the answer, or why there is none (update: this
// Longhaul is older than the pane and has no `status --json`).
export type LonghaulAccountsRead = { status: LonghaulStatus | null; problem: string | null; update: boolean }

// The action running now: which module started it (a reload makes a new
// one), what it is, and what the user should do meanwhile.
export type LonghaulAccountsRunning = { by: string; label: string; note: string }

// The last action's end: ok (null: not known), its output's last lines,
// emails masked, and a line on what to do next.
export type LonghaulAccountsResult = { ok: boolean | null; label: string; lines: string[]; after: string | null }

// What the Add fields hold, and what was wrong with them.
export type LonghaulAccountsForm = { name: string; email: string; only: string; problem: string | null }

declare module 'claude-code' {
  interface PluginState {
    longhaul: {
      accountsRead: Shaped<LonghaulAccountsRead | null>
      accountsRunning: Shaped<LonghaulAccountsRunning | null>
      accountsResult: Shaped<LonghaulAccountsResult | null>
      accountsConfirm: Shaped<string | null> // the account whose Remove waits for a yes
      accountsForm: Shaped<LonghaulAccountsForm>
    }
  }
}
