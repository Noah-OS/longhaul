import { expect, mock, test } from 'claude-code/testing'

// hooks/accounts.tsx, the accounts pane: `claude plugin test .` from the
// checkout. The plugin loads whole (display.ts and autocompact.ts too), so
// the world answers what each calls at a session's start. Every program the
// pane runs is recorded here and answered from memory: nothing runs, and no
// real file or account is touched. Emails are example.com, names generic.

const PANE_ID = 'longhaul-accounts'
const NOW = Date.UTC(2026, 9, 31, 12, 0, 0)
const HOUR = 3600
const AT = NOW / 1000
const NOTE = "finish in the browser that opened, with that account's email. If the browser is signed in to claude.ai "
  + 'as someone else, copy the link into a private window.'
const RELOAD = 'Reload VS Code: Cmd+Shift+P, Developer: Reload Window.'

const ACCOUNTS = [
  { name: 'personal-1', email: 'a•••@example.com', signed_in: true, as_expected: true, check_failed: null, plan: 'max',
    limited_until: null, five_hour: { used: 40, resets_at: AT + 2 * HOUR }, week: { used: 12, resets_at: AT + 50 * HOUR },
    only_in: [], folder: '~/.claude-personal-1', this_chat: true },
  { name: 'work-2', email: 'b•••@example.com', signed_in: false, as_expected: null, check_failed: null, plan: 'pro',
    limited_until: AT + 3 * HOUR, five_hour: null, week: null, only_in: ['~/work', '~/clients'],
    folder: '~/.claude-work-2', this_chat: false },
  { name: 'spare-3', email: 'c•••@example.com', signed_in: true, as_expected: false, check_failed: null, plan: null,
    limited_until: null, five_hour: null, week: null, only_in: null, folder: '~/.claude-spare-3', this_chat: false },
  { name: 'team-4', email: null, signed_in: null, as_expected: null, check_failed: 'the check timed out', plan: null,
    limited_until: null, five_hour: null, week: null, only_in: [], folder: '~/.claude-team-4', this_chat: false },
]
const STATUS = { accounts: ACCOUNTS, listed: true, here: { folder: '~/code/x', may_use: ['personal-1', 'team-4'] },
  switch: 'ask', switch_problem: null, vscode: false, problem: null }

