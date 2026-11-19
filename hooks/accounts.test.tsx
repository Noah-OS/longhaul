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
  { name: 'personal-1', email: 'a…@example.com', signed_in: true, as_expected: true, check_failed: null, plan: 'max',
    limited_until: null, five_hour: { used: 40, resets_at: AT + 2 * HOUR }, week: { used: 12, resets_at: AT + 50 * HOUR },
    only_in: [], fallback: false, folder: '~/.claude-personal-1', this_chat: true },
  { name: 'work-2', email: 'b…@example.com', signed_in: false, as_expected: null, check_failed: null, plan: 'pro',
    limited_until: AT + 3 * HOUR, five_hour: null, week: null, only_in: ['~/work', '~/clients'], fallback: false,
    folder: '~/.claude-work-2', this_chat: false },
  { name: 'spare-3', email: 'c…@example.com', signed_in: true, as_expected: false, check_failed: null, plan: null,
    limited_until: null, five_hour: null, week: null, only_in: null, fallback: false, folder: '~/.claude-spare-3',
    this_chat: false },
  { name: 'team-4', email: null, signed_in: null, as_expected: null, check_failed: 'the check timed out', plan: null,
    limited_until: null, five_hour: null, week: null, only_in: [], fallback: false, folder: '~/.claude-team-4',
    this_chat: false },
]
const STATUS = { accounts: ACCOUNTS, listed: true, here: { folder: '~/code/x', may_use: ['personal-1', 'team-4'] },
  switch: 'ask', switch_problem: null, vscode: false, panel_paused: false, problem: null }
// Under a list row's text (M8): the mark and a space, the 10 of personal-1, two spaces.
const UNDER = ' '.repeat(14)
const ROWS = ['row-personal-1', 'row-work-2', 'row-spare-3', 'row-team-4', 'row-add', 'row-activity', 'row-settings',
  'row-troubleshoot']
// What Remove asks, and Remove and delete its sign-in folder… asks then:
// what `remove NAME --delete-folder --dry-run --json` says it would take,
// in delete_lines()'s words (the UX review's M4, §4.7 string 7).
const REMOVE_WORK_2 = "Remove work-2 from Longhaul's list? Its sign-in folder ~/.claude-work-2 stays, and its sign-in "
  + 'with it. Your project folders are never touched.'
const DELETE_WORK_2 = "Delete work-2's sign-in folder for good?"
const UNTOUCHED = 'Your project folders are never touched.'
const DELETE_LINES = (name: string) => [
  `${name}'s sign-in folder ~/.claude-${name} (412 MB) goes for good: ${name}'s sign-in. Its chat history is in `
    + '~/.claude/projects, which it only links to, and stays.',
  `Longhaul does not sign ${name} out: on a Mac, the sign-in Claude Code keeps for this folder in the Keychain may remain.`]
// What it said before it could read that (an older longhaul.py, or one that failed).
const DELETE_VAGUE = 'Remove work-2 and delete its sign-in folder ~/.claude-work-2? Its sign-in and the chat history '
  + 'kept in it go for good. Your project folders are never touched.'
// What `folders NAME ... --dry-run --json` says a change would do (folders_preview()).
const PREVIEW = { changes: ['would remove ~/clients from work-2\'s folders'], notes: [],
  effects: ['Then work-2 may be used only in ~/work.',
    '~/clients would no longer be reserved for work-2: a chat there on personal-1 or team-4 would go through again.',
    'Chats it would change (running now, or started in the last 7 days):',
    '  chat 5ca8a797 in ~/clients/api on personal-1 (started 09:00): its messages would go through again.'],
  stops: 0, problem: null }
const STOPS = { ...PREVIEW, changes: ['would add ~/new to work-2\'s folders'],
  effects: ['Then work-2 may be used only in ~/work, ~/clients, ~/new.',
    '~/new would be reserved for work-2: a chat there on personal-1 or team-4 would stop at its next message.',
    'Chats it would change (running now, or started in the last 7 days):',
    '  chat 1b2c3d4e in ~/new on personal-1 (running now): its next message would stop, as ~/new would be reserved '
      + 'for work-2.'],
  stops: 1 }
// The dry run's answer from its arguments, and the change's own.
function dryOr(dry: (rest: string[]) => any, real: Ran = done('Done.')): (rest: string[]) => Ran {
  return rest => {
    if (!rest.includes('--dry-run')) return real
    const said = dry(rest)
    return done(JSON.stringify(said), said?.refused || said?.problem ? 2 : 0)
  }
}
const DELETE_DRY = (rest: string[]) => ({ name: rest[1], folder: `~/.claude-${rest[1]}`, there: true, size: '412 MB',
  conversations: 0, linked: '~/.claude/projects', lines: DELETE_LINES(rest[1]!), refused: null, problem: null })

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
  runs: string[][] // each longhaul.py run's arguments, after the script; the quick reads apart, in quick
  timeouts: number[] // each run's timeoutMs, in the same order
  envs: (Record<string, string> | null)[] // each run's env, in the same order
  // Each `status --json --no-check` (M7), which each read of the rows runs
  // beside the full one: kept apart, so runs reads as it did before it.
  quick: { rest: string[]; timeoutMs: number; env: Record<string, string> | null }[]
  opened: any[] // each $.ui.open's argument
  closed: any[] // each ui.close that reached the engine: the pane closed
  focused: string[] // each element $.ui.focus was asked to move the ring to
  toasts: string[]
  files: Record<string, { text: string; at: number }> // LONGHAUL_HOME's files, by path
  alive: Set<number> // the pids `kill -0` finds
  status: Ran // what `status --json` answers
  fails: string | null // why `status --json` does not answer at all (it ran past its time), or null
  // What `status --json --no-check` answers; null: `status`'s, with no sign-in checked (unchecked()).
  noCheck: Ran | null
  // What each other command answers, by its first word: as it is, or from its arguments.
  answer: Record<string, Ran | ((rest: string[]) => Ran)>
  slow: Set<string> // commands that answer only once the test moves the clock on; "quick": the quick read
  placed: boolean
  clock: ReturnType<typeof mock.clock>
}

const done = (stdout = '', exitCode = 0, stderr = ''): Ran => ({ exitCode, stdout, stderr })

// What `status --json --no-check` answers where `status --json` answers
// `full`: its record with no sign-in checked, as longhaul.py's NOT_CHECKED
// makes it (null, no reason, no plan; use_here trusts it), "checked": false;
// or its failure, as it is.
function unchecked(full: Ran): Ran {
  let s: any = null
  try { s = JSON.parse(full.stdout) } catch {}
  if (!s || typeof s !== 'object' || !Array.isArray(s.accounts)) return full
  const accounts = s.accounts.map((a: any) => (a && typeof a === 'object'
    ? { ...a, signed_in: null, as_expected: null, check_failed: null, plan: null,
      ...(a.use_here === 'not_signed_in' ? { use_here: 'ok' } : {}) } : a))
  return { ...full, stdout: JSON.stringify({ ...s, accounts, checked: false }) }
}

// /accounts as the user types it at the prompt of a fullscreen terminal.
const TYPED = { command: 'accounts', args: '', origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 160 } } as const

