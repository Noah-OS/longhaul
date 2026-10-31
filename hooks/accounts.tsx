import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { LonghaulAccount, LonghaulAccountsForm, LonghaulAccountsRead, LonghaulAccountsResult,
  LonghaulAccountsRunning, LonghaulStatus } from '../types'

// Longhaul's accounts screen (S2): /accounts opens a pane, as /mcp opens its
// list, where the user manages Longhaul's accounts with buttons (the owner,
// 2026-10-31: "add a new one, remove, get a login again if some account get
// expired"). The pane draws `longhaul.py status --json` and runs the same
// commands /longhaul:manage runs in words, by argv, never through a shell:
// setup, add, remove, switch, vscode, clear. One at a time; the rows are
// read again when the pane opens and after each one.
//
// What it draws comes from $.state (types/index.d.ts): a redraw follows each
// write, and the values outlive a reload of this module, which its variables
// do not. So an action the module before a reload was running is shown as
// not known, its process gone with that module, and the rows are read again.
// Emails are never shown whole (the owner's decision): the record's come
// masked, and every email in a command's output is masked here.

const PANE = 'longhaul-accounts'
const TITLE = 'Longhaul accounts'
const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$/ // what longhaul.py takes as a name, and never an option
const MODES = ['ask', 'auto', 'never'] as const
// The surfaces that draw Input and Select (the phone does not), by name: the
// element table answers every constructor whatever the surface draws.
const TYPING = ['terminal', 'desktop', 'vscode']
const STATUS_TIMEOUT = 60_000 // ms: it checks every account's sign-in
const SIGN_IN_TIMEOUT = 600_000 // ms: a browser sign-in, the most process.run allows
const SHOWN = 10 // lines of a command's output shown
const NOTE = "finish in the browser that opened, with that account's email. If the browser is signed in to claude.ai "
  + 'as someone else, copy the link into a private window.'
const RELOAD = 'Reload VS Code: Cmd+Shift+P, Developer: Reload Window.'
const UPDATE = 'To update Longhaul: claude plugin marketplace update longhaul, then claude plugin update '
  + 'longhaul@longhaul, then /reload-plugins.'

// Change a tag when its value's fields change: it declines what an earlier
// version stored, which outlives a reload.
const lastRead = atom({ plugin: 'longhaul', key: 'accountsRead' } as const, null, { shape: 'accounts-read-1' })
const running = atom({ plugin: 'longhaul', key: 'accountsRunning' } as const, null, { shape: 'accounts-running-1' })
const result = atom({ plugin: 'longhaul', key: 'accountsResult' } as const, null, { shape: 'accounts-result-1' })
const confirming = atom({ plugin: 'longhaul', key: 'accountsConfirm' } as const, null, { shape: 'accounts-confirm-1' })
const EMPTY: LonghaulAccountsForm = { name: '', email: '', only: '', problem: null }
const form = atom({ plugin: 'longhaul', key: 'accountsForm' } as const, EMPTY, { shape: 'accounts-form-1' })

// This module's own mark on what it runs: a reload loads a new module, with
// a new mark, so an action marked by another one was cut off by the reload.
const SELF = `m${Math.random().toString(36).slice(2)}`
let busy = false // an action of this module runs: one at a time
let kicked = false // this module asked for its first read from a drawing
let reads: Promise<void> = Promise.resolve() // the reads, one after another

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

