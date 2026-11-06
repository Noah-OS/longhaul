import { expect, mock, test } from 'claude-code/testing'

// hooks/accounts.tsx, the accounts pane: `claude plugin test .` from the
// checkout. The plugin loads whole (display.ts and autocompact.ts too), so
// the world answers what each calls at a session's start. Every program the
// pane runs is recorded here and answered from memory: nothing runs, and no
// real file or account is touched. Emails are example.com, names generic.

const PANE_ID = 'longhaul-accounts'
const SID = 'fcd7fbcc-bc91-4a30-9dce-e7496a7f4aa6'
const HOME = '/scratch/longhaul'
const NOW = Date.UTC(2026, 9, 31, 12, 0, 0)
const HOUR = 3600
const AT = NOW / 1000
const NOTE = "finish in the browser that opened, with that account's email. If the browser is signed in to claude.ai "
  + 'as someone else, copy the link into a private window.'
const RELOAD = 'Reload VS Code: Cmd+Shift+P, Developer: Reload Window.'
const OPEN = { id: PANE_ID, title: 'Longhaul accounts', focus: true, closeOnEscape: true }

const ACCOUNTS = [
  { name: 'personal-1', email: 'a•••@example.com', signed_in: true, as_expected: true, check_failed: null, plan: 'max',
    limited_until: null, five_hour: { used: 40, resets_at: AT + 2 * HOUR }, week: { used: 12, resets_at: AT + 50 * HOUR },
    only_in: [], fallback: false, folder: '~/.claude-personal-1', this_chat: true },
  { name: 'work-2', email: 'b•••@example.com', signed_in: false, as_expected: null, check_failed: null, plan: 'pro',
    limited_until: AT + 3 * HOUR, five_hour: null, week: null, only_in: ['~/work', '~/clients'], fallback: false,
    folder: '~/.claude-work-2', this_chat: false },
  { name: 'spare-3', email: 'c•••@example.com', signed_in: true, as_expected: false, check_failed: null, plan: null,
    limited_until: null, five_hour: null, week: null, only_in: null, fallback: false, folder: '~/.claude-spare-3',
    this_chat: false },
  { name: 'team-4', email: null, signed_in: null, as_expected: null, check_failed: 'the check timed out', plan: null,
    limited_until: null, five_hour: null, week: null, only_in: [], fallback: false, folder: '~/.claude-team-4',
    this_chat: false },
]
const STATUS = { accounts: ACCOUNTS, listed: true, here: { folder: '~/code/x', may_use: ['personal-1', 'team-4'] },
  switch: 'ask', switch_problem: null, vscode: false, problem: null }
const ROWS = ['row-personal-1', 'row-work-2', 'row-spare-3', 'row-team-4', 'row-add', 'row-settings']

// A question as longhaul.py's Relay.ask() writes it, and a wait as Relay.schedule() does.
const RELAY = 4242
const QUESTION = { id: 'q-0123456789ab', chat: SID, relay_pid: RELAY, asked_at: AT, kind: 'move', from: 'personal-1',
  limit: 'weekly', resets_at: AT + 38 * HOUR, to: 'work-2', to_resets_at: null, choices: ['move', 'wait'] }
const FIRST = { ...QUESTION, id: 'q-ba9876543210', kind: 'first_reset', to: 'work-2', to_resets_at: AT + 2 * HOUR,
  carries_on_at: AT + 2 * HOUR + 60, choices: ['yes', 'no'] }
const WAIT = { chat: SID, until: AT + 2 * HOUR, account: 'personal-1', why: 'answered' }
const QUESTION_FILE = `${HOME}/questions/${SID}.json`
const WAIT_FILE = `${HOME}/waits/${SID}.json`

type Ran = { exitCode: number; stdout: string; stderr: string }
type World = {
  runs: string[][] // each longhaul.py run's arguments, after the script
  timeouts: number[] // each run's timeoutMs, in the same order
  opened: any[] // each $.ui.open's argument
  closed: any[] // each ui.close that reached the engine: the pane closed
  focused: string[] // each element $.ui.focus was asked to move the ring to
  toasts: string[]
  files: Record<string, { text: string; at: number }> // LONGHAUL_HOME's files, by path
  alive: Set<number> // the pids `kill -0` finds
  status: Ran // what `status --json` answers
  answer: Record<string, Ran> // what each other command answers, by its first word
  slow: Set<string> // commands that answer only once the test moves the clock on
  placed: boolean
  clock: ReturnType<typeof mock.clock>
}

const done = (stdout = '', exitCode = 0, stderr = ''): Ran => ({ exitCode, stdout, stderr })

// /accounts as the user types it at the prompt of a fullscreen terminal.
const TYPED = { command: 'accounts', args: '', origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 160 } } as const

