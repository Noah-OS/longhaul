import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { LonghaulAccount, LonghaulAccountsAnswer, LonghaulAccountsFolder, LonghaulAccountsForm, LonghaulAccountsPage,
  LonghaulAccountsRead, LonghaulAccountsResult, LonghaulAccountsRunning, LonghaulQuestion, LonghaulStatus,
  LonghaulWait } from '../types'
import { status as showStatus } from './display.ts'
import { answerArgs, asks, carries, clock, look, waits } from './question.ts'

// Longhaul's accounts screen (S2): /accounts opens a pane that works as /mcp
// does (the owner, 2026-10-30: "a model showing up front of us and we can do
// some action there"). First a short list, one line per account, then
// "+ Add account" and "Settings"; the arrows move the focus, Enter opens an
// item's page, Escape goes back, and from the list closes the pane. An
// account's page has its details and its actions (Sign in again or Sign in,
// Remove), then its folders, each with Remove, a field to add one and
// Remove all folder limits… (the owner, 2026-11-05: "I can't edit the
// accounts"; it asks first since the UX review's Q12), then
// whether it is a fallback, with the button that turns it (2026-11-06); Add
// account's page the form; Settings' page switching, the VS Code panel and
// forgetting the limits. The pane draws `longhaul.py status --json` and runs
// the same commands /longhaul:manage runs in words, by argv, never through a
// shell: setup, add, remove, switch, vscode, clear; and folders, which
// /longhaul:manage runs too since 0.3.8 (the owner, 2026-11-06); and
// fallback, which it does not (bin/longhaul refuses it until the owner says
// otherwise). One at a time; the rows are read again when the pane opens and
// after each.
//
// Each line of the list is a Button: the pane is opened with focus and
// closeOnEscape, so it holds the keys as a dialog does, the arrows and Tab
// walk its Buttons and Enter presses one (the API's own "a list row is one
// Button"). Escape raises ui.close; on a page this module keeps the pane open
// and goes back to the list instead, then takes the keys back.
//
// At a usage limit in the VS Code panel, in ask, the chat's relay asks a
// question (hooks/question.ts, the keep-working plan's K1): it shows at the
// top of every page with its two answers, which run `longhaul.py answer`.
// Every 3 s this module looks for it, and opens the pane for a new one with
// a pop-up beside it, in case the pane cannot open by itself.
//
// What it draws comes from $.state (types/index.d.ts): a redraw follows each
// write, and the values outlive a reload of this module, which its variables
// do not. So an action the module before a reload was running is shown as
// not known, its process gone with that module, and the rows are read again.
// Emails are never shown whole (the owner's decision): the record's come
// masked, and every email in a command's output is masked here.

const PANE = 'longhaul-accounts'
const TITLE = 'Longhaul accounts'
const OPEN = { id: PANE, title: TITLE, focus: true, closeOnEscape: true } as const
const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$/ // what longhaul.py takes as a name, and never an option
const MODES = ['ask', 'auto', 'never'] as const
// The surfaces that draw Input and Select (the phone does not), by name: the
// element table answers every constructor whatever the surface draws.
const TYPING = ['terminal', 'desktop', 'vscode']
const STATUS_TIMEOUT = 60_000 // ms: it checks every account's sign-in
const SIGN_IN_TIMEOUT = 600_000 // ms: a browser sign-in, the most process.run allows
const POLL = 3000 // ms between looks for this chat's question
const SHOWN = 10 // lines of a command's output shown
const NOTE = "finish in the browser that opened, with that account's email. If the browser is signed in to claude.ai "
  + 'as someone else, copy the link into a private window.'
const RELOAD = 'Reload VS Code: Cmd+Shift+P, Developer: Reload Window.'
const UPDATE = 'To update Longhaul: claude plugin marketplace update longhaul, then claude plugin update '
  + 'longhaul@longhaul, then /reload-plugins.'

// Change a tag when its value's fields change: it declines what an earlier
// version stored, which outlives a reload.
const lastRead = atom({ plugin: 'longhaul', key: 'accountsRead' } as const, null, { shape: 'accounts-read-1' })
const running = atom({ plugin: 'longhaul', key: 'accountsRunning' } as const, null, { shape: 'accounts-running-2' })
const result = atom({ plugin: 'longhaul', key: 'accountsResult' } as const, null, { shape: 'accounts-result-2' })
const confirming = atom({ plugin: 'longhaul', key: 'accountsConfirm' } as const, null, { shape: 'accounts-confirm-1' })
const EMPTY: LonghaulAccountsForm = { name: '', email: '', only: '', problem: null }
const form = atom({ plugin: 'longhaul', key: 'accountsForm' } as const, EMPTY, { shape: 'accounts-form-1' })
const NO_FOLDER: LonghaulAccountsFolder = { account: '', path: '', problem: null }
const folder = atom({ plugin: 'longhaul', key: 'accountsFolder' } as const, NO_FOLDER, { shape: 'accounts-folder-1' })
const shown = atom({ plugin: 'longhaul', key: 'accountsPage' } as const, 'list', { shape: 'accounts-page-1' })
const marked = atom({ plugin: 'longhaul', key: 'accountsMark' } as const, null, { shape: 'accounts-mark-1' })
const askedFor = atom({ plugin: 'longhaul', key: 'accountsAsked' } as const, null, { shape: 'accounts-asked-1' })
const answered = atom({ plugin: 'longhaul', key: 'accountsAnswer' } as const, null, { shape: 'accounts-answer-1' })
// What the last look found (question.ts), for the drawing to follow.
const question = atom({ plugin: 'longhaul', key: 'accountsQuestion' } as const, null, { shape: 'accounts-question-1' })
const wait = atom({ plugin: 'longhaul', key: 'accountsWait' } as const, null, { shape: 'accounts-wait-1' })

// This module's own mark on what it runs: a reload loads a new module, with
// a new mark, so an action marked by another one was cut off by the reload.
const SELF = `m${Math.random().toString(36).slice(2)}`
let busy = false // an action of this module runs: one at a time
let answering = false // an answer runs: apart from the actions, as a question cannot wait for a sign-in
let kicked = false // this module looked at what it found once, from a drawing
let readOnce = false // this module has read the rows, or is reading them
let reads: Promise<void> = Promise.resolve() // the reads, one after another
let polling = false // the 3 s look for a question runs
let looking = false // one look at a time

