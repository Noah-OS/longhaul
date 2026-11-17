import { expect, mock, test } from 'claude-code/testing'

import { line } from './display.ts'
import { clock } from './question.ts'
import { CASES } from './status-line.cases.ts'

// hooks/display.ts's status entry, the panel's stand-in for the status line:
// `claude plugin test .` from the checkout. It ends with "update 0.3.3" when
// release.json's latest is newer than the plugin's own manifest (the
// keep-working plan's K3, 2026-10-31). The plugin is loaded whole
// (autocompact.ts too), so each test answers what both modules call;
// nothing here touches a real file.

const SID = 'fcd7fbcc-bc91-4a30-9dce-e7496a7f4aa6'
const HOME = '/scratch/longhaul'
const RELEASE = `${HOME}/release.json`
const MANIFEST = '/.claude-plugin/plugin.json' // under the plugin's root
const NOW = Date.UTC(2026, 9, 31, 12, 0, 0)
const RESETS = 4_000_000_000 // seconds: a window open whatever the clock says
// The entry of a chat on ~/.claude's own sign-in with no reading kept yet.
const BARE = 'default sign-in · no usage reading yet'
const USAGE = { input_tokens: 6, cache_read_input_tokens: 600_000, cache_creation_input_tokens: 2_000, output_tokens: 900, model: 'claude-opus-4-5' }

type World = {
  release?: string // release.json as it is on disk; undefined for none
  manifest?: string // the plugin's .claude-plugin/plugin.json
  usage?: string // usage/main.json
  files: Record<string, { text: string; at: number }> // this chat's question and wait, by path
  alive: Set<number> // the pids `kill -0` finds
  context: number // the live context, in tokens
  reads: string[]
  statuses: string[]
  clock: ReturnType<typeof mock.clock>
}

const json = (value: unknown) => JSON.stringify(value)

// The engine beneath the plugin, from memory: the files, the log (empty),
// the session, the status entry.
function world(on: any, env: Record<string, string> = {}): World {
  const w: World = { release: json({ checked_at: NOW / 1000, latest: '0.3.3' }), manifest: json({ name: 'longhaul', version: '0.3.2' }),
    files: {}, alive: new Set(), context: 612_345, reads: [], statuses: [], clock: mock.clock(on, { now: NOW }) }
  mock.env(on, { LONGHAUL_HOME: HOME, HOME: '/scratch/home', ...env })
  on('session.id', () => ({ value: SID }))
  on('session.start', ($: any, e: any) => ({ cwd: e.cwd, source: e.source }))
  on('session.usage', () => ({ value: { startedAt: NOW - 3_600_000, rateLimits: [],
    context: { tokens: w.context, window: 1_000_000, percent: 61 } } }))
  on('fs.stat', ($: any, e: any) => {
    const f = w.files[e.path]
    return f ? { value: { kind: 'file', size: f.text.length, mtimeMs: f.at, isLink: false } } : { deny: 'no such file' }
  })
  on('fs.read', ($: any, e: any) => {
    w.reads.push(e.path)
    const found = e.path === RELEASE ? w.release : e.path.endsWith(MANIFEST) ? w.manifest
      : e.path === `${HOME}/usage/main.json` ? w.usage : w.files[e.path]?.text
    return found === undefined ? { deny: 'no such file' } : { value: found }
  })
  // The programs run here: kill -0 on a relay's pid (the accounts pane's look,
  // and the line's for a choice a relay took), and tail -c N on the log.
  const ran = (exitCode: number, stdout: string) => ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false,
    isStderrTruncated: false } })
  on('process.run', ($: any, e: any) => e.argv[0] === 'kill' && e.argv[1] === '-0'
    ? ran(w.alive.has(Number(e.argv[2])) ? 0 : 1, '')
    : e.argv[0] === 'tail' && e.argv[1] === '-c' && w.files[e.argv[3]] ? ran(0, w.files[e.argv[3]]!.text.slice(-Number(e.argv[2])))
    : { deny: 'not run here' })
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', ($: any, e: any) => { w.statuses.push(e.text); return { value: undefined } })
  on('turn.complete', ($: any, e: any) => ({ text: e.answer, usage: e.usage }))
  return w
}