// The engine beneath the plugins, from memory: programs, files, panes, the
// clock, and what display.ts asks at a session's start.
function world(on: any): World {
  const w: World = { runs: [], timeouts: [], opened: [], closed: [], focused: [], toasts: [], files: {}, alive: new Set([RELAY]),
    status: done(JSON.stringify(STATUS)), answer: {}, slow: new Set(), placed: true, clock: mock.clock(on, { now: NOW }) }
  mock.env(on, { LONGHAUL_HOME: HOME, HOME: '/scratch/home' })
  on('session.start', (_: any, e: any) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: SID }))
  on('command.register', (_: any, e: any) => ({ value: { command: e.name } }))
  on('fs.stat', (_: any, e: any) => {
    const f = w.files[e.path]
    return f ? { value: { kind: 'file', size: f.text.length, mtimeMs: f.at, isLink: false } } : { deny: 'no such file' }
  })
  on('fs.read', (_: any, e: any) => (w.files[e.path] ? { value: w.files[e.path]!.text } : { deny: 'no such file' }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', (_: any, e: any) => { w.toasts.push(e.text); return { value: undefined } })
  on('ui.open', (_: any, e: any) => {
    w.opened.push(e)
    return { value: w.placed ? { isPlaced: true } : { isPlaced: false, reason: 'the terminal is 80 columns wide' } }
  })
  on('ui.close', (_: any, e: any) => { w.closed.push(e); return { value: undefined } })
  on('ui.focus', (_: any, e: any) => { w.focused.push(e.element ?? e.key); return { value: {} } })
  on('process.run', async (_: any, e: any) => {
    const argv: string[] = [...e.argv]
    if (argv[0] === 'kill' && argv[1] === '-0' && argv.length === 3)
      return { value: { ...done('', w.alive.has(Number(argv[2])) ? 0 : 1), isStdoutTruncated: false, isStderrTruncated: false } }
    if (argv[0] !== 'python3' || argv[1] !== '-I' || !/^\/.*\/longhaul\.py$/.test(argv[2] ?? ''))
      return { deny: 'not run here' }
    const rest = argv.slice(3)
    w.runs.push(rest)
    w.timeouts.push(e.init?.timeoutMs)
    if (w.slow.has(rest[0]!)) await w.clock.sleep(60_000)
    const ran = rest[0] === 'status' ? w.status : w.answer[rest[0]!] ?? done('Done.')
    return { value: { ...ran, isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return w
}

// A file in LONGHAUL_HOME, written now (a later write has a later time), or gone.
function write(w: World, path: string, value: unknown | null): void {
  if (value === null) delete w.files[path]
  else w.files[path] = { text: JSON.stringify(value), at: (w.files[path]?.at ?? 0) + 1 }
}

// The pane as the engine asks for it, on the surface a test names.
function pane(surface: 'terminal' | 'desktop' | 'vscode' | 'mobile') {
  return { plugin: 'longhaul', component: 'Pane', requestId: PANE_ID, surface,
    props: { title: 'Longhaul accounts', isFocused: true, bodyColumns: 60, placement: 'dock',
      scroll: { offset: 0, bodyRows: 40 }, view: {} } } as const
}

// A session on that surface, /accounts typed, and the pane it opened.
async function opened($: any, w: World, surface: 'terminal' | 'vscode') {
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const said = await $.command.run(TYPED)
  const ui = await $.ui.mount(pane(surface))
  return { said, ui }
}

// Every text the pane shows, a list line's whole text among them.
async function texts(ui: any): Promise<string[]> {
  const found = [...(await ui.findAll({ type: 'Text' })), ...(await ui.findAll({ type: 'Button' }))]
  return found.map((t: any) => t.text)
}

async function buttons(ui: any): Promise<string[]> {
  return (await ui.findAll({ type: 'Button' })).map((b: any) => b.key)
}

// The focus ring moved by the person (the arrows, Tab), as the engine raises it.
async function ring($: any, element: string): Promise<void> {
  await $.ui.focus({ component: 'Pane', requestId: PANE_ID, element, origin: { kind: 'person' } })
}

// Opens a list line's page, as Enter on it does.
async function open(ui: any, key: string): Promise<void> {
  await ui.press({ key })
}

// A time as the pane shows it (longhaul.py's clock()): 14:05 today, Tue
// 14:05 within six days, else 24 Nov 14:05, in this machine's time.
function clock(seconds: number): string {
  const d = new Date(seconds * 1000), n = new Date(NOW)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const day = (t: Date) => new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()
  const days = Math.abs(Math.round((day(d) - day(n)) / 86_400_000))
  if (!days) return hm
  if (days < 7) return `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]} ${hm}`
  return `${d.getDate()} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()]} ${hm}`
}

// Each test on the terminal and in VS Code, the two surfaces that take
// input, each run its own (a fresh plugin, state and world).
type Surface = 'terminal' | 'vscode'
function both(name: string, body: ($: any, on: any, surface: Surface) => Promise<void>) {
  for (const surface of ['terminal', 'vscode'] as const) test(`${name} (${surface})`, ($, on) => body($, on, surface))
}

// The runs after the first read, each followed by the read again.
function after(w: World): string[][] {
  return w.runs.slice(1)
}

both('/accounts opens the pane and reads the accounts', async ($, on, surface) => {
  const w = world(on)
  const { said, ui } = await opened($, w, surface)
  expect(w.opened).toEqual([OPEN])
  expect(said.text).toMatch(/pane/)
  expect(w.runs).toEqual([['status', '--json']])
  expect(w.timeouts).toEqual([60_000])
  await ui.unmount()
})

test('the pane that cannot be placed says why, and that /longhaul:manage does the same', async ($, on) => {
  const w = world(on)
  w.placed = false
  await $.session.start({ cwd: '/scratch/code/x', surface: 'terminal', isInteractive: true })
  const said = await $.command.run(TYPED)
  expect(said.text).toContain('the terminal is 80 columns wide')
  expect(said.text).toContain('/longhaul:manage')
})

both('the list: a line per account, + Add account, Settings, then where we are; no action on it', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  expect(await buttons(ui)).toEqual(ROWS)
  const row = async (key: string) => (await ui.find({ key }))?.text
  expect(await row('row-personal-1')).toBe('  personal-1  max · signed in · week 12%   (this chat)')
  expect(await row('row-work-2')).toBe(`  work-2      pro · NOT signed in · at its limit until ${clock(AT + 3 * HOUR)}   `
    + '(only in ~/work, ~/clients)')
  expect(await row('row-spare-3')).toBe('  spare-3     signed in as someone else   (used nowhere)')
  expect(await row('row-team-4')).toBe('  team-4      could not check')
  expect(await row('row-add')).toBe('  + Add account')
  expect(await row('row-settings')).toBe('  Settings (switching: ask · VS Code panel: off)')
  const all = await texts(ui)
  expect(all[0]).toBe('Longhaul accounts')
  expect(all).toContain('Here, in ~/code/x: personal-1, team-4 may be used.')
  // The keys, on the terminal; the VS Code panel may keep Escape for itself.
  expect(all.includes('↑ ↓ to move · Enter to open · Esc to close')).toBe(surface === 'terminal')
  // No email on the list: each is on its account's page.
  expect(all.join('\n')).not.toMatch(/@/)
  await ui.unmount()
})

both('the focus starts on the first line, and the › mark follows the ring', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'row-personal-1' }))?.props.autoFocus).toBe(true)
  expect((await ui.findAll({ type: 'Button' })).filter((b: any) => b.props.autoFocus).length).toBe(1)
  await ring($, 'row-work-2')
  expect((await ui.find({ key: 'row-work-2' }))?.text).toMatch(/^› work-2 /)
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toMatch(/^ {2}personal-1 /)
  await ring($, 'row-settings')
  expect((await ui.find({ key: 'row-settings' }))?.text).toMatch(/^› Settings/)
  expect((await ui.find({ key: 'row-work-2' }))?.text).toMatch(/^ {2}work-2 /)
  await ui.unmount()
})