type Ran = { exitCode: number; stdout: string; stderr: string }
type World = {
  runs: string[][] // each longhaul.py run's arguments, after the script
  timeouts: number[] // each run's timeoutMs, in the same order
  opened: any[] // each $.ui.open's argument
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

// The engine beneath the plugins, from memory: programs, panes, the clock,
// and what display.ts asks at a session's start (it shows nothing here).
function world(on: any): World {
  const w: World = { runs: [], timeouts: [], opened: [], status: done(JSON.stringify(STATUS)), answer: {},
    slow: new Set(), placed: true, clock: mock.clock(on, { now: NOW }) }
  mock.env(on, { LONGHAUL_HOME: '/scratch/longhaul', HOME: '/scratch/home' })
  on('session.start', (_: any, e: any) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'fcd7fbcc-bc91-4a30-9dce-e7496a7f4aa6' }))
  on('command.register', (_: any, e: any) => ({ value: { command: e.name } }))
  on('fs.stat', () => ({ deny: 'no such file' }))
  on('fs.read', () => ({ deny: 'no such file' }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.open', (_: any, e: any) => {
    w.opened.push(e)
    return { value: w.placed ? { isPlaced: true } : { isPlaced: false, reason: 'the terminal is 80 columns wide' } }
  })
  on('process.run', async (_: any, e: any) => {
    const argv: string[] = [...e.argv]
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

async function texts(ui: any): Promise<string[]> {
  return (await ui.findAll({ type: 'Text' })).map((t: any) => t.text)
}

async function buttons(ui: any): Promise<string[]> {
  return (await ui.findAll({ type: 'Button' })).map((b: any) => b.key)
}

// Each test on the terminal and in VS Code, the two surfaces that take
// input, each run its own (a fresh plugin, state and world).
type Surface = 'terminal' | 'vscode'
function both(name: string, body: ($: any, on: any, surface: Surface) => Promise<void>) {
  for (const surface of ['terminal', 'vscode'] as const) test(`${name} (${surface})`, ($, on) => body($, on, surface))
}

both('/accounts opens the pane and reads the accounts', async ($, on, surface) => {
  const w = world(on)
  const { said, ui } = await opened($, w, surface)
  expect(w.opened).toEqual([{ id: PANE_ID, title: 'Longhaul accounts', focus: true, closeOnEscape: true }])
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

both('a row per account: plan, sign-in, use, limit, where, the masked email; then where we are', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  const all = (await texts(ui)).join('\n')
  expect(all).toMatch(/personal-1 \(this chat\)/)
  expect(all).toMatch(/max · signed in/)
  expect(all).toMatch(/5-hour 40%/)
  expect(all).toMatch(/week 12%/)
  expect(all).toMatch(/work-2/)
  expect(all).toMatch(/NOT signed in/)
  expect(all).toMatch(/at its limit until /)
  expect(all).toMatch(/only in ~\/work, ~\/clients/)
  expect(all).toMatch(/signed in as someone else/)
  expect(all).toMatch(/nowhere/)
  expect(all).toMatch(/could not check: the check timed out/)
  expect(all).toMatch(/anywhere/)
  for (const email of ['a•••@example.com', 'b•••@example.com', 'c•••@example.com']) expect(all).toContain(email)
  expect(all).toContain('Here, in ~/code/x: personal-1, team-4 may be used.')
  await ui.unmount()
})

// The runs after the first read, each followed by the read again.
function after(w: World): string[][] {
  return w.runs.slice(1)
}

both('Sign in again for a signed-in account, Sign in for one that is not, the note while it runs', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  expect((await ui.find({ key: 'signin-personal-1' }))?.text).toBe('Sign in again')
  expect((await ui.find({ key: 'signin-work-2' }))?.text).toBe('Sign in')
  expect((await ui.find({ key: 'signin-spare-3' }))?.text).toBe('Sign in')
  expect((await ui.find({ key: 'signin-team-4' }))?.text).toBe('Sign in again')
  w.slow.add('setup')
  await ui.press({ key: 'signin-personal-1' })
  expect(await texts(ui)).toContain(`Signing in personal-1: ${NOTE}`)
  await w.clock.advance(60_000)
  await ui.press({ key: 'signin-work-2' })
  await w.clock.advance(60_000)
  expect(after(w)).toEqual([['setup', 'personal-1', '--relogin'], ['status', '--json'], ['setup', 'work-2'], ['status', '--json']])
  expect(w.timeouts.slice(1)).toEqual([600_000, 60_000, 600_000, 60_000])
  expect((await texts(ui)).some(t => t.startsWith('✔') && /work-2/.test(t))).toBe(true)
  await ui.unmount()
})

both('Remove runs only after its confirmation, and Cancel runs nothing', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  await ui.press({ key: 'remove-work-2' })
  expect(after(w)).toEqual([])
  expect(await texts(ui)).toContain("Remove work-2 from Longhaul's list? Its folder and sign-in stay.")
  await ui.press({ key: 'remove-no' })
  expect(after(w)).toEqual([])
  expect(await ui.find({ key: 'remove-yes' })).toBeUndefined()
  await ui.press({ key: 'remove-work-2' })
  await ui.press({ key: 'remove-yes' })
  expect(after(w)).toEqual([['remove', 'work-2'], ['status', '--json']])
  await ui.unmount()
})

both('Add refuses a bad name, email or folder and runs nothing', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
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
  await ui.input({ key: 'add-name', text: 'work-5' })
  await ui.input({ key: 'add-email', text: 'new@example.com' })
  await ui.input({ key: 'add-only', text: ' ~/work , /srv/clients,' })
  w.slow.add('add')
  await ui.press({ key: 'add' })
  expect(await texts(ui)).toContain(`Adding work-5: ${NOTE}`)
  await w.clock.advance(60_000)
  expect(after(w)).toEqual([['add', 'work-5', 'new@example.com', '--repo', '~/work', '--repo', '/srv/clients'], ['status', '--json']])
  expect(w.timeouts[1]).toBe(600_000)
  // Once it is done the fields are empty again; with no folder, no --repo.
  w.slow.clear()
  await ui.input({ key: 'add-name', text: 'spare-6' })
  await ui.input({ key: 'add-email', text: 'other@example.com' })
  await ui.press({ key: 'add' })
  expect(after(w).slice(2)).toEqual([['add', 'spare-6', 'other@example.com'], ['status', '--json']])
  await ui.unmount()
})

both('switching, the VS Code panel and forgetting the limits run their commands', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  const all = (await texts(ui)).join('\n')
  for (const line of [/ask: asks first/, /auto: moves by itself/, /never: waits for the reset/]) expect(all).toMatch(line)
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
  await ui.unmount()
})

both("the last action's output: its last ten lines, stdout and stderr, every email masked", async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  const lines = Array.from({ length: 14 }, (_, i) => `line ${i + 1}`)
  w.answer.setup = done(`${lines.join('\n')}\nsigned in as first.last@example.com\n`, 1,
    'should be second@example.com, not <other+tag@example.com>\n')
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
  expect(await ui.find({ key: 'signin-personal-1' })).toBeDefined()
  expect(w.runs.filter(r => r[0] !== 'status')).toEqual([])
  await ui.unmount()
})

