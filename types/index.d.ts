// Longhaul's contract (plugin.json "types"): the record the accounts pane
// draws (hooks/accounts.tsx), the question a panel chat's relay asks
// (hooks/question.ts), and the values they keep in $.state. A redraw needs
// $.state, and its values outlive a reload of the hooks module, which module
// variables do not. Each is kept under a shape tag, so a change here changes
// the tag and new code declines what old code stored.

// One usage window of an account: how much is used, in percent, and when it resets (epoch seconds).
export type LonghaulWindow = { used: number; resets_at: number }

// One account as `longhaul.py status --json` gives it. The email comes masked.
export type LonghaulAccount = {
  name: string
  email: string | null
  signed_in: boolean | null // null: it could not be checked
  as_expected: boolean | null // signed in with the email on Longhaul's list
  check_failed: string | null // why the check failed
  // When a chat on it last stopped on a failed sign-in (epoch seconds; S3).
  // Optional: an older longhaul.py does not give it.
  sign_in_failed_at?: number | null
  plan: string | null
  limited_until: number | null // epoch seconds
  five_hour: LonghaulWindow | null
  week: LonghaulWindow | null
  only_in: string[] | null // [] anywhere, null nowhere
  // For an account with no list ([] only_in), the accounts whose folders
  // include /, which keep it out of every folder; [] otherwise. Optional:
  // an older longhaul.py does not give it.
  kept_out_by?: string[]
  // A fallback account, used only when no other account allowed there has
  // room (the owner, 2026-11-06). Optional: an older longhaul.py does not
  // give it, and the pane then offers no button for it.
  fallback?: boolean
  // Whether `longhaul use NAME` would take this chat here (0.3.9): "ok",
  // "this_chat", "not_allowed", "limited" or "not_signed_in"; null with no
  // accounts list or folder. Optional: an older longhaul.py does not give
  // it, and the pane then offers no button for it.
  use_here?: string | null
  // The folders the account is preferred in (0.3.9); null for a value that
  // is not a list of paths.
  preferred?: string[] | null
  // When Longhaul last read the account's usage (epoch seconds; the UX
  // review's Q6), which the pane shows as "seen 11:52"; null when it has no
  // reading. The pane's clean() gives null for one absent (an older
  // longhaul.py) or not a number; optional for a read stored before it.
  reading_at?: number | null
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
  // The VS Code panel's own mode, which "switch" in ~/.longhaul/vscode.json
  // can set apart from "switch". Optional: an older longhaul.py does not give it.
  panel_switch?: 'ask' | 'auto' | 'never' | null
  vscode: boolean | null
  // Whether `longhaul panel pause` is in force: Claude runs alone in the
  // VS Code panel's chats. Optional: an older longhaul.py does not give it,
  // and the Troubleshoot page then offers both Pause and Resume.
  panel_paused?: boolean | null
  problem: string | null
  // The chat it runs in and why it is on its account (M1): the log's latest
  // reason; null outside a chat. Optional: an older longhaul.py does not give it.
  chat?: LonghaulChat | null
  // Its status line (M3), as `longhaul statusline` draws it with no colours and
  // the panel's entry draws it (hooks/display.ts's line()). Optional: an older
  // longhaul.py does not give it.
  line?: string
}

// This chat as `status --json` gives it: its short id, its account (null:
// not one Longhaul knows), and the latest event that put it there, what,
// why and when (epoch seconds), each null when the log gives none.
export type LonghaulChat = {
  id: string; account: string | null; what: string | null; why: string | null; at: number | null
}

// One thing Longhaul did by itself, as `longhaul.py activity --json` gives
// it: when (epoch seconds), the chat (its first 8 characters, null for an
// event of no one chat), what happened and why.
export type LonghaulActivityEvent = { time: number; chat: string | null; what: string; why: string }

// The Activity page's last read: the events over `days` days (null: the
// read failed, and why; update: this Longhaul is older than the pane and
// has no activity), the command's last lines for its Details, this chat's
// short id (null: not known), and where the full log is.
export type LonghaulAccountsActivity = {
  events: LonghaulActivityEvent[] | null; days: number; problem: string | null; update: boolean; lines: string[]
  chat: string | null; log: string
}

// The pane's last read: the answer, or why there is none (update: this
// Longhaul is older than the pane and has no `status --json`), with the
// command's last lines, masked, for its Details.
export type LonghaulAccountsRead = {
  status: LonghaulStatus | null; problem: string | null; update: boolean; lines: string[]
}

// Which page the pane shows: the list, or one item's page.
export type LonghaulAccountsPage = 'list' | 'add' | 'settings' | 'activity' | 'troubleshoot' | `account:${string}`