// The engine beneath the plugins, from memory: programs, files, panes, the
// clock, and what display.ts asks at a session's start.
function world(on: any): World {
  const w: World = { runs: [], timeouts: [], envs: [], quick: [], opened: [], closed: [], focused: [], toasts: [], files: {},
    alive: new Set([RELAY]), status: done(JSON.stringify(STATUS)), fails: null, noCheck: null,
    answer: { remove: dryOr(DELETE_DRY), folders: dryOr(() => PREVIEW) },
    slow: new Set(), placed: true, clock: mock.clock(on, { now: NOW }) }
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
    if (rest[0] === 'status' && rest.includes('--no-check')) {
      w.quick.push({ rest, timeoutMs: e.init?.timeoutMs, env: e.init?.env ?? null })
      const ran = w.noCheck ?? unchecked(w.status) // as the files stood when it started
      if (w.slow.has('quick')) await w.clock.sleep(60_000)
      return { value: { ...ran, isStdoutTruncated: false, isStderrTruncated: false } }
    }
    w.runs.push(rest)
    w.timeouts.push(e.init?.timeoutMs)
    w.envs.push(e.init?.env ?? null)
    const read = rest[0] === 'status' ? { ran: w.status, fails: w.fails } : null // as the files stood when it started
    if (w.slow.has(rest[0]!)) await w.clock.sleep(60_000)
    if (read?.fails) return { deny: read.fails }
    const said = w.answer[rest[0]!]
    const ran = read ? read.ran : typeof said === 'function' ? said(rest) : said ?? done('Done.')
    return { value: { ...ran, isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return w
}

// A file in LONGHAUL_HOME, written now (a later write has a later time), or gone.
function write(w: World, path: string, value: unknown | null): void {
  if (value === null) delete w.files[path]
  else w.files[path] = { text: JSON.stringify(value), at: (w.files[path]?.at ?? 0) + 1 }
}

// The pane as the engine asks for it, on the surface a test names, its
// body `columns` across: 80 unless a test says, the width the list's rows
// are laid out for (M8).
function pane(surface: 'terminal' | 'desktop' | 'vscode' | 'mobile', columns = 80) {
  return { plugin: 'longhaul', component: 'Pane', requestId: PANE_ID, surface,
    props: { title: 'Longhaul accounts', isFocused: true, bodyColumns: columns, placement: 'dock',
      scroll: { offset: 0, bodyRows: 40 }, view: {} } } as const
}

// A session on that surface, /accounts typed, and the pane it opened.
async function opened($: any, w: World, surface: 'terminal' | 'vscode', columns = 80) {
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const said = await $.command.run(TYPED)
  const ui = await $.ui.mount(pane(surface, columns))
  return { said, ui }
}

// The pane as a terminal lays it out, line by line, as far as these tests
// need (M8, the UX review's §3.7 "Width"): the kit checks the tree, never a
// surface's paint, so the columns are counted here. A column Box stacks its
// children, gap lines between; a row Box sets them side by side, gap
// spaces between; a Text, or a plain Button, is its text; another Button is
// its label as the terminal draws it, "[ Remove ]"; an Input its label and text.
function laid(node: any): string[] {
  if (typeof node === 'string') return node.split('\n')
  if (!node || typeof node !== 'object') return []
  const kids: any[] = Array.isArray(node.children) ? node.children.flat(Infinity) : []
  const text = (n: any): string => (typeof n === 'string' ? n : Array.isArray(n?.children)
    ? n.children.flat(Infinity).map(text).join('') : '')
  if (node.type === 'Text') return text(node).split('\n')
  if (node.type === 'Button')
    return [node.props?.plain ? (kids.length ? text(node) : String(node.props?.label ?? ''))
      : `[ ${String(node.props?.label ?? text(node))} ]`]
  if (node.type === 'Input') return [`${node.props?.label ?? ''}${node.props?.value || node.props?.placeholder || ''}`]
  const parts = kids.map(laid).filter(l => l.length)
  const gap = Number(node.props?.gap ?? 0)
  if (node.props?.flexDirection === 'row') {
    const widths = parts.map(l => Math.max(...l.map(s => s.length)))
    const tall = Math.max(0, ...parts.map(l => l.length))
    return Array.from({ length: tall }, (_, i) => parts.map((l, j) => (l[i] ?? '').padEnd(widths[j]!))
      .join(' '.repeat(gap)).trimEnd())
  }
  return parts.flatMap((l, i) => (i && gap ? [...Array<string>(gap).fill(''), ...l] : l))
}

// Every element of the drawn tree, outermost first.
function nodes(node: any): any[] {
  if (!node || typeof node !== 'object') return []
  const kids: any[] = Array.isArray(node.children) ? node.children.flat(Infinity) : []
  return [node, ...kids.flatMap(nodes)]
}

// A list row as drawn, its lines joined by newlines: its Button's line,
// then the lines under it (the row's Box, whose first child is the Button).
async function rowOf(ui: any, key: string): Promise<string | undefined> {
  const all = nodes(await ui.drawn())
  const box = all.find(n => n.type === 'Box' && n.children?.[0]?.type === 'Button' && n.children[0].props?.key === key)
  const button = all.find(n => n.type === 'Button' && n.props?.key === key)
  return box ? laid(box).join('\n') : button ? laid(button).join('\n') : undefined
}

// The lines of the pane wider than `columns`: every line, or the rows of
// controls alone (each Box set out as a row), where the text around them is prose.
async function over(ui: any, columns: number, rowsOnly = false): Promise<string[]> {
  const tree = await ui.drawn()
  const lines = rowsOnly ? nodes(tree).filter(n => n.type === 'Box' && n.props?.flexDirection === 'row').flatMap(laid)
    : laid(tree)
  return lines.filter(l => l.length > columns)
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
  // Beside it, the quick read the rows are drawn from first (M7), for this chat too.
  expect(w.quick).toEqual([{ rest: ['status', '--json', '--no-check'], timeoutMs: 15_000, env: { CLAUDE_CODE_SESSION_ID: SID } }])
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
  const row = (key: string) => rowOf(ui, key)
  // Where it may run, and its role, on a line of their own under its text (M8).
  expect(await row('row-personal-1')).toBe(`  personal-1  max · signed in · 5h 40% · week 12%\n${UNDER}this chat`)
  expect(await row('row-work-2')).toBe(`  work-2      pro · NOT signed in · limit until ${clock(AT + 3 * HOUR)} (in 3 h)\n`
    + `${UNDER}only in ~/work, ~/clients`)
  expect(await row('row-spare-3')).toBe(`  spare-3     signed in as someone else\n${UNDER}used nowhere`)
  expect(await row('row-team-4')).toBe('  team-4      could not check')
  expect(await row('row-add')).toBe('  + Add account')
  expect(await row('row-activity')).toBe('  Activity: what Longhaul did, and why')
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

// M8, the UX review's §3.7 "Width": a list row with every part filled ran
// to about 110 columns and wrapped inside a phrase. Long names, three
// folders, a limit with its time and when it was seen, a sign-in gone wrong.
const LONG = [
  { name: 'personal-account', email: 'p…@example.com', signed_in: true, as_expected: true, check_failed: null,
    plan: 'max', limited_until: null, reading_at: AT - 600, five_hour: { used: 40, resets_at: AT + 2 * HOUR },
    week: { used: 12, resets_at: AT + 50 * HOUR }, only_in: [], fallback: false, use_here: 'this_chat',
    folder: '~/.claude-personal-account', this_chat: true },
  { name: 'acme-client-work', email: 'a…@example.com', signed_in: false, as_expected: null, check_failed: null,
    plan: 'pro', limited_until: AT + 52 * HOUR, reading_at: AT - 13 * HOUR, five_hour: null, week: null,
    only_in: ['~/clients/acme-api', '~/clients/acme-web', '~/clients/acme-docs'], fallback: true, use_here: 'limited',
    folder: '~/.claude-acme-client-work', this_chat: false },
  { name: 'globex-consult', email: 'g…@example.com', signed_in: true, as_expected: false, check_failed: null,
    plan: 'team', limited_until: AT + 5 * HOUR, reading_at: AT - HOUR, five_hour: { used: 100, resets_at: AT + 5 * HOUR },
    week: { used: 64, resets_at: AT + 90 * HOUR }, only_in: ['~/clients/globex-app', '~/clients/globex-data'],
    fallback: false, use_here: 'not_allowed', folder: '~/.claude-globex-consult', this_chat: false },
  { name: 'spare-account', email: 's…@example.com', signed_in: true, as_expected: true, check_failed: null,
    sign_in_failed_at: AT - 2 * HOUR, plan: 'max', limited_until: null, reading_at: AT - 300,
    five_hour: { used: 100, resets_at: AT + HOUR }, week: { used: 95, resets_at: AT + 30 * HOUR }, only_in: [],
    fallback: false, use_here: 'ok', folder: '~/.claude-spare-account', this_chat: false },
]
const LONG_STATUS = { ...STATUS, accounts: LONG, here: { folder: '~/code/longhaul', may_use: ['personal-account', 'spare-account'] } }
const PAD = ' '.repeat(20) // under a row's text: the mark, a space, the 16 of the longest name, two spaces

for (const columns of [80, 60])
  both(`each list row fits a pane ${columns} columns across, broken only between its phrases`, async ($, on, surface) => {
    const w = world(on)
    w.status = done(JSON.stringify(LONG_STATUS))
    const { ui } = await opened($, w, surface, columns)
    const rows = await Promise.all((await buttons(ui)).map(key => rowOf(ui, key)))
    expect(rows.flatMap(r => (r ?? '').split('\n')).filter(l => l.length > columns)).toEqual([])
    if (columns === 80) expect(await over(ui, 80)).toEqual([]) // and every other line of the list
    // Where it may run and its role on a line of their own, under its text; three folders too many to
    // name, by their number (its page names them).
    const limit = `limit until ${clock(AT + 52 * HOUR)} (in 2 d 4 h)`, seen = `seen ${clock(AT - 13 * HOUR)}`
    expect(await rowOf(ui, 'row-acme-client-work')).toBe(columns === 80
      ? [`  acme-client-work  pro · NOT signed in`, `${PAD}${limit}, ${seen}`, `${PAD}fallback · only in 3 folders`].join('\n')
      : [`  acme-client-work  pro · NOT signed in`, `${PAD}${limit}`, `${PAD}${seen}`, `${PAD}fallback · only in 3 folders`]
        .join('\n'))
    expect(await rowOf(ui, 'row-globex-consult')).toBe([`  globex-consult    team · signed in as someone else`,
      `${PAD}limit until ${clock(AT + 5 * HOUR)} (in 5 h), seen ${clock(AT - HOUR)}`,
      `${PAD}${columns === 80 ? 'only in ~/clients/globex-app, ~/clients/globex-data' : 'only in 2 folders'}`].join('\n'))
    expect(await rowOf(ui, 'row-personal-account')).toBe(columns === 80
      ? `  personal-account  max · signed in · room now · 5h 40% · week 12%\n${PAD}this chat`
      : `  personal-account  max · signed in · room now · 5h 40%\n${PAD}week 12%\n${PAD}this chat`)
    // The account page keeps the folders whole, and its rows of buttons fit 80 columns: its actions,
    // Remove's question, the delete's, and Remove all folder limits'.
    for (const a of LONG) {
      await open(ui, `row-${a.name}`)
      if (columns === 80) expect(await over(ui, 80, true)).toEqual([])
      for (const path of a.only_in) expect(await texts(ui)).toContain(`  ${path}`)
      if (columns === 80) {
        await ui.press({ key: `remove-${a.name}` })
        expect(await over(ui, 80, true)).toEqual([])
        await ui.press({ key: 'remove-folder' })
        expect(await over(ui, 80, true)).toEqual([])
        await ui.press({ key: 'remove-no' })
        if (a.only_in.length) {
          await ui.press({ key: 'folder-anywhere' })
          expect(await over(ui, 80, true)).toEqual([])
          await ui.press({ key: 'anywhere-no' })
        }
      }
      await ui.press({ key: 'back' })
    }
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
                   `week 12%, resets ${clock(AT + 50 * HOUR)}`, 'may be used in any folder no other account reserves', 'a…@example.com',
                   'sign-in folder ~/.claude-personal-1'])
    expect(all).toContain(t)
  expect(await buttons(ui)).toEqual(['signin-personal-1', 'remove-personal-1', 'folder-add', 'fallback', 'back'])
  expect(all.includes('Esc to go back')).toBe(surface === 'terminal')
  await ui.press({ key: 'back' })
  expect(await buttons(ui)).toEqual(ROWS)
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toMatch(/^› personal-1/)
  // The others, each on its own page.
  await open(ui, 'row-work-2')
  all = await texts(ui)
  for (const t of ['pro · NOT signed in', `limit until ${clock(AT + 3 * HOUR)} (in 3 h)`,
                   'may be used only in ~/work, ~/clients', 'b…@example.com'])
    expect(all).toContain(t)
  await ui.press({ key: 'back' })
  expect((await ui.find({ key: 'row-work-2' }))?.text).toMatch(/^› work-2/)
  await open(ui, 'row-spare-3')
  all = await texts(ui)
  expect(all).toContain('signed in as someone else')
  expect(all).toContain('may be used nowhere')
  expect(all).toContain('c…@example.com')
  await ui.press({ key: 'back' })
  await open(ui, 'row-team-4')
  expect(await texts(ui)).toContain('could not check: the check timed out')
  expect(w.runs).toEqual([['status', '--json']]) // opening and going back run nothing
  await ui.unmount()
})

// The ring a control draws itself to: its autoFocus. (The plugin's own
// $.ui.focus is the engine's to grant, to a pane holding the keys, and this
// test's pane holds none, so the drawing is what is checked.)
async function ringed(ui: any): Promise<string[]> {
  return [...(await ui.findAll({ type: 'Button' })), ...(await ui.findAll({ type: 'Input' }))]
    .filter((b: any) => b.props.autoFocus).map((b: any) => b.key)
}

both("an account's page opens with the focus on its first harmless control, never Sign in", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  // A double Enter on the list: the second lands on the folder field, where Enter only keeps what is typed.
  await open(ui, 'row-personal-1')
  expect(await ringed(ui)).toEqual(['folder-path'])
  await ui.input({ key: 'folder-path', text: '', kind: 'submit' })
  expect(after(w)).toEqual([])
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect(await ringed(ui)).toEqual(['folder-path'])
  await ui.press({ key: 'back' })
  // No folder field (its folders field is not a list): Back.
  await open(ui, 'row-spare-3')
  expect(await ringed(ui)).toEqual(['back'])
  // Remove asks with the ring on Cancel, so another Enter cancels.
  await ui.press({ key: 'remove-spare-3' })
  expect(await ringed(ui)).toEqual(['remove-no'])
  await ui.press({ key: 'remove-folder' })
  expect(await ringed(ui)).toEqual(['remove-no'])
  await ui.press({ key: 'remove-no' })
  expect(after(w)).toEqual([['remove', 'spare-3', '--delete-folder', '--dry-run', '--json']]) // a read: nothing changed
  await ui.unmount()
})