both("an account's page: its details, masked email, folder, and its actions; Back returns to its line", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-personal-1')
  let all = await texts(ui)
  for (const t of ['personal-1 (this chat)', 'max · signed in', `5-hour 40%, resets ${clock(AT + 2 * HOUR)}`,
                   `week 12%, resets ${clock(AT + 50 * HOUR)}`, 'may be used anywhere', 'a•••@example.com',
                   'folder ~/.claude-personal-1'])
    expect(all).toContain(t)
  expect(await buttons(ui)).toEqual(['signin-personal-1', 'remove-personal-1', 'folder-add', 'fallback', 'back'])
  expect(all.includes('Esc to go back')).toBe(surface === 'terminal')
  await ui.press({ key: 'back' })
  expect(await buttons(ui)).toEqual(ROWS)
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toMatch(/^› personal-1/)
  // The others, each on its own page.
  await open(ui, 'row-work-2')
  all = await texts(ui)
  for (const t of ['pro · NOT signed in', `at its limit until ${clock(AT + 3 * HOUR)}`,
                   'may be used only in ~/work, ~/clients', 'b•••@example.com'])
    expect(all).toContain(t)
  await ui.press({ key: 'back' })
  expect((await ui.find({ key: 'row-work-2' }))?.text).toMatch(/^› work-2/)
  await open(ui, 'row-spare-3')
  all = await texts(ui)
  expect(all).toContain('signed in as someone else')
  expect(all).toContain('may be used nowhere')
  expect(all).toContain('c•••@example.com')
  await ui.press({ key: 'back' })
  await open(ui, 'row-team-4')
  expect(await texts(ui)).toContain('could not check: the check timed out')
  expect(w.runs).toEqual([['status', '--json']]) // opening and going back run nothing
  await ui.unmount()
})

both('Back cancels a Remove waiting for its yes before it leaves the page', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  expect(await texts(ui)).toContain("Remove work-2 from Longhaul's list? Its folder and sign-in stay.")
  await ui.press({ key: 'back' })
  expect(await ui.find({ key: 'remove-yes' })).toBeUndefined()
  expect(await ui.find({ key: 'signin-work-2' })).toBeDefined()
  await ui.press({ key: 'back' })
  expect(await buttons(ui)).toEqual(ROWS)
  expect(after(w)).toEqual([])
  await ui.unmount()
})

both('Sign in again for a signed-in account, Sign in for one that is not, the note while it runs', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  for (const [name, label] of [['personal-1', 'Sign in again'], ['work-2', 'Sign in'], ['spare-3', 'Sign in'],
                               ['team-4', 'Sign in again']]) {
    await open(ui, `row-${name}`)
    expect((await ui.find({ key: `signin-${name}` }))?.text).toBe(label)
    await ui.press({ key: 'back' })
  }
  w.slow.add('setup')
  await open(ui, 'row-personal-1')
  await ui.press({ key: 'signin-personal-1' })
  expect(await texts(ui)).toContain(`Signing in personal-1: ${NOTE}`)
  await w.clock.advance(60_000)
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  await ui.press({ key: 'signin-work-2' })
  await w.clock.advance(60_000)
  expect(after(w)).toEqual([['setup', 'personal-1', '--relogin'], ['status', '--json'], ['setup', 'work-2'], ['status', '--json']])
  expect(w.timeouts.slice(1)).toEqual([600_000, 60_000, 600_000, 60_000])
  // The result shows on the page it ran from, and only there.
  expect((await texts(ui)).some(t => t.startsWith('✔') && /work-2/.test(t))).toBe(true)
  await ui.press({ key: 'back' })
  expect((await texts(ui)).some(t => t.startsWith('✔'))).toBe(false)
  await ui.unmount()
})

both('Remove runs only after its confirmation, Cancel runs nothing, and its result shows on the list', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  expect(after(w)).toEqual([])
  expect(await texts(ui)).toContain("Remove work-2 from Longhaul's list? Its folder and sign-in stay.")
  await ui.press({ key: 'remove-no' })
  expect(after(w)).toEqual([])
  expect(await ui.find({ key: 'remove-yes' })).toBeUndefined()
  await ui.press({ key: 'remove-work-2' })
  w.status = done(JSON.stringify({ ...STATUS, accounts: ACCOUNTS.filter(a => a.name !== 'work-2') }))
  await ui.press({ key: 'remove-yes' })
  expect(after(w)).toEqual([['remove', 'work-2'], ['status', '--json']])
  // The account is gone, so the pane is back on the list, with the result there.
  expect(await buttons(ui)).toEqual(ROWS.filter(k => k !== 'row-work-2'))
  expect((await texts(ui)).some(t => t.startsWith('✔') && /Remove: work-2/.test(t))).toBe(true)
  await ui.unmount()
})

both('Add refuses a bad name, email or folder and runs nothing', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-add')
  expect(await texts(ui)).toContain('Add an account')
  for (const [name, email, only, said] of [
    ['bad name!', 'new@example.com', '', /name/],
    ['-rf', 'new@example.com', '', /name/],
    ['work-5', 'nobody', '', /email/],
    ['work-5', 'new@example.com', '~/work, clients', /folder/],
  ] as const) {
    await ui.input({ key: 'add-name', text: name, kind: 'change' })
    await ui.input({ key: 'add-email', text: email, kind: 'change' })
    await ui.input({ key: 'add-only', text: only, kind: 'change' })
    await ui.press({ key: 'add' })
    expect(after(w)).toEqual([])
    expect((await texts(ui)).some(t => said.test(t) && t.startsWith('✘'))).toBe(true)
  }
  await ui.unmount()
})

both('a good Add passes the name, the email and each folder as its own --repo, with the note while it runs', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-add')
  await ui.input({ key: 'add-name', text: 'work-5' })
  await ui.input({ key: 'add-email', text: 'new@example.com' })
  await ui.input({ key: 'add-only', text: ' ~/work , /srv/clients,' })
  w.slow.add('add')
  await ui.press({ key: 'add' })
  expect(await texts(ui)).toContain(`Adding work-5: ${NOTE}`)
  await w.clock.advance(60_000)
  expect(after(w)).toEqual([['add', 'work-5', 'new@example.com', '--repo', '~/work', '--repo', '/srv/clients'], ['status', '--json']])
  expect(w.timeouts[1]).toBe(600_000)
  expect((await texts(ui)).some(t => t.startsWith('✔') && /Add: work-5/.test(t))).toBe(true)
  // Once it is done the fields are empty again; with no folder, no --repo.
  w.slow.clear()
  expect((await ui.find({ key: 'add-name' }))?.text).toBe('')
  await ui.input({ key: 'add-name', text: 'spare-6' })
  await ui.input({ key: 'add-email', text: 'other@example.com' })
  await ui.press({ key: 'add' })
  expect(after(w).slice(2)).toEqual([['add', 'spare-6', 'other@example.com'], ['status', '--json']])
  await ui.unmount()
})