async function start($: any, w: World): Promise<string | undefined> {
  // isInteractive as a session gives it: the accounts pane's start hook, which starts its look, matches on it.
  await $.session.start({ source: 'startup', cwd: '/scratch/project', isInteractive: true })
  return w.statuses[w.statuses.length - 1]
}

test('a newer release ends the status entry with its version', async ($, on) => {
  const w = world(on)
  expect(await start($, w)).toBe(`${BARE} · update 0.3.3`)
  expect(w.reads.filter(p => p.endsWith(MANIFEST))[0]).toMatch(/^\/.*\/\.claude-plugin\/plugin\.json$/)
})

test('the same or an older release, or one compared as text, shows nothing', async ($, on) => {
  const w = world(on)
  for (const latest of ['0.3.2', '0.3.1', '0.2.9']) {
    w.release = json({ latest })
    expect(await start($, w)).toBe(BARE)
  }
  // As numbers: 0.3.10 is newer than 0.3.9, though not as text.
  w.manifest = json({ version: '0.3.9' })
  w.release = json({ latest: '0.3.10' })
  expect(await start($, w)).toBe(`${BARE} · update 0.3.10`)
  w.manifest = json({ version: '0.3.10' })
  w.release = json({ latest: '0.3.9' })
  expect(await start($, w)).toBe(BARE)
})

for (const env of [{ LONGHAUL_UPDATE_CHECK: 'off' }, { LONGHAUL_UPDATE_CHECK: 'OFF' }, { LONGHAUL_UPDATE_CHECK: 'Off' },
                   { CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1' }])
  test(`the check turned off (${Object.entries(env)[0]!.join('=')}) shows nothing and reads no record`, async ($, on) => {
    const w = world(on, env)
    expect(await start($, w)).toBe(BARE)
    expect(w.reads).not.toContain(RELEASE)
  })

test('a record or a manifest it cannot use shows nothing extra', async ($, on) => {
  const w = world(on)
  for (const release of [undefined, 'not json', '', '[]', 'null', '3', '{}', json({ latest: null }), json({ latest: 3 }),
                         json({ latest: [0, 3, 3] }), json({ latest: 'soon' }), json({ latest: '0.3.3-beta' }),
                         json({ latest: '0.3' }), json({ latest: ' 0.3.3' }), json({ error: 'timeout', checked_at: 1 })]) {
    w.release = release
    expect(await start($, w)).toBe(BARE)
  }
  w.release = json({ latest: '0.3.3' })
  for (const manifest of [undefined, 'not json', '[]', 'null', '{}', json({ version: null }), json({ version: 'v0.3.2' }),
                          json({ version: 'dev' })]) {
    w.manifest = manifest
    expect(await start($, w)).toBe(BARE)
  }
  w.manifest = json({ version: '0.3.2' })
  expect(await start($, w)).toBe(`${BARE} · update 0.3.3`)
})

test('after a turn the word comes last, after the allowance and the context', async ($, on) => {
  const w = world(on)
  w.usage = json({ at: NOW / 1000, five_hour: { used_percentage: 23, resets_at: RESETS }, seven_day: { used_percentage: 51, resets_at: RESETS } })
  await start($, w)
  await $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: 't1', reason: 'answer', usage: USAGE })
  expect(w.statuses[w.statuses.length - 1]).toBe('default sign-in · 5h 23% · wk 51% · ctx 612k · update 0.3.3')
  w.release = json({ latest: '0.3.2' })
  await $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: 't2', reason: 'answer', usage: USAGE })
  expect(w.statuses[w.statuses.length - 1]).toBe('default sign-in · 5h 23% · wk 51% · ctx 612k')
})