both('Back cancels a Remove waiting for its yes before it leaves the page', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  expect(await texts(ui)).toContain(REMOVE_WORK_2)
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

both('Remove with its sign-in folder asks twice, the second time with what it would take, and runs remove --delete-folder --yes; Cancel runs nothing', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  expect(after(w)).toEqual([])
  // The second question reads what the delete would take first, and changes nothing (M4).
  await ui.press({ key: 'remove-folder' })
  expect(after(w)).toEqual([['remove', 'work-2', '--delete-folder', '--dry-run', '--json']])
  const all = await texts(ui)
  for (const t of [DELETE_WORK_2, ...DELETE_LINES('work-2'), UNTOUCHED]) expect(all).toContain(t)
  expect(all).not.toContain(DELETE_VAGUE)
  expect(all.indexOf(DELETE_WORK_2)).toBeLessThan(all.indexOf(DELETE_LINES('work-2')[0]!))
  expect(await ui.find({ key: 'remove-yes' })).toBeUndefined()
  expect(await ringed(ui)).toEqual(['remove-no'])
  await ui.press({ key: 'remove-no' })
  expect(after(w).length).toBe(1)
  expect(await ui.find({ key: 'remove-folder-yes' })).toBeUndefined()
  expect(await texts(ui)).not.toContain(DELETE_LINES('work-2')[0])
  await ui.press({ key: 'remove-work-2' })
  await ui.press({ key: 'remove-folder' })
  w.status = done(JSON.stringify({ ...STATUS, accounts: ACCOUNTS.filter(a => a.name !== 'work-2') }))
  await ui.press({ key: 'remove-folder-yes' })
  expect(after(w).slice(2)).toEqual([['remove', 'work-2', '--delete-folder', '--yes'], ['status', '--json']])
  expect(await buttons(ui)).toEqual(ROWS.filter(k => k !== 'row-work-2'))
  await ui.unmount()
})

both('the second Remove question says why the sign-in folder would not go, and then offers only Cancel', async ($, on, surface) => {
  const w = world(on)
  const why = '1 chat still runs on work-2: pid 4242 (a VS Code tab) in ~/work; close it first'
  w.answer.remove = dryOr(rest => ({ ...DELETE_DRY(rest), there: false, size: null, conversations: null, linked: null,
    refused: why, lines: [`It would not be deleted: ${why}.`] }))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  // While it reads, it says so and offers nothing.
  w.slow.add('remove')
  await ui.press({ key: 'remove-folder' })
  expect(await texts(ui)).toContain("Reading what deleting work-2's sign-in folder would take…")
  expect(await ui.find({ key: 'remove-no' })).toBeUndefined()
  await w.clock.advance(60_000)
  w.slow.delete('remove')
  const all = await texts(ui)
  expect(all).toContain(DELETE_WORK_2)
  expect(all).toContain(`It would not be deleted: ${why}.`)
  expect(await ui.find({ key: 'remove-folder-yes' })).toBeUndefined()
  expect(await ringed(ui)).toEqual(['remove-no'])
  await ui.press({ key: 'remove-no' })
  expect(after(w)).toEqual([['remove', 'work-2', '--delete-folder', '--dry-run', '--json']])
  // A longhaul.py with no --dry-run: today's sentence, why there is no more, and the delete still offered
  // (longhaul.py checks again before it deletes anything).
  w.answer.remove = rest => (rest.includes('--dry-run')
    ? done('', 2, 'longhaul remove: error: unrecognized arguments: --dry-run --json\n') : done('Done.'))
  await ui.press({ key: 'remove-work-2' })
  await ui.press({ key: 'remove-folder' })
  const old = await texts(ui)
  expect(old).toContain(DELETE_VAGUE)
  expect(old).toContain('What it would take could not be read: longhaul remove: error: unrecognized arguments: '
    + '--dry-run --json')
  expect(await ui.find({ key: 'remove-folder-yes' })).toBeDefined()
  await ui.unmount()
})

both('a folder change whose dry run cannot be read still asks, and says why it says no more', async ($, on, surface) => {
  const w = world(on)
  w.answer.folders = rest => (rest.includes('--dry-run') ? done('', 2, 'longhaul folders: error: unrecognized '
    + 'arguments: --dry-run --json\n') : done('Done.'))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'folder-remove-0' })
  const all = await texts(ui)
  expect(all).toContain("Take ~/work off work-2's folders?")
  expect(all).toContain('What it would change could not be read: longhaul folders: error: unrecognized arguments: '
    + '--dry-run --json')
  await ui.press({ key: 'folders-yes' })
  expect(after(w).slice(1)).toEqual([['folders', 'work-2', '--remove', '~/work'], ['status', '--json']])
  await ui.unmount()
})

both('the pane says "sign-in folder" for an account\'s own folder, never a bare "folder"', async ($, on, surface) => {
  const w = world(on)
  w.answer.remove = dryOr(DELETE_DRY, done('', 2, 'Not deleted, nothing changed: a chat runs on it.\n'))
  const { ui } = await opened($, w, surface)
  const seen: string[] = []
  for (const name of ['personal-1', 'work-2', 'spare-3', 'team-4']) {
    await open(ui, `row-${name}`)
    seen.push(...(await texts(ui)))
    await ui.press({ key: `remove-${name}` })
    seen.push(...(await texts(ui)))
    expect((await ui.find({ key: 'remove-folder' }))?.text).toBe('Remove and delete its sign-in folder…')
    await ui.press({ key: 'remove-folder' })
    seen.push(...(await texts(ui)))
    expect((await ui.find({ key: 'remove-folder-yes' }))?.text).toBe('Delete the sign-in folder')
    await ui.press({ key: 'remove-folder-yes' }) // refused: the page, its result and its buttons stay
    seen.push(...(await texts(ui)))
    await ui.press({ key: 'back' })
  }
  expect(seen).toContain(REMOVE_WORK_2)
  expect(seen).toContain(DELETE_WORK_2)
  expect(seen.some(t => t.includes('Remove and delete its sign-in folder: work-2'))).toBe(true)
  // Every place the account's folder is named, or "its folder" or "the folder" said, it is the sign-in folder.
  for (const t of seen) {
    for (const m of t.matchAll(/(\S+) folder (~\/\.claude|\/)/g)) expect(m[1]).toBe('sign-in')
    expect(t).not.toMatch(/\b(its|the|that) folder\b/i)
  }
  await ui.unmount()
})

both('an account at its limit offers It has room: forget this limit, which runs clear NAME and shows what it said', async ($, on, surface) => {
  const w = world(on)
  const NOTE_ONE = 'If it is still full, the next reply meets the limit and Longhaul records it again.'
  const SAID = 'longhaul forgot work-2\'s recorded limit (it was until 15:00); a chat waiting for it carries on within a few seconds'
  w.answer.clear = done(`${SAID}\n`)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-personal-1')
  expect(await ui.find({ key: 'clear-personal-1' })).toBeUndefined()
  expect(await texts(ui)).not.toContain(NOTE_ONE)
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect((await ui.find({ key: 'clear-work-2' }))?.text).toBe('It has room: forget this limit')
  expect(await texts(ui)).toContain(NOTE_ONE)
  await ui.press({ key: 'clear-work-2' })
  expect(after(w)).toEqual([['clear', 'work-2'], ['status', '--json']])
  const all = await texts(ui)
  expect(all.some(t => t.startsWith('✔') && t.includes('Forget this limit: work-2'))).toBe(true)
  expect(all).toContain(SAID)
  await ui.unmount()
})

both('Use for this chat shows only where use would take the chat, and runs use NAME --chat ID', async ($, on, surface) => {
  const w = world(on)
  w.status = done(JSON.stringify({ ...STATUS, accounts: ACCOUNTS.map(a => ({ ...a,
    use_here: a.name === 'team-4' ? 'ok' : a.name === 'personal-1' ? 'this_chat' : 'not_allowed' })) }))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  expect(await ui.find({ key: 'use-work-2' })).toBeUndefined()
  await ui.press({ key: 'back' })
  await open(ui, 'row-team-4')
  await ui.press({ key: 'use-team-4' })
  expect(after(w)).toEqual([['use', 'team-4', '--chat', SID], ['status', '--json']])
  await ui.unmount()
})

// Q2: every page whose record says has a Use row: the button, or why not,
// with the action that fixes it where there is one.
both('the Use row says why Use for this chat is not offered, with its fix', async ($, on, surface) => {
  const w = world(on)
  const keeper = { ...ACCOUNTS[0], name: 'keeper-5', this_chat: false, only_in: ['/'], use_here: 'ok' }
  const accounts = [{ ...ACCOUNTS[0], use_here: 'this_chat' },
    { ...ACCOUNTS[1], use_here: 'not_allowed' }, // only in ~/work, ~/clients, and limited
    { ...ACCOUNTS[2], use_here: 'not_allowed' }, // its folders field is not a list
    { ...ACCOUNTS[3], use_here: 'not_allowed' }, // no list: ~/code/x is reserved for others
    { ...ACCOUNTS[3], name: 'kept-6', kept_out_by: ['keeper-5'], use_here: 'not_allowed' },
    { ...ACCOUNTS[0], name: 'full-7', this_chat: false, limited_until: AT + 2 * HOUR, use_here: 'limited' },
    { ...ACCOUNTS[0], name: 'out-8', this_chat: false, signed_in: false, as_expected: null, use_here: 'not_signed_in' },
    keeper, { ...ACCOUNTS[0], name: 'old-9', this_chat: false }]
  w.status = done(JSON.stringify({ ...STATUS, here: { folder: '~/code/x', may_use: ['personal-1', 'keeper-5'] }, accounts }))
  const { ui } = await opened($, w, surface)
  const row = async (name: string) => {
    await open(ui, `row-${name}`)
    const said = (await texts(ui)).filter(t => t.startsWith('Use for this chat'))
    const keys = await buttons(ui)
    await ui.press({ key: 'back' })
    return { said, keys }
  }
  expect((await row('personal-1')).said).toEqual(['Use for this chat: this chat runs on it already.'])
  let r = await row('work-2')
  expect(r.said).toEqual(['Use for this chat: not offered here, as it runs only in ~/work, ~/clients, and this chat is in '
    + '~/code/x.'])
  expect(r.keys.slice(0, 4)).toEqual(['use-add-work-2', 'clear-work-2', 'signin-work-2', 'remove-work-2'])
  expect((await row('spare-3')).said).toEqual(['Use for this chat: not offered, as its repositories field is not a list of '
    + 'paths.'])
  expect((await row('team-4')).said).toEqual(['Use for this chat: not offered here, as ~/code/x is reserved for personal-1, '
    + 'keeper-5.'])
  expect((await row('kept-6')).said).toEqual(['Use for this chat: not offered, as keeper-5 keeps every folder.'])
  // At its limit: the fix is Forget its limit, here and not again among the actions.
  r = await row('full-7')
  expect(r.said).toEqual(['Use for this chat: not offered while it is at its limit.'])
  expect(r.keys.filter(k => k.startsWith('clear-'))).toEqual(['clear-full-7'])
  expect(r.keys.slice(0, 3)).toEqual(['clear-full-7', 'signin-full-7', 'remove-full-7'])
  // Not signed in: the fix is its Sign in, once.
  r = await row('out-8')
  expect(r.said).toEqual(['Use for this chat: not offered until it is signed in with its email.'])
  expect(r.keys.slice(0, 2)).toEqual(['signin-out-8', 'remove-out-8'])
  expect((await row('keeper-5')).keys[0]).toBe('use-keeper-5')
  // An older Longhaul that does not say: no Use row.
  expect((await row('old-9')).said).toEqual([])
  // The fixes run their commands.
  await open(ui, 'row-work-2')
  expect((await ui.find({ key: 'use-add-work-2' }))?.text).toBe('Add ~/code/x to its folders')
  await ui.press({ key: 'use-add-work-2' }) // what it would do first, then a yes (M4)
  expect(after(w)).toEqual([['folders', 'work-2', '--add', '~/code/x', '--dry-run', '--json']])
  expect(await texts(ui)).toContain('Add ~/code/x to work-2\'s folders?')
  await ui.press({ key: 'folders-yes' })
  expect(after(w).slice(1)).toEqual([['folders', 'work-2', '--add', '~/code/x'], ['status', '--json']])
  await ui.press({ key: 'back' })
  await open(ui, 'row-full-7')
  await ui.press({ key: 'clear-full-7' })
  expect(after(w).slice(3)).toEqual([['clear', 'full-7'], ['status', '--json']])
  await ui.press({ key: 'back' })
  await open(ui, 'row-out-8')
  await ui.press({ key: 'signin-out-8' })
  expect(after(w).slice(5)).toEqual([['setup', 'out-8'], ['status', '--json']])
  await ui.unmount()
})

