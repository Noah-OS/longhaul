import type { Register } from 'claude-code'

// Longhaul's display inside Claude Code (the owner, 2026-10-23): VS Code's
// panel shows no status line and no hook message, so this shows them. It
// only reads ~/.longhaul (accounts.json, usage/<account>.json, longhaul.log)
// and sends nothing anywhere; any failure shows nothing.

type Window = { used_percentage?: number; resets_at?: number }
type Reading = { at?: number; five_hour?: Window; seven_day?: Window }

const TAIL = 65536 // bytes of the log read at most per turn
let seen = -1 // the log's size when last read: a reload starts afresh
let context: number | undefined // the last turn's context, input + cache read + cache write

async function home($: any): Promise<string> {
  return (await $.env.get('LONGHAUL_HOME')) || `${await $.env.get('HOME')}/.longhaul`
}

async function expand($: any, path: string): Promise<string> {
  return path.startsWith('~/') ? `${await $.env.get('HOME')}${path.slice(1)}` : path
}

// The account this chat runs on: the one whose folder is CLAUDE_CONFIG_DIR, else main.
async function account($: any, lh: string): Promise<string> {
  const dir = ((await $.env.get('CLAUDE_CONFIG_DIR')) || '').replace(/\/+$/, '')
  try {
    const accounts = JSON.parse(await $.fs.read(`${lh}/accounts.json`))
    if (Array.isArray(accounts) && dir)
      for (const a of accounts)
        if (a && typeof a.name === 'string' && typeof a.config_dir === 'string'
            && (await expand($, a.config_dir)).replace(/\/+$/, '') === dir) return a.name
  } catch {}
  return 'main'
}

async function reading($: any, lh: string, name: string): Promise<Reading | undefined> {
  try {
    const r = JSON.parse(await $.fs.read(`${lh}/usage/${name}.json`))
    return r && typeof r === 'object' ? r : undefined
  } catch { return undefined }
}

function pct(w: Window | undefined, now: number): string | undefined {
  return w && typeof w.used_percentage === 'number' && (w.resets_at ?? 0) > now ? `${Math.round(w.used_percentage)}%` : undefined
}

async function status($: any): Promise<void> {
  try {
    const lh = await home($)
    const name = await account($, lh)
    const r = await reading($, lh, name)
    const now = Date.now() / 1000
    const parts = [name]
    const five = pct(r?.five_hour, now), week = pct(r?.seven_day, now)
    if (five) parts.push(`5h ${five}`)
    if (week) parts.push(`week ${week}`)
    if (context !== undefined) parts.push(`context ${Math.round(context / 1000)}k`)
    $.ui.status(parts.join(' · '))
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

async function popUps($: any, lines: string[]): Promise<void> {
  const sid = await $.session.id()
  const name = await account($, await home($))
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
    else if ((m = body.match(/^account: refused a prompt in (\S+?): (.+?) is kept for (.+?); the chat runs on (.+)$/)) && m[1] === sid)
      $.ui.toast(`Longhaul: ${m[2]} is kept for ${m[3]}; this chat runs on ${m[4]}. Close it and open it again.`, { timeoutMs: 12000 })
    // A newer Longhaul (said at a chat's start, once per version a day): a
    // release names no chat, so any chat shows it; an install names the
    // chat that runs the older version, and that chat alone shows it.
    else if ((m = body.match(/^release: Longhaul (\S+) is out \(this is (\S+)\)$/)))
      $.ui.toast(`Longhaul: ${m[1]} is out. To update: claude plugin marketplace update longhaul, then claude plugin update longhaul@longhaul, then /reload-plugins.`, { timeoutMs: 12000 })
    else if ((m = body.match(/^release: Longhaul (\S+) is installed; (\S+) runs (\S+)$/)) && m[2] === sid)
      $.ui.toast(`Longhaul: ${m[1]} is installed; this chat runs ${m[3]}. Type /reload-plugins to use it.`, { timeoutMs: 12000 })
  }
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