// An epoch time as the user reads it, in this machine's time: "Sat 14:05".
function when(seconds: number): string {
  const d = new Date(seconds * 1000)
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]
  return `${day} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function base($: any): string[] {
  return ['python3', '-I', `${$.plugin.root}/longhaul.py`]
}

// Whether an account needs a first sign-in (`setup NAME`) rather than a new one (`--relogin`).
function needsSignIn(a: LonghaulAccount): boolean {
  return a.signed_in === false || a.as_expected === false
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
  return { status: status as LonghaulStatus, problem: null, update: false }
}

// Reads the rows again, after any read already under way.
function refresh($: any): Promise<void> {
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
}

// Runs one action, if none runs now, then reads the rows again. The result
// keeps the output's last lines, masked; while it runs only the rows show.
async function act($: any, a: Action): Promise<void> {
  if (busy) return
  busy = true
  try {
    await update($, confirming, () => null)
    await update($, running, () => ({ by: SELF, label: a.label, note: a.doing }))
    let end: LonghaulAccountsResult
    try {
      const ran = await $.process.run([...base($), ...a.argv], { timeoutMs: a.timeoutMs })
      const ok = ran.exitCode === 0
      end = { ok, label: a.label, lines: tail(ran.stdout, ran.stderr), after: ok ? a.after ?? null : null }
    } catch (err: any) { // it could not start, or ran past its time and was stopped
      end = { ok: false, label: a.label, lines: [mask(`It did not finish: ${err?.message || err}`)], after: null }
    }
    if (end.ok && a.done) await a.done()
    await update($, result, () => end)
    await refresh($)
  } finally {
    busy = false
    await update($, running, r => (r && r.by === SELF ? null : r)).catch(() => {})
  }
}

// The first drawing of this module: an action another module (the one
// before a reload) marked running is not known to have ended, so it is
// said so; then, and when nothing was read yet, the rows are read. A
// drawing writes nothing itself, so this runs on the clock, just after.
function firstLook($: any, before: LonghaulAccountsRunning | null, have: LonghaulAccountsRead | null): void {
  const stale = before !== null && before.by !== SELF
  if (kicked || (!stale && have !== null)) return
  kicked = true
  try {
    $.clock.after(0, () => {
      void (async () => {
        if (stale) {
          await update($, running, r => (r && r.by !== SELF ? null : r))
          await update($, result, () => ({ ok: null, label: before.label, after: null,
            lines: ['Longhaul was reloaded while this ran, so how it ended is not known. The rows were read again.'] }))
        }
        await refresh($)
      })().catch(() => {})
    })
  } catch {}
}

// The actions behind the buttons. A name comes from the last read alone and
// is checked again, so nothing else reaches longhaul.py as an account.
async function known($: any, name: string): Promise<boolean> {
  const have = await read($, lastRead)
  return NAME.test(name) && !!have?.status?.accounts.some(a => a.name === name)
}

async function signIn($: any, name: string, again: boolean): Promise<void> {
  if (busy || !(await known($, name))) return
  await act($, { label: again ? `Sign in again: ${name}` : `Sign in: ${name}`, doing: `Signing in ${name}: ${NOTE}`,
    argv: again ? ['setup', name, '--relogin'] : ['setup', name], timeoutMs: SIGN_IN_TIMEOUT })
}

async function remove($: any, name: string): Promise<void> {
  if (busy || !(await known($, name))) return
  await act($, { label: `Remove: ${name}`, doing: `Removing ${name}…`, argv: ['remove', name], timeoutMs: STATUS_TIMEOUT })
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

// What a part of the pane draws from: the surface's elements and the state.
// `$` is passed beside it, as the engine takes it only spelled out.
type Ctx = {
  E: any // the surface's elements, $.ui.resolve(e)
  canType: boolean // the surface draws Input and Select (the phone does not)
  idle: boolean // no action runs: the buttons are offered
  have: LonghaulAccountsRead | null // null: not read yet
  status: LonghaulStatus | null
  now: LonghaulAccountsRunning | null // this module's action, running
  last: LonghaulAccountsResult | null
  confirm: string | null
  form: LonghaulAccountsForm
}

// The pane, top to bottom, one part each; a part draws nothing where it has
// nothing to say. A new part (a question at the top) is one more line here.
function drawPane($: any, c: Ctx): any {
  const { Box } = c.E
  return (
    <Box flexDirection="column" gap={1}>
      {readPart($, c)}
      {rowsPart($, c)}
      {herePart($, c)}
      {phonePart($, c)}
      {runningPart($, c)}
      {addPart($, c)}
      {switchPart($, c)}
      {vscodePart($, c)}
      {clearPart($, c)}
      {resultPart($, c)}
    </Box>
  )
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
  if (a.signed_in === null)
    return `could not check${a.check_failed ? `: ${mask(String(a.check_failed))}` : ''}`
  if (a.signed_in === false) return 'NOT signed in'
  if (a.as_expected === false) return 'signed in as someone else'
  return 'signed in'
}

function use(label: string, w: LonghaulAccount['week']): string | null {
  if (!w || typeof w.used !== 'number') return null
  return `${label} ${Math.round(w.used)}%${typeof w.resets_at === 'number' ? `, resets ${when(w.resets_at)}` : ''}`
}

function where(a: LonghaulAccount): string {
  if (a.only_in === null) return 'may be used nowhere'
  return a.only_in.length ? `may be used only in ${a.only_in.join(', ')}` : 'may be used anywhere'
}

// One account: its name, what is known of it, and its buttons.
function row($: any, c: Ctx, a: LonghaulAccount): any {
  const { Box, Text, Button } = c.E
  const name = String(a.name)
  const usage = [use('5-hour', a.five_hour), use('week', a.week)].filter(Boolean).join(' · ')
  const offered = c.idle && c.canType && NAME.test(name)
  return (
    <Box flexDirection="column">
      <Text bold>{`${mask(name)}${a.this_chat ? ' (this chat)' : ''}`}</Text>
      <Text>{[a.plan, signedIn(a)].filter(Boolean).join(' · ')}</Text>
      {usage && <Text>{usage}</Text>}
      {typeof a.limited_until === 'number' && <Text color="warning">{`at its limit until ${when(a.limited_until)}`}</Text>}
      <Text dimColor>{where(a)}</Text>
      {a.email && <Text dimColor>{mask(String(a.email))}</Text>}
      {offered && c.confirm !== name && (
        <Box flexDirection="row" gap={1}>
          <Button key={`signin-${name}`} label={needsSignIn(a) ? 'Sign in' : 'Sign in again'}
            onPress={() => void signIn($, name, !needsSignIn(a))} />
          <Button key={`remove-${name}`} label="Remove" dimColor
            onPress={() => void update($, confirming, () => name)} />
        </Box>
      )}
      {offered && c.confirm === name && (
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

function rowsPart($: any, c: Ctx): any {
  const { Box, Text } = c.E
  if (!c.status) return null
  if (!c.status.accounts.length)
    return <Text>{c.status.listed ? "Longhaul's list has no accounts." : 'Longhaul has no list of accounts yet.'}</Text>
  return <Box flexDirection="column" gap={1}>{c.status.accounts.map(a => row($, c, a))}</Box>
}

function herePart($: any, c: Ctx): any {
  const { Text } = c.E
  const here = c.status?.here
  if (!here) return null
  const may = here.may_use.length ? here.may_use.join(', ') : 'no account'
  return <Text>{`Here, in ${here.folder}: ${may} may be used.`}</Text>
}

// The phone draws no field: it shows the rows, and says where to manage them.
function phonePart($: any, c: Ctx): any {
  const { Text } = c.E
  return c.canType ? null : <Text dimColor>To add, sign in or remove an account, manage accounts from the computer.</Text>
}

function runningPart($: any, c: Ctx): any {
  const { Text } = c.E
  return c.now ? <Text color="warning">{c.now.note}</Text> : null
}

// The parts below act; each is offered only while nothing runs, on a
// surface that draws fields, once the rows were read.
function offered(c: Ctx): boolean {
  return c.idle && c.canType && c.status !== null
}

// What a field's typing keeps: each change, and Enter, is saved as it stands.
function keep($: any, field: 'name' | 'email' | 'only'): (value: string) => void {
  return value => void update($, form, f => ({ ...f, [field]: value, problem: null }))
}

function addPart($: any, c: Ctx): any {
  const { Box, Text, Button, Input } = c.E
  if (!offered(c)) return null
  return (
    <Box flexDirection="column">
      <Text bold>Add an account</Text>
      <Input key="add-name" label="Name: " placeholder="work-2" value={c.form.name}
        onInput={keep($, 'name')} onSubmit={keep($, 'name')} />
      <Input key="add-email" label="Email: " placeholder="name@example.com" value={c.form.email}
        onInput={keep($, 'email')} onSubmit={keep($, 'email')} />
      <Input key="add-only" label="Only in (optional, folders split by commas): " placeholder="~/work, ~/clients"
        value={c.form.only} onInput={keep($, 'only')} onSubmit={keep($, 'only')} />
      {c.form.problem && <Text color="error">{`✘ ${c.form.problem}`}</Text>}
      <Button key="add" label="Add account" onPress={() => void add($)} />
    </Box>
  )
}

function switchPart($: any, c: Ctx): any {
  const { Box, Text, Select } = c.E
  if (!offered(c)) return null
  const mode = c.status!.switch
  const problem = c.status!.switch_problem
  return (
    <Box flexDirection="column">
      <Select key="switch" label="Switching at a limit: " options={MODES.map(m => ({ value: m, label: m }))}
        {...(mode ? { value: mode } : {})} onSelect={(value: string) => void setSwitch($, value)} />
      <Text dimColor>ask: asks first</Text>
      <Text dimColor>auto: moves by itself</Text>
      <Text dimColor>never: waits for the reset</Text>
      {problem && <Text color="warning">{`⚠ ${mask(String(problem))}`}</Text>}
    </Box>
  )
}

function vscodePart($: any, c: Ctx): any {
  const { Box, Text, Button } = c.E
  if (!offered(c)) return null
  const on = c.status!.vscode
  return (
    <Box flexDirection="column">
      <Text>{`VS Code panel on Longhaul's accounts: ${on === true ? 'on' : on === false ? 'off' : 'not known'}`}</Text>
      <Box flexDirection="row" gap={1}>
        {on !== true && <Button key="vscode-on" label="Turn on" onPress={() => void vscode($, true)} />}
        {on !== false && <Button key="vscode-off" label="Turn off" onPress={() => void vscode($, false)} />}
      </Box>
    </Box>
  )
}