// This chat's question and carry-on (the keep-working plan's K1, K2), as
// the relay writes them and the accounts pane's look finds them every 3 s.
const RELAY = 4242
const QUESTION = { id: 'q-0123456789ab', chat: SID, relay_pid: RELAY, asked_at: NOW / 1000, kind: 'move', from: 'main',
  limit: 'weekly', resets_at: NOW / 1000 + 7200, to: 'personal-2', to_resets_at: null, choices: ['move', 'wait'] }
const WAIT = { chat: SID, until: NOW / 1000 + 7200, account: 'personal-1', why: 'answered' }

function write(w: World, path: string, value: unknown | null): void {
  if (value === null) delete w.files[path]
  else w.files[path] = { text: json(value), at: (w.files[path]?.at ?? 0) + 1 }
}

test("the question and the carry-on take the entry's place while they are open, the update word still last", async ($, on) => {
  const w = world(on)
  w.alive.add(RELAY)
  expect(await start($, w)).toBe(`${BARE} · update 0.3.3`)
  write(w, `${HOME}/questions/${SID}.json`, QUESTION)
  await w.clock.advance(3000)
  expect(w.statuses[w.statuses.length - 1]).toBe('default sign-in at limit · move to personal-2? /accounts · update 0.3.3')
  // The answer was wait: the relay removes the question and schedules the carry-on.
  write(w, `${HOME}/questions/${SID}.json`, null)
  write(w, `${HOME}/waits/${SID}.json`, WAIT)
  await w.clock.advance(3000)
  const carries = `default sign-in at limit · carries on ${clock(WAIT.until, NOW + 6000)} on personal-1`
  expect(w.statuses[w.statuses.length - 1]).toBe(`${carries} · update 0.3.3`)
  // After a turn: the carry-on still holds the place of the numbers.
  w.usage = json({ at: NOW / 1000, five_hour: { used_percentage: 23, resets_at: RESETS }, seven_day: { used_percentage: 51, resets_at: RESETS } })
  await $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: 't1', reason: 'answer', usage: USAGE })
  expect(w.statuses[w.statuses.length - 1]).toBe(`${carries} · update 0.3.3`)
  // The carry-on went: its file with it.
  write(w, `${HOME}/waits/${SID}.json`, null)
  await w.clock.advance(3000)
  expect(w.statuses[w.statuses.length - 1]).toBe('default sign-in · 5h 23% · wk 51% · ctx 612k · update 0.3.3')
})

test("a question its relay left behind, or another chat's, adds nothing", async ($, on) => {
  const w = world(on)
  write(w, `${HOME}/questions/${SID}.json`, QUESTION) // its relay, killed, runs no more
  write(w, `${HOME}/questions/another-chat.json`, { ...QUESTION, chat: 'another-chat' })
  await start($, w)
  await w.clock.advance(9000)
  expect(w.statuses[w.statuses.length - 1]).toBe(`${BARE} · update 0.3.3`)
  w.alive.add(RELAY)
  write(w, `${HOME}/questions/${SID}.json`, { ...QUESTION, chat: 'another-chat' })
  await w.clock.advance(3000)
  expect(w.statuses[w.statuses.length - 1]).toBe(`${BARE} · update 0.3.3`)
})

// One status line, everywhere (the UX review of 2026-11-11, 4.6 and M3): each
// shared case's state draws, here, the line longhaul.py's status_line() draws
// for it (test_longhaul.py reads the same cases), so the panel's entry and the
// terminal's line cannot drift apart.
for (const c of CASES)
  test(`the line for: ${c.state}`, () => {
    expect(line(c.given)).toBe(c.line)
  })

test('a state it cannot read still names the account', () => {
  for (const given of [{ account: 'work' }, { account: 'work', five: 'x', week: [], ctx: 'big', event: 3 },
                       { account: 'work', five: { used: null }, event: { kind: 'question' } },
                       { account: 'work', event: { kind: 'refused', where: '~/x', by: 'work' } }])
    expect(line(given).startsWith('work')).toBe(true)
  expect(line(null)).toBe('longhaul · no usage reading yet')
})

