import type { Register } from 'claude-code'

import { CHAT, clock, found, running } from './question.ts'

// Longhaul's display inside Claude Code (the owner, 2026-10-23): VS Code's
// panel shows no status line and no hook message, so this shows them. It
// only reads ~/.longhaul (accounts.json, usage/<account>.json, this chat's
// usage/sessions record, state.json, uses/<chat>.json, longhaul.log,
// release.json), the plugin's own manifest and this chat's question and
// carry-on as the accounts pane's look found them, and sends nothing
// anywhere; any failure shows nothing. It runs tail on the log, and
// question.ts's kill -0 on the relay that took a `longhaul use` choice.

type Window = { used_percentage?: number; resets_at?: number }
type Reading = { at?: number; five_hour?: Window; seven_day?: Window }

const TAIL = 65536 // bytes of the log read at most per turn
let seen = -1 // the log's size when last read: a reload starts afresh
let context: number | undefined // the last turn's context, input + cache read + cache write
// This chat's last prompt the account check stopped (its log line, ACCOUNT_LOGGED
// in longhaul.py): when (epoch seconds), the folder and the accounts that keep it.
let refused: { sid: string; at: number; where: string; by: string[] } | undefined
const MOVED_FOR = 3600 // seconds a move shows on the line, as longhaul.py's STATUS_MOVED_FOR
const USE_DAYS = 30 // a `longhaul use` choice older than this is none, as longhaul.py's

async function home($: any): Promise<string> {
  return (await $.env.get('LONGHAUL_HOME')) || `${await $.env.get('HOME')}/.longhaul`
}

async function expand($: any, path: string): Promise<string> {
  return path.startsWith('~/') ? `${await $.env.get('HOME')}${path.slice(1)}` : path
}

// The account this chat runs on: the one whose folder is CLAUDE_CONFIG_DIR;
// with none set, the one account with no folder of its own, as longhaul.py's
// account_for_config() finds it; else main, ~/.claude's own sign-in, which
// no list names (known: false), and which the line calls DEFAULT_NAME.
async function account($: any, lh: string): Promise<{ name: string; known: boolean }> {
  const dir = ((await $.env.get('CLAUDE_CONFIG_DIR')) || '').replace(/\/+$/, '')
  try {
    const accounts = JSON.parse(await $.fs.read(`${lh}/accounts.json`))
    if (Array.isArray(accounts) && dir) {
      for (const a of accounts)
        if (a && typeof a.name === 'string' && typeof a.config_dir === 'string'
            && (await expand($, a.config_dir)).replace(/\/+$/, '') === dir) return { name: a.name, known: true }
    } else if (Array.isArray(accounts)) {
      const mine = accounts.filter(a => a && typeof a.name === 'string' && !a.config_dir)
      if (mine.length === 1) return { name: mine[0].name, known: true }
    }
  } catch {}
  return { name: 'main', known: false }
}

async function readJson($: any, path: string): Promise<unknown> {
  try { return JSON.parse(await $.fs.read(path)) } catch { return null }
}

async function reading($: any, lh: string, name: string): Promise<Reading | undefined> {
  try {
    const r = JSON.parse(await $.fs.read(`${lh}/usage/${name}.json`))
    return r && typeof r === 'object' ? r : undefined
  } catch { return undefined }
}

// A window of the reading as line() takes it, only while it is open: its
// share and its reset as clock() shows it.
function window(w: Window | undefined, nowMs: number): { used: number; resets: string } | null {
  return w && typeof w.used_percentage === 'number' && (w.resets_at ?? 0) > nowMs / 1000
    ? { used: w.used_percentage, resets: clock(w.resets_at!, nowMs) } : null
}

// [0, 3, 10] from "0.3.10" (or a tag's "v0.3.10"), as longhaul.py's
// release_version() reads one; undefined for anything else.
function version(text: unknown): number[] | undefined {
  const m = typeof text === 'string' ? /^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})$/.exec(text) : null
  return m ? m.slice(1).map(Number) : undefined
}

// Whether a is newer than b, part by part as numbers: 0.3.10 > 0.3.9.
function newer(a: number[], b: number[]): boolean {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i]! > b[i]!
  return false
}