function clearPart($: any, c: Ctx): any {
  const { Button } = c.E
  if (!offered(c)) return null
  return <Button key="clear" label="Forget recorded limits" onPress={() => void clear($)} />
}

// The last action's end: ✔ or ✘ (? when not known), its output's last lines, what next.
function resultPart($: any, c: Ctx): any {
  const { Box, Text } = c.E
  if (!c.last || !c.canType) return null
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

export const register: Register = on => {
  // Every start, interactive or not (the VS Code panel may say either). The
  // matcher is there because a plugin hooks an event once without one, and
  // display.ts's session.start hook is that one.
  on('session.start', { isInteractive: [true, false] }, async ($, e, next) => {
    try {
      await $.command.register({ name: 'accounts',
        description: "Longhaul's accounts in a pane: sign in again, add, remove, switching" })
    } catch {}
    return next(e)
  })

  // Opens the pane and reads the rows; where it cannot be drawn, says why.
  on('command.run', { command: 'accounts' }, async $ => {
    let opened: { isPlaced: boolean; reason?: string }
    try {
      opened = await $.ui.open({ id: PANE, title: TITLE, focus: true, closeOnEscape: true })
    } catch (err: any) {
      opened = { isPlaced: false, reason: String(err?.message || err) }
    }
    await refresh($)
    if (!opened.isPlaced)
      return { text: `Longhaul: the accounts pane cannot be shown here (${opened.reason}). /longhaul:manage does the same in words.` }
    return { text: 'Longhaul: the accounts pane is open. Escape closes it.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const E: any = $.ui.resolve(e)
    const [have, now, last, confirm, typed] = await Promise.all([read($, lastRead), read($, running), read($, result),
      read($, confirming), read($, form)])
    firstLook($, now, have)
    const mine = now && now.by === SELF ? now : null
    const c: Ctx = { E, canType: TYPING.includes(String(e.surface)), idle: mine === null, have,
      status: have?.status ?? null, now: mine, last, confirm, form: typed }
    return drawPane($, c)
  })
}