both("Settings' page: switching, the VS Code panel and forgetting the limits run their commands", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-settings')
  const all = (await texts(ui)).join('\n')
  for (const line of [/ask: asks first/, /auto: moves by itself/, /never: waits for the reset/]) expect(all).toMatch(line)
  expect((await ui.find({ key: 'switch' }))?.props.value).toBe('ask')
  expect(await ui.find({ key: 'vscode-off' })).toBeUndefined() // it is off: only Turn on
  await ui.select({ key: 'switch', value: 'auto' })
  expect(after(w)).toEqual([['switch', 'auto'], ['status', '--json']])
  w.status = done(JSON.stringify({ ...STATUS, vscode: true }))
  await ui.press({ key: 'vscode-on' })
  expect(after(w).slice(2)).toEqual([['vscode', 'on', '--yes'], ['status', '--json']])
  expect(await texts(ui)).toContain(RELOAD)
  await ui.press({ key: 'clear' })
  expect(after(w).slice(4)).toEqual([['clear'], ['status', '--json']])
  expect(await texts(ui)).not.toContain(RELOAD)
  await ui.press({ key: 'vscode-off' })
  expect(after(w).slice(6)).toEqual([['vscode', 'off'], ['status', '--json']])
  expect(await texts(ui)).toContain(RELOAD)
  // The list's Settings line says what the rows read last.
  await ui.press({ key: 'back' })
  expect((await ui.find({ key: 'row-settings' }))?.text).toMatch(/Settings \(switching: ask · VS Code panel: on\)$/)
  await ui.unmount()
})

both("Settings' page names the VS Code panel's own mode when vscode.json sets it apart", async ($, on, surface) => {
  const w = world(on)
  const LINE = 'The VS Code panel uses never (set in ~/.longhaul/vscode.json).'
  w.status = done(JSON.stringify({ ...STATUS, panel_switch: 'never' }))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-settings')
  expect(await texts(ui)).toContain(LINE)
  expect((await ui.find({ key: 'switch' }))?.props.value).toBe('ask') // the Select is still the saved mode
  // The same mode, none (an older longhaul.py), one that is not a mode, or
  // no "switch" to differ from (its problem is said instead): no line.
  // Each read again after Forget recorded limits, which reads the rows again.
  const cases = [{ panel_switch: 'ask' }, {}, { panel_switch: 'sometimes' },
    { switch: 'sometimes', switch_problem: 'LONGHAUL_SWITCH is not a mode', panel_switch: 'never' }]
  for (const more of cases) {
    w.status = done(JSON.stringify({ ...STATUS, ...more }))
    await ui.press({ key: 'clear' })
    expect(w.runs.at(-1)).toEqual(['status', '--json'])
    expect((await texts(ui)).filter(t => t.startsWith('The VS Code panel uses'))).toEqual([])
  }
  w.status = done(JSON.stringify({ ...STATUS, switch: 'auto', panel_switch: 'ask' }))
  await ui.press({ key: 'clear' })
  expect(await texts(ui)).toContain('The VS Code panel uses ask (set in ~/.longhaul/vscode.json).')
  await ui.unmount()
})

both("the last action's output: its last ten lines, stdout and stderr, every email masked", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  const lines = Array.from({ length: 14 }, (_, i) => `line ${i + 1}`)
  w.answer.setup = done(`${lines.join('\n')}\nsigned in as first.last@example.com\n`, 1,
    'should be second@example.com, not <other+tag@example.com>\n')
  await open(ui, 'row-personal-1')
  await ui.press({ key: 'signin-personal-1' })
  const shown = await texts(ui)
  expect(shown.some(t => t.startsWith('✘') && /personal-1/.test(t))).toBe(true)
  expect(shown).toContain('signed in as f•••@example.com')
  expect(shown).toContain('should be s•••@example.com, not <o•••@example.com>')
  expect(shown).toContain('line 7')
  expect(shown).not.toContain('line 6')
  expect(shown.join('\n')).not.toMatch(/first\.last|second@|other\+tag/)
  await ui.unmount()
})

both('a status read that fails, or is not JSON, says so with Retry', async ($, on, surface) => {
  const w = world(on)
  w.status = done('', 1, 'Traceback: something broke')
  const { ui } = await opened($, w, surface)
  expect((await texts(ui)).join('\n')).toMatch(/could not read/i)
  expect(await buttons(ui)).toEqual(['retry'])
  w.status = done('not json')
  await ui.press({ key: 'retry' })
  expect((await texts(ui)).join('\n')).toMatch(/not JSON/)
  w.status = done('', 2, 'longhaul: error: unrecognized arguments: --json\n')
  await ui.press({ key: 'retry' })
  expect((await texts(ui)).join('\n')).toMatch(/update Longhaul/)
  w.status = done(JSON.stringify(STATUS))
  await ui.press({ key: 'retry' })
  expect(await buttons(ui)).toEqual(ROWS)
  expect(w.runs.filter(r => r[0] !== 'status')).toEqual([])
  await ui.unmount()
})

both('while an action runs no other is offered, a press then runs nothing, and the pages still open', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-settings')
  w.slow.add('clear')
  await ui.press({ key: 'clear' })
  expect(await buttons(ui)).toEqual(['back'])
  expect(await ui.find({ type: 'Input' })).toBeUndefined()
  expect(await ui.find({ type: 'Select' })).toBeUndefined()
  expect(await texts(ui)).toContain('Forgetting the recorded limits…')
  await expect(ui.press({ key: 'clear' })).rejects.toBeDefined()
  // Elsewhere too: an account's page shows its details, and no action.
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect(await buttons(ui)).toEqual(['back'])
  expect(await texts(ui)).toContain('Forgetting the recorded limits…')
  await expect(ui.press({ key: 'signin-work-2' })).rejects.toBeDefined()
  await ui.press({ key: 'back' })
  await open(ui, 'row-add')
  expect(await ui.find({ type: 'Input' })).toBeUndefined()
  await w.clock.advance(60_000)
  expect(after(w)).toEqual([['clear'], ['status', '--json']])
  expect(await ui.find({ key: 'add-name' })).toBeDefined()
  await ui.unmount()
})

