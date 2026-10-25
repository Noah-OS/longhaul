import { expect, mock, test } from 'claude-code/testing'

// hooks/autocompact.ts turn by turn: `claude plugin test .` from the
// checkout. The plugin is loaded whole (display.ts too), so each test
// answers what both modules call; nothing here touches a real file.

const SID = 'fcd7fbcc-bc91-4a30-9dce-e7496a7f4aa6'
const OTHER = '0b0e5c1a-7d3f-4c2e-9a61-2f4d8e6b1c90'
const HOME = '/scratch/longhaul'
const LOG = `${HOME}/longhaul.log`
const RECORD = `${HOME}/autocompact.json`
const NOW = Date.UTC(2026, 9, 25, 12, 0, 0)
const TOAST = 'Longhaul: compacted this chat after its checkpoint (612k tokens); the checkpoint comes back now.'
const USAGE = { input_tokens: 6, cache_read_input_tokens: 600_000, cache_creation_input_tokens: 2_000, output_tokens: 900, model: 'claude-opus-4-5' }

type World = {
  log: string
  record?: Record<string, number>
  live?: number // the live window's context, as the status line has it
  refuse: boolean
  commands: string[]
  notes: string[][]
  toasts: string[]
  clock: ReturnType<typeof mock.clock>
}