both('Remove runs only after its confirmation, Cancel runs nothing, and its result shows on the list', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'remove-work-2' })
  expect(after(w)).toEqual([])
  expect(await texts(ui)).toContain(REMOVE_WORK_2)
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
  // Forget all recorded limits, with when to use it.
  expect((await ui.find({ key: 'clear' }))?.text).toBe('Forget all recorded limits')
  expect(await texts(ui)).toContain('Use this when claude.ai shows room but Longhaul still waits.')
  expect((await ui.find({ key: 'switch' }))?.props.value).toBe('ask')
  expect(await ui.find({ key: 'vscode-off' })).toBeUndefined() // it is off: only Turn on
  await ui.select({ key: 'switch', value: 'auto' })
  expect(after(w)).toEqual([['switch', 'auto'], ['status', '--json']])
  w.status = done(JSON.stringify({ ...STATUS, vscode: true }))
  await ui.press({ key: 'vscode-on' })
  expect(after(w).slice(2)).toEqual([['vscode', 'on', '--yes'], ['status', '--json']])
  expect(await texts(ui)).toContain(RELOAD)
  // Its result as longhaul.py prints it, the chats that carry on named.
  const CARRIED = 'longhaul forgot the recorded limits: work-2 (it was until 15:00); carrying on: the chat in ~/code/x'
  w.answer.clear = done(`${CARRIED}\n`)
  await ui.press({ key: 'clear' })
  expect(after(w).slice(4)).toEqual([['clear'], ['status', '--json']])
  expect(await texts(ui)).not.toContain(RELOAD)
  expect(await texts(ui)).toContain(CARRIED)
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Forget all recorded limits'))).toBe(true)
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
  // Each read again after Forget all recorded limits, which reads the rows again.
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

// What `repair --json` finds, as longhaul.py's repair_findings() words them.
const FINDINGS = [
  { id: 'limit:work-2', what: 'work-2: its recorded usage limit in state.json is not a time, which stops Longhaul '
    + 'deciding where a chat runs', fix: 'forget it', fixable: true },
  { id: `wait:${SID}`, what: `Chat ${SID.slice(0, 8)} waits to carry on on personal-1 at 14:01, and its relay is gone`,
    fix: 'cancel the wait; nothing is sent', fixable: true },
  { id: 'accounts', what: "accounts.json is not readable: not JSON (Expecting ',' delimiter at line 4 column 3); "
    + 'Longhaul refuses to run chats on your accounts until it is fixed', fix: 'none here, as there is no good copy to '
    + 'put back: fix it by hand at that line and column, or remove it', fixable: false },
]
const PAUSE_NOTE = 'Stops questions, carry-ons and moves in the panel chats opened after it.'
const PAUSED = 'Longhaul is paused in panel chats: Claude runs alone there, with no question, carry-on or move at a limit.'

// repair as longhaul.py answers it: --json lists what is left in `found`,
// --fix takes one out of it and says Fixed.
function repairs(w: World, found: any[]): void {
  w.answer.repair = rest => {
    if (rest[1] === '--json') return done(`${JSON.stringify(found)}\n`)
    const i = found.findIndex(f => f.id === rest[2])
    const f = found.splice(i, 1)[0]
    return done(`To repair: ${f.what}. Fix: ${f.fix}.\nFixed.\n`)
  }
}

both('Troubleshoot lists what repair --json finds; Fix asks first, then runs repair --fix ID --yes', async ($, on, surface) => {
  const w = world(on)
  repairs(w, [...FINDINGS])
  const { ui } = await opened($, w, surface)
  expect(after(w)).toEqual([]) // the list looks for nothing stuck
  expect((await ui.find({ key: 'row-troubleshoot' }))?.text).toBe('  Troubleshoot')
  await open(ui, 'row-troubleshoot')
  expect(after(w)).toEqual([['repair', '--json']])
  expect(await ringed(ui)).toEqual(['back']) // never on a Fix or Pause: Enter there would start one
  const all = await texts(ui)
  expect(all).toContain('Troubleshoot')
  expect(all).toContain('Nothing changes until you press a button.')
  for (const f of FINDINGS) expect(all).toContain(`⚠ ${f.what}.`)
  expect(all).toContain('Fix: forget it.')
  expect(all).toContain(`Fix: ${FINDINGS[2]!.fix}.`)
  // A Fix for each one with a fix here, none for the other.
  expect((await buttons(ui)).filter(k => k.startsWith('fix-'))).toEqual(['fix-0', 'fix-1'])
  expect((await ui.find({ key: 'fix-0' }))?.text).toBe('Fix')
  // Fix asks a second time, with the focus on its Cancel; Cancel runs nothing.
  await ui.press({ key: 'fix-0' })
  expect(after(w)).toEqual([['repair', '--json']])
  expect(await texts(ui)).toContain('Fix it? Forget it.')
  expect(await ringed(ui)).toEqual(['fix-no'])
  expect(await ui.find({ key: 'fix-1' })).toBeUndefined() // one yes asked at a time
  expect(await ui.find({ key: 'panel-pause' })).toBeUndefined()
  await ui.press({ key: 'fix-no' })
  expect(after(w)).toEqual([['repair', '--json']])
  expect(await ui.find({ key: 'fix-yes' })).toBeUndefined()
  await ui.press({ key: 'fix-0' })
  await ui.press({ key: 'fix-yes' })
  expect(after(w)).toEqual([['repair', '--json'], ['repair', '--fix', 'limit:work-2', '--yes'], ['status', '--json'],
    ['repair', '--json']])
  const now = await texts(ui)
  expect(now.some(t => t.startsWith('✔ Fix: work-2: its recorded usage limit'))).toBe(true)
  expect(now).toContain('Fixed.')
  // Looked at again: the one fixed is gone, the others stay.
  expect(now).not.toContain(`⚠ ${FINDINGS[0]!.what}.`)
  expect(now).toContain(`⚠ ${FINDINGS[1]!.what}.`)
  expect((await buttons(ui)).filter(k => k.startsWith('fix-'))).toEqual(['fix-0'])
  // Back to its line on the list.
  await ui.press({ key: 'back' })
  expect((await ui.find({ key: 'row-troubleshoot' }))?.text).toBe('› Troubleshoot')
  expect(await buttons(ui)).toEqual(ROWS)
  await ui.unmount()
})

both('Troubleshoot with nothing stuck says so; Pause and Resume run panel pause and panel resume', async ($, on, surface) => {
  const w = world(on)
  w.answer.repair = done('[]\n')
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-troubleshoot')
  expect(await texts(ui)).toContain('Nothing to repair.')
  expect(await ui.find({ key: 'panel-resume' })).toBeUndefined() // not paused: only Pause
  expect((await ui.find({ key: 'panel-pause' }))?.text).toBe('Pause Longhaul in panel chats')
  expect(await texts(ui)).toContain(PAUSE_NOTE)
  expect(await texts(ui)).not.toContain(PAUSED)
  w.status = done(JSON.stringify({ ...STATUS, panel_paused: true }))
  w.answer.panel = done('Paused: Claude runs alone in the VS Code panel chats opened from now on.\n')
  await ui.press({ key: 'panel-pause' })
  expect(after(w).slice(1)).toEqual([['panel', 'pause'], ['status', '--json'], ['repair', '--json']])
  let all = await texts(ui)
  expect(all.some(t => t.startsWith('✔ Pause Longhaul in panel chats'))).toBe(true)
  expect(all).toContain('Paused: Claude runs alone in the VS Code panel chats opened from now on.')
  expect(all).toContain('A chat open now keeps what it started with until it is closed and opened again.')
  expect(all).toContain(PAUSED)
  expect(await ui.find({ key: 'panel-pause' })).toBeUndefined()
  expect((await ui.find({ key: 'panel-resume' }))?.text).toBe('Resume Longhaul in panel chats')
  // The list and Settings say it while it lasts.
  await ui.press({ key: 'back' })
  expect((await ui.find({ key: 'row-troubleshoot' }))?.text).toBe('› Troubleshoot (Longhaul paused in panel chats)')
  await open(ui, 'row-settings')
  expect(await texts(ui)).toContain('Longhaul is paused in panel chats: Claude runs alone there. Troubleshoot resumes it.')
  await ui.press({ key: 'back' })
  await open(ui, 'row-troubleshoot')
  w.status = done(JSON.stringify(STATUS))
  w.answer.panel = done('Resumed: Longhaul stays in the VS Code panel chats opened from now on.\n')
  await ui.press({ key: 'panel-resume' })
  expect(w.runs.slice(-3)).toEqual([['panel', 'resume'], ['status', '--json'], ['repair', '--json']])
  all = await texts(ui)
  expect(all.some(t => t.startsWith('✔ Resume Longhaul in panel chats'))).toBe(true)
  expect(all).not.toContain(PAUSED)
  expect(await ui.find({ key: 'panel-pause' })).toBeDefined()
  // An older Longhaul, whose status does not say: both are offered.
  w.status = done(JSON.stringify({ ...STATUS, panel_paused: undefined }))
  await ui.press({ key: 'panel-pause' })
  expect(await ui.find({ key: 'panel-pause' })).toBeDefined()
  expect(await ui.find({ key: 'panel-resume' })).toBeDefined()
  await ui.unmount()
})

both('a Fix refused shows why with the finding still there; a fix for VS Code says to reload it', async ($, on, surface) => {
  const w = world(on)
  const VSCODE = { id: 'vscode', what: "VS Code's Claude panel points at ~/old/longhaul-vscode, a Longhaul that is not "
    + 'there any more', fix: "point it at this Longhaul (~/new/longhaul-vscode), a copy of VS Code's settings kept; then "
    + 'reload VS Code', fixable: true }
  const found = [FINDINGS[0]!, VSCODE]
  w.answer.repair = rest => (rest[1] === '--json' ? done(JSON.stringify(found))
    : rest[2] === 'vscode' ? (found.splice(1, 1), done('Fixed.\n'))
    : done(`To repair: ${FINDINGS[0]!.what}. Fix: forget it.\n`, 1,
      "Not fixed: work-2's recorded limit changed meanwhile, so it was left as it is.\n"))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-troubleshoot')
  await ui.press({ key: 'fix-0' })
  await ui.press({ key: 'fix-yes' })
  let all = await texts(ui)
  expect(all.some(t => t.startsWith('✘ Fix: work-2'))).toBe(true)
  expect(all).toContain("Not fixed: work-2's recorded limit changed meanwhile, so it was left as it is.")
  expect(all).toContain(`⚠ ${FINDINGS[0]!.what}.`)
  expect(await ui.find({ key: 'fix-0' })).toBeDefined()
  await ui.press({ key: 'fix-1' })
  expect(await texts(ui)).toContain("Fix it? Point it at this Longhaul (~/new/longhaul-vscode), a copy of VS Code's "
    + 'settings kept; then reload VS Code.')
  await ui.press({ key: 'fix-yes' })
  expect(w.runs.at(-3)).toEqual(['repair', '--fix', 'vscode', '--yes'])
  all = await texts(ui)
  expect(all).toContain(RELOAD)
  expect(all).not.toContain(`⚠ ${VSCODE.what}.`)
  await ui.unmount()
})