both('while an action runs no other is offered, and a press then runs nothing', async ($, on, surface) => {
  const w = world(on)
  const { ui } = await opened($, w, surface)
  w.slow.add('clear')
  await ui.press({ key: 'clear' })
  expect(await buttons(ui)).toEqual([])
  expect(await ui.find({ type: 'Input' })).toBeUndefined()
  expect(await ui.find({ type: 'Select' })).toBeUndefined()
  await expect(ui.press({ key: 'clear' })).rejects.toBeDefined()
  await expect(ui.press({ key: 'signin-work-2' })).rejects.toBeDefined()
  await w.clock.advance(60_000)
  expect(after(w)).toEqual([['clear'], ['status', '--json']])
  expect(await buttons(ui)).toContain('clear')
  await ui.unmount()
})

both('a name the check refuses gets no button, and nothing runs with it', async ($, on, surface) => {
  const w = world(on)
  w.status = done(JSON.stringify({ ...STATUS, accounts: [{ ...ACCOUNTS[1], name: '--purge' }, ACCOUNTS[0]] }))
  const { ui } = await opened($, w, surface)
  expect((await buttons(ui)).filter(k => k.includes('purge'))).toEqual([])
  expect(await ui.find({ key: 'signin-personal-1' })).toBeDefined()
  await ui.unmount()
})

both('an account with no folder and times in decimals, and an odd record, are drawn as far as they go', async ($, on, surface) => {
  const w = world(on)
  // S1's record: `folder` is "" when config_dir is not text, and times may be decimals.
  const noFolder = { ...ACCOUNTS[0], name: 'odd-5', folder: '', signed_in: null, as_expected: null, this_chat: false,
    check_failed: "its config_dir is not a folder's path", limited_until: AT + 3600.5,
    five_hour: { used: 99.6, resets_at: AT + 3600.5 } }
  const odd = { name: 'odd-6', only_in: 'everywhere', five_hour: 'full', signed_in: 'yes' }
  w.status = done(JSON.stringify({ ...STATUS, accounts: [noFolder, odd, 7], here: 'x', switch: 'sometimes' }))
  const { ui } = await opened($, w, surface)
  const all = (await texts(ui)).join('\n')
  expect(all).toContain("could not check: its config_dir is not a folder's path")
  expect(all).toMatch(/5-hour 100%, resets \w{3} \d\d:\d\d/)
  expect(all).toMatch(/at its limit until \w{3} \d\d:\d\d/)
  expect(all).toMatch(/odd-6\n/)
  expect(all).toMatch(/may be used nowhere/)
  expect(all).not.toMatch(/NaN|undefined/)
  expect(await ui.find({ key: 'signin-odd-5' })).toBeDefined()
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
  expect(await ui.find({ key: 'signin-personal-1' })).toBeDefined()
  await ui.unmount()
})

test('on the phone: the rows, nothing to type or pick, and to manage accounts from the computer', async ($, on) => {
  const w = world(on)
  await $.session.start({ cwd: '/scratch/code/x', surface: 'mobile', isInteractive: true })
  await $.command.run(TYPED)
  const ui = await $.ui.mount(pane('mobile'))
  const all = (await texts(ui)).join('\n')
  expect(all).toContain('a•••@example.com')
  expect(all).toMatch(/NOT signed in/)
  expect(all).toMatch(/manage accounts from the computer/i)
  expect(await buttons(ui)).toEqual([])
  expect(w.runs).toEqual([['status', '--json']])
  await ui.unmount()
})

both('an action left running by a reload shows as not known, and the rows are read again', async ($, on, surface) => {
  const w = world(on)
  // What the module before the reload stored: its own action, running.
  let written = false
  on('state.set', (_: any, e: any, next: any) => {
    if (e.key === 'accountsRunning') written = true
    return next(e)
  })
  on('state.get', (_: any, e: any, next: any) => e.key === 'accountsRunning' && !written
    ? { value: { value: { shape: 'accounts-running-1', value: { by: 'before', label: 'Sign in work-2', note: 'Signing in work-2' } }, version: 1 } }
    : next(e))
  await $.session.start({ cwd: '/scratch/code/x', surface, isInteractive: true })
  const ui = await $.ui.mount(pane(surface))
  await w.clock.settle()
  const shown = await texts(ui)
  expect(shown.some(t => /Sign in work-2/.test(t) && /not known/.test(t))).toBe(true)
  expect(w.runs).toEqual([['status', '--json']])
  expect(await ui.find({ key: 'signin-work-2' })).toBeDefined()
  await ui.unmount()
})