// The engine beneath the plugin, from memory: the log, the clock, the
// session, the programs run (tail on the log, the note), /compact, pop-ups.
function world(on: any, env: Record<string, string> = {}): World {
  const w: World = { log: '2026-10-25 11:00:00 index: started codegraph init in ~/p (pid 1)\n', live: 612_345,
    refuse: false, commands: [], notes: [], toasts: [], clock: mock.clock(on, { now: NOW }) }
  mock.env(on, { LONGHAUL_HOME: HOME, HOME: '/scratch/home', ...env })
  on('session.id', () => ({ value: SID }))
  on('session.usage', () => ({ value: { startedAt: NOW - 3_600_000, rateLimits: [],
    context: w.live === undefined ? { window: 1_000_000 } : { tokens: w.live, window: 1_000_000, percent: 61 } } }))
  on('fs.stat', ($: any, e: any) => e.path === LOG
    ? { value: { kind: 'file', size: w.log.length, mtimeMs: NOW, isLink: false } } : { deny: 'no such file' })
  on('fs.read', ($: any, e: any) => e.path === RECORD && w.record ? { value: JSON.stringify(w.record) } : { deny: 'no such file' })
  on('process.run', ($: any, e: any) => {
    const argv: string[] = [...e.argv]
    const ran = { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
    if (argv[0] === 'tail' && argv[1] === '-c' && argv[3] === LOG) return { value: { ...ran, stdout: w.log.slice(-Number(argv[2])) } }
    if (argv[0] === 'python3') { w.notes.push(argv); return { value: ran } }
    return { deny: 'not run here' }
  })
  on('command.run', ($: any, e: any) => {
    if (w.refuse) throw new Error('refused')
    w.commands.push(e.command)
    return { text: 'Compacted' }
  })
  on('ui.toast', ($: any, e: any) => { w.toasts.push(e.text); return { value: undefined } })
  on('ui.status', () => ({ value: undefined }))
  on('turn.complete', ($: any, e: any) => ({ text: e.answer, usage: e.usage }))
  return w
}

let turns = 0
function turn($: any, fields: Record<string, unknown> = {}) {
  turns += 1
  return $.turn.complete({ answer: 'done', durationMs: 1000, isAborted: false, turnId: `t${turns}`, reason: 'answer',
    usage: USAGE, ...fields })
}

function line(w: World, text: string) {
  w.log += `2026-10-25 12:00:00 ${text}\n`
}
const reminded = (sid = SID) => `reminder: ${sid} reminded at 600,000 tokens (the context is 612,345)`
const saved = (sid = SID) => `checkpoint for ${sid} saved (4,214 bytes)`

function compacted(w: World): string[] {
  return w.toasts.filter(t => t.startsWith('Longhaul: compacted'))
}

test('a reminder, then this chat\'s save, compacts it once at the end of the turn', async ($, on) => {
  const w = world(on)
  await turn($) // the module's first turn marks where the log ends
  line(w, reminded())
  await turn($)
  expect(w.commands).toEqual([])
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
  expect(compacted(w)).toEqual([TOAST])
  expect(w.notes).toHaveLength(1)
  const [python, isolated, script, ...rest] = w.notes[0]!
  expect([python, isolated]).toEqual(['python3', '-I'])
  expect(script).toMatch(/^\/.*\/longhaul\.py$/)
  expect(rest).toEqual(['autocompact-note', SID, '612345'])
  await turn($)
  await turn($)
  expect(w.commands).toEqual(['compact'])
})

test('a reminder and the save in one turn compact it at that turn\'s end', async ($, on) => {
  const w = world(on)
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
})

test('a save with no reminder before it does nothing', async ($, on) => {
  const w = world(on)
  await turn($)
  line(w, saved())
  await turn($)
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual([])
  expect(w.notes).toEqual([])
  expect(compacted(w)).toEqual([])
})

test('LONGHAUL_AUTO_COMPACT=off, in any case, does nothing', async ($, on) => {
  const w = world(on, { LONGHAUL_AUTO_COMPACT: 'OFF' })
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($)
  await turn($)
  expect(w.commands).toEqual([])
  expect(w.notes).toEqual([])
  expect(compacted(w)).toEqual([])
})

test('a second save after the compaction does nothing until a new reminder, ten minutes on', async ($, on) => {
  const w = world(on)
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
  // A new reminder within ten minutes of the compaction is let go.
  await w.clock.advance(5 * 60_000)
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
  await w.clock.advance(6 * 60_000)
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
  // Past ten minutes, a new reminder and a save compact again.
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact', 'compact'])
})

test('another session\'s lines do nothing', async ($, on) => {
  const w = world(on)
  await turn($)
  line(w, reminded(OTHER))
  line(w, saved(OTHER))
  await turn($)
  line(w, reminded(OTHER))
  line(w, saved())
  await turn($)
  line(w, reminded())
  line(w, saved(OTHER))
  await turn($)
  expect(w.commands).toEqual([])
  expect(w.notes).toEqual([])
})

test('a refused /compact shows nothing and logs nothing', async ($, on) => {
  const w = world(on)
  w.refuse = true
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual([])
  expect(w.notes).toEqual([])
  expect(compacted(w)).toEqual([])
  // Disarmed: the same save does not try again at the next turn.
  w.refuse = false
  await turn($)
  expect(w.commands).toEqual([])
})

test('a compaction recorded in the last ten minutes, as before a reload, holds the next one off', async ($, on) => {
  const w = world(on)
  w.record = { [SID]: NOW / 1000 - 300, [OTHER]: NOW / 1000 - 900 }
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual([])
  w.record = { [SID]: NOW / 1000 - 700 }
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
})

test('a subagent\'s turn does nothing, and leaves the lines for the main turn', async ($, on) => {
  const w = world(on)
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($, { agentId: 'a0123456789abcdef' })
  expect(w.commands).toEqual([])
  await turn($)
  expect(w.commands).toEqual(['compact'])
})

test('an interrupted turn waits for the next answered one', async ($, on) => {
  const w = world(on)
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($, { reason: 'aborted', isAborted: true, usage: undefined })
  expect(w.commands).toEqual([])
  await turn($)
  expect(w.commands).toEqual(['compact'])
})

test('without the live window\'s figure the turn\'s own usage counts', async ($, on) => {
  const w = world(on)
  w.live = undefined
  await turn($)
  line(w, reminded())
  line(w, saved())
  await turn($)
  expect(w.commands).toEqual(['compact'])
  expect(compacted(w)).toEqual(['Longhaul: compacted this chat after its checkpoint (602k tokens); the checkpoint comes back now.'])
  expect(w.notes[0]!.slice(-1)).toEqual(['602006'])
})

// A first read after a load (a start, or /reload-plugins) takes the last
// fifteen minutes of the log too (2026-10-25, the first live check: the
// reminder and the save came in the very turn /reload-plugins had loaded it).
function stamped(w: World, minutesAgo: number, text: string) {
  const d = new Date(NOW - minutesAgo * 60_000)
  const two = (n: number) => String(n).padStart(2, '0')
  const at = `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
  w.log += `${at} ${text}\n`
}

test('a reminder and its save just before the module loaded compact at its first turn', async ($, on) => {
  const w = world(on)
  stamped(w, 6, reminded())
  stamped(w, 4, saved())
  await turn($) // the module's first turn
  expect(w.commands).toEqual(['compact'])
  expect(compacted(w)).toEqual([TOAST])
})

test('lines older than fifteen minutes at the first turn do nothing', async ($, on) => {
  const w = world(on)
  stamped(w, 30, reminded())
  stamped(w, 20, saved())
  await turn($)
  expect(w.commands).toEqual([])
})

test('a recent save the module already compacted after does nothing at a first turn', async ($, on) => {
  const w = world(on)
  stamped(w, 9, reminded())
  stamped(w, 8, saved())
  stamped(w, 7, `autocompact: compacted ${SID} after its checkpoint (the context was 612,345)`)
  await turn($)
  expect(w.commands).toEqual([])
})