both('a name the check refuses gets no page, and nothing runs with it', async ($, on, surface) => {
  const w = world(on)
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[1], name: '--purge' }, ACCOUNTS[0], ACCOUNTS[0]] }))
  const { ui } = await opened($, w, surface)
  expect((await buttons(ui)).filter(k => k.includes('purge'))).toEqual([])
  expect((await texts(ui)).some(t => /--purge/.test(t))).toBe(true) // drawn, as text
  // A name listed twice has one line to press.
  expect((await buttons(ui)).filter(k => k === 'row-personal-1')).toEqual(['row-personal-1'])
  await open(ui, 'row-personal-1')
  expect(await ui.find({ key: 'signin-personal-1' })).toBeDefined()
  await ui.unmount()
})

both('an account with no folder and times in decimals, and an odd record, are drawn as far as they go', async ($, on, surface) => {
  const w = world(on)
  // S1's record: `folder` is "" when config_dir is not text, and times may be decimals.
  const noFolder = { ...ACCOUNTS[0], name: 'odd-5', folder: '', signed_in: null, as_expected: null, this_chat: false,
    check_failed: "its config_dir is not a folder's path", limited_until: AT + 3600.5,
    five_hour: { used: 99.6, resets_at: AT + 3600.5 } }
  const odd = { name: 'odd-6', only_in: 'everywhere', five_hour: 'full', signed_in: 'yes', sign_in_failed_at: 'today' }
  w.status = done(JSON.stringify({ ...STATUS, accounts: [noFolder, odd, 7], here: 'x', switch: 'sometimes' }))
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'row-odd-6' }))?.text).toMatch(/^ {2}odd-6\s+could not check {3}\(used nowhere\)$/)
  expect((await ui.find({ key: 'row-settings' }))?.text).toMatch(/switching: not known/)
  await open(ui, 'row-odd-5')
  let all = (await texts(ui)).join('\n')
  expect(all).toContain("could not check: its config_dir is not a folder's path")
  expect(all).toContain(`5-hour 100%, resets ${clock(AT + 3600.5)}`)
  expect(all).toContain(`at its limit until ${clock(AT + 3600.5)}`)
  expect(all).not.toMatch(/folder |NaN|undefined/)
  expect(await ui.find({ key: 'signin-odd-5' })).toBeDefined()
  await ui.press({ key: 'back' })
  await open(ui, 'row-odd-6')
  all = (await texts(ui)).join('\n')
  expect(all).toMatch(/odd-6\n/)
  expect(all).toMatch(/may be used nowhere/)
  expect(all).not.toMatch(/NaN|undefined|failed/)
  await ui.unmount()
})

both('a pane drawn before /accounts has read the rows reads them once', async ($, on, surface) => {
  const w = world(on)
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const ui = await $.ui.mount(pane(surface))
  expect((await texts(ui)).join('\n')).toMatch(/Reading Longhaul's accounts/)
  await $.command.run(TYPED)
  await w.clock.settle()
  expect(w.runs).toEqual([['status', '--json']])
  expect(await buttons(ui)).toEqual(ROWS)
  await ui.unmount()
})

test('on the phone: the list as text, no page, and to manage accounts from the computer', async ($, on) => {
  const w = world(on)
  await $.session.start({ cwd: '/scratch/code/x', surface: 'mobile', isInteractive: true })
  await $.command.run(TYPED)
  const ui = await $.ui.mount(pane('mobile'))
  const all = (await texts(ui)).join('\n')
  expect(all).toMatch(/personal-1\s+max · signed in · week 12%/)
  expect(all).toMatch(/NOT signed in/)
  expect(all).toMatch(/manage accounts from the computer/i)
  expect(all).not.toMatch(/Add account|Settings/)
  expect(await buttons(ui)).toEqual([])
  expect(w.runs).toEqual([['status', '--json']])
  await ui.unmount()
})

both('an action left running by a reload shows as not known on its page, and the rows are read again', async ($, on, surface) => {
  const w = world(on)
  // What the module before the reload stored: its own action, running, and the page it ran from.
  let written = false
  on('state.set', (_: any, e: any, next: any) => {
    if (e.key === 'accountsRunning') written = true
    return next(e)
  })
  on('state.get', (_: any, e: any, next: any) => e.key === 'accountsRunning' && !written
    ? { value: { value: { shape: 'accounts-running-2', value: { by: 'before', label: 'Sign in work-2',
      note: 'Signing in work-2', page: 'account:work-2' } }, version: 1 } }
    : next(e))
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const ui = await $.ui.mount(pane(surface))
  await w.clock.settle()
  expect(w.runs).toEqual([['status', '--json']])
  expect((await texts(ui)).some(t => /not known/.test(t))).toBe(false) // not on the list
  await open(ui, 'row-work-2')
  expect((await texts(ui)).some(t => /Sign in work-2/.test(t) && /not known/.test(t))).toBe(true)
  expect(await ui.find({ key: 'signin-work-2' })).toBeDefined()
  await ui.unmount()
})

both('a sign-in that failed in a chat: the line says so, the page says when, and Sign in signs in again', async ($, on, surface) => {
  const w = world(on)
  const failed = AT - 3 * HOUR
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[0], sign_in_failed_at: failed }, ACCOUNTS[1]] }))
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toMatch(/max · sign-in failed · week 12%/)
  expect((await ui.find({ key: 'row-work-2' }))?.text).not.toMatch(/failed/) // null, or not there: an older Longhaul
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain(`Its sign-in failed at ${clock(failed)}.`)
  expect((await ui.find({ key: 'signin-personal-1' }))?.text).toBe('Sign in')
  await ui.press({ key: 'signin-personal-1' })
  expect(after(w)).toEqual([['setup', 'personal-1', '--relogin'], ['status', '--json']])
  await ui.unmount()
})

// An account's folders (the owner, 2026-11-05: "I can't edit the accounts"):
// the Folders part of its page runs `longhaul.py folders NAME ...`, one
// action at a time, and reads the rows again after each.