both('a look for what is stuck that fails says why, with Retry; an older Longhaul says to update', async ($, on, surface) => {
  const w = world(on)
  w.answer.repair = done('', 2, 'usage: longhaul [-h]\nlonghaul: error: unrecognized arguments: repair --json\n')
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-troubleshoot')
  let all = await texts(ui)
  expect(all).toContain('This Longhaul is older than this pane: it has no repair.')
  expect(all.some(t => t.startsWith('To update Longhaul:'))).toBe(true)
  expect(await ui.find({ key: 'panel-pause' })).toBeDefined() // the pause is a command of its own
  w.answer.repair = done('', 1, 'Traceback (most recent call last):\nPermissionError: [Errno 13] Permission denied\n')
  await ui.press({ key: 'trouble-retry' })
  expect(w.runs.at(-1)).toEqual(['repair', '--json'])
  all = await texts(ui)
  expect(all).toContain('Longhaul could not look for what is stuck: PermissionError: [Errno 13] Permission denied.')
  expect(await ui.find({ key: 'trouble-details' })).toBeDefined()
  await ui.press({ key: 'trouble-details' })
  expect(await texts(ui)).toContain('Traceback (most recent call last):')
  w.answer.repair = done('not json\n')
  await ui.press({ key: 'trouble-retry' })
  all = await texts(ui)
  expect(all).toContain('Longhaul could not look for what is stuck: its answer was not a list.')
  for (const t of all) expect(t).not.toMatch(/\brows?\b|exit code/i)
  // A finding that is not one is left out; an id that is not one gets no Fix.
  w.answer.repair = done(JSON.stringify([null, 'x', { id: '--yes', what: 'odd', fix: 'none', fixable: true },
    { ...FINDINGS[0], what: 7 }, FINDINGS[1]]))
  await ui.press({ key: 'trouble-retry' })
  all = await texts(ui)
  expect(all).toContain('⚠ odd.')
  expect(all).toContain(`⚠ ${FINDINGS[1]!.what}.`)
  expect(all.filter(t => t.startsWith('⚠ ')).length).toBe(2)
  expect((await buttons(ui)).filter(k => k.startsWith('fix-'))).toEqual(['fix-1'])
  await ui.unmount()
})

// What `activity --json` gives, as longhaul.py's activity() words it: this
// chat's events and others', oldest first.
const OTHER = 'b71e22c0'
const EVENTS = [
  { time: AT - 3 * HOUR, chat: SID.slice(0, 8), what: 'Started on personal-1', why: '~/code prefers personal-1, and it had room' },
  { time: AT - 2 * HOUR, chat: OTHER, what: 'Stopped a message; nothing was sent',
    why: '~/side is kept for work-2, and the chat ran on personal-1' },
  { time: AT - HOUR, chat: null, what: "Forgot work-2's recorded limit (it was until 14:00)", why: 'a reply came on it' },
  { time: AT - 600, chat: SID.slice(0, 8), what: 'Compacted it at 412,345 tokens',
    why: 'its checkpoint was saved, and it comes back' },
]
const EARLIER = { time: AT - 50 * HOUR, chat: SID.slice(0, 8), what: 'Set to run on personal-1', why: 'chosen with longhaul use' }

// activity as longhaul.py answers it: today's events, and with more days the earlier one first.
function activities(w: World, found = EVENTS): void {
  w.answer.activity = rest => done(`${JSON.stringify(rest[3] === '1' ? found : [EARLIER, ...found])}\n`)
}

both('Activity lists what activity --json gives, this chat first; Earlier days asks for more days', async ($, on, surface) => {
  const w = world(on)
  activities(w)
  const { ui } = await opened($, w, surface)
  expect(after(w)).toEqual([]) // the list reads no activity
  await open(ui, 'row-activity')
  expect(after(w)).toEqual([['activity', '--json', '--days', '1']])
  expect(w.envs.at(-1)).toEqual({ CLAUDE_CODE_SESSION_ID: SID }) // so it knows this chat
  expect(await ringed(ui)).toEqual(['back'])
  let all = await texts(ui)
  const at = (e: { time: number }) => clock(e.time)
  expect(all).toContain('What Longhaul did · today')
  const mine = all.indexOf('This chat'), others = all.indexOf('Other chats')
  expect(mine).toBeGreaterThan(-1)
  expect(others).toBeGreaterThan(mine)
  expect(all.slice(mine + 1, others)).toEqual([
    `${at(EVENTS[0]!)}  Started on personal-1: ~/code prefers personal-1, and it had room.`,
    `${at(EVENTS[3]!)}  Compacted it at 412,345 tokens: its checkpoint was saved, and it comes back.`])
  expect(all).toContain(`${at(EVENTS[1]!)}  ${OTHER} · Stopped a message; nothing was sent: ~/side is kept for work-2, `
    + 'and the chat ran on personal-1.')
  expect(all).toContain(`${at(EVENTS[2]!)}  Forgot work-2's recorded limit (it was until 14:00): a reply came on it.`)
  expect(all).toContain(`Full log: ${HOME}/longhaul.log`)
  // Earlier days: a week, then a month, then no more.
  await ui.press({ key: 'activity-earlier' })
  expect(w.runs.at(-1)).toEqual(['activity', '--json', '--days', '7'])
  all = await texts(ui)
  expect(all).toContain('What Longhaul did · the last 7 days')
  expect(all).toContain(`${clock(EARLIER.time)}  Set to run on personal-1: chosen with longhaul use.`)
  await ui.press({ key: 'activity-earlier' })
  expect(w.runs.at(-1)).toEqual(['activity', '--json', '--days', '30'])
  expect(await texts(ui)).toContain('What Longhaul did · the last 30 days')
  expect(await ui.find({ key: 'activity-earlier' })).toBeUndefined()
  // Back to its line; opened again, today's once more.
  await ui.press({ key: 'back' })
  expect((await ui.find({ key: 'row-activity' }))?.text).toBe('› Activity: what Longhaul did, and why')
  await open(ui, 'row-activity')
  expect(w.runs.at(-1)).toEqual(['activity', '--json', '--days', '1'])
  expect(await texts(ui)).toContain('What Longhaul did · today')
  await ui.unmount()
})

both('Activity with nothing to show says so; a read that fails says why, with Retry; an older Longhaul says to update',
  async ($, on, surface) => {
    const w = world(on)
    w.answer.activity = done('[]\n') // nothing on any day
    const { ui } = await opened($, w, surface)
    await open(ui, 'row-activity')
    let all = await texts(ui)
    expect(all).toContain('Nothing Longhaul did by itself today.')
    expect(all).not.toContain('This chat')
    await ui.press({ key: 'activity-earlier' })
    expect(await texts(ui)).toContain('Nothing Longhaul did by itself in the last 7 days.')
    await ui.press({ key: 'back' })
    // An event of no shape the pane takes is left out; text is masked.
    activities(w, [null, 'x', { time: 'now', chat: null, what: 'odd', why: 'odd' }, { ...EVENTS[2], why: 7 },
      { ...EVENTS[1], why: 'mailed someone@example.com' }] as any)
    await open(ui, 'row-activity')
    all = await texts(ui)
    expect(all.filter(t => /odd|recorded limit/.test(t))).toEqual([])
    expect(all.some(t => t.includes('s…@example.com') && !t.includes('someone@'))).toBe(true)
    await ui.press({ key: 'back' })
    w.answer.activity = done('', 2, 'usage: longhaul [-h]\nlonghaul: error: unrecognized arguments: activity --json --days 1\n')
    await open(ui, 'row-activity')
    all = await texts(ui)
    expect(all).toContain('This Longhaul is older than this pane: it has no activity.')
    expect(all.some(t => t.startsWith('To update Longhaul:'))).toBe(true)
    w.answer.activity = done('', 1, 'Traceback (most recent call last):\nPermissionError: [Errno 13] Permission denied\n')
    await ui.press({ key: 'activity-retry' })
    expect(w.runs.at(-1)).toEqual(['activity', '--json', '--days', '1'])
    all = await texts(ui)
    expect(all).toContain('Longhaul could not read what it did: PermissionError: [Errno 13] Permission denied.')
    await ui.press({ key: 'activity-details' })
    expect(await texts(ui)).toContain('Traceback (most recent call last):')
    w.answer.activity = done('not json\n')
    await ui.press({ key: 'activity-retry' })
    expect(await texts(ui)).toContain('Longhaul could not read what it did: its answer was not a list.')
    await ui.unmount()
  })

both("the list says why this chat runs on its account, from status --json's chat", async ($, on, surface) => {
  const w = world(on)
  const CHAT = { id: SID.slice(0, 8), account: 'personal-1', what: 'Started on personal-1',
    why: '~/code prefers personal-1, and it had room', at: AT - 3 * HOUR }
  w.status = done(JSON.stringify({ ...STATUS, chat: CHAT }))
  const { ui } = await opened($, w, surface)
  expect(w.envs[0]).toEqual({ CLAUDE_CODE_SESSION_ID: SID }) // the read names this chat
  expect(await texts(ui)).toContain('This chat: personal-1. Why: ~/code prefers personal-1, and it had room.')
  // No reason in the log, no chat, or an older Longhaul: no line.
  for (const chat of [{ ...CHAT, what: null, why: null, at: null }, null, undefined]) {
    w.status = done(JSON.stringify({ ...STATUS, chat }))
    await open(ui, 'row-settings')
    await ui.press({ key: 'clear' })
    await ui.press({ key: 'back' })
    expect((await texts(ui)).filter(t => t.startsWith('This chat:'))).toEqual([])
  }
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
  // The reason, its last line, shows at once; the rest behind Details.
  expect(await texts(ui)).toContain('should be s…@example.com, not <o…@example.com>')
  expect(await texts(ui)).not.toContain('line 7')
  await ui.press({ key: 'details' })
  const shown = await texts(ui)
  expect(shown.some(t => t.startsWith('✘') && /personal-1/.test(t))).toBe(true)
  expect(shown).toContain('signed in as f…@example.com')
  expect(shown).toContain('should be s…@example.com, not <o…@example.com>')
  expect(shown).toContain('line 7')
  expect(shown).not.toContain('line 6')
  expect(shown.join('\n')).not.toMatch(/first\.last|second@|other\+tag/)
  // M8: one "…" for the rest of the local part, read as a pause, where "•••" was read "bullet" three times.
  expect(shown.join('\n')).not.toMatch(/•/)
  await ui.unmount()
})

