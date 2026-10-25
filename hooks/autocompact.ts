import type { Register } from 'claude-code'

// Longhaul's compaction after a checkpoint (the owner, 2026-10-25: "Yes,
// probe then build"). The reminder (`longhaul reminder`) asks the model, at
// each multiple of LONGHAUL_CHECKPOINT_AT tokens, to save a checkpoint at
// its next stopping point. It saved, and nothing compacted: the owner's chat
// stayed at 600-840k tokens for hours after each save. So once the log has
// this chat's reminder and then its save, at the end of that turn this runs
// /compact once, shows a pop-up and has Longhaul log it; the SessionStart
// (compact) hook then puts the fresh checkpoint back.
// LONGHAUL_AUTO_COMPACT=off turns it off.
//
// It reads ~/.longhaul as display.ts does and writes no file: the log line
// goes through `longhaul.py autocompact-note`, which also keeps the time of
// each chat's last compaction in autocompact.json. That record is what
// keeps two compactions of a chat ten minutes apart when a reload of this
// module has forgotten its own count; a reload forgets a reminder heard too,
// so it can only miss a compaction, never add one. Any failure shows nothing.

const TAIL = 65536 // bytes of the log read at most per turn
const RECENT = 15 * 60 * 1000 // ms of the log a first read takes into account
const GAP = 600 // seconds: never two compactions of one chat closer than this
let seen = -1 // the log's size when last read: a reload starts afresh
let armed: string | undefined // the chat whose reminder was heard, until it compacts
let due: string | undefined // the same chat once its checkpoint was saved after that reminder
const last: Record<string, number> = {} // when this module compacted each chat, in seconds

async function home($: any): Promise<string> {
  return (await $.env.get('LONGHAUL_HOME')) || `${await $.env.get('HOME')}/.longhaul`
}

// The log's new lines, at most TAIL bytes of them; a log moved aside starts over.
async function newLines($: any): Promise<string[]> {
  try {
    const path = `${await home($)}/longhaul.log`
    const { size } = await $.fs.stat(path)
    // A first read (the module just loaded, by a start or a /reload-plugins) takes
    // the last RECENT of the log too: a reminder and its save in the turn that
    // loaded it were missed before (2026-10-25, the first live check).
    const first = seen < 0 || size < seen
    if (!first && size === seen) return []
    const take = first ? Math.min(size, TAIL) : Math.min(size - seen, TAIL)
    seen = size
    if (!take) return []
    const ran = await $.process.run(['tail', '-c', String(take), path])
    const text = typeof ran?.stdout === 'string' ? ran.stdout : ''
    const lines = text.split('\n').filter(Boolean)
    if (!first) return lines
    const since = (await $.clock.now()) - RECENT
    return lines.filter(line => {
      const m = line.match(/^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) /)
      return !!m && new Date(`${m[1]}T${m[2]}`).getTime() >= since
    })
  } catch { return [] }
}

// A reminder for this chat arms it; its save after that makes it due.
function hear(lines: string[], sid: string): void {
  for (const line of lines) {
    const body = line.replace(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d /, '')
    let m: RegExpMatchArray | null
    if ((m = body.match(/^reminder: (\S+) reminded at [\d,]+ tokens/)) && m[1] === sid) { armed = sid; due = undefined }
    else if ((m = body.match(/^checkpoint for (\S+) saved \([\d,]+ bytes\)/)) && m[1] === sid && armed === sid) due = sid
    // Its own compaction disarms, so a first read's replay never compacts twice.
    else if ((m = body.match(/^autocompact: compacted (\S+) after its checkpoint/)) && m[1] === sid) { armed = undefined; due = undefined }
  }
}

// When this chat last compacted, in seconds: this module's own count, or the
// note's record, which outlives a reload; 0 for never.
async function lastCompacted($: any, sid: string): Promise<number> {
  let kept = 0
  try {
    const record = JSON.parse(await $.fs.read(`${await home($)}/autocompact.json`))
    if (record && typeof record[sid] === 'number') kept = record[sid]
  } catch {}
  return Math.max(kept, last[sid] ?? 0)
}

// The context before the compaction: the live window's, as the status line
// has it; else the turn's usage, input + cache read + cache write, as
// display.ts counts it (a turn's usage sums its requests, so it can be more).
async function contextSize($: any, e: any): Promise<number | undefined> {
  try {
    const { context } = await $.session.usage()
    if (typeof context?.tokens === 'number') return Math.round(context.tokens)
  } catch {}
  const u = e.usage
  return u && typeof u.input_tokens === 'number'
    ? Math.round(u.input_tokens + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0))
    : undefined
}

async function compact($: any, e: any, sid: string): Promise<void> {
  const now = (await $.clock.now()) / 1000
  if (now - (await lastCompacted($, sid)) < GAP) { armed = due = undefined; return }
  const size = await contextSize($, e)
  if (size === undefined) return // due still: the next answered turn has a figure
  armed = due = undefined // before the call: one compaction per reminder, whatever happens
  last[sid] = now
  try {
    await $.command.run({ command: 'compact' })
  } catch (err: any) {
    $.ui.log(`longhaul autocompact: /compact was refused (${err?.name || 'Error'})`, { to: 'debug' })
    return
  }
  try {
    await $.process.run(['python3', '-I', `${$.plugin.root}/longhaul.py`, 'autocompact-note', sid, String(size)])
  } catch {}
  $.ui.toast(`Longhaul: compacted this chat after its checkpoint (${Math.round(size / 1000)}k tokens); the checkpoint comes back now.`, { timeoutMs: 10000 })
}

async function afterTurn($: any, e: any): Promise<void> {
  try {
    if (((await $.env.get('LONGHAUL_AUTO_COMPACT')) || '').trim().toLowerCase() === 'off') return
    const sid = await $.session.id()
    hear(await newLines($), sid)
    if (due === sid) await compact($, e, sid)
  } catch {}
}

export const register: Register = on => {
  // Answered turns only: an interrupted one leaves a due compaction for the
  // next. A subagent's turn is not the chat's, and its lines wait for the main one.
  on('turn.complete', { reason: 'answer' }, async ($, e, next) => {
    const done = await next(e)
    if (e.agentId !== undefined) return done
    await afterTurn($, e)
    return done
  })
}