both("an account's Folders: each folder with Remove, a field to add one, and Anywhere, each running folders", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  const all = await texts(ui)
  for (const t of ['Folders:', '  ~/work', '  ~/clients']) expect(all).toContain(t)
  expect(await buttons(ui)).toEqual(['signin-work-2', 'remove-work-2', 'folder-remove-0', 'folder-remove-1', 'folder-add',
    'folder-anywhere', 'fallback', 'back'])
  expect((await ui.find({ key: 'folder-remove-1' }))?.text).toBe('Remove')
  expect((await ui.find({ key: 'folder-anywhere' }))?.text).toBe('Anywhere')
  expect(await ui.find({ key: 'folder-path' })).toBeDefined()
  await ui.press({ key: 'folder-remove-1' })
  expect(after(w)).toEqual([['folders', 'work-2', '--remove', '~/clients'], ['status', '--json']])
  expect(w.timeouts.slice(1)).toEqual([60_000, 60_000])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Remove folder ~/clients: work-2'))).toBe(true)
  // A folder typed, trimmed, then the field empty again once it worked.
  await ui.input({ key: 'folder-path', text: ' ~/new ' })
  await ui.press({ key: 'folder-add' })
  expect(after(w).slice(2)).toEqual([['folders', 'work-2', '--add', '~/new'], ['status', '--json']])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Add folder ~/new: work-2'))).toBe(true)
  expect((await ui.find({ key: 'folder-path' }))?.text).toBe('')
  await ui.press({ key: 'folder-anywhere' })
  expect(after(w).slice(4)).toEqual([['folders', 'work-2', '--anywhere'], ['status', '--json']])
  await ui.unmount()
})

both('Add folder refuses an empty or relative folder and runs nothing; the field is per page', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain('Folders: anywhere')
  expect(await ui.find({ key: 'folder-anywhere' })).toBeUndefined() // it has no list to drop
  for (const [typed, said] of [['', /Type a folder/], ['work/x', /starts with \/ or ~\//], ['-rf', /starts with/]] as const) {
    await ui.input({ key: 'folder-path', text: typed, kind: 'change' })
    await ui.press({ key: 'folder-add' })
    expect(after(w)).toEqual([])
    expect((await texts(ui)).some(t => t.startsWith('✘') && said.test(t))).toBe(true)
  }
  await ui.input({ key: 'folder-path', text: '~/half', kind: 'change' })
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect((await ui.find({ key: 'folder-path' }))?.text).toBe('') // what was typed for personal-1 is not offered here
  await ui.press({ key: 'folder-add' })
  expect(after(w)).toEqual([])
  await ui.unmount()
})

both('an account kept out of every folder: its line says so, its page names who keeps them and the way out', async ($, on, surface) => {
  const w = world(on)
  const keeper = (name: string) => ({ ...ACCOUNTS[0], name, this_chat: false, only_in: ['/'], kept_out_by: [] })
  const spare = { ...ACCOUNTS[0], name: 'spare-5', this_chat: false, only_in: [], kept_out_by: ['personal-1', 'personal-2'] }
  w.status = done(JSON.stringify({ ...STATUS, accounts: [keeper('personal-1'), keeper('personal-2'), spare] }))
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'row-spare-5' }))?.text).toMatch(/spare-5 .*\(used nowhere\)$/)
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toMatch(/\(everywhere \(its folders include \/\)\)$/)
  await open(ui, 'row-spare-5')
  const all = await texts(ui)
  expect(all).toContain('may be used nowhere for now')
  expect(all).toContain('⚠ personal-1, personal-2 keep every folder, so spare-5 is used nowhere until a folder is added '
    + 'here (or / is removed from theirs).')
  await ui.input({ key: 'folder-path', text: '~/code' })
  await ui.press({ key: 'folder-add' })
  expect(after(w)).toEqual([['folders', 'spare-5', '--add', '~/code'], ['status', '--json']])
  // Kept out by one: worded for one.
  w.status = done(JSON.stringify({ ...STATUS, accounts: [keeper('personal-1'), { ...spare, kept_out_by: ['personal-1'] }] }))
  await ui.press({ key: 'signin-spare-5' })
  expect(await texts(ui)).toContain('⚠ personal-1 keeps every folder, so spare-5 is used nowhere until a folder is added '
    + "here (or / is removed from personal-1's).")
  // The keeper's own page: / with its Remove, and Anywhere.
  await ui.press({ key: 'back' })
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain('  /')
  expect(await texts(ui)).toContain('may be used everywhere (its folders include /)')
  expect(await buttons(ui)).toEqual(['signin-personal-1', 'remove-personal-1', 'folder-remove-0', 'folder-add',
    'folder-anywhere', 'fallback', 'back'])
  await ui.press({ key: 'folder-remove-0' })
  expect(after(w).slice(-2)).toEqual([['folders', 'personal-1', '--remove', '/'], ['status', '--json']])
  await ui.unmount()
})

both('a folders field that is not a list: the page says so, offers Anywhere and no field to add to it', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-spare-3')
  expect(await texts(ui)).toContain('Folders: its repositories field is not a list of absolute paths, so it is used '
    + 'nowhere. Anywhere drops it.')
  expect(await buttons(ui)).toEqual(['signin-spare-3', 'remove-spare-3', 'folder-anywhere', 'fallback', 'back'])
  expect(await ui.find({ key: 'folder-path' })).toBeUndefined()
  await ui.press({ key: 'folder-anywhere' })
  expect(after(w)).toEqual([['folders', 'spare-3', '--anywhere'], ['status', '--json']])
  await ui.unmount()
})

both('a folders run that is refused shows why; a typed email is masked, and passed whole', async ($, on, surface) => {
  const w = world(on)
  w.answer.folders = done('', 2, "longhaul ~/clients is not one of work-2's folders; it may be used only in ~/work\n")
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'folder-remove-1' })
  let all = await texts(ui)
  expect(all.some(t => t.startsWith('✘') && t.includes('Remove folder ~/clients: work-2'))).toBe(true)
  expect(all).toContain("longhaul ~/clients is not one of work-2's folders; it may be used only in ~/work")
  expect(await ui.find({ key: 'folder-remove-1' })).toBeDefined() // the rows read again, and the buttons back
  w.answer.folders = done('Done.')
  await ui.input({ key: 'folder-path', text: '/srv/first.last@example.com' })
  await ui.press({ key: 'folder-add' })
  expect(after(w).slice(-2)).toEqual([['folders', 'work-2', '--add', '/srv/first.last@example.com'], ['status', '--json']])
  all = await texts(ui)
  expect(all.some(t => t.startsWith('✔') && t.includes('Add folder /•••@example.com: work-2'))).toBe(true)
  expect(all.join('\n')).not.toMatch(/first\.last/)
  await ui.unmount()
})