// One thing `longhaul.py repair --json` finds stuck or broken: its id (what
// `repair --fix ID` takes), what is wrong and what its fix does, in plain
// words, and whether there is a fix here.
export type LonghaulRepairFinding = { id: string; what: string; fix: string; fixable: boolean }

// The Troubleshoot page's last look: the findings, or why there are none
// (update: this Longhaul is older than the pane and has no repair), with
// the command's last lines, masked, for its Details.
export type LonghaulAccountsTrouble = {
  findings: LonghaulRepairFinding[] | null; problem: string | null; update: boolean; lines: string[]
}

// The action running now: which module started it (a reload makes a new
// one), what it is, what the user should do meanwhile, the page it ran from.
export type LonghaulAccountsRunning = { by: string; label: string; note: string; page: LonghaulAccountsPage }

// The last action's end: ok (null: not known), its output's last lines,
// emails masked, a line on what to do next, and the page it shows on.
export type LonghaulAccountsResult = {
  ok: boolean | null; label: string; lines: string[]; after: string | null; page: LonghaulAccountsPage
}

// What a dry run said a change would do (the UX review's M4), for the
// question that asks before it runs: the account; which change (delete:
// its sign-in folder; add, remove, anywhere: its folders); the change's
// own arguments, label and what the pane says while it runs; the folder
// (add, remove); whether the typed folder is cleared once it worked; the
// lines to show, emails masked; why it would refuse (delete); how many
// chats it would stop; and, when the answer could not be read, why.
export type LonghaulAccountsPreview = {
  account: string; kind: 'delete' | 'add' | 'remove' | 'anywhere'; argv: string[]; label: string; doing: string
  path: string | null; clears: boolean; lines: string[]; refused: string | null; stops: number; unread: string | null
}

// What the Add fields hold, and what was wrong with them.
export type LonghaulAccountsForm = { name: string; email: string; only: string; problem: string | null }

// The folder typed on an account's page to add to its list: which
// account's page, the text, and what was wrong with it.
export type LonghaulAccountsFolder = { account: string; path: string; problem: string | null }

// The question a panel chat's relay asks at a usage limit, in ask (the
// keep-working plan's K1): LONGHAUL_HOME/questions/<chat>.json as
// longhaul.py's Relay.ask() writes it. Times are epoch seconds.
export type LonghaulQuestion = {
  id: string // q-<hex>: what `longhaul answer` names
  chat: string // the session id
  relay_pid: number // the relay that acts on the answer
  asked_at: number
  kind: 'move' | 'first_reset'
  from: string // the account at its limit
  limit: string // its kind word: session, weekly
  resets_at: number
  to: string // move: the account offered; first_reset: the one that resets first
  to_resets_at: number | null
  // When the answer that waits (yes, or wait) carries on: the reset plus a
  // margin, as the wait's until will be. Optional: an older relay does not write it.
  carries_on_at?: number | null
  choices: string[] // move: move, wait; first_reset: yes, no
}

// A carry-on the relay scheduled for a reset: LONGHAUL_HOME/waits/<chat>.json.
export type LonghaulWait = { chat: string; until: number; account: string; why: string }

// The pane's answer to a question: which question, the choice, and how it
// ended (ok null: still running, in the module `by` names).
export type LonghaulAccountsAnswer = { by: string; qid: string; choice: string; ok: boolean | null; lines: string[] }

declare module 'claude-code' {
  interface PluginState {
    longhaul: {
      accountsRead: Shaped<LonghaulAccountsRead | null>
      accountsRunning: Shaped<LonghaulAccountsRunning | null>
      accountsResult: Shaped<LonghaulAccountsResult | null>
      accountsConfirm: Shaped<string | null> // the account whose Remove waits for a yes, or fix:ID for a Fix
      accountsForm: Shaped<LonghaulAccountsForm>
      accountsFolder: Shaped<LonghaulAccountsFolder>
      accountsPage: Shaped<LonghaulAccountsPage>
      accountsMark: Shaped<string | null> // the key of the element the focus ring is on
      accountsQuestion: Shaped<LonghaulQuestion | null> // this chat's open question
      accountsWait: Shaped<LonghaulWait | null> // this chat's carry-on at a reset
      accountsAsked: Shaped<string | null> // the last question id the pane was opened for
      accountsAnswer: Shaped<LonghaulAccountsAnswer | null>
      accountsDetails: Shaped<string | null> // whose Details are open: "result", "read", "trouble", "activity", or none
      accountsTrouble: Shaped<LonghaulAccountsTrouble | null> // null: not looked yet, or looking again
      accountsActivity: Shaped<LonghaulAccountsActivity | null> // null: not read yet, or reading again
      accountsPreview: Shaped<LonghaulAccountsPreview | null> // what the open question's dry run said
    }
  }
}