// Q6: a limit with how far off its end is and when Longhaul last read the
// account (reading_at); with no limit and a reading, "room now".
both('a limit says how far off it ends and when it was seen; a reading with no limit says room now', async ($, on, surface) => {
  const w = world(on)
  const seen = AT - 25 * 60 // 11:35, today
  w.status = done(JSON.stringify({ ...STATUS, accounts: [
    { ...ACCOUNTS[0], reading_at: seen },
    { ...ACCOUNTS[1], limited_until: AT + 40 * 60, reading_at: seen },
    { ...ACCOUNTS[1], name: 'week-5', signed_in: true, as_expected: true, limited_until: AT + 52 * HOUR, reading_at: null },
    { ...ACCOUNTS[0], name: 'full-6', this_chat: false, five_hour: { used: 100, resets_at: AT + HOUR }, reading_at: seen },
    { ...ACCOUNTS[0], name: 'odd-7', this_chat: false, reading_at: 'yesterday' },
    { ...ACCOUNTS[0], name: 'old-8', this_chat: false }] }))
  const { ui } = await opened($, w, surface)
  const row = (key: string) => rowOf(ui, key)
  expect(await row('row-personal-1')).toBe(`  personal-1  max · signed in · room now · 5h 40% · week 12%\n${UNDER}this chat`)
  expect(await row('row-work-2')).toBe(`  work-2      pro · NOT signed in · limit until ${clock(AT + 40 * 60)} (in 40 min), `
    + `seen ${clock(seen)}\n${UNDER}only in ~/work, ~/clients`)
  expect(await row('row-week-5')).toMatch(new RegExp(`pro · limit until ${clock(AT + 52 * HOUR)} \\(in 2 d 4 h\\)\n`))
  expect(await row('row-full-6')).toMatch(/max · signed in · 5h 100% · week 12%$/) // a full window: no room now
  expect(await row('row-odd-7')).toMatch(/max · signed in · 5h 40% · week 12%$/) // not a time: not known
  expect(await row('row-old-8')).toMatch(/max · signed in · 5h 40% · week 12%$/) // an older Longhaul
  // The pages say the same.
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain(`room now, seen ${clock(seen)}`)
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect(await texts(ui)).toContain(`limit until ${clock(AT + 40 * 60)} (in 40 min), seen ${clock(seen)}`)
  await ui.press({ key: 'back' })
  await open(ui, 'row-week-5')
  expect(await texts(ui)).toContain(`limit until ${clock(AT + 52 * HOUR)} (in 2 d 4 h)`)
  await ui.press({ key: 'back' })
  await open(ui, 'row-odd-7')
  expect((await texts(ui)).some(t => /room now|seen/.test(t))).toBe(false)
  // Two hours off, to the hour.
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[1], limited_until: AT + 2 * HOUR + 10 * 60 }] }))
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  await ui.press({ key: 'clear-work-2' })
  expect(await texts(ui)).toContain(`limit until ${clock(AT + 2 * HOUR + 10 * 60)} (in 2 h)`)
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

// Q14: a failure's reason in normal weight, the raw output behind Details,
// and no "rows" or "exit code" in what the pane says.

// The Texts drawn with exactly this text.
async function line(ui: any, text: string): Promise<any[]> {
  return (await ui.findAll({ type: 'Text' })).filter((t: any) => t.text === text)
}
both("a failed action's reason is not dim, and its raw output waits behind Details", async ($, on, surface) => {
  const w = world(on)
  w.answer.fallback = done('Traceback (most recent call last):\n  File "longhaul.py", line 1\n', 1,
    'longhaul no account called work-2; the accounts are personal-1\n')
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'fallback' })
  const head = (await ui.findAll({ type: 'Text' })).find((t: any) => t.text.startsWith('✘'))
  expect(head?.text).toBe('✘ Use as fallback: work-2')
  expect(head?.props.dimColor).toBeFalsy()
  const reason = await line(ui, 'longhaul no account called work-2; the accounts are personal-1')
  expect(reason.length).toBe(1)
  expect(reason[0].props.dimColor).toBeFalsy()
  expect(await texts(ui)).not.toContain('Traceback (most recent call last):')
  expect((await ui.find({ key: 'details' }))?.text).toBe('Details')
  await ui.press({ key: 'details' })
  const raw = (await ui.findAll({ type: 'Text' })).filter((t: any) => t.text === 'Traceback (most recent call last):')
  expect(raw.length).toBe(1)
  expect(raw[0].props.dimColor).toBe(true)
  expect((await ui.find({ key: 'details' }))?.text).toBe('Hide details')
  await ui.press({ key: 'details' })
  expect(await texts(ui)).not.toContain('Traceback (most recent call last):')
  // A next result opens closed; one line has no Details.
  await ui.press({ key: 'details' })
  w.answer.fallback = done('', 2, 'longhaul fallback: refused\n')
  await ui.press({ key: 'fallback' })
  expect(await ui.find({ key: 'details' })).toBeUndefined()
  expect((await line(ui, 'longhaul fallback: refused')).length).toBe(1)
  await ui.unmount()
})

both('a read that fails says why in normal weight, that chats still run, with Retry and Details', async ($, on, surface) => {
  const w = world(on)
  const PAUSED = 'Your chats still run; this screen works again once this is fixed.'
  w.status = done('', 1, 'Traceback (most recent call last):\nValueError: something broke\n')
  const { ui } = await opened($, w, surface)
  const first = (await ui.findAll({ type: 'Text' }))[1] // after the title
  expect(first?.text).toBe(`Longhaul could not read your accounts: ValueError: something broke. ${PAUSED}`)
  expect(first?.props.dimColor).toBeFalsy()
  expect(await buttons(ui)).toEqual(['retry', 'read-details'])
  await ui.press({ key: 'read-details' })
  expect(await texts(ui)).toContain('Traceback (most recent call last):')
  // accounts.json that cannot be read: status ends with 2, its record still said, the problem in it.
  w.status = done(JSON.stringify({ ...STATUS, accounts: [], listed: false, here: null,
    problem: 'accounts.json is not readable: not JSON (Expecting value at line 1 column 1); fix or remove it' }), 2)
  await ui.press({ key: 'retry' })
  const all = await texts(ui)
  expect(all).toContain('Longhaul could not read your accounts: accounts.json is not readable: not JSON (Expecting value '
    + `at line 1 column 1); fix or remove it. ${PAUSED}`)
  expect(all.some(t => /no list of accounts|Traceback/.test(t))).toBe(false)
  expect((await buttons(ui)).slice(0, 1)).toEqual(['retry'])
  await ui.unmount()
})

// M8 (the UX review's §3.7): dim, low contrast in many themes, is for hints
// alone. Where an account may run, its email, its sign-in folder and the
// "Here" line answer "why", so they are in normal weight; the keys, a
// sign-in still "checking…" and the notes beside a button stay dim.
both('the facts are in normal weight, and dim is for hints: the keys, checking…, a note', async ($, on, surface) => {
  const w = world(on)
  w.slow.add('status') // the quick read's rows, each sign-in checking…
  const { ui } = await opened($, w, surface)
  // Whether each Text drawn with exactly this text is dim, the innermost (a Text
  // holding a dim word is drawn as that word); at least one is drawn.
  const dim = async (text: string) => {
    const found = (await ui.findAll({ type: 'Text' })).filter((t: any) => t.text === text
      && !t.children.some((k: any) => k && typeof k === 'object' && k.type === 'Text'))
    expect(found.length).toBeGreaterThan(0)
    return found.map((t: any) => t.props.dimColor === true)
  }
  expect(await dim(`${UNDER}only in ~/work, ~/clients`)).toEqual([false])
  expect(await dim(`${UNDER}this chat`)).toEqual([false])
  expect(await dim(`${UNDER}used nowhere`)).toEqual([false])
  expect(await dim('Here, in ~/code/x: personal-1, team-4 may be used.')).toEqual([false])
  expect(await dim('checking…')).toEqual([true, true, true, true]) // each row's sign-in, not known yet
  if (surface === 'terminal') expect(await dim('↑ ↓ to move · Enter to open · Esc to close')).toEqual([true])
  await w.clock.advance(60_000)
  await open(ui, 'row-work-2')
  for (const fact of ['may be used only in ~/work, ~/clients', 'b…@example.com', 'sign-in folder ~/.claude-work-2'])
    expect(await dim(fact)).toEqual([false])
  expect(await dim('If it is still full, the next reply meets the limit and Longhaul records it again.')).toEqual([true])
  if (surface === 'terminal') expect(await dim('Esc to go back')).toEqual([true])
  await ui.unmount()
})

both('no "rows" and no "exit code" in what the pane says', async ($, on, surface) => {
  const w = world(on)
  const seen: string[] = []
  w.status = done('', 2, 'longhaul: error: something\n')
  const { ui } = await opened($, w, surface)
  seen.push(...(await texts(ui)))
  w.status = done('not json')
  await ui.press({ key: 'retry' })
  seen.push(...(await texts(ui)))
  w.status = done(JSON.stringify(STATUS))
  await ui.press({ key: 'retry' })
  w.answer.setup = done('one\ntwo\n', 3, 'it broke\n')
  await open(ui, 'row-personal-1')
  await ui.press({ key: 'signin-personal-1' })
  await ui.press({ key: 'details' })
  seen.push(...(await texts(ui)).filter(t => !['one', 'two', 'it broke'].includes(t)))
  for (const t of seen) expect(t).not.toMatch(/\brows?\b|exit code/i)
  expect(seen.some(t => t.startsWith('Longhaul could not read your accounts: longhaul: error: something.'))).toBe(true)
  expect(seen.some(t => t.startsWith('Longhaul could not read your accounts: its answer was not JSON.'))).toBe(true)
  await ui.unmount()
})

// M7 (the UX review of 2026-11-11): the rows come from `status --json
// --no-check`, which runs no claude, at once; each sign-in says
// "checking…" until the full read, which checks them and may take 20 s
// for an account that does not answer, comes back.

const QUICK_ROWS = {
  'row-personal-1': `  personal-1  checking… · 5h 40% · week 12%\n${UNDER}this chat`,
  'row-work-2': `  work-2      checking… · limit until ${clock(AT + 3 * HOUR)} (in 3 h)\n${UNDER}only in ~/work, ~/clients`,
  'row-spare-3': `  spare-3     checking…\n${UNDER}used nowhere`,
  'row-team-4': '  team-4      checking…',
}
const FULL_ROWS = {
  'row-personal-1': `  personal-1  max · signed in · 5h 40% · week 12%\n${UNDER}this chat`,
  'row-work-2': `  work-2      pro · NOT signed in · limit until ${clock(AT + 3 * HOUR)} (in 3 h)\n${UNDER}only in ~/work, ~/clients`,
  'row-spare-3': `  spare-3     signed in as someone else\n${UNDER}used nowhere`,
  'row-team-4': '  team-4      could not check',
}

// Each account's row, by its key, its lines joined by newlines, the focus ring's › left out.
async function rows(ui: any): Promise<Record<string, string | undefined>> {
  const found: Record<string, string | undefined> = {}
  for (const key of ['row-personal-1', 'row-work-2', 'row-spare-3', 'row-team-4'])
    found[key] = (await rowOf(ui, key))?.replace(/^›/, ' ')
  return found
}