// The latest release when it is newer than this copy, as the terminal's
// status line shows it (release_word() in longhaul.py): release.json is the
// day's check's answer, and the running version is the plugin's manifest.
// Off as the check is; this only reads, and asks GitHub nothing.
async function update($: any, lh: string): Promise<string | undefined> {
  try {
    if (((await $.env.get('LONGHAUL_UPDATE_CHECK')) || '').toLowerCase() === 'off') return undefined
    if (await $.env.get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')) return undefined
    const latest = version(JSON.parse(await $.fs.read(`${lh}/release.json`))?.latest)
    if (!latest) return undefined
    const running = JSON.parse(await $.fs.read(`${$.plugin.root}/.claude-plugin/plugin.json`))?.version
    const have = typeof running === 'string' && !running.startsWith('v') ? version(running) : undefined
    return have && newer(latest, have) ? latest.join('.') : undefined
  } catch { return undefined }
}

// One status line, everywhere (the UX review of 2026-11-11, 4.6 and M3):
// longhaul.py's status_line() in TypeScript, the same state to the same line,
//   <account>[ (event)] · 5h <n>%[ (<reset>)] · wk <n>% · ctx <n>k[ · update x]
// the account first, at most one event, a window's reset only from 70% on,
// the update word last. hooks/status-line.cases.ts holds the states and
// their lines, and both test suites check each one. The state: account, as
// shown; five and week, { used, resets (a time as clock() shows it) } or
// null; ctx, the context in tokens, or null; event, null or { kind:
// 'question' (to, and at for a carry-on at a reset), 'waiting' (at, on),
// 'stopped' (until), 'refused' (where, by), 'pending' (to, when: 'turn' or
// 'open') or 'moved' (from, at) }; update, a newer release, or null. An
// event at a limit, a choice pending or a message stopped takes the place of
// the numbers; one it cannot read, or of a kind it does not know, is none.
const RESET_AT = 70 // % of a window from which its reset is shown
export const DEFAULT_NAME = 'default sign-in' // ~/.claude's own sign-in, "main" in the files (the review's Q11)

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const text = (v: unknown): string | null => (typeof v === 'string' && v.length > 0 ? v : null)
const halfUp = (v: number): number => Math.floor(v + 0.5) // as longhaul.py's half_up()
const obj = (v: unknown): Record<string, any> => (v && typeof v === 'object' && !Array.isArray(v) ? v as any : {})

export function line(state: unknown): string {
  const s = obj(state)
  const name = text(s.account) ?? 'longhaul'
  const ev = obj(s.event)
  const kind = ev.kind, to = text(ev.to), at = text(ev.at)
  const by: unknown[] = Array.isArray(ev.by) ? ev.by : []
  const owners = by.filter(n => text(n) !== null) as string[]
  let parts: string[] | null = null
  if (kind === 'question' && to)
    parts = [`${name} at limit`, `${at ? `carry on ${at}${to === name ? '' : ` on ${to}`}?` : `move to ${to}?`} /accounts`]
  else if (kind === 'waiting' && at) {
    const on = text(ev.on)
    parts = [`${name} at limit`, `carries on ${at}${on && on !== name ? ` on ${on}` : ''}`]
  } else if (kind === 'stopped' && text(ev.until))
    parts = [`${name} at limit until ${ev.until}`, 'stopped', '/accounts']
  else if (kind === 'refused' && text(ev.where) && owners.length && owners.length === by.length)
    parts = [name, `message stopped: ${ev.where} is ${owners.map(n => `${n}'s`).join(' or ')}`, '/accounts']
  else if (kind === 'pending' && to)
    parts = [name, `→ ${to} ${ev.when === 'open' ? 'at its next open' : 'after this turn'}`]
  if (!parts) {
    const from = text(ev.from)
    parts = [kind === 'moved' && from && at ? `${name} (moved from ${from} ${at})` : name]
    const shares: string[] = []
    for (const [label, key] of [['5h', 'five'], ['wk', 'week']] as const) {
      const w = obj(s[key])
      const used = num(w.used)
      if (used === null) continue
      const resets = text(w.resets)
      shares.push(`${label} ${halfUp(used)}%${halfUp(used) >= RESET_AT && resets ? ` (${resets})` : ''}`)
    }
    parts.push(...(shares.length ? shares : ['no usage reading yet']))
    const ctx = num(s.ctx)
    if (ctx !== null && ctx >= 0) parts.push(`ctx ${halfUp(ctx / 1000)}k`)
  }
  const update = text(s.update)
  if (update) parts.push(`update ${update}`)
  return parts.join(' · ')
}

// The one event of this chat's line (line()'s event), the first that holds,
// in longhaul.py's status_event() order: its question at a limit, open while
// its relay runs, and its carry-on waiting, as the accounts pane's look last
// found them (hooks/question.ts, every 3 s); its account's recorded limit in
// state.json (stopped: neither of those); its last prompt stopped by a
// folder rule with no reading since (the usage/sessions record's "at"); a
// `longhaul use` choice of another account, after this turn when a relay
// took it (its relay_pid runs), else at its next open; a move in the last
// hour (the record's moved_from and moved_at). Null for none.
async function event($: any, lh: string, sid: string, name: string, nowMs: number): Promise<Record<string, unknown> | null> {
  const nowS = nowMs / 1000
  const { asked, waiting } = found()
  if (asked) {
    if (asked.kind === 'move') return { kind: 'question', to: asked.to }
    return { kind: 'question', to: asked.to, at: clock(asked.carries_on_at ?? asked.to_resets_at ?? asked.resets_at, nowMs) }
  }
  if (waiting) return { kind: 'waiting', at: clock(waiting.until, nowMs), on: waiting.account }
  const limited = num(obj(obj(await readJson($, `${lh}/state.json`)).limited_until)[name])
  if (limited !== null && limited > nowS) return { kind: 'stopped', until: clock(limited, nowMs) }
  if (!CHAT.test(sid)) return null
  const record = obj(await readJson($, `${lh}/usage/sessions/${sid}.json`))
  const seen = num(record.at)
  if (refused && refused.sid === sid && (seen === null || refused.at >= Math.floor(seen)))
    return { kind: 'refused', where: refused.where, by: refused.by }
  const use = obj(await readJson($, `${lh}/uses/${sid}.json`))
  const chosen = text(use.account), at = num(use.at)
  if (use.chat === sid && chosen && chosen !== name && at !== null && nowS - at < USE_DAYS * 86400) {
    const pid = use.relay_pid
    const taken = Number.isInteger(pid) && pid > 0 && (await running($, pid))
    return { kind: 'pending', to: chosen, when: taken ? 'turn' : 'open' }
  }
  const movedAt = num(record.moved_at), from = text(record.moved_from)
  if (movedAt !== null && nowS - movedAt < MOVED_FOR && from && record.account === name)
    return { kind: 'moved', from, at: clock(movedAt, nowMs) }
  return null
}

// The status entry: line() of this chat's state, the terminal's status line
// in the same words (longhaul.py's status_line()): the account, at most one
// event, its allowance and the context, a newer release last. accounts.tsx
// calls it too, when its look finds the question or the carry-on changed.
export async function status($: any): Promise<void> {
  try {
    const lh = await home($)
    const { name, known } = await account($, lh)
    let nowMs = Date.now()
    try { nowMs = await $.clock.now() } catch {}
    let sid = ''
    try { sid = String(await $.session.id()) } catch {}
    const r = await reading($, lh, name)
    $.ui.status(line({ account: known ? name : DEFAULT_NAME, five: window(r?.five_hour, nowMs),
      week: window(r?.seven_day, nowMs), ctx: context ?? null, event: await event($, lh, sid, name, nowMs),
      update: (await update($, lh)) ?? null }))
  } catch {}
}

// A file's name from the path a log line gives, and "412,345" bytes in KB.
function base(path: string): string {
  return path.split('/').pop() || path
}

function kb(bytes: string): number {
  return Math.round(Number(bytes.replace(/,/g, '')) / 1000)
}

// The log's new lines, at most TAIL bytes of them; a log moved aside starts over.
async function newLines($: any): Promise<string[]> {
  try {
    const path = `${await home($)}/longhaul.log`
    const { size } = await $.fs.stat(path)
    if (seen < 0 || size < seen) { seen = size; return [] }
    if (size === seen) return []
    const take = Math.min(size - seen, TAIL)
    seen = size
    const ran = await $.process.run(['tail', '-c', String(take), path])
    const text = typeof ran?.stdout === 'string' ? ran.stdout : ''
    return text.split('\n').filter(Boolean)
  } catch { return [] }
}

// A log line's stamp, "2026-11-11 14:05:09" in this machine's time, in epoch seconds.
function stamp(text: string): number | null {
  const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) /.exec(text)
  if (!m) return null
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number) as [number, number, number, number, number, number]
  const at = new Date(y, mo - 1, d, h, mi, s).getTime() / 1000
  return Number.isFinite(at) ? at : null
}

