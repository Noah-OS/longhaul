import type { LonghaulQuestion, LonghaulWait } from '../types'

// This chat's question and its carry-on (the keep-working plan's K1, K2). At
// a usage limit in the VS Code panel, in ask, the relay asks in
// LONGHAUL_HOME/questions/<chat>.json and waits for `longhaul answer`; a
// carry-on it scheduled for a reset waits in LONGHAUL_HOME/waits/<chat>.json.
// Both go when done or when the chat closes. look() finds them, for the
// accounts pane (accounts.tsx keeps them in $.state for its drawing; the
// state library reads only atoms a file makes itself) and for display.ts's
// status entry (found(), the last look). It only reads, and runs one
// program: `kill -0` on the relay's pid, since a relay killed with kill -9
// leaves its question behind, with no one left to act on an answer.

const CHAT = /^[A-Za-z0-9_-]{1,100}$/ // longhaul.py's GUARD_ID: the chat's id names a file
const ID = /^q-[A-Za-z0-9]{1,64}$/ // longhaul.py writes q-<12 hex>; never an option
const CHOICES: Record<string, readonly string[]> = { move: ['move', 'wait'], first_reset: ['yes', 'no'] }
// A wait whose time is this long past is one its relay left (the relay
// removes it once it carried on, at the first poll after a sleep too).
const STALE = 600 // seconds

export type Found = { asked: LonghaulQuestion | null; waiting: LonghaulWait | null }

// Each file as last read, by path: its size and time, and what it held. A
// file whose size and time are the same is not read again.
const kept: Record<string, { stamp: string; value: unknown }> = {}
let last: Found = { asked: null, waiting: null } // the last look's finding; a reload starts with none

async function home($: any): Promise<string> {
  return (await $.env.get('LONGHAUL_HOME')) || `${await $.env.get('HOME')}/.longhaul`
}

// A file's JSON, read only when it changed; null when it is not there or not JSON.
async function file($: any, path: string): Promise<unknown> {
  let stamp: string
  try {
    const st = await $.fs.stat(path)
    if (st.kind !== 'file') throw new Error('not a file')
    stamp = `${st.size}:${st.mtimeMs}`
  } catch {
    delete kept[path]
    return null
  }
  if (kept[path]?.stamp === stamp) return kept[path]!.value
  let value: unknown = null
  try { value = JSON.parse(await $.fs.read(path)) } catch {}
  kept[path] = { stamp, value }
  return value
}

const num = (v: any): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const word = (v: any): string | null => (typeof v === 'string' && v.length > 0 && v.length <= 100 ? v : null)

// The question as the pane draws and answers it, or null for one that is
// not this chat's or that it could not answer as written.
export function cleanQuestion(v: any, chat: string): LonghaulQuestion | null {
  if (!v || typeof v !== 'object' || v.chat !== chat || typeof v.id !== 'string' || !ID.test(v.id)) return null
  const pid = v.relay_pid
  const kind = v.kind
  const choices = CHOICES[kind]
  if (!(Number.isInteger(pid) && pid > 0) || !choices) return null
  if (!Array.isArray(v.choices) || v.choices.length !== choices.length || !choices.every(c => v.choices.includes(c)))
    return null
  const from = word(v.from), to = word(v.to), limit = word(v.limit), resets = num(v.resets_at)
  const toResets = num(v.to_resets_at)
  if (from === null || to === null || limit === null || resets === null || (kind === 'first_reset' && toResets === null))
    return null
  return { id: v.id, chat, relay_pid: pid, asked_at: num(v.asked_at) ?? 0, kind, from, limit, resets_at: resets, to,
    to_resets_at: toResets, choices: [...choices] }
}

export function cleanWait(v: any, chat: string, now: number): LonghaulWait | null {
  if (!v || typeof v !== 'object' || v.chat !== chat) return null
  const until = num(v.until), account = word(v.account)
  if (until === null || account === null || until + STALE < now / 1000) return null
  return { chat, until, account, why: word(v.why) ?? '' }
}

// Whether the relay that asked still runs: `kill -0` sends no signal.
async function running($: any, pid: number): Promise<boolean> {
  try {
    const ran = await $.process.run(['kill', '-0', String(pid)], { timeoutMs: 5000 })
    return ran.exitCode === 0
  } catch { return false }
}

// One look at this chat's two files: the question open now (only while its
// relay runs) and the carry-on waiting. Any failure finds nothing.
export async function look($: any): Promise<Found> {
  let sid = '', lh = '', now = 0
  try {
    sid = String(await $.session.id())
    lh = await home($)
    now = await $.clock.now()
  } catch {}
  const ok = CHAT.test(sid)
  const q = ok ? cleanQuestion(await file($, `${lh}/questions/${sid}.json`), sid) : null
  const asked = q && (await running($, q.relay_pid)) ? q : null
  const waiting = ok ? cleanWait(await file($, `${lh}/waits/${sid}.json`), sid, now) : null
  last = { asked, waiting }
  return last
}

// What the last look found.
export function found(): Found {
  return last
}

// `longhaul.py answer CHAT QID CHOICE`'s arguments for an answer to this
// chat's question, or null: each part checked again, so nothing else
// reaches the command, and never an option.
export function answerArgs(q: LonghaulQuestion, choice: string, sid: string): string[] | null {
  return q.chat === sid && CHAT.test(sid) && ID.test(q.id) && q.choices.includes(choice) && /^[a-z]+$/.test(choice)
    ? ['answer', sid, q.id, choice] : null
}

// A time as longhaul.py's clock() shows it, in this machine's time: 14:05
// today, Tue 14:05 within six days either side, else 24 Nov 14:05.
export function clock(seconds: number, nowMs: number): string {
  const d = new Date(seconds * 1000)
  if (Number.isNaN(d.getTime())) return '?'
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const day = (t: Date) => new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()
  const days = Math.abs(Math.round((day(d) - day(new Date(nowMs))) / 86_400_000))
  if (!days) return hm
  if (days < 7) return `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]} ${hm}`
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()]
  return `${d.getDate()} ${month} ${hm}`
}

// What the question says, and each choice's button, in the relay's words.
export function asks(q: LonghaulQuestion, nowMs: number): { says: string; labels: Record<string, string> } {
  if (q.kind === 'move')
    return { says: `${q.from} hit its ${q.limit} limit (resets ${clock(q.resets_at, nowMs)}). `
      + `Move this chat to ${q.to} and carry on?`, labels: { move: 'Move and carry on', wait: 'Wait for the reset' } }
  const at = clock(q.to_resets_at ?? q.resets_at, nowMs)
  return { says: q.to === q.from ? `${q.from} resets at ${at}. Carry on then?`
    : `Every account is at its limit. ${q.to} resets first, at ${at}. Carry on there then?`,
  labels: { yes: `Yes, carry on at ${at}`, no: "No, I'll come back" } }
}

// The pop-up that says a question waits, for a pane that may not have opened.
export function waits(q: LonghaulQuestion): string {
  const what = q.kind === 'first_reset' && q.to !== q.from ? 'every account is at its limit'
    : `${q.from} hit its ${q.limit} limit`
  return `Longhaul: ${what}. A question waits: type /accounts if it did not open.`
}

// The carry-on in a few words: "carries on at 14:00 on personal-1".
export function carries(w: LonghaulWait, nowMs: number): string {
  return `carries on at ${clock(w.until, nowMs)} on ${w.account}`
}
