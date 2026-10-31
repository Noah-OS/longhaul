import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { LonghaulAccount, LonghaulAccountsAnswer, LonghaulAccountsForm, LonghaulAccountsPage, LonghaulAccountsRead,
  LonghaulAccountsResult, LonghaulAccountsRunning, LonghaulQuestion, LonghaulStatus, LonghaulWait } from '../types'
import { status as showStatus } from './display.ts'
import { answerArgs, asks, carries, clock, look, waits } from './question.ts'

// Longhaul's accounts screen (S2): /accounts opens a pane that works as /mcp
// does (the owner, 2026-10-30: "a model showing up front of us and we can do
// some action there"). First a short list, one line per account, then
// "+ Add account" and "Settings"; the arrows move the focus, Enter opens an
// item's page, Escape goes back, and from the list closes the pane. An
// account's page has its details and its actions (Sign in again or Sign in,
// Remove); Add account's page the form; Settings' page switching, the VS
// Code panel and forgetting the limits. The pane draws `longhaul.py status
// --json` and runs the same commands /longhaul:manage runs in words, by
// argv, never through a shell: setup, add, remove, switch, vscode, clear.
// One at a time; the rows are read again when the pane opens and after each.
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
      folder: str(a.folder) ?? '', this_chat: a.this_chat === true }))
  const here = s.here && typeof s.here === 'object' && typeof s.here.folder === 'string'
    ? { folder: s.here.folder as string, may_use: strs(s.here.may_use) ?? [] } : null
  return { accounts, listed: s.listed === true, here,
    switch: (MODES as readonly unknown[]).includes(s.switch) ? s.switch : null, switch_problem: str(s.switch_problem),
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

async function clear($: any): Promise<void> {
  await act($, { label: 'Forget recorded limits', doing: 'Forgetting the recorded limits…', argv: ['clear'],
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

// The list line each page is opened from, so going back puts the focus there.
function rowKey(p: LonghaulAccountsPage): string {
  return p.startsWith('account:') ? `row-${p.slice('account:'.length)}` : p === 'list' ? 'row-first' : `row-${p}`
}

// Moves the focus ring onto an element the next drawing brings, where the
// pane holds the keys; anywhere else it stays as it is.
function focusOn($: any, key: string): void {
  try { void Promise.resolve($.ui.focus({ requestId: PANE, key })).catch(() => {}) } catch {}
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

// What the line adds in brackets: this chat's account, where it is kept to.
function extras(a: LonghaulAccount): string {
  const said = [a.this_chat ? 'this chat' : '',
    a.only_in === null ? 'used nowhere' : a.only_in.length ? `only in ${a.only_in.join(', ')}` : ''].filter(Boolean)
  return said.length ? mask(`   (${said.join(' · ')})`) : ''
}

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
      open: key ? () => void go($, `account:${name}`, `signin-${name}`) : undefined })
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
  return a.only_in.length ? `may be used only in ${mask(a.only_in.join(', '))}` : 'may be used anywhere'
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
      {a.folder && <Text dimColor>{mask(`folder ${a.folder}`)}</Text>}
      {c.idle && c.confirm !== name && (
        <Box flexDirection="row" gap={1}>
          <Button key={`signin-${name}`} label={how.label} onPress={() => void signIn($, name)} />
          <Button key={`remove-${name}`} label="Remove" dimColor onPress={() => void update($, confirming, () => name)} />
        </Box>
      )}
      {c.idle && c.confirm === name && (
        <Box flexDirection="column">
          <Text color="warning">{`Remove ${name} from Longhaul's list? Its folder and sign-in stay.`}</Text>
          <Box flexDirection="row" gap={1}>
            <Button key="remove-yes" label="Remove" variant="primary" onPress={() => void remove($, name)} />
            <Button key="remove-no" label="Cancel" onPress={() => void update($, confirming, () => null)} />
          </Box>
        </Box>
      )}
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
      {c.idle && <Button key="clear" label="Forget recorded limits" onPress={() => void clear($)} />}
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
  return (
    <Box flexDirection="column">
      <Button key="back" label="Back" dimColor onPress={() => void back($)} />
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
    const [have, now, last, confirm, typed, asked, mark, q, w, ans] = await Promise.all([read($, lastRead), read($, running),
      read($, result), read($, confirming), read($, form), read($, shown), read($, marked), read($, question), read($, wait),
      read($, answered)])
    let nowMs = Date.now()
    try { nowMs = await $.clock.now() } catch {}
    firstLook($, now, have)
    const mine = now && now.by === SELF ? now : null
    const canType = TYPING.includes(String(e.surface))
    const status = have?.status ?? null
    const c: Ctx = { E, surface: String(e.surface), canType, idle: mine === null, have, status, now: mine, last, confirm,
      form: typed, page: pageOf(asked, status, canType), mark, question: q, wait: w, answer: ans, nowMs }
    return drawPane($, c)
  })
}