async function popUps($: any, lines: string[]): Promise<void> {
  const sid = await $.session.id()
  const { name } = await account($, await home($))
  for (const line of lines) {
    const body = line.replace(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d /, '')
    let m: RegExpMatchArray | null
    if ((m = body.match(/^checkpoint for (\S+) saved \(([\d,]+) bytes\)/)) && m[1] === sid)
      $.ui.toast('Longhaul: checkpoint saved. /clear starts fresh, and it comes back.', { timeoutMs: 8000 })
    else if ((m = body.match(/^checkpoint for \S+ carried into (\S+) after clear/)) && m[1] === sid)
      $.ui.toast('Longhaul: your checkpoint from before /clear is back.', { timeoutMs: 8000 })
    else if ((m = body.match(/^reminder: (\S+) reminded at [\d,]+ tokens \(the context is ([\d,]+)\)/)) && m[1] === sid)
      $.ui.toast(`Longhaul: the context is ${Math.round(Number(m[2].replace(/,/g, '')) / 1000)}k tokens; `
                 + 'the model will save a checkpoint at its next stopping point.', { timeoutMs: 10000 })
    else if ((m = body.match(/^(\S+) hit its (.+?) limit \(resets ([^)]+)\); the panel chat carries on with (\S+?)\.$/)) && m[4] === name)
      $.ui.toast(`Longhaul: ${m[1]} hit its ${m[2]} limit (resets ${m[3]}); this chat now runs on ${m[4]}.`, { timeoutMs: 12000 })
    else if ((m = body.match(/^index: started (\S+) init in (\S+)/)))
      $.ui.toast(`Longhaul: building a ${m[1] === 'trail' ? 'Trail-Code' : 'CodeGraph'} map of ${m[2]} in the background.`, { timeoutMs: 8000 })
    // The guard's advice is Claude's alone; these show the user it acted.
    // A subagent's line shows nothing: the main chat did not make that read.
    else if ((m = body.match(/^guard: (advised on|refused) a whole read of (.+) \(([\d,]+) bytes\) in (\S+)( \(a subagent\))?$/)) && m[4] === sid && !m[5])
      $.ui.toast(m[1] === 'refused'
        ? `Longhaul: refused a whole read of ${base(m[2])} (${kb(m[3])} KB); Claude reads a part instead.`
        : `Longhaul: told Claude ${base(m[2])} is ${kb(m[3])} KB; reading only the part it needs keeps the context small.`,
        { timeoutMs: 8000 })
    else if ((m = body.match(/^guard: advised on a repeat read of (.+) in (\S+)( \(a subagent\))?$/)) && m[2] === sid && !m[3])
      $.ui.toast(`Longhaul: told Claude ${base(m[1])} hasn't changed since it last read it.`, { timeoutMs: 8000 })
    else if ((m = body.match(/^account: refused a prompt in (\S+?): (.+?) is kept for (.+?); the chat runs on (.+)$/)) && m[1] === sid) {
      const at = stamp(line)
      if (at !== null) refused = { sid, at, where: m[2], by: m[3].split(', ') }
      $.ui.toast(`Longhaul stopped your message: ${m[2]} is reserved for ${m[3]}, and this chat is on ${m[4]}. Nothing was sent. Type /accounts to move it.`, { timeoutMs: 12000 })
    }
    // A newer Longhaul (said at a chat's start, once per version a day): a
    // release names no chat, so any chat shows it; an install names the
    // chat that runs the older version, and that chat alone shows it.
    else if ((m = body.match(/^release: Longhaul (\S+) is out \(this is (\S+)\)$/)))
      $.ui.toast(`Longhaul ${m[1]} is out. /longhaul:doctor shows how to update.`, { timeoutMs: 12000 })
    else if ((m = body.match(/^release: Longhaul (\S+) is installed; (\S+) runs (\S+)$/)) && m[2] === sid)
      $.ui.toast(`Longhaul: ${m[1]} is installed; this chat runs ${m[3]}. Type /reload-plugins to use it.`, { timeoutMs: 12000 })
    // The first chat after an install or an update: the panel shows no
    // systemMessage. What works now (longhaul.py's quick checks name only
    // what passed), what to try, and the lines to fix or the extras.
    else if ((m = body.match(/^setup: Longhaul (\S+) is on; working now: (.+); to fix: (\d+)$/)))
      $.ui.toast(`Longhaul ${m[1]} is on. `
                 + (m[2] === 'nothing' ? '' : `Working now: ${m[2]}. Try it: /longhaul:checkpoint, then /clear. `)
                 + (m[3] === '0' ? 'Extras: /longhaul:doctor.' : `${m[3]} to fix: /longhaul:doctor.`), { timeoutMs: 12000 })
    // A checkpoint put back from six hours ago or more: it may be out of date (review issue 6).
    else if ((m = body.match(/^checkpoint for (\S+) put back after (\S+) \((\d+) bytes, written (\d+) s earlier\)$/)) && m[1] === sid && Number(m[4]) >= 21600)
      $.ui.toast(`Longhaul: brought back a checkpoint from ${Math.round(Number(m[4]) / 3600)} h ago; it may be out of date. /longhaul:checkpoint saves a fresh one.`, { timeoutMs: 12000 })
    // Claude Code's own word that this chat stopped (its StopFailure and
    // Notification hooks, Sprint 10): the stops and the wait the user must act on.
    else if ((m = body.match(/^stop: (\S+) turn ended on an API error \((rate_limit|authentication_failed|billing_error|account_on_hold)\)$/)) && m[1] === sid)
      $.ui.toast(STOPPED[m[2]], { timeoutMs: 12000 })
    else if ((m = body.match(/^quota: (\S+) waits for Enter: the usage limit reset while the computer slept$/)) && m[1] === sid)
      $.ui.toast('Longhaul: the usage limit reset while the computer slept. Press Enter in this chat to carry on.', { timeoutMs: 12000 })
    else if ((m = body.match(/^quota: Claude Code stopped waiting at a usage limit in (\S+)$/)) && m[1] === sid)
      $.ui.toast('Longhaul: Claude Code stopped waiting at the usage limit and will not carry on by itself. Send a message once it resets.', { timeoutMs: 12000 })
  }
}

// What the pop-up says for each stop the user has to act on.
const STOPPED: Record<string, string> = {
  rate_limit: 'Longhaul: this turn stopped at a rate or usage limit. If Claude Code does not carry on by itself once it lifts, send a message then.',
  authentication_failed: "Longhaul: this turn stopped because the chat's sign-in failed. Sign in again, then send a message.",
  billing_error: "Longhaul: this turn stopped on a billing error. Check the account's billing, then send a message.",
  account_on_hold: 'Longhaul: this turn stopped because the account is on hold. Check it at claude.ai before you go on.',
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const done = await next(e)
    await newLines($) // marks where the log ends now
    await status($)
    return done
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    try {
      // The live window, as the status line has it: a turn's usage sums all its
      // requests, so on a turn of several it overstated the context (2026-10-25).
      let live: number | undefined
      try {
        const { context: now } = await $.session.usage()
        if (typeof now?.tokens === 'number') live = now.tokens
      } catch {}
      const u: any = (e as any).usage
      if (live !== undefined) context = live
      else if (u && typeof u.input_tokens === 'number')
        context = u.input_tokens + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0)
      await popUps($, await newLines($))
    } catch {}
    await status($)
    return done
  })
}