// Every email in a text masked: its local part's first character, then
// "•••", the domain kept. A masked one stays as it is.
function mask(text: string): string {
  return text.replace(/([^\s@<>()[\]{},;:"'`])[^\s@<>()[\]{},;:"'`]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)/g, '$1•••@$2')
}

// A command's output as the pane shows it: stdout then stderr, colour codes
// dropped, its last SHOWN lines, each masked and cut to a readable length.
function tail(stdout: string, stderr: string): string[] {
  const lines = `${stdout}\n${stderr}`.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').split('\n')
    .map(line => line.replace(/\s+$/, '')).filter(Boolean)
  return lines.slice(-SHOWN).map(line => mask(line.length > 300 ? `${line.slice(0, 300)}…` : line))
}

function base($: any): string[] {
  return ['python3', '-I', `${$.plugin.root}/longhaul.py`]
}

// How an account signs in: a first sign-in (`setup NAME`) when it is not
// signed in or is signed in as someone else, else a new one (`--relogin`),
// as for a sign-in that failed in a chat (S3), which Claude Code still
// counts as signed in.
function signInOf(a: LonghaulAccount): { label: string; argv: string[] } {
  const name = String(a.name)
  if (typeof a.sign_in_failed_at === 'number') return { label: 'Sign in', argv: ['setup', name, '--relogin'] }
  if (a.signed_in === false || a.as_expected === false) return { label: 'Sign in', argv: ['setup', name] }
  return { label: 'Sign in again', argv: ['setup', name, '--relogin'] }
}

// The Add fields checked as longhaul.py would, so a mistake is said here and nothing runs.
function checkAdd(f: LonghaulAccountsForm): { argv: string[] } | { problem: string } {
  const name = f.name.trim(), email = f.email.trim()
  const folders = f.only.split(',').map(p => p.trim()).filter(Boolean)
  if (!NAME.test(name))
    return { problem: 'The name is 1 to 31 letters, digits, - or _, and starts with a letter or a digit.' }
  if (!/^[^\s@-][^\s@]*@[^\s@]+$/.test(email)) return { problem: 'The email needs an @, like name@example.com.' }
  const bad = folders.find(p => !(p.startsWith('/') || p.startsWith('~/')))
  if (bad !== undefined) return { problem: `Each folder in "only in" starts with / or ~/ (not ${bad}).` }
  return { argv: ['add', name, email, ...folders.flatMap(p => ['--repo', p])] }
}

// A folder to add, checked as longhaul.py would: absolute, or from ~/.
// Never an option, as it starts with / or ~.
function folderProblem(path: string): string | null {
  if (!path) return 'Type a folder first, like ~/work.'
  return path.startsWith('/') || path.startsWith('~/') ? null : `A folder starts with / or ~/ (not ${path}).`
}

// `status --json` read into what the pane draws: the record, or why there is none.
async function readStatus($: any): Promise<LonghaulAccountsRead> {
  let ran: { exitCode: number; stdout: string; stderr: string }
  try {
    ran = await $.process.run([...base($), 'status', '--json'], { timeoutMs: STATUS_TIMEOUT })
  } catch (err: any) {
    return { status: null, problem: mask(`Longhaul did not answer: ${err?.message || err}`), update: false }
  }
  const said = tail(ran.stdout, ran.stderr)
  // An older longhaul.py has no --json: argparse refuses it by name.
  if (/unrecognized arguments:.*--json|--json.*(unknown|not recognized)/.test(`${ran.stdout}\n${ran.stderr}`))
    return { status: null, problem: 'This Longhaul is older than this pane: it has no status --json.', update: true }
  if (ran.exitCode !== 0)
    return { status: null, problem: `longhaul.py status ended with exit code ${ran.exitCode}: ${said.at(-1) ?? 'no output'}`, update: false }
  let status: any
  try { status = JSON.parse(ran.stdout) } catch {
    return { status: null, problem: 'Its answer was not JSON.', update: false }
  }
  if (!status || typeof status !== 'object' || !Array.isArray(status.accounts))
    return { status: null, problem: 'Its answer has no list of accounts.', update: false }
  return { status: clean(status), problem: null, update: false }
}

// The record as the pane draws it: each field of its type, or "not known",
// so an odd record never makes a drawing throw. A name stays as given: a
// row whose name fails the check gets no button.
function clean(s: any): LonghaulStatus {
  const str = (v: any) => (typeof v === 'string' ? v : null)
  const flag = (v: any) => (typeof v === 'boolean' ? v : null)
  const num = (v: any) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const strs = (v: any) => (Array.isArray(v) ? v.filter((p: any) => typeof p === 'string') : null)
  const win = (w: any) => (w && typeof w === 'object' && num(w.used) !== null
    ? { used: w.used as number, resets_at: num(w.resets_at) ?? 0 } : null)
  const accounts = (s.accounts as any[]).filter(a => a && typeof a === 'object' && typeof a.name === 'string')
    .map(a => ({ name: a.name as string, email: str(a.email), signed_in: flag(a.signed_in), as_expected: flag(a.as_expected),
      check_failed: str(a.check_failed), sign_in_failed_at: num(a.sign_in_failed_at), plan: str(a.plan),
      limited_until: num(a.limited_until), five_hour: win(a.five_hour), week: win(a.week), only_in: strs(a.only_in),
      kept_out_by: strs(a.kept_out_by) ?? [], ...(typeof a.fallback === 'boolean' ? { fallback: a.fallback } : {}),
      ...(typeof a.use_here === 'string' ? { use_here: a.use_here } : {}),
      ...(Array.isArray(a.preferred) ? { preferred: strs(a.preferred) } : a.preferred === null ? { preferred: null } : {}),
      folder: str(a.folder) ?? '', this_chat: a.this_chat === true }))
  const here = s.here && typeof s.here === 'object' && typeof s.here.folder === 'string'
    ? { folder: s.here.folder as string, may_use: strs(s.here.may_use) ?? [] } : null
  return { accounts, listed: s.listed === true, here,
    switch: (MODES as readonly unknown[]).includes(s.switch) ? s.switch : null, switch_problem: str(s.switch_problem),
    panel_switch: (MODES as readonly unknown[]).includes(s.panel_switch) ? s.panel_switch : null,
    vscode: flag(s.vscode), problem: str(s.problem) }
}

// Reads the rows again, after any read already under way.
function refresh($: any): Promise<void> {
  readOnce = true
  reads = reads.then(async () => {
    const got = await readStatus($)
    await update($, lastRead, () => got)
  }).catch(() => {})
  return reads
}

type Action = {
  label: string // what it is, as the result line names it
  doing: string // what the pane says while it runs
  argv: string[] // longhaul.py's arguments
  timeoutMs: number
  after?: string // what to do once it worked
  done?: () => Promise<void> // what the pane does once it worked
  then?: LonghaulAccountsPage // the page it shows once it worked, with its result: Remove's account is gone
}

// Runs one action, if none runs now, then reads the rows again. The result
// keeps the output's last lines, masked, for the page it ran from; while it
// runs no other action is offered.
async function act($: any, a: Action): Promise<void> {
  if (busy) return
  busy = true
  try {
    const from = await read($, shown)
    await update($, confirming, () => null)
    await update($, running, () => ({ by: SELF, label: a.label, note: a.doing, page: from }))
    let end: LonghaulAccountsResult
    try {
      const ran = await $.process.run([...base($), ...a.argv], { timeoutMs: a.timeoutMs })
      const ok = ran.exitCode === 0
      end = { ok, label: a.label, lines: tail(ran.stdout, ran.stderr), after: ok ? a.after ?? null : null, page: from }
    } catch (err: any) { // it could not start, or ran past its time and was stopped
      end = { ok: false, label: a.label, lines: [mask(`It did not finish: ${err?.message || err}`)], after: null, page: from }
    }
    if (end.ok && a.done) await a.done()
    if (end.ok && a.then) {
      const to = a.then
      end = { ...end, page: to }
      await update($, shown, () => to)
    }
    await update($, result, () => end)
    await refresh($)
  } finally { // the mark goes before the next action may start, so it never clears that one's
    await update($, running, r => (r && r.by === SELF ? null : r)).catch(() => {})
    busy = false
  }
}

// The first drawing of this module: an action another module (the one
// before a reload) marked running is not known to have ended, so it is
// said so; then the rows are read, unless this module has read them (the
// pane /accounts opened: the command reads them). A drawing writes nothing
// itself, so this runs on the clock, just after.
function firstLook($: any, before: LonghaulAccountsRunning | null, have: LonghaulAccountsRead | null): void {
  const stale = before !== null && before.by !== SELF
  if (kicked || !(stale || (have === null && !readOnce))) return
  kicked = true
  try {
    $.clock.after(0, () => {
      void (async () => {
        if (stale) {
          await update($, running, r => (r && r.by !== SELF ? null : r))
          await update($, result, () => ({ ok: null, label: before.label, after: null, page: before.page ?? 'list',
            lines: ['Longhaul was reloaded while this ran, so how it ended is not known. The rows were read again.'] }))
        }
        if (!readOnce) await refresh($)
      })().catch(() => {})
    })
  } catch {}
}

// The actions behind the buttons. A name comes from the last read alone and
// is checked again, so nothing else reaches longhaul.py as an account.
async function known($: any, name: string): Promise<LonghaulAccount | null> {
  const have = await read($, lastRead)
  return (NAME.test(name) && have?.status?.accounts.find(a => a.name === name)) || null
}

async function signIn($: any, name: string): Promise<void> {
  const a = busy ? null : await known($, name)
  if (!a) return
  const how = signInOf(a)
  await act($, { label: `${how.label}: ${name}`, doing: `Signing in ${name}: ${NOTE}`, argv: how.argv,
    timeoutMs: SIGN_IN_TIMEOUT })
}

async function remove($: any, name: string): Promise<void> {
  if (busy || !(await known($, name))) return
  await act($, { label: `Remove: ${name}`, doing: `Removing ${name}…`, argv: ['remove', name], timeoutMs: STATUS_TIMEOUT,
    then: 'list' })
}

// With its sign-in folder, the account's own Claude Code folder (0.3.9; the
// owner, 2026-11-11: "when I delete an account it does not delete its
// files"): `remove NAME --delete-folder --yes`, after the pane's own second
// yes. longhaul.py checks first and refuses, nothing changed, a folder it
// may not delete (the default one, another account's, one a chat runs on);
// its words show in the result. The pane says "sign-in folder" wherever it
// means that folder (the UX review's Q4): "folder" alone is a project's.
async function removeWithFolder($: any, name: string): Promise<void> {
  if (busy || !(await known($, name))) return
  await act($, { label: `Remove and delete its sign-in folder: ${name}`,
    doing: `Removing ${name} and deleting its sign-in folder…`,
    argv: ['remove', name, '--delete-folder', '--yes'], timeoutMs: STATUS_TIMEOUT, then: 'list' })
}

// This chat on the account (`use NAME --chat ID`): the relay moves it the
// next time Claude is idle in it; longhaul.py says when it cannot.
async function useHere($: any, name: string): Promise<void> {
  if (busy || !(await known($, name))) return
  let sid = ''
  try { sid = String(await $.session.id()) } catch {}
  if (!sid) return
  await act($, { label: `Use for this chat: ${name}`, doing: `Choosing ${name} for this chat…`,
    argv: ['use', name, '--chat', sid], timeoutMs: STATUS_TIMEOUT })
}

// One account's recorded limit forgotten (`clear NAME`): it was reset
// before its time. The chats waiting for it carry on within a few seconds.
// The button says when to press it, and its line what happens if the
// account is still full (the UX review's Q7, §4.7 row 3).
const FORGET_ONE = 'It has room: forget this limit'
const FORGET_ONE_NOTE = 'If it is still full, the next reply meets the limit and Longhaul records it again.'
const FORGET_ALL = 'Forget all recorded limits'
const FORGET_ALL_NOTE = 'Use this when claude.ai shows room but Longhaul still waits.'

async function clearOne($: any, name: string): Promise<void> {
  if (busy || !(await known($, name))) return
  await act($, { label: `Forget this limit: ${name}`, doing: `Forgetting ${name}'s recorded limit…`,
    argv: ['clear', name], timeoutMs: STATUS_TIMEOUT })
}

// Whether a yes is being asked for on this account's page: its Remove, its
// Remove with the sign-in folder, or Remove all folder limits.
function asking(c: Ctx, name: string): boolean {
  return c.confirm === name || c.confirm === `delete:${name}` || c.confirm === `anywhere:${name}`
}

// The Folders part's three: a folder typed on this account's page added,
// one of its folders from the last read taken off, or its list dropped.
async function addFolder($: any, name: string): Promise<void> {
  if (busy || !(await known($, name))) return
  const typed = await read($, folder)
  const path = typed.account === name ? typed.path.trim() : ''
  const problem = folderProblem(path)
  await update($, folder, f => ({ account: name, path: f.account === name ? f.path : '', problem }))
  if (problem) return
  await act($, { label: mask(`Add folder ${path}: ${name}`), doing: mask(`Adding ${path} to ${name}'s folders…`),
    argv: ['folders', name, '--add', path], timeoutMs: STATUS_TIMEOUT,
    done: async () => { await update($, folder, () => NO_FOLDER) } })
}

// This chat's folder added to the folders an account runs only in, the fix
// its Use row offers where that list alone keeps it out (`folders NAME
// --add HERE`). Only from the last read: its list, and where this chat is.
async function addHere($: any, name: string): Promise<void> {
  const a = busy ? null : await known($, name)
  const here = (await read($, lastRead))?.status?.here
  if (!a || !here || a.use_here !== 'not_allowed' || !a.only_in?.length || folderProblem(here.folder)) return
  await act($, { label: mask(`Add folder ${here.folder}: ${name}`), doing: mask(`Adding ${here.folder} to ${name}'s folders…`),
    argv: ['folders', name, '--add', here.folder], timeoutMs: STATUS_TIMEOUT })
}

async function removeFolder($: any, name: string, path: string): Promise<void> {
  const a = busy ? null : await known($, name)
  if (!a?.only_in?.includes(path)) return
  await act($, { label: mask(`Remove folder ${path}: ${name}`), doing: mask(`Taking ${path} off ${name}'s folders…`),
    argv: ['folders', name, '--remove', path], timeoutMs: STATUS_TIMEOUT })
}

// One of the folders it is first choice in taken off (`folders NAME
// --no-prefer PATH`, 0.3.9), only one the last read listed.
async function unprefer($: any, name: string, path: string): Promise<void> {
  const a = busy ? null : await known($, name)
  if (!a?.preferred?.includes(path)) return
  await act($, { label: mask(`Remove first choice ${path}: ${name}`), doing: mask(`Taking ${path} off ${name}'s first choices…`),
    argv: ['folders', name, '--no-prefer', path], timeoutMs: STATUS_TIMEOUT })
}

async function anywhere($: any, name: string): Promise<void> {
  const a = busy ? null : await known($, name)
  if (!a || (a.only_in !== null && !a.only_in.length)) return // no list to drop
  await act($, { label: `Remove all folder limits: ${name}`, doing: `Removing ${name}'s folder limits…`,
    argv: ['folders', name, '--anywhere'],
    timeoutMs: STATUS_TIMEOUT })
}

// Its fallback mark turned the other way (the owner, 2026-11-06): `fallback
// NAME on|off`, only from what the last read said, so a press on a page drawn
// before a change never sets what is set already.
async function setFallback($: any, name: string, on: boolean): Promise<void> {
  const a = busy ? null : await known($, name)
  if (!a || typeof a.fallback !== 'boolean' || a.fallback === on) return
  await act($, { label: `${on ? 'Use as fallback' : 'Use as a normal account'}: ${name}`,
    doing: on ? `Marking ${name} as a fallback…` : `Marking ${name} as a normal account…`,
    argv: ['fallback', name, on ? 'on' : 'off'], timeoutMs: STATUS_TIMEOUT })
}

async function add($: any): Promise<void> {
  if (busy) return
  const checked = checkAdd(await read($, form))
  if ('problem' in checked) {
    await update($, form, f => ({ ...f, problem: checked.problem }))
    return
  }
  const name = checked.argv[1]!
  await update($, form, f => ({ ...f, problem: null }))
  await act($, { label: `Add: ${name}`, doing: `Adding ${name}: ${NOTE}`, argv: checked.argv, timeoutMs: SIGN_IN_TIMEOUT,
    done: async () => { await update($, form, () => EMPTY) } })
}

async function setSwitch($: any, mode: string): Promise<void> {
  const have = await read($, lastRead)
  if (busy || !(MODES as readonly string[]).includes(mode) || have?.status?.switch === mode) return
  await act($, { label: `Switching at a limit: ${mode}`, doing: `Setting switching to ${mode}…`, argv: ['switch', mode],
    timeoutMs: STATUS_TIMEOUT })
}

async function vscode($: any, on: boolean): Promise<void> {
  await act($, { label: `VS Code panel on Longhaul's accounts: ${on ? 'on' : 'off'}`,
    doing: `Turning the VS Code panel ${on ? 'on' : 'off'}…`, argv: on ? ['vscode', 'on', '--yes'] : ['vscode', 'off'],
    timeoutMs: STATUS_TIMEOUT, after: RELOAD })
}

// Every recorded limit forgotten (`clear`): its result, as longhaul.py
// prints it, names the chats that carry on.
async function clear($: any): Promise<void> {
  await act($, { label: FORGET_ALL, doing: 'Forgetting all recorded limits…', argv: ['clear'],
    timeoutMs: STATUS_TIMEOUT })
}

async function retry($: any): Promise<void> {
  if (busy) return
  busy = true
  try { await refresh($) } finally { busy = false }
}

// The page the pane can show of the one asked for: an account's page only
// while the last read lists it under a name the check takes, a page at all
// only once the rows were read and on a surface that draws fields.
function pageOf(asked: LonghaulAccountsPage, status: LonghaulStatus | null, canType: boolean): LonghaulAccountsPage {
  if (!status || !canType) return 'list'
  if (asked.startsWith('account:')) {
    const name = asked.slice('account:'.length)
    return NAME.test(name) && status.accounts.some(a => a.name === name) ? asked : 'list'
  }
  return asked === 'add' || asked === 'settings' ? asked : 'list'
}

// The control an account's page opens with the focus on (the UX review's
// Q5): the first one a press changes nothing with, so a double Enter on the
// list never starts a browser sign-in, which holds the pane for up to ten
// minutes. Its folder field, where it has one (Enter there only keeps what
// is typed), else Back. Never an action: Use, Sign in, a limit forgotten or
// a folder changed all run at once, and Remove asks with its yes in reach.
function harmless(a: LonghaulAccount): string {
  return a.only_in !== null ? 'folder-path' : 'back'
}

// The list line each page is opened from, so going back puts the focus there.
function rowKey(p: LonghaulAccountsPage): string {
  return p.startsWith('account:') ? `row-${p.slice('account:'.length)}` : p === 'list' ? 'row-first' : `row-${p}`
}

// Moves the focus ring onto an element the next drawing brings, where the
// pane holds the keys; anywhere else it stays as it is.
function focusOn($: any, key: string): void {
  try { void Promise.resolve($.ui.focus({ requestId: PANE, key })).catch(() => {}) } catch {}
}

// A yes asked for (a Remove's), with the focus on its Cancel: a second
// Enter after the press that asked cancels, and never says yes.
async function ask($: any, what: string, cancel: string): Promise<void> {
  await update($, confirming, () => what)
  focusOn($, cancel)
}

// Opens an item's page from the list: Enter on its line, or a click.
async function go($: any, to: LonghaulAccountsPage, first: string): Promise<void> {
  if (to.startsWith('account:') && !(await known($, to.slice('account:'.length)))) return
  await update($, confirming, () => null)
  await update($, shown, () => to)
  focusOn($, first)
}

// One step back: a Remove waiting for its yes is cancelled, a page goes
// back to the list with the focus on its line. False on the list: there,
// Escape closes the pane.
async function back($: any): Promise<boolean> {
  if (await read($, confirming)) {
    await update($, confirming, () => null)
    return true
  }
  const [asked, have] = await Promise.all([read($, shown), read($, lastRead)])
  const on = pageOf(asked, have?.status ?? null, true)
  if (asked !== 'list') await update($, shown, () => 'list')
  if (on === 'list') return false
  await update($, marked, () => rowKey(on))
  focusOn($, rowKey(on))
  return true
}

// The question's answer, as its button says: `longhaul.py answer CHAT QID
// CHOICE` (not bin/longhaul, which refuses it: the question is the user's).
// Apart from the actions' one-at-a-time, since the chat waits on it.
async function answer($: any, choice: string): Promise<void> {
  if (answering) return
  const q = await read($, question)
  let sid = ''
  try { sid = String(await $.session.id()) } catch {}
  const argv = q ? answerArgs(q, choice, sid) : null
  if (!q || !argv) return
  answering = true
  try {
    await update($, answered, () => ({ by: SELF, qid: q.id, choice, ok: null, lines: [] }))
    let end: LonghaulAccountsAnswer
    try {
      const ran = await $.process.run([...base($), ...argv], { timeoutMs: STATUS_TIMEOUT })
      end = { by: SELF, qid: q.id, choice, ok: ran.exitCode === 0, lines: tail(ran.stdout, ran.stderr) }
    } catch (err: any) {
      end = { by: SELF, qid: q.id, choice, ok: false, lines: [mask(`It did not finish: ${err?.message || err}`)] }
    }
    await update($, answered, () => end)
  } finally { answering = false }
  await poll($) // at once: the relay may have taken it already
}

// One look for this chat's question and carry-on. A change redraws the
// status entry; a question the pane was not opened for yet opens it, with a
// pop-up beside it, once per question: an open the user did not ask for may
// stay undrawn (in a terminal under 144 columns; in the VS Code panel it is
// not known), and the pop-up says where to find it. From /accounts, which
// opens the pane itself, it neither opens nor pops up.
async function poll($: any, byCommand = false): Promise<void> {
  if (looking) return
  looking = true
  try {
    const { asked, waiting } = await look($)
    const [q, w] = await Promise.all([read($, question), read($, wait)])
    // Written only on a change, so the drawing that follows them is not drawn again for nothing.
    const newQ = JSON.stringify(q) !== JSON.stringify(asked), newW = JSON.stringify(w) !== JSON.stringify(waiting)
    if (newQ) await update($, question, () => asked)
    if (newW) await update($, wait, () => waiting)
    if (newQ || newW) await showStatus($)
    if (asked && asked.id !== (await read($, askedFor))) {
      await update($, askedFor, () => asked.id)
      if (!byCommand) {
        try { await $.ui.open(OPEN) } catch {}
        try { $.ui.toast(mask(waits(asked)), { timeoutMs: 15000 }) } catch {}
      }
    }
  } catch {} finally { looking = false }
}

// The look every POLL ms, from the first hook that can start it: a reload
// ends this module's timer with it, and the next turn starts it again.
function startPolling($: any): void {
  if (polling) return
  polling = true
  try { $.clock.every(POLL, () => { void poll($) }) } catch { polling = false }
}

// What a part of the pane draws from: the surface's elements and the state.
// `$` is passed beside it, as the engine takes it only spelled out.
type Ctx = {
  E: any // the surface's elements, $.ui.resolve(e)
  surface: string
  canType: boolean // the surface draws Input and Select (the phone does not)
  idle: boolean // no action runs: the actions are offered
  have: LonghaulAccountsRead | null // null: not read yet
  status: LonghaulStatus | null
  now: LonghaulAccountsRunning | null // this module's action, running
  last: LonghaulAccountsResult | null
  confirm: string | null
  form: LonghaulAccountsForm
  folder: LonghaulAccountsFolder // the folder typed on an account's page
  page: LonghaulAccountsPage // the page drawn (pageOf)
  mark: string | null // the key the focus ring is on
  question: LonghaulQuestion | null
  wait: LonghaulWait | null
  answer: LonghaulAccountsAnswer | null
  nowMs: number // the clock, for times shown as longhaul.py shows them
}

// The pane, top to bottom: the question, the page, what runs, the last
// result of this page, the way back. A part draws nothing where it has
// nothing to say.
function drawPane($: any, c: Ctx): any {
  const { Box } = c.E
  const body = c.page === 'list' ? listPage($, c) : c.page === 'add' ? addPage($, c)
    : c.page === 'settings' ? settingsPage($, c) : accountPage($, c)
  return (
    <Box flexDirection="column" gap={1}>
      {questionPart($, c)}
      {body}
      {runningPart($, c)}
      {resultPart($, c)}
      {footPart($, c)}
    </Box>
  )
}

// This chat's question, with its two answers, until its file goes. An
// answer that worked leaves its result in place of the buttons; one refused
// shows why, and the buttons again.
function questionPart($: any, c: Ctx): any {
  const { Box, Text, Button } = c.E
  const q = c.question
  if (!q) return null
  const { says, labels } = asks(q, c.nowMs)
  const mine = c.answer && c.answer.qid === q.id ? c.answer : null
  const runs = mine !== null && mine.ok === null && mine.by === SELF
  const offered = !runs && mine?.ok !== true
  return (
    <Box flexDirection="column">
      <Text color="warning" bold>{mask(says)}</Text>
      {offered && (
        <Box flexDirection="row" gap={1}>
          {q.choices.map(choice => (
            <Button key={`answer-${choice}`} label={labels[choice] ?? choice} onPress={() => void answer($, choice)} />
          ))}
        </Box>
      )}
      {runs && <Text dimColor>Answering…</Text>}
      {mine && mine.ok !== null && (
        <Text color={mine.ok ? 'success' : 'error'}>
          {`${mine.ok ? '✔' : '✘'} ${mine.lines.join(' ') || (mine.ok ? 'Answered.' : 'It was not answered.')}`}
        </Text>
      )}
    </Box>
  )
}

// The list: its title (the engine draws a pane's own only while several
// are open), the carry-on waiting, then one line per account, "+ Add
// account" and "Settings", then where we are.
function listPage($: any, c: Ctx): any {
  const { Box, Text } = c.E
  return (
    <Box flexDirection="column" gap={1}>
      <Text bold>{TITLE}</Text>
      {waitPart($, c)}
      {readPart($, c)}
      {itemsPart($, c)}
      {herePart($, c)}
      {phonePart($, c)}
    </Box>
  )
}

function waitPart($: any, c: Ctx): any {
  const { Text } = c.E
  return c.wait ? <Text>{mask(`This chat ${carries(c.wait, c.nowMs)}.`)}</Text> : null
}

// Before the rows: still reading, or why they could not be read, with Retry.
function readPart($: any, c: Ctx): any {
  const { Box, Text, Button } = c.E
  if (c.have === null) return <Text dimColor>Reading Longhaul's accounts…</Text>
  if (c.status) return c.status.problem ? <Text color="warning">{`⚠ ${mask(c.status.problem)}`}</Text> : null
  return (
    <Box flexDirection="column">
      <Text color="error">{`Could not read Longhaul's accounts. ${c.have.problem ?? ''}`}</Text>
      {c.have.update && <Text>{UPDATE}</Text>}
      {c.idle && <Button key="retry" label="Retry" onPress={() => void retry($)} />}
    </Box>
  )
}

// What the sign-in check found, in a word or two.
function signedIn(a: LonghaulAccount): string {
  if (a.signed_in === null) return 'could not check'
  if (a.signed_in === false) return 'NOT signed in'
  if (a.as_expected === false) return 'signed in as someone else'
  return 'signed in'
}

// An account's line in the list: its plan, its sign-in unless that is
// fine and the limit says more, then the limit or the week's use.
function summary(a: LonghaulAccount, nowMs: number): { text: string; warn: boolean } {
  const limited = typeof a.limited_until === 'number'
  const sign = typeof a.sign_in_failed_at === 'number' ? 'sign-in failed' : signedIn(a)
  const parts = a.plan ? [mask(a.plan)] : []
  if (!(limited && sign === 'signed in')) parts.push(sign)
  if (limited) parts.push(`at its limit until ${clock(a.limited_until!, nowMs)}`)
  else if (a.week) parts.push(`week ${Math.round(a.week.used)}%`)
  return { text: parts.join(' · '), warn: limited || sign !== 'signed in' }
}

// A list that holds / covers every folder, so "only in /" read as a narrow one
// (2026-11-06); the same words as longhaul.py's EVERYWHERE_BY_ROOT.
const EVERYWHERE = 'everywhere (its folders include /)'

// What the line adds in brackets: this chat's account, where it is kept to;
// used nowhere, too, for one with no list that others keep out of every
// folder; and fallback, for one used only when the others have no room.
function extras(a: LonghaulAccount): string {
  const said = [a.this_chat ? 'this chat' : '',
    a.only_in === null || a.kept_out_by?.length ? 'used nowhere'
      : a.only_in.includes('/') ? EVERYWHERE : a.only_in.length ? `only in ${a.only_in.join(', ')}` : '',
    a.fallback === true ? 'fallback' : ''].filter(Boolean)
  return said.length ? mask(`   (${said.join(' · ')})`) : ''
}

// What both Remove confirmations end with: the account's sign-in folder is
// all that a delete takes, never a folder the user works in.
const UNTOUCHED = 'Your project folders are never touched.'

// What a fallback is, on its page: longhaul.py's FALLBACK_RULE, "here".
const FALLBACK = 'used only when no other account allowed here has room'

// The list's lines. Each is a Button on a surface that draws fields, so the
// arrows walk them and Enter opens the page; on the phone they are text. A
// name the check refuses, or one listed twice, gets no page. The focus ring
// starts on the line it was last on, or the first, but on nothing while a
// question is open, so a stray Enter cannot answer it.
function itemsPart($: any, c: Ctx): any {
  const { Box, Text, Button } = c.E
  if (!c.status) return null
  const s = c.status
  const width = Math.min(24, Math.max(0, ...s.accounts.map(a => mask(String(a.name)).length)))
  const keys = new Set<string>()
  const items: { key: string | null; head: string; text: string; warn: boolean; extra: string; open?: () => void }[] = []
  for (const a of s.accounts) {
    const name = String(a.name)
    const key = NAME.test(name) && !keys.has(`row-${name}`) ? `row-${name}` : null
    if (key) keys.add(key)
    const { text, warn } = summary(a, c.nowMs)
    items.push({ key, head: mask(name).padEnd(width), text, warn, extra: extras(a),
      open: key ? () => void go($, `account:${name}`, harmless(a)) : undefined })
  }
  if (c.canType) {
    items.push({ key: 'row-add', head: '+ Add account', text: '', warn: false, extra: '',
      open: () => void go($, 'add', 'add-name') })
    const vs = s.vscode === true ? 'on' : s.vscode === false ? 'off' : 'not known'
    items.push({ key: 'row-settings', head: `Settings (switching: ${s.switch ?? 'not known'} · VS Code panel: ${vs})`,
      text: '', warn: false, extra: '', open: () => void go($, 'settings', 'switch') })
  }
  const pressable = items.filter(i => c.canType && i.key && i.open)
  const start = c.question ? null : (pressable.find(i => i.key === c.mark) ?? pressable[0])?.key ?? null
  return (
    <Box flexDirection="column">
      {!s.accounts.length && (
        <Text>{s.listed ? "Longhaul's list has no accounts." : 'Longhaul has no list of accounts yet.'}</Text>
      )}
      {items.map(i => {
        const parts = [`${c.mark === i.key && i.key ? '›' : ' '} ${i.head}`, i.text ? '  ' : '']
        const text = i.text ? <Text color={i.warn ? 'warning' : undefined}>{i.text}</Text> : null
        const extra = i.extra ? <Text dimColor>{i.extra}</Text> : null
        if (c.canType && i.key && i.open)
          return (
            <Button key={i.key} plain onPress={i.open} {...(i.key === start ? { autoFocus: true } : {})}>
              {parts.join('')}{text}{extra}
            </Button>
          )
        return <Box flexDirection="row"><Text>{parts.join('')}</Text>{text}{extra}</Box>
      })}
    </Box>
  )
}

function herePart($: any, c: Ctx): any {
  const { Text } = c.E
  const here = c.status?.here
  if (!here) return null
  const may = here.may_use.length ? here.may_use.join(', ') : 'no account'
  return <Text dimColor>{mask(`Here, in ${here.folder}: ${may} may be used.`)}</Text>
}

// The phone draws no field: it shows the list, and says where to manage it.
function phonePart($: any, c: Ctx): any {
  const { Text } = c.E
  return c.canType ? null : <Text dimColor>To add, sign in or remove an account, manage accounts from the computer.</Text>
}

function use(label: string, w: LonghaulAccount['week'], nowMs: number): string | null {
  if (!w || typeof w.used !== 'number') return null
  return `${label} ${Math.round(w.used)}%${w.resets_at > 0 ? `, resets ${clock(w.resets_at, nowMs)}` : ''}`
}

function where(a: LonghaulAccount): string {
  if (a.only_in === null) return 'may be used nowhere'
  if (a.kept_out_by?.length) return 'may be used nowhere for now'
  if (a.only_in.includes('/')) return `may be used ${EVERYWHERE}`
  return a.only_in.length ? `may be used only in ${mask(a.only_in.join(', '))}` : 'may be used anywhere'
}

// Its Folders: where it runs (any folder no other account reserves), or
// each folder it runs only in with Remove; a field to add one and Add
// folder; Remove all folder limits… while it has a list to drop, which asks
// first and names the folders it would un-reserve (the UX review's Q12: one
// press dropped a whole list, opening client folders to other accounts). A
// field that is not a list is said, with Remove all folder limits… alone.
// Who keeps it out of every folder, and the way out. The controls while
// nothing runs and no yes is asked for.
function foldersPart($: any, c: Ctx, a: LonghaulAccount): any {
  const { Box, Text, Button, Input } = c.E
  const name = String(a.name)
  const offered = c.idle && !asking(c, name)
  const kept = (a.kept_out_by ?? []).map(String)
  const typed = c.folder.account === name ? c.folder : NO_FOLDER
  return (
    <Box flexDirection="column">
      {a.only_in === null && <Text>{'Runs in: no folder, as its repositories field is not a list of absolute paths. '
        + 'Remove all folder limits drops it.'}</Text>}
      {a.only_in !== null && !a.only_in.length && <Text>Runs in: any folder no other account reserves</Text>}
      {a.only_in !== null && a.only_in.length > 0 && <Text>Runs only in:</Text>}
      {(a.only_in ?? []).map((path, i) => (
        <Box flexDirection="row" gap={1}>
          <Text>{`  ${mask(path)}`}</Text>
          {offered && <Button key={`folder-remove-${i}`} label="Remove" dimColor
            onPress={() => void removeFolder($, name, path)} />}
        </Box>
      ))}
      {kept.length > 0 && (
        <Text color="warning">{mask(`⚠ ${kept.join(', ')} keep${kept.length > 1 ? '' : 's'} every folder, so ${name} is `
          + `used nowhere until a folder is added here (or / is removed from ${kept.length > 1 ? 'theirs' : `${kept[0]}'s`}).`)}</Text>
      )}
      {offered && a.only_in !== null && <Input key="folder-path" label="Add a folder: " placeholder="~/work"
        value={typed.path} onInput={keepFolder($, name)} onSubmit={keepFolder($, name)} autoFocus />}
      {offered && typed.problem && <Text color="error">{`✘ ${typed.problem}`}</Text>}
      {offered && (
        <Box flexDirection="row" gap={1}>
          {a.only_in !== null && <Button key="folder-add" label="Add folder" onPress={() => void addFolder($, name)} />}
          {(a.only_in === null || a.only_in.length > 0) && <Button key="folder-anywhere" label="Remove all folder limits…"
            onPress={() => void ask($, `anywhere:${name}`, 'anywhere-no')} />}
        </Box>
      )}
      {c.idle && c.confirm === `anywhere:${name}` && (
        <Box flexDirection="column">
          <Text color="warning">{mask(a.only_in === null
            ? `Drop ${name}'s repositories field? It is not a list of paths; ${name} could then run in any folder no other `
              + 'account reserves.'
            : `Remove all of ${name}'s folder limits? ${a.only_in.join(', ')} would no longer be reserved for ${name}, `
              + 'and it could run in any folder no other account reserves.')}</Text>
          <Box flexDirection="row" gap={1}>
            <Button key="anywhere-yes" label="Remove all folder limits" variant="primary"
              onPress={() => void anywhere($, name)} />
            <Button key="anywhere-no" label="Cancel" autoFocus onPress={() => void update($, confirming, () => null)} />
          </Box>
        </Box>
      )}
    </Box>
  )
}

// The folders it is first choice in (0.3.9's "preferred", the UX review's
// Q3: used there while it has room), each with Remove, while nothing runs
// and no yes is asked for. Nothing where it is first choice nowhere, or the
// record does not say (an older longhaul.py, or a field that is not a list).
function preferredPart($: any, c: Ctx, a: LonghaulAccount): any {
  const { Box, Text, Button } = c.E
  const liked = a.preferred ?? []
  if (!liked.length) return null
  const name = String(a.name)
  const offered = c.idle && !asking(c, name)
  const drop = (path: string, i: number) => offered
    && <Button key={`prefer-remove-${i}`} label="Remove" dimColor onPress={() => void unprefer($, name, path)} />
  if (liked.length === 1)
    return <Box flexDirection="row" gap={1}><Text>{mask(`First choice in: ${liked[0]}`)}</Text>{drop(liked[0]!, 0)}</Box>
  return (
    <Box flexDirection="column">
      <Text>First choice in:</Text>
      {liked.map((path, i) => <Box flexDirection="row" gap={1}><Text>{`  ${mask(path)}`}</Text>{drop(path, i)}</Box>)}
    </Box>
  )
}

// Whether it is a fallback (the owner, 2026-11-06), and the button that
// turns it the other way, while nothing runs and no Remove waits for a yes.
// Nothing for a record that does not say: an older longhaul.py has no
// `fallback` to run.
function fallbackPart($: any, c: Ctx, a: LonghaulAccount): any {
  const { Box, Text, Button } = c.E
  if (typeof a.fallback !== 'boolean') return null
  const name = String(a.name), on = a.fallback
  return (
    <Box flexDirection="column">
      <Text>{on ? `Fallback: ${FALLBACK}` : 'Normal account'}</Text>
      {c.idle && !asking(c, name) && <Button key="fallback" label={on ? 'Use as a normal account' : 'Use as fallback'}
        onPress={() => void setFallback($, name, !on)} />}
    </Box>
  )
}

// What the folder field's typing keeps, for the account whose page it is on.
function keepFolder($: any, name: string): (value: string) => void {
  return value => void update($, folder, () => ({ account: name, path: value, problem: null }))
}

// Why Use for this chat is not offered (`use_here`, the UX review's Q2: the
// button used to vanish without a word): "" where it is, or for a record
// that does not say. Not allowed here is told by its own rule's words.
function notHere(a: LonghaulAccount, here: LonghaulStatus['here']): string {
  const at = here ? mask(here.folder) : 'this folder'
  switch (a.use_here) {
    case 'this_chat': return 'Use for this chat: this chat runs on it already.'
    case 'limited': return 'Use for this chat: not offered while it is at its limit.'
    case 'not_signed_in': return 'Use for this chat: not offered until it is signed in with its email.'
    case 'not_allowed': {
      const kept = (a.kept_out_by ?? []).map(String)
      if (a.only_in === null) return 'Use for this chat: not offered, as its repositories field is not a list of paths.'
      if (kept.length)
        return mask(`Use for this chat: not offered, as ${kept.join(', ')} keep${kept.length > 1 ? '' : 's'} every folder.`)
      if (a.only_in.length)
        return mask(`Use for this chat: not offered here, as it runs only in ${a.only_in.join(', ')}, and this chat is in ${at}.`)
      if (here?.may_use.length)
        return mask(`Use for this chat: not offered here, as ${at} is reserved for ${here.may_use.join(', ')}.`)
      return `Use for this chat: not offered in ${at}.`
    }
    default: return a.use_here === 'ok' || a.use_here == null ? '' : 'Use for this chat: not offered here.'
  }
}

// The Use row, on every page whose record says (`use_here` not null): the
// button, or why there is none with the action that fixes it where there is
// one: its limit forgotten, its sign-in, this folder added to its folders.
// Each fix lives here alone, not again among the actions.
function useRow($: any, c: Ctx, a: LonghaulAccount): any {
  const { Box, Text, Button } = c.E
  if (typeof a.use_here !== 'string') return null
  const name = String(a.name)
  const offered = c.idle && !asking(c, name)
  if (a.use_here === 'ok')
    return offered ? <Button key={`use-${name}`} label="Use for this chat" onPress={() => void useHere($, name)} /> : null
  const here = c.status?.here ?? null
  const fix = !offered ? null
    : a.use_here === 'limited'
      ? <Button key={`clear-${name}`} label={FORGET_ONE} onPress={() => void clearOne($, name)} />
    : a.use_here === 'not_signed_in'
      ? <Button key={`signin-${name}`} label={signInOf(a).label} onPress={() => void signIn($, name)} />
    : a.use_here === 'not_allowed' && here && a.only_in?.length && !(a.kept_out_by ?? []).length
      && folderProblem(here.folder) === null
      ? <Button key={`use-add-${name}`} label={mask(`Add ${here.folder} to its folders`)}
        onPress={() => void addHere($, name)} />
    : null
  return (
    <Box flexDirection="row" gap={1}>
      <Text>{notHere(a, here)}</Text>
      {fix}
    </Box>
  )
}

// An account's page: what is known of it, then its actions, offered while
// nothing runs. Remove asks first, in place of the actions.
function accountPage($: any, c: Ctx): any {
  const { Box, Text, Button } = c.E
  const name = c.page.slice('account:'.length)
  const a = c.status!.accounts.find(x => x.name === name)!
  const how = signInOf(a)
  const check = a.signed_in === null && a.check_failed ? `could not check: ${mask(String(a.check_failed))}` : signedIn(a)
  const lines = [use('5-hour', a.five_hour, c.nowMs), use('week', a.week, c.nowMs)].filter(Boolean) as string[]
  return (
    <Box flexDirection="column">
      <Text bold>{`${mask(name)}${a.this_chat ? ' (this chat)' : ''}`}</Text>
      <Text>{[a.plan && mask(a.plan), check].filter(Boolean).join(' · ')}</Text>
      {lines.map(line => <Text>{line}</Text>)}
      {typeof a.limited_until === 'number' && (
        <Text color="warning">{`at its limit until ${clock(a.limited_until, c.nowMs)}`}</Text>
      )}
      {typeof a.sign_in_failed_at === 'number' && (
        <Text color="warning">{`Its sign-in failed at ${clock(a.sign_in_failed_at, c.nowMs)}.`}</Text>
      )}
      <Text dimColor>{where(a)}</Text>
      {a.email && <Text dimColor>{mask(String(a.email))}</Text>}
      {a.folder && <Text dimColor>{mask(`sign-in folder ${a.folder}`)}</Text>}
      {useRow($, c, a)}
      {c.idle && !asking(c, name) && (
        <Box flexDirection="row" gap={1}>
          {typeof a.limited_until === 'number' && a.use_here !== 'limited'
            && <Button key={`clear-${name}`} label={FORGET_ONE} onPress={() => void clearOne($, name)} />}
          {a.use_here !== 'not_signed_in'
            && <Button key={`signin-${name}`} label={how.label} onPress={() => void signIn($, name)} />}
          <Button key={`remove-${name}`} label="Remove" dimColor onPress={() => void ask($, name, 'remove-no')} />
        </Box>
      )}
      {c.idle && !asking(c, name) && (typeof a.limited_until === 'number' || a.use_here === 'limited')
        && <Text dimColor>{FORGET_ONE_NOTE}</Text>}
      {c.idle && c.confirm === name && (
        <Box flexDirection="column">
          <Text color="warning">{mask(`Remove ${name} from Longhaul's list? Its sign-in folder${a.folder ? ` ${a.folder}` : ''} `
            + `stays, and its sign-in with it. ${UNTOUCHED}`)}</Text>
          <Box flexDirection="row" gap={1}>
            <Button key="remove-yes" label="Remove" variant="primary" onPress={() => void remove($, name)} />
            <Button key="remove-folder" label="Remove and delete its sign-in folder…" dimColor
              onPress={() => void ask($, `delete:${name}`, 'remove-no')} />
            <Button key="remove-no" label="Cancel" autoFocus onPress={() => void update($, confirming, () => null)} />
          </Box>
        </Box>
      )}
      {c.idle && c.confirm === `delete:${name}` && (
        <Box flexDirection="column">
          <Text color="warning">{mask(`Remove ${name} and delete its sign-in folder${a.folder ? ` ${a.folder}` : ''}? Its `
            + `sign-in and the chat history kept in it go for good. ${UNTOUCHED}`)}</Text>
          <Box flexDirection="row" gap={1}>
            <Button key="remove-folder-yes" label="Delete the sign-in folder" variant="primary"
              onPress={() => void removeWithFolder($, name)} />
            <Button key="remove-no" label="Cancel" autoFocus onPress={() => void update($, confirming, () => null)} />
          </Box>
        </Box>
      )}
      {preferredPart($, c, a)}
      {foldersPart($, c, a)}
      {fallbackPart($, c, a)}
    </Box>
  )
}

// What a field's typing keeps: each change, and Enter, is saved as it stands.
function keep($: any, field: 'name' | 'email' | 'only'): (value: string) => void {
  return value => void update($, form, f => ({ ...f, [field]: value, problem: null }))
}

// Add account's page: the form, while nothing runs.
function addPage($: any, c: Ctx): any {
  const { Box, Text, Button, Input } = c.E
  return (
    <Box flexDirection="column">
      <Text bold>Add an account</Text>
      {c.idle && <Input key="add-name" label="Name: " placeholder="work-2" value={c.form.name}
        onInput={keep($, 'name')} onSubmit={keep($, 'name')} />}
      {c.idle && <Input key="add-email" label="Email: " placeholder="name@example.com" value={c.form.email}
        onInput={keep($, 'email')} onSubmit={keep($, 'email')} />}
      {c.idle && <Input key="add-only" label="Only in (optional, folders split by commas): " placeholder="~/work, ~/clients"
        value={c.form.only} onInput={keep($, 'only')} onSubmit={keep($, 'only')} />}
      {c.idle && c.form.problem && <Text color="error">{`✘ ${c.form.problem}`}</Text>}
      {c.idle && <Button key="add" label="Add account" onPress={() => void add($)} />}
    </Box>
  )
}

// Settings' page: switching at a limit, the VS Code panel, the recorded limits.
function settingsPage($: any, c: Ctx): any {
  const { Box, Text, Button, Select } = c.E
  const mode = c.status!.switch
  const problem = c.status!.switch_problem
  // The panel's own mode: LONGHAUL_SWITCH, then "switch" in vscode.json,
  // then config.json's. With the same environment as "switch", it differs
  // only by vscode.json's key, which the Select above does not change.
  const panel = c.status!.panel_switch
  const on = c.status!.vscode
  return (
    <Box flexDirection="column" gap={1}>
      <Text bold>Settings</Text>
      <Box flexDirection="column">
        {c.idle && <Select key="switch" label="Switching at a limit: " options={MODES.map(m => ({ value: m, label: m }))}
          {...(mode ? { value: mode } : {})} onSelect={(value: string) => void setSwitch($, value)} />}
        {!c.idle && <Text>{`Switching at a limit: ${mode ?? 'not known'}`}</Text>}
        <Text dimColor>ask: asks first</Text>
        <Text dimColor>auto: moves by itself</Text>
        <Text dimColor>never: waits for the reset</Text>
        {mode && panel && panel !== mode
          && <Text key="panel-switch">{`The VS Code panel uses ${panel} (set in ~/.longhaul/vscode.json).`}</Text>}
        {problem && <Text color="warning">{`⚠ ${mask(String(problem))}`}</Text>}
      </Box>
      <Box flexDirection="column">
        <Text>{`VS Code panel on Longhaul's accounts: ${on === true ? 'on' : on === false ? 'off' : 'not known'}`}</Text>
        {c.idle && (
          <Box flexDirection="row" gap={1}>
            {on !== true && <Button key="vscode-on" label="Turn on" onPress={() => void vscode($, true)} />}
            {on !== false && <Button key="vscode-off" label="Turn off" onPress={() => void vscode($, false)} />}
          </Box>
        )}
      </Box>
      {c.idle && (
        <Box flexDirection="column">
          <Text>{FORGET_ALL_NOTE}</Text>
          <Button key="clear" label={FORGET_ALL} onPress={() => void clear($)} />
        </Box>
      )}
    </Box>
  )
}

function runningPart($: any, c: Ctx): any {
  const { Text } = c.E
  return c.now ? <Text color="warning">{c.now.note}</Text> : null
}

// The last action's end, on the page it ran from: ✔ or ✘ (? when not
// known), its output's last lines, what next.
function resultPart($: any, c: Ctx): any {
  const { Box, Text } = c.E
  if (!c.last || !c.canType || !c.idle || c.last.page !== c.page) return null
  const { ok, label, lines, after } = c.last
  return (
    <Box flexDirection="column">
      <Text color={ok ? 'success' : ok === false ? 'error' : 'warning'}>
        {`${ok ? '✔' : ok === false ? '✘' : '?'} ${label}${ok === null ? ': not known' : ''}`}
      </Text>
      {lines.map(line => <Text dimColor>{line}</Text>)}
      {after && <Text>{after}</Text>}
    </Box>
  )
}

// The way back from a page (a Back button: the VS Code panel may give
// Escape to itself), and on the terminal the keys, in a dim line.
function footPart($: any, c: Ctx): any {
  const { Box, Text, Button } = c.E
  if (!c.canType) return null
  const keys = c.surface === 'terminal'
    ? <Text dimColor>{c.page === 'list' ? '↑ ↓ to move · Enter to open · Esc to close' : 'Esc to go back'}</Text> : null
  if (c.page === 'list') return keys
  // Back holds the ring on an account's page with nothing harmless above it (harmless()).
  const a = c.page.startsWith('account:') ? c.status?.accounts.find(x => `account:${x.name}` === c.page) : undefined
  const ring = a !== undefined && harmless(a) === 'back' && c.idle && !asking(c, String(a.name))
  return (
    <Box flexDirection="column">
      <Button key="back" label="Back" dimColor onPress={() => void back($)} {...(ring ? { autoFocus: true } : {})} />
      {keys}
    </Box>
  )
}

export const register: Register = on => {
  // Every start, interactive or not (the VS Code panel may say either). The
  // matcher is there because a plugin hooks an event once without one, and
  // display.ts's session.start hook is that one.
  on('session.start', { isInteractive: [true, false] }, async ($, e, next) => {
    try {
      await $.command.register({ name: 'accounts',
        description: "Longhaul's accounts in a pane: sign in again, add, remove, switching" })
    } catch {}
    startPolling($)
    return next(e)
  })

  // After a reload, which ends the look's timer, the next turn starts it
  // again. The matcher, as above: display.ts hooks turn.complete without one.
  on('turn.complete', { isAborted: [true, false] }, async ($, e, next) => {
    startPolling($)
    return next(e)
  })

  // Opens the pane on its list and reads the rows; where it cannot be drawn, says why.
  on('command.run', { command: 'accounts' }, async $ => {
    startPolling($)
    // The read starts first, so the pane's first drawing finds it under way and asks for no other.
    const reading = refresh($)
    try { // as /mcp opens: on the list, the focus on its first line
      await update($, shown, () => 'list')
      await update($, confirming, () => null)
      await update($, marked, () => null)
    } catch {}
    await poll($, true)
    let opened: { isPlaced: boolean; reason?: string }
    try {
      opened = await $.ui.open(OPEN)
    } catch (err: any) {
      opened = { isPlaced: false, reason: String(err?.message || err) }
    }
    await reading
    if (!opened.isPlaced)
      return { text: `Longhaul: the accounts pane cannot be shown here (${opened.reason}). /longhaul:manage does the same in words.` }
    return { text: 'Longhaul: the accounts pane is open. Escape closes it.' }
  })

  // Escape: on a page, back to the list, the pane kept open; on the list it
  // closes. Escape gave the keys back to the prompt, so the pane asks for
  // them again once it has them (the prompt grants that over an empty composer).
  on('ui.close', { id: PANE }, async ($, e, next) => {
    if (e.origin?.kind !== 'person') return next(e)
    let stays = false
    try { stays = await back($) } catch {}
    if (!stays) return next(e)
    try { $.clock.after(0, () => { void Promise.resolve($.ui.open(OPEN)).catch(() => {}) }) } catch {}
    return { value: undefined } // answered without next: the pane stays open
  })

  // Where the focus ring is, for the list's › mark; the ring itself is the engine's.
  on('ui.focus', { component: 'Pane', requestId: PANE }, async ($, e, next) => {
    const done = await next(e)
    try {
      const at = e.element ?? null
      if (!done?.deny && (await read($, marked)) !== at) await update($, marked, () => at)
    } catch {}
    return done
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const E: any = $.ui.resolve(e)
    const [have, now, last, confirm, typed, asked, mark, q, w, ans, dir] = await Promise.all([read($, lastRead),
      read($, running), read($, result), read($, confirming), read($, form), read($, shown), read($, marked),
      read($, question), read($, wait), read($, answered), read($, folder)])
    let nowMs = Date.now()
    try { nowMs = await $.clock.now() } catch {}
    firstLook($, now, have)
    const mine = now && now.by === SELF ? now : null
    const canType = TYPING.includes(String(e.surface))
    const status = have?.status ?? null
    const c: Ctx = { E, surface: String(e.surface), canType, idle: mine === null, have, status, now: mine, last, confirm,
      form: typed, folder: dir, page: pageOf(asked, status, canType), mark, question: q, wait: w, answer: ans, nowMs }
    return drawPane($, c)
  })
}