both('the rows are drawn before any sign-in check returns, each sign-in checking…, then filled in', async ($, on, surface) => {
  const w = world(on)
  w.slow.add('status') // the full read answers only once the clock moves on, as a check that hangs
  const { said, ui } = await opened($, w, surface)
  expect(said.text).toMatch(/pane is open/) // the command waited for the rows, not for the checks
  expect(w.quick.map(q => q.rest)).toEqual([['status', '--json', '--no-check']])
  expect(w.runs).toEqual([['status', '--json']]) // under way
  expect(await buttons(ui)).toEqual(ROWS)
  expect(await rows(ui)).toEqual(QUICK_ROWS)
  let all = await texts(ui)
  expect(all.join('\n')).not.toMatch(/Reading Longhaul's accounts|could not|NOT signed in|someone else/)
  expect(all).toContain('Here, in ~/code/x: personal-1, team-4 may be used.')
  // "checking…" is no warning: only the limit colours a line.
  const shade = async (text: string) => (await ui.findAll({ type: 'Text' })).find((t: any) => t.text === text)?.props.color
  expect(await shade('checking… · 5h 40% · week 12%')).toBeUndefined()
  expect(await shade(`checking… · limit until ${clock(AT + 3 * HOUR)} (in 3 h)`)).toBe('warning')
  // An account's page says it too, and its buttons are as for a check that could not say.
  await open(ui, 'row-work-2')
  all = await texts(ui)
  expect(all).toContain('checking…')
  expect((await ui.find({ key: 'signin-work-2' }))?.text).toBe('Sign in again')
  await ui.press({ key: 'back' })
  await w.clock.advance(60_000)
  expect(w.runs).toEqual([['status', '--json']]) // that one read, no other
  expect(await rows(ui)).toEqual(FULL_ROWS)
  expect((await texts(ui)).join('\n')).not.toMatch(/checking…/)
  await open(ui, 'row-work-2')
  expect((await ui.find({ key: 'signin-work-2' }))?.text).toBe('Sign in')
  await ui.unmount()
})

both('a full read that fails after the quick one keeps the rows, says so, and each sign-in could not check', async ($, on, surface) => {
  const w = world(on)
  w.noCheck = unchecked(done(JSON.stringify(STATUS)))
  w.status = done('', 1, 'Traceback (most recent call last):\nValueError: something broke\n')
  const { ui } = await opened($, w, surface)
  expect(await buttons(ui)).toEqual(['retry', 'read-details', ...ROWS])
  const first = (await ui.findAll({ type: 'Text' }))[1] // after the title, where a failed read shows
  expect(first?.text).toBe('Longhaul could not check the sign-ins: ValueError: something broke.')
  expect(first?.props.color).toBe('error')
  expect(await rows(ui)).toEqual({ 'row-personal-1': `  personal-1  could not check · 5h 40% · week 12%\n${UNDER}this chat`,
    'row-work-2': `  work-2      could not check · limit until ${clock(AT + 3 * HOUR)} (in 3 h)\n${UNDER}only in ~/work, ~/clients`,
    'row-spare-3': `  spare-3     could not check\n${UNDER}used nowhere`, 'row-team-4': '  team-4      could not check' })
  expect((await texts(ui)).join('\n')).not.toMatch(/checking…|could not read/)
  await ui.press({ key: 'read-details' })
  expect(await texts(ui)).toContain('Traceback (most recent call last):')
  // One that ran past its time, the same; Retry reads both again.
  w.fails = 'it ran past 60000 ms'
  await ui.press({ key: 'retry' })
  expect((await ui.findAll({ type: 'Text' }))[1]?.text).toMatch(/^Longhaul could not check the sign-ins: it did not answer/)
  expect(await buttons(ui)).toEqual(['retry', ...ROWS])
  w.fails = null
  w.status = done(JSON.stringify(STATUS))
  w.noCheck = null
  await ui.press({ key: 'retry' })
  expect(await buttons(ui)).toEqual(ROWS)
  expect(await rows(ui)).toEqual(FULL_ROWS)
  expect(w.runs.filter(r => r[0] !== 'status')).toEqual([])
  await ui.unmount()
})

both('an older Longhaul with no --no-check: the one full read, as before, and nothing said of it', async ($, on, surface) => {
  const w = world(on)
  w.noCheck = done('', 2, 'usage: longhaul status [-h] [--json]\nlonghaul status: error: unrecognized arguments: --no-check\n')
  w.slow.add('status')
  // /accounts waits for that read then, as it always did: the pane is drawn meanwhile.
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const said = $.command.run(TYPED)
  const ui = await $.ui.mount(pane(surface))
  let all = (await texts(ui)).join('\n')
  expect(all).toMatch(/Reading Longhaul's accounts…/)
  expect(all).not.toMatch(/unrecognized|older|update|could not/i)
  await w.clock.advance(60_000)
  expect((await said).text).toMatch(/pane is open/)
  expect(w.quick.length).toBe(1)
  expect(await buttons(ui)).toEqual(ROWS)
  expect(await rows(ui)).toEqual(FULL_ROWS)
  all = (await texts(ui)).join('\n')
  expect(all).not.toMatch(/unrecognized|older|update Longhaul|could not read|checking…/i)
  await ui.unmount()
})

both('a refresh keeps the last rows on screen, and a late full read never covers a newer one', async ($, on, surface) => {
  const w = world(on)
  const NEWER = { ...STATUS, accounts: ACCOUNTS.map(a => (a.name === 'work-2' ? { ...a, fallback: true } : a)) }
  const FALLBACK = `  work-2      pro · NOT signed in · limit until ${clock(AT + 3 * HOUR)} (in 3 h)\n`
    + `${UNDER}fallback · only in ~/work, ~/clients`
  // Opened while a check hangs: the rows from the quick read, the first full read under way.
  w.slow.add('status')
  const { ui } = await opened($, w, surface)
  expect(await rows(ui)).toEqual(QUICK_ROWS)
  // An action meanwhile reads again, at once this time, and finds what it changed.
  w.slow.delete('status')
  w.status = done(JSON.stringify(NEWER))
  await open(ui, 'row-work-2')
  await ui.press({ key: 'fallback' })
  expect(after(w)).toEqual([['fallback', 'work-2', 'on'], ['status', '--json']])
  await ui.press({ key: 'back' })
  expect(await rows(ui)).toEqual({ ...FULL_ROWS, 'row-work-2': FALLBACK })
  // The first full read comes back late, with the record as it was: it is dropped.
  await w.clock.advance(60_000)
  expect(await rows(ui)).toEqual({ ...FULL_ROWS, 'row-work-2': FALLBACK })
  // A refresh whose reads are both slow keeps the last record drawn, never "Reading…" nor "checking…".
  w.slow.add('status')
  w.slow.add('quick')
  w.status = done(JSON.stringify(STATUS))
  await open(ui, 'row-work-2')
  await ui.press({ key: 'fallback' })
  expect(await texts(ui)).toContain('Marking work-2 as a normal account…')
  await ui.press({ key: 'back' })
  expect(await rows(ui)).toEqual({ ...FULL_ROWS, 'row-work-2': FALLBACK })
  expect((await texts(ui)).join('\n')).not.toMatch(/Reading Longhaul's accounts|checking…/)
  // Both come back at the same moment: the full one stands, whichever is last.
  await w.clock.advance(60_000)
  expect(await rows(ui)).toEqual(FULL_ROWS)
  // A quick read slower than its full one comes too late to be drawn.
  w.slow.delete('status')
  w.status = done(JSON.stringify(NEWER))
  await open(ui, 'row-work-2')
  await ui.press({ key: 'fallback' })
  await ui.press({ key: 'back' })
  expect(await rows(ui)).toEqual({ ...FULL_ROWS, 'row-work-2': FALLBACK })
  await w.clock.advance(60_000)
  expect(await rows(ui)).toEqual({ ...FULL_ROWS, 'row-work-2': FALLBACK })
  expect(w.quick.length).toBe(4)
  await ui.unmount()
})

both('a quick read left by a reload, its checks cut off, is read again', async ($, on, surface) => {
  const w = world(on)
  // What the module before the reload stored: the rows of a quick read, its full read gone with that module.
  // Given under the store's own version, so the writes after it go through.
  const QUICK = JSON.parse(unchecked(done(JSON.stringify(STATUS))).stdout)
  let written = false
  on('state.set', (_: any, e: any, next: any) => {
    if (e.key === 'accountsRead') written = true
    return next(e)
  })
  on('state.get', async (_: any, e: any, next: any) => {
    if (e.key !== 'accountsRead' || written) return next(e)
    const real = await next(e)
    return { value: { version: 0, ...(real?.value ?? {}),
      value: { shape: 'accounts-read-2', value: { status: QUICK, problem: null, update: false, lines: [] } } } }
  })
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const ui = await $.ui.mount(pane(surface))
  await w.clock.settle()
  expect(w.runs).toEqual([['status', '--json']])
  expect(await rows(ui)).toEqual(FULL_ROWS)
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
  expect(await texts(ui)).toContain('Forgetting all recorded limits…')
  await expect(ui.press({ key: 'clear' })).rejects.toBeDefined()
  // Elsewhere too: an account's page shows its details, and no action.
  await ui.press({ key: 'back' })
  await open(ui, 'row-work-2')
  expect(await buttons(ui)).toEqual(['back'])
  expect(await texts(ui)).toContain('Forgetting all recorded limits…')
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
  expect(await rowOf(ui, 'row-odd-6')).toMatch(/^ {2}odd-6 {2}could not check\n {9}used nowhere$/) // under the text of names of 5
  expect((await ui.find({ key: 'row-settings' }))?.text).toMatch(/switching: not known/)
  await open(ui, 'row-odd-5')
  let all = (await texts(ui)).join('\n')
  expect(all).toContain("could not check: its config_dir is not a folder's path")
  expect(all).toContain(`5-hour 100%, resets ${clock(AT + 3600.5)}`)
  expect(all).toContain(`limit until ${clock(AT + 3600.5)} (in 1 h)`)
  expect(all).not.toMatch(/sign-in folder|NaN|undefined/)
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
  expect(all).toMatch(/personal-1\s+max · signed in · 5h 40% · week 12%/)
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
  expect((await ui.find({ key: 'row-personal-1' }))?.text).toMatch(/max · sign-in failed · 5h 40% · week 12%/)
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

both("an account's Folders: each folder with Remove, a field to add one, and Remove all folder limits, each running folders", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  const all = await texts(ui)
  for (const t of ['Runs only in:', '  ~/work', '  ~/clients']) expect(all).toContain(t)
  expect(await buttons(ui)).toEqual(['clear-work-2', 'signin-work-2', 'remove-work-2', 'folder-remove-0', 'folder-remove-1', 'folder-add',
    'folder-anywhere', 'fallback', 'back'])
  expect((await ui.find({ key: 'folder-remove-1' }))?.text).toBe('Remove')
  expect((await ui.find({ key: 'folder-anywhere' }))?.text).toBe('Remove all folder limits…')
  expect(await ui.find({ key: 'folder-path' })).toBeDefined()
  // Each change says first what it would do (`folders ... --dry-run --json`, M4), then waits for a yes.
  await ui.press({ key: 'folder-remove-1' })
  expect(after(w)).toEqual([['folders', 'work-2', '--remove', '~/clients', '--dry-run', '--json']])
  let seen = await texts(ui)
  expect(seen).toContain("Take ~/clients off work-2's folders?")
  for (const t of PREVIEW.effects) expect(seen).toContain(t)
  expect(seen).not.toContain(PREVIEW.changes[0]) // the question says the change
  expect(await ringed(ui)).toEqual(['folders-no'])
  expect(await ui.find({ key: 'folder-remove-0' })).toBeUndefined() // nothing else while it asks
  expect(await ui.find({ key: 'signin-work-2' })).toBeUndefined()
  expect((await ui.find({ key: 'folders-yes' }))?.text).toBe('Take it off')
  await ui.press({ key: 'folders-yes' })
  expect(after(w).slice(1)).toEqual([['folders', 'work-2', '--remove', '~/clients'], ['status', '--json']])
  expect(w.timeouts.slice(1)).toEqual([60_000, 60_000, 60_000])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Remove folder ~/clients: work-2'))).toBe(true)
  expect(await texts(ui)).not.toContain(PREVIEW.effects[1]) // the question is gone with its yes
  // A folder typed, trimmed: one that would stop a chat says so first; Cancel runs nothing.
  w.answer.folders = dryOr(() => STOPS)
  await ui.input({ key: 'folder-path', text: ' ~/new ' })
  await ui.press({ key: 'folder-add' })
  expect(after(w).slice(3)).toEqual([['folders', 'work-2', '--add', '~/new', '--dry-run', '--json']])
  seen = await texts(ui)
  expect(seen).toContain("Add ~/new to work-2's folders? It would stop 1 chat.")
  for (const t of STOPS.effects) expect(seen).toContain(t)
  expect((await ui.find({ key: 'folders-yes' }))?.text).toBe('Add the folder')
  await ui.press({ key: 'folders-no' })
  expect(after(w).length).toBe(4)
  expect(await ui.find({ key: 'folders-yes' })).toBeUndefined()
  // Then the field empty again once it worked.
  await ui.press({ key: 'folder-add' })
  await ui.press({ key: 'folders-yes' })
  expect(after(w).slice(4)).toEqual([['folders', 'work-2', '--add', '~/new', '--dry-run', '--json'],
    ['folders', 'work-2', '--add', '~/new'], ['status', '--json']])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Add folder ~/new: work-2'))).toBe(true)
  expect((await ui.find({ key: 'folder-path' }))?.text).toBe('')
  // It asks first, naming the folders it would un-reserve (the read after Add folder still lists two),
  // with what it would do.
  w.answer.folders = dryOr(() => PREVIEW)
  await ui.press({ key: 'folder-anywhere' })
  expect(after(w).slice(7)).toEqual([['folders', 'work-2', '--anywhere', '--dry-run', '--json']])
  seen = await texts(ui)
  expect(seen).toContain('Remove all of work-2\'s folder limits? ~/work, ~/clients would no longer be '
    + 'reserved for work-2, and it could run in any folder no other account reserves.')
  for (const t of PREVIEW.effects) expect(seen).toContain(t)
  expect(await ringed(ui)).toEqual(['anywhere-no'])
  expect(await ui.find({ key: 'folder-remove-0' })).toBeUndefined() // nothing else while it asks
  expect(await ui.find({ key: 'signin-work-2' })).toBeUndefined()
  await ui.press({ key: 'anywhere-no' })
  expect(after(w).length).toBe(8)
  expect(await ui.find({ key: 'anywhere-yes' })).toBeUndefined()
  // Back cancels it too.
  await ui.press({ key: 'folder-anywhere' })
  await ui.press({ key: 'back' })
  expect(await ui.find({ key: 'anywhere-yes' })).toBeUndefined()
  expect(await ui.find({ key: 'folder-anywhere' })).toBeDefined()
  expect(after(w).length).toBe(9)
  // Only a yes runs it.
  await ui.press({ key: 'folder-anywhere' })
  await ui.press({ key: 'anywhere-yes' })
  expect(after(w).slice(9)).toEqual([['folders', 'work-2', '--anywhere', '--dry-run', '--json'],
    ['folders', 'work-2', '--anywhere'], ['status', '--json']])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Remove all folder limits: work-2'))).toBe(true)
  await ui.unmount()
})

both('Add folder refuses an empty or relative folder and runs nothing; the field is per page', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain('Runs in: any folder no other account reserves')
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
  expect(await rowOf(ui, 'row-spare-5')).toMatch(/spare-5 .*\n {14}used nowhere$/)
  expect(await rowOf(ui, 'row-personal-1')).toMatch(/\n {14}everywhere \(its folders include \/\)$/)
  await open(ui, 'row-spare-5')
  const all = await texts(ui)
  expect(all).toContain('may be used nowhere for now')
  expect(all).toContain('⚠ personal-1, personal-2 keep every folder, so spare-5 is used nowhere until a folder is added '
    + 'here (or / is removed from theirs).')
  await ui.input({ key: 'folder-path', text: '~/code' })
  await ui.press({ key: 'folder-add' })
  await ui.press({ key: 'folders-yes' })
  expect(after(w)).toEqual([['folders', 'spare-5', '--add', '~/code', '--dry-run', '--json'],
    ['folders', 'spare-5', '--add', '~/code'], ['status', '--json']])
  // Kept out by one: worded for one.
  w.status = done(JSON.stringify({ ...STATUS, accounts: [keeper('personal-1'), { ...spare, kept_out_by: ['personal-1'] }] }))
  await ui.press({ key: 'signin-spare-5' })
  expect(await texts(ui)).toContain('⚠ personal-1 keeps every folder, so spare-5 is used nowhere until a folder is added '
    + "here (or / is removed from personal-1's).")
  // The keeper's own page: / with its Remove, and Remove all folder limits.
  await ui.press({ key: 'back' })
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain('  /')
  expect(await texts(ui)).toContain('may be used everywhere (its folders include /)')
  expect(await buttons(ui)).toEqual(['signin-personal-1', 'remove-personal-1', 'folder-remove-0', 'folder-add',
    'folder-anywhere', 'fallback', 'back'])
  await ui.press({ key: 'folder-remove-0' })
  await ui.press({ key: 'folders-yes' })
  expect(after(w).slice(-2)).toEqual([['folders', 'personal-1', '--remove', '/'], ['status', '--json']])
  await ui.unmount()
})

both('a folders field that is not a list: the page says so, offers Remove all folder limits and no field to add to it', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-spare-3')
  expect(await texts(ui)).toContain('Runs in: no folder, as its repositories field is not a list of absolute paths. '
    + 'Remove all folder limits drops it.')
  expect(await buttons(ui)).toEqual(['signin-spare-3', 'remove-spare-3', 'folder-anywhere', 'fallback', 'back'])
  expect(await ui.find({ key: 'folder-path' })).toBeUndefined()
  await ui.press({ key: 'folder-anywhere' })
  expect(after(w)).toEqual([['folders', 'spare-3', '--anywhere', '--dry-run', '--json']])
  expect(await texts(ui)).toContain("Drop spare-3's repositories field? It is not a list of paths; spare-3 could then "
    + 'run in any folder no other account reserves.')
  await ui.press({ key: 'anywhere-yes' })
  expect(after(w).slice(1)).toEqual([['folders', 'spare-3', '--anywhere'], ['status', '--json']])
  await ui.unmount()
})

both('a folders change its dry run refuses shows why and asks nothing; a typed email is masked, and passed whole', async ($, on, surface) => {
  const w = world(on)
  const why = "~/clients is not one of work-2's folders; it may be used only in ~/work"
  w.answer.folders = dryOr(() => ({ changes: [], notes: [], effects: [], stops: 0, problem: why }))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-work-2')
  await ui.press({ key: 'folder-remove-1' })
  let all = await texts(ui)
  expect(all.some(t => t.startsWith('✘') && t.includes('Remove folder ~/clients: work-2'))).toBe(true)
  expect(all).toContain(why)
  expect(await ui.find({ key: 'folders-yes' })).toBeUndefined()
  expect(after(w)).toEqual([['folders', 'work-2', '--remove', '~/clients', '--dry-run', '--json']]) // nothing else ran
  expect(await ui.find({ key: 'folder-remove-1' })).toBeDefined() // the buttons back
  // A run the change itself refuses shows its words, as before.
  w.answer.folders = dryOr(() => PREVIEW, done('', 2, `longhaul ${why}\n`))
  await ui.press({ key: 'folder-remove-1' })
  await ui.press({ key: 'folders-yes' })
  expect(await texts(ui)).toContain(`longhaul ${why}`)
  w.answer.folders = dryOr(() => PREVIEW)
  await ui.input({ key: 'folder-path', text: '/srv/first.last@example.com' })
  await ui.press({ key: 'folder-add' })
  expect(await texts(ui)).toContain("Add /…@example.com to work-2's folders?")
  await ui.press({ key: 'folders-yes' })
  expect(after(w).slice(-2)).toEqual([['folders', 'work-2', '--add', '/srv/first.last@example.com'], ['status', '--json']])
  all = await texts(ui)
  expect(all.some(t => t.startsWith('✔') && t.includes('Add folder /…@example.com: work-2'))).toBe(true)
  expect(all.join('\n')).not.toMatch(/first\.last/)
  await ui.unmount()
})

// First choice (0.3.9's "preferred"): the folders an account is used first
// in while it has room. Its page shows them, each with Remove, which runs
// `longhaul.py folders NAME --no-prefer PATH`.

both("an account's first choices: each with Remove, which runs folders NAME --no-prefer PATH", async ($, on, surface) => {
  const w = world(on)
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[0], preferred: ['~/code/client'] },
    { ...ACCOUNTS[1], preferred: ['~/work/a', '~/work/b'] }, { ...ACCOUNTS[2], preferred: [] },
    { ...ACCOUNTS[3], preferred: 'everywhere' }] }))
  const { ui } = await opened($, w, surface)
  await open(ui, 'row-personal-1')
  expect(await texts(ui)).toContain('First choice in: ~/code/client')
  expect(await buttons(ui)).toEqual(['signin-personal-1', 'remove-personal-1', 'prefer-remove-0', 'folder-add', 'fallback',
    'back'])
  expect((await ui.find({ key: 'prefer-remove-0' }))?.text).toBe('Remove')
  await ui.press({ key: 'prefer-remove-0' })
  expect(after(w)).toEqual([['folders', 'personal-1', '--no-prefer', '~/code/client'], ['status', '--json']])
  expect((await texts(ui)).some(t => t.startsWith('✔') && t.includes('Remove first choice ~/code/client: personal-1')))
    .toBe(true)
  await ui.press({ key: 'back' })
  // Several: one line each.
  await open(ui, 'row-work-2')
  const all = await texts(ui)
  for (const t of ['First choice in:', '  ~/work/a', '  ~/work/b']) expect(all).toContain(t)
  await ui.press({ key: 'prefer-remove-1' })
  expect(after(w).slice(2)).toEqual([['folders', 'work-2', '--no-prefer', '~/work/b'], ['status', '--json']])
  // No Remove while a yes is asked for.
  await ui.press({ key: 'remove-work-2' })
  expect(await ui.find({ key: 'prefer-remove-0' })).toBeUndefined()
  expect(await texts(ui)).toContain('  ~/work/a')
  await ui.press({ key: 'remove-no' })
  await ui.press({ key: 'back' })
  // First choice nowhere, or a field that is not a list: no line.
  for (const name of ['spare-3', 'team-4']) {
    await open(ui, `row-${name}`)
    expect((await texts(ui)).some(t => /First choice/.test(t))).toBe(false)
    await ui.press({ key: 'back' })
  }
  await ui.unmount()
})