// A fallback account (the owner, 2026-11-06: "A fallback mark"): used only
// when no other account allowed here has room. Its line says so, its page
// says what it is, and a button runs `longhaul.py fallback NAME on|off`.

both('a fallback: its line and page say so, and the button runs fallback NAME off or on', async ($, on, surface) => {
  const w = world(on)
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[0], fallback: true }, ...ACCOUNTS.slice(1)] }))
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toBe('  personal-1  max · signed in · week 12%   '
    + '(this chat · fallback)')
  expect((await ui.find({ key: 'row-work-2' }))?.text).toMatch(/\(only in ~\/work, ~\/clients\)$/) // normal: no word
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain('Fallback: used only when no other account allowed here has room')
  expect((await ui.find({ key: 'fallback' }))?.text).toBe('Use as a normal account')
  w.status = done(JSON.stringify(STATUS)) // what the read after it finds
  await ui.press({ key: 'fallback' })
  expect(after(w)).toEqual([['fallback', 'personal-1', 'off'], ['status', '--json']])
  expect(w.timeouts.slice(1)).toEqual([60_000, 60_000])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Use as a normal account: personal-1')))
    .toBe(true)
  // The rows read again: a normal account now, with the button the other way.
  expect(await texts(ui)).toContain('Normal account')
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect(await texts(ui)).toContain('Normal account')
  expect((await ui.find({ key: 'fallback' }))?.text).toBe('Use as fallback')
  await ui.press({ key: 'fallback' })
  expect(after(w).slice(2)).toEqual([['fallback', 'work-2', 'on'], ['status', '--json']])
  await ui.unmount()
})

both('no fallback button while Remove waits for its yes, and a refused run shows why', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  expect(await ui.find({ key: 'fallback' })).toBeUndefined()
  await ui.press({ key: 'remove-no' })
  w.answer.fallback = done('', 2, 'longhaul no account called work-2; the accounts are personal-1\n')
  await ui.press({ key: 'fallback' })
  const all = await texts(ui)
  expect(all.some(t => t.startsWith('✘') && t.includes('Use as fallback: work-2'))).toBe(true)
  expect(all).toContain('longhaul no account called work-2; the accounts are personal-1')
  await ui.unmount()
})

both('an older Longhaul that does not say: no fallback line and no button', async ($, on, surface) => {
  const w = world(on)
  const older = ACCOUNTS.map(({ fallback: _, ...a }) => a)
  w.status = done(JSON.stringify({ ...STATUS, accounts: older }))
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'row-personal-1' }))?.text)
    .toBe('  personal-1  max · signed in · week 12%   (this chat)')
  await open(ui, 'row-personal-1')
  expect(await buttons(ui)).toEqual(['signin-personal-1', 'remove-personal-1', 'folder-add', 'back'])
  const all = await texts(ui)
  expect(all.some(t => /fallback|Normal account/i.test(t))).toBe(false)
  await ui.unmount()
})

// The question (the keep-working plan's K1): asked by the relay in its
// file, shown at the top of the pane while its relay runs, answered by
// `longhaul.py answer CHAT QID CHOICE`.

const MOVE_SAYS = `personal-1 hit its weekly limit (resets ${clock(AT + 38 * HOUR)}). Move this chat to work-2 and carry on?`

