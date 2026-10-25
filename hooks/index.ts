import type { Register } from 'claude-code'
import { register as autocompact } from './autocompact.ts'
import { register as display } from './display.ts'

// Longhaul's one hooks module: Claude Code takes one per plugin (hooks.json
// "modules" refuses a second entry), so this registers each part's hooks.
// A plugin's registrations nest in order, first outermost: autocompact's
// hooks come first, so a compaction follows display's pop-ups and status
// for that turn, and its own pop-up is the last word.
export const register: Register = (on, options) => {
  autocompact(on, options)
  display(on, options)
}