// Each row of the review's table (4.6, M3), as the panel's entry finds it from
// Longhaul's files: the words are the shared cases' (above); these show the
// entry finds the state. A chat on work or personal, as CLAUDE_CONFIG_DIR says.
const ACCOUNTS = json([{ name: 'personal', config_dir: '~/.claude-personal' }, { name: 'work', config_dir: '~/.claude-work' }])
const RESET = NOW / 1000 + 3600
const READING = (five: number | null, week: number | null) => json({ at: NOW / 1000,
  ...(five === null ? {} : { five_hour: { used_percentage: five, resets_at: RESET } }),
  ...(week === null ? {} : { seven_day: { used_percentage: week, resets_at: RESET + 86400 } }) })
const TURN = { answer: 'done', durationMs: 1000, isAborted: false, turnId: 't1', reason: 'answer', usage: USAGE }

// A world on `on` (work or personal), no newer release, the context 88k, and
// `five`% and `week`% read on that account; the entry after a turn.
async function row($: any, on: any, account: string, five: number | null = 12, week: number | null = 40,
                   files: Record<string, unknown> = {}): Promise<{ w: World; entry: () => string }> {
  const w = world(on, { CLAUDE_CONFIG_DIR: `/scratch/home/.claude-${account}` })
  w.release = json({ latest: '0.3.2' })
  w.context = 88_000
  write(w, `${HOME}/accounts.json`, JSON.parse(ACCOUNTS))
  if (five !== null || week !== null) w.files[`${HOME}/usage/${account}.json`] = { text: READING(five, week), at: 1 }
  for (const [path, value] of Object.entries(files)) write(w, `${HOME}/${path}`, value)
  await start($, w)
  await $.turn.complete(TURN)
  return { w, entry: () => w.statuses[w.statuses.length - 1]! }
}

test('the entry: normal', async ($, on) => {
  expect((await row($, on, 'work')).entry()).toBe('work · 5h 12% · wk 40% · ctx 88k')
})

test('the entry: near full, with the reset', async ($, on) => {
  expect((await row($, on, 'work', 91)).entry()).toBe(`work · 5h 91% (${clock(RESET, NOW)}) · wk 40% · ctx 88k`)
})

test('the entry: at a limit, question open', async ($, on) => {
  const { w, entry } = await row($, on, 'personal', 100)
  w.alive.add(RELAY)
  write(w, `${HOME}/questions/${SID}.json`, { ...QUESTION, from: 'personal', to: 'work', limit: '5-hour' })
  await w.clock.advance(3000)
  expect(entry()).toBe('personal at limit · move to work? /accounts')
})

test('the entry: at a limit, waiting', async ($, on) => {
  const { w, entry } = await row($, on, 'personal', 100)
  write(w, `${HOME}/waits/${SID}.json`, { ...WAIT, account: 'work' })
  await w.clock.advance(3000)
  expect(entry()).toBe(`personal at limit · carries on ${clock(WAIT.until, NOW + 3000)} on work`)
})

test('the entry: at a limit, stopped', async ($, on) => {
  const { entry } = await row($, on, 'personal', 100, 40, { 'state.json': { limited_until: { personal: RESET } } })
  expect(entry()).toBe(`personal at limit until ${clock(RESET, NOW)} · stopped · /accounts`)
})

test('the entry: a recorded limit past, the numbers', async ($, on) => {
  const { entry } = await row($, on, 'personal', 100, 40, { 'state.json': { limited_until: { personal: NOW / 1000 - 5 } } })
  expect(entry()).toBe(`personal · 5h 100% (${clock(RESET, NOW)}) · wk 40% · ctx 88k`)
})