both('a move question at the top of the list, its two answers, and Move runs answer with this chat and its id', async ($, on, surface) => {
  const w = world(on)
  write(w, QUESTION_FILE, QUESTION)
  w.answer.answer = done('Answered move: the chat acts on it within a few seconds.\n')
  const { ui } = await opened($, w, surface)
  expect((await texts(ui))[0]).toBe(MOVE_SAYS)
  expect((await ui.find({ key: 'answer-move' }))?.text).toBe('Move and carry on')
  expect((await ui.find({ key: 'answer-wait' }))?.text).toBe('Wait for the reset')
  expect((await buttons(ui)).slice(0, 2)).toEqual(['answer-move', 'answer-wait'])
  // The focus starts on nothing while a question is open: a stray Enter answers nothing.
  expect((await ui.findAll({ type: 'Button' })).filter((b: any) => b.props.autoFocus)).toEqual([])
  // /accounts opened the pane itself: no second open, no pop-up.
  expect(w.opened).toEqual([OPEN])
  expect(w.toasts).toEqual([])
  await ui.press({ key: 'answer-move' })
  expect(after(w)).toEqual([['answer', SID, 'q-0123456789ab', 'move']])
  expect(w.timeouts[1]).toBe(60_000)
  expect(await texts(ui)).toContain('✔ Answered move: the chat acts on it within a few seconds.')
  expect(await ui.find({ key: 'answer-wait' })).toBeUndefined()
  // The relay takes the answer and removes the question: it goes at the next look.
  write(w, QUESTION_FILE, null)
  await w.clock.advance(3000)
  expect((await texts(ui)).join('\n')).not.toMatch(/limit \(resets|Answered/)
  expect(await buttons(ui)).toEqual(ROWS)
  await ui.unmount()
})

both('the question shows on every page, and Wait runs answer wait', async ($, on, surface) => {
  const w = world(on)
  write(w, QUESTION_FILE, QUESTION)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-settings')
  expect((await texts(ui))[0]).toBe(MOVE_SAYS)
  await ui.press({ key: 'answer-wait' })
  expect(after(w)).toEqual([['answer', SID, 'q-0123456789ab', 'wait']])
  await ui.unmount()
})

both('every account at its limit: the first reset, and the same account worded as its own reset', async ($, on, surface) => {
  const w = world(on)
  write(w, QUESTION_FILE, FIRST)
  const { ui } = await opened($, w, surface)
  const at = clock(AT + 2 * HOUR)
  expect((await texts(ui))[0]).toBe(`Every account is at its limit. work-2 resets first, at ${at}. Carry on there then?`)
  // Yes names the carry-on's own minute, the relay's carries_on_at: the
  // reset plus its margin, as the wait's until will be.
  const later = clock(AT + 2 * HOUR + 60)
  expect(later).not.toBe(at)
  expect((await ui.find({ key: 'answer-yes' }))?.text).toBe(`Yes, carry on at ${later}`)
  expect((await ui.find({ key: 'answer-no' }))?.text).toBe("No, I'll come back")
  await ui.press({ key: 'answer-no' })
  expect(after(w)).toEqual([['answer', SID, 'q-ba9876543210', 'no']])
  // A new question, for the account this chat is on: it resets first itself.
  write(w, QUESTION_FILE, { ...FIRST, id: 'q-aaaaaaaaaaaa', to: 'personal-1' })
  await w.clock.advance(3000)
  expect((await texts(ui))[0]).toBe(`personal-1 resets at ${at}. Carry on then?`)
  await ui.press({ key: 'answer-yes' })
  expect(after(w).slice(-1)).toEqual([['answer', SID, 'q-aaaaaaaaaaaa', 'yes']])
  await ui.unmount()
})

both("the yes names the minute the carry-on then shows; an older relay's question names the reset", async ($, on, surface) => {
  const w = world(on)
  write(w, QUESTION_FILE, FIRST)
  const { ui } = await opened($, w, surface)
  const yes = (await ui.find({ key: 'answer-yes' }))?.text
  await ui.press({ key: 'answer-yes' })
  // The relay acts on the yes: the question goes, and its wait's until is the question's carries_on_at.
  write(w, QUESTION_FILE, null)
  write(w, WAIT_FILE, { ...WAIT, until: FIRST.carries_on_at, account: 'work-2' })
  await w.clock.advance(3000)
  expect(yes).toBe(`Yes, carry on at ${clock(FIRST.carries_on_at)}`)
  expect(await texts(ui)).toContain(`This chat carries on at ${clock(FIRST.carries_on_at)} on work-2.`)
  // A relay older than carries_on_at: the reset, as before.
  const { carries_on_at: _, ...older } = FIRST
  write(w, WAIT_FILE, null)
  write(w, QUESTION_FILE, { ...older, id: 'q-cccccccccccc' })
  await w.clock.advance(3000)
  expect((await ui.find({ key: 'answer-yes' }))?.text).toBe(`Yes, carry on at ${clock(AT + 2 * HOUR)}`)
  await ui.unmount()
})

both('an answer refused says why in one sentence, and the answers stay', async ($, on, surface) => {
  const w = world(on)
  write(w, QUESTION_FILE, QUESTION)
  w.answer.answer = done('', 2, 'longhaul answer: the chat that asked has closed, so no one is left to act on the answer.\n')
  const { ui } = await opened($, w, surface)
  await ui.press({ key: 'answer-move' })
  expect(await texts(ui)).toContain('✘ longhaul answer: the chat that asked has closed, so no one is left to act on the answer.')
  expect(await ui.find({ key: 'answer-move' })).toBeDefined()
  await ui.unmount()
})

both("a question whose relay has ended, another chat's, or one written oddly shows nothing", async ($, on, surface) => {
  const w = world(on)
  w.alive.clear() // the relay was killed: its question stayed behind
  write(w, QUESTION_FILE, QUESTION)
  const { ui } = await opened($, w, surface)
  expect(await buttons(ui)).toEqual(ROWS)
  w.alive.add(RELAY)
  for (const odd of [{ ...QUESTION, chat: 'another-chat' }, { ...QUESTION, id: '--purge' }, { ...QUESTION, choices: ['move'] },
                     { ...QUESTION, kind: 'first_reset' }, { ...QUESTION, relay_pid: 'x' }, [QUESTION], 'not json']) {
    write(w, QUESTION_FILE, odd)
    await w.clock.advance(3000)
    expect(await buttons(ui)).toEqual(ROWS)
  }
  // Another chat's file is never read: only this chat's name is looked for.
  write(w, QUESTION_FILE, null)
  write(w, `${HOME}/questions/another-chat.json`, { ...QUESTION, chat: 'another-chat' })
  await w.clock.advance(3000)
  expect(await buttons(ui)).toEqual(ROWS)
  expect(after(w)).toEqual([])
  expect(w.toasts).toEqual([])
  await ui.unmount()
})

both('every 3 s it looks: a new question opens the pane, with one pop-up per question', async ($, on, surface) => {
  const w = world(on)
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  await w.clock.advance(3000)
  expect(w.opened).toEqual([])
  write(w, QUESTION_FILE, QUESTION)
  await w.clock.advance(3000)
  expect(w.opened).toEqual([OPEN])
  expect(w.toasts).toEqual(['Longhaul: personal-1 hit its weekly limit. A question waits: type /accounts if it did not open.'])
  await w.clock.advance(9000)
  expect(w.opened.length).toBe(1)
  expect(w.toasts.length).toBe(1)
  // The pane, drawn now, has it at the top.
  const ui = await $.ui.mount(pane(surface))
  await w.clock.settle()
  expect((await texts(ui))[0]).toBe(MOVE_SAYS)
  // The next question is another: it opens and pops up once again.
  write(w, QUESTION_FILE, FIRST)
  await w.clock.advance(3000)
  expect(w.opened.length).toBe(2)
  expect(w.toasts.slice(1)).toEqual(['Longhaul: every account is at its limit. A question waits: type /accounts if it did not open.'])
  await ui.unmount()
})

both('a carry-on scheduled for a reset shows on the list until its file goes', async ($, on, surface) => {
  const w = world(on)
  write(w, WAIT_FILE, WAIT)
  const { ui } = await opened($, w, surface)
  expect(await texts(ui)).toContain(`This chat carries on at ${clock(AT + 2 * HOUR)} on personal-1.`)
  write(w, WAIT_FILE, null)
  await w.clock.advance(3000)
  expect((await texts(ui)).some(t => /carries on/.test(t))).toBe(false)
  await ui.unmount()
})

test('on the phone the question shows, and its answers work', async ($, on) => {
  const w = world(on)
  write(w, QUESTION_FILE, QUESTION)
  await $.session.start({ cwd: '/scratch/code/x', surface: 'mobile', isInteractive: true })
  await $.command.run(TYPED)
  const ui = await $.ui.mount(pane('mobile'))
  expect((await texts(ui))[0]).toBe(MOVE_SAYS)
  expect(await buttons(ui)).toEqual(['answer-move', 'answer-wait'])
  await ui.press({ key: 'answer-wait' })
  expect(after(w)).toEqual([['answer', SID, 'q-0123456789ab', 'wait']])
  await ui.unmount()
})
