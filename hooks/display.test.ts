import { expect, mock, test } from 'claude-code/testing'

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
const USAGE = { input_tokens: 6, cache_read_input_tokens: 600_000, cache_creation_input_tokens: 2_000, output_tokens: 900, model: 'claude-opus-4-5' }

type World = {
  release?: string // release.json as it is on disk; undefined for none
  manifest?: string // the plugin's .claude-plugin/plugin.json
  usage?: string // usage/main.json
  files: Record<string, { text: string; at: number }> // this chat's question and wait, by path
  alive: Set<number> // the pids `kill -0` finds
  reads: string[]
  statuses: string[]
  clock: ReturnType<typeof mock.clock>
}

const json = (value: unknown) => JSON.stringify(value)

// The engine beneath the plugin, from memory: the files, the log (empty),
// the session, the status entry.
function world(on: any, env: Record<string, string> = {}): World {
  const w: World = { release: json({ checked_at: NOW / 1000, latest: '0.3.3' }), manifest: json({ name: 'longhaul', version: '0.3.2' }),
    files: {}, alive: new Set(), reads: [], statuses: [], clock: mock.clock(on, { now: NOW }) }
  mock.env(on, { LONGHAUL_HOME: HOME, HOME: '/scratch/home', ...env })
  on('session.id', () => ({ value: SID }))
  on('session.start', ($: any, e: any) => ({ cwd: e.cwd, source: e.source }))
  on('session.usage', () => ({ value: { startedAt: NOW - 3_600_000, rateLimits: [],
    context: { tokens: 612_345, window: 1_000_000, percent: 61 } } }))
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
  // The one program the accounts pane's look runs: kill -0 on the relay's pid.
  on('process.run', ($: any, e: any) => e.argv[0] === 'kill' && e.argv[1] === '-0'
    ? { value: { exitCode: w.alive.has(Number(e.argv[2])) ? 0 : 1, stdout: '', stderr: '', isStdoutTruncated: false,
      isStderrTruncated: false } }
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
  expect(await start($, w)).toBe('main · update 0.3.3')
  expect(w.reads.filter(p => p.endsWith(MANIFEST))[0]).toMatch(/^\/.*\/\.claude-plugin\/plugin\.json$/)
})

test('the same or an older release, or one compared as text, shows nothing', async ($, on) => {
  const w = world(on)
  for (const latest of ['0.3.2', '0.3.1', '0.2.9']) {
    w.release = json({ latest })
    expect(await start($, w)).toBe('main')
  }
  // As numbers: 0.3.10 is newer than 0.3.9, though not as text.
  w.manifest = json({ version: '0.3.9' })
  w.release = json({ latest: '0.3.10' })
  expect(await start($, w)).toBe('main · update 0.3.10')
  w.manifest = json({ version: '0.3.10' })
  w.release = json({ latest: '0.3.9' })
  expect(await start($, w)).toBe('main')
})