const MOVED = (ago: number) => ({ [`usage/sessions/${SID}.json`]: { at: NOW / 1000, session: SID, account: 'work',
  moved_from: 'personal', moved_at: NOW / 1000 - ago } })

test('the entry: just moved, for an hour', async ($, on) => {
  expect((await row($, on, 'work', 3, null, MOVED(600))).entry())
    .toBe(`work (moved from personal ${clock(NOW / 1000 - 600, NOW)}) · 5h 3% · ctx 88k`)
})

test('the entry: moved over an hour ago, the numbers alone', async ($, on) => {
  expect((await row($, on, 'work', 3, null, MOVED(3601))).entry()).toBe('work · 5h 3% · ctx 88k')
})

const USE = { id: 'u-0123456789ab', chat: SID, account: 'work', at: NOW / 1000 }

test('the entry: a choice pending, no relay took it', async ($, on) => {
  expect((await row($, on, 'personal', 12, 40, { [`uses/${SID}.json`]: USE })).entry())
    .toBe('personal · → work at its next open')
})

test('the entry: a choice pending', async ($, on) => {
  const { w, entry } = await row($, on, 'personal', 12, 40, { [`uses/${SID}.json`]: { ...USE, relay_pid: RELAY } })
  w.alive.add(RELAY)
  await $.turn.complete(TURN)
  expect(entry()).toBe('personal · → work after this turn')
})

// "2026-10-31 13:00:00", a log line's stamp for a time, in this machine's time.
function logStamp(ms: number): string {
  const d = new Date(ms), p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

test('the entry: a message stopped by a folder rule, until a reading after it', async ($, on) => {
  const { w, entry } = await row($, on, 'personal', 12, 40, {
    [`usage/sessions/${SID}.json`]: { at: NOW / 1000 - 60, session: SID, account: 'personal' } })
  w.files[`${HOME}/longhaul.log`] = { text: '', at: 1 }
  await start($, w) // marks where the log ends
  w.files[`${HOME}/longhaul.log`] = { at: 2, text: `${logStamp(NOW)} account: refused a prompt in ${SID}: ~/clients is kept `
    + 'for work; the chat runs on personal\n' }
  await $.turn.complete(TURN)
  expect(entry()).toBe("personal · message stopped: ~/clients is work's · /accounts")
  write(w, `${HOME}/usage/sessions/${SID}.json`, { at: NOW / 1000 + 5, session: SID, account: 'personal' })
  await $.turn.complete(TURN)
  expect(entry()).toBe('personal · 5h 12% · wk 40% · ctx 88k')
})

test('the entry: no reading yet', async ($, on) => {
  const { w, entry } = await row($, on, 'work', null, null)
  w.context = 12_000
  await $.turn.complete(TURN)
  expect(entry()).toBe('work · no usage reading yet · ctx 12k')
})

test("the entry: the default sign-in", async ($, on) => {
  const w = world(on)
  w.release = json({ latest: '0.3.2' })
  w.context = 88_000
  w.usage = READING(12, null)
  await start($, w)
  await $.turn.complete(TURN)
  expect(w.statuses[w.statuses.length - 1]).toBe('default sign-in · 5h 12% · ctx 88k')
  // The one account with no folder of its own is ~/.claude's, as longhaul.py names it.
  write(w, `${HOME}/accounts.json`, [{ name: 'Max', config_dir: null }, { name: 'work', config_dir: '~/.claude-work' }])
  w.files[`${HOME}/usage/Max.json`] = { text: READING(12, null), at: 1 }
  await $.turn.complete(TURN)
  expect(w.statuses[w.statuses.length - 1]).toBe('Max · 5h 12% · ctx 88k')
})

test('the entry: a newer release', async ($, on) => {
  const { w, entry } = await row($, on, 'work')
  w.release = json({ latest: '0.3.9' })
  w.manifest = json({ version: '0.3.8' })
  await $.turn.complete(TURN)
  expect(entry()).toBe('work · 5h 12% · wk 40% · ctx 88k · update 0.3.9')
})