// A fallback account (the owner, 2026-11-06: "A fallback mark"): used only
// when no other account allowed here has room. Its line says so, its page
// says what it is, and a button runs `longhaul.py fallback NAME on|off`.

both('a fallback: its line and page say so, and the button runs fallback NAME off or on', async ($, on, surface) => {
  const w = world(on)
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[0], fallback: true }, ...ACCOUNTS.slice(1)] }))
  const { ui } = await opened($, w, surface)
  expect(await rowOf(ui, 'row-personal-1')).toBe(`  personal-1  max · signed in · 5h 40% · week 12%\n`
    + `${UNDER}this chat · fallback`)
  expect(await rowOf(ui, 'row-work-2')).toMatch(/\n {14}only in ~\/work, ~\/clients$/) // normal: no word
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
  expect(await rowOf(ui, 'row-personal-1')).toBe(`  personal-1  max · signed in · 5h 40% · week 12%\n${UNDER}this chat`)
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
  // Wait names the minute it carries on: the reset, from a relay with no carries_on_at.
  expect((await ui.find({ key: 'answer-wait' }))?.text).toBe(`Wait, carry on at ${clock(AT + 38 * HOUR)}`)
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

both("Wait names the relay's carry-on minute; a question with no time waits for the reset", async ($, on, surface) => {
  const w = world(on)
  write(w, QUESTION_FILE, { ...QUESTION, carries_on_at: AT + 38 * HOUR + 60 })
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'answer-wait' }))?.text).toBe(`Wait, carry on at ${clock(AT + 38 * HOUR + 60)}`)
  write(w, QUESTION_FILE, { ...QUESTION, id: 'q-dddddddddddd', resets_at: 0 })
  await w.clock.advance(3000)
  expect((await ui.find({ key: 'answer-wait' }))?.text).toBe('Wait for the reset')
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
  expect((await ui.find({ key: 'answer-no' }))?.text).toBe('No, leave this chat stopped')
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