for (const env of [{ LONGHAUL_UPDATE_CHECK: 'off' }, { LONGHAUL_UPDATE_CHECK: 'OFF' }, { LONGHAUL_UPDATE_CHECK: 'Off' },
                   { CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1' }])
  test(`the check turned off (${Object.entries(env)[0]!.join('=')}) shows nothing and reads no record`, async ($, on) => {
    const w = world(on, env)
    expect(await start($, w)).toBe('main')
    expect(w.reads).not.toContain(RELEASE)
  })

test('a record or a manifest it cannot use shows nothing extra', async ($, on) => {
  const w = world(on)
  for (const release of [undefined, 'not json', '', '[]', 'null', '3', '{}', json({ latest: null }), json({ latest: 3 }),
                         json({ latest: [0, 3, 3] }), json({ latest: 'soon' }), json({ latest: '0.3.3-beta' }),
                         json({ latest: '0.3' }), json({ latest: ' 0.3.3' }), json({ error: 'timeout', checked_at: 1 })]) {
    w.release = release
    expect(await start($, w)).toBe('main')
  }
  w.release = json({ latest: '0.3.3' })
  for (const manifest of [undefined, 'not json', '[]', 'null', '{}', json({ version: null }), json({ version: 'v0.3.2' }),
                          json({ version: 'dev' })]) {
    w.manifest = manifest
    expect(await start($, w)).toBe('main')
  }
  w.manifest = json({ version: '0.3.2' })
  expect(await start($, w)).toBe('main · update 0.3.3')
})

test('after a turn the word comes last, after the allowance and the context', async ($, on) => {
  const w = world(on)
  w.usage = json({ at: NOW / 1000, five_hour: { used_percentage: 23, resets_at: RESETS }, seven_day: { used_percentage: 51, resets_at: RESETS } })
  await start($, w)
  await $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: 't1', reason: 'answer', usage: USAGE })
  expect(w.statuses[w.statuses.length - 1]).toBe('main · 5h 23% · week 51% · context 612k · update 0.3.3')
  w.release = json({ latest: '0.3.2' })
  await $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: 't2', reason: 'answer', usage: USAGE })
  expect(w.statuses[w.statuses.length - 1]).toBe('main · 5h 23% · week 51% · context 612k')
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

// 14:05 as longhaul.py's clock() shows a time today, in this machine's time.
function hm(seconds: number): string {
  const d = new Date(seconds * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

test("the question and the carry-on go in the entry while they are open, before the update word", async ($, on) => {
  const w = world(on)
  w.alive.add(RELAY)
  expect(await start($, w)).toBe('main · update 0.3.3')
  write(w, `${HOME}/questions/${SID}.json`, QUESTION)
  await w.clock.advance(3000)
  expect(w.statuses[w.statuses.length - 1]).toBe('main · question: /accounts · update 0.3.3')
  // The answer was wait: the relay removes the question and schedules the carry-on.
  write(w, `${HOME}/questions/${SID}.json`, null)
  write(w, `${HOME}/waits/${SID}.json`, WAIT)
  await w.clock.advance(3000)
  const carries = `carries on at ${new Date(NOW).toDateString() === new Date(NOW + 7_200_000).toDateString() ? '' : 'Sun '}`
    + `${hm(NOW / 1000 + 7200)} on personal-1`
  expect(w.statuses[w.statuses.length - 1]).toBe(`main · ${carries} · update 0.3.3`)
  // After a turn: the allowance and the context, then these, the update word last.
  w.usage = json({ at: NOW / 1000, five_hour: { used_percentage: 23, resets_at: RESETS }, seven_day: { used_percentage: 51, resets_at: RESETS } })
  await $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: 't1', reason: 'answer', usage: USAGE })
  expect(w.statuses[w.statuses.length - 1]).toBe(`main · 5h 23% · week 51% · context 612k · ${carries} · update 0.3.3`)
  // The carry-on went: its file with it.
  write(w, `${HOME}/waits/${SID}.json`, null)
  await w.clock.advance(3000)
  expect(w.statuses[w.statuses.length - 1]).toBe('main · 5h 23% · week 51% · context 612k · update 0.3.3')
})

test("a question its relay left behind, or another chat's, adds nothing", async ($, on) => {
  const w = world(on)
  write(w, `${HOME}/questions/${SID}.json`, QUESTION) // its relay, killed, runs no more
  write(w, `${HOME}/questions/another-chat.json`, { ...QUESTION, chat: 'another-chat' })
  await start($, w)
  await w.clock.advance(9000)
  expect(w.statuses[w.statuses.length - 1]).toBe('main · update 0.3.3')
  w.alive.add(RELAY)
  write(w, `${HOME}/questions/${SID}.json`, { ...QUESTION, chat: 'another-chat' })
  await w.clock.advance(3000)
  expect(w.statuses[w.statuses.length - 1]).toBe('main · update 0.3.3')
})
