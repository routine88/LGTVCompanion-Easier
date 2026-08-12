#!/usr/bin/env node
'use strict';

// Claude Code status line.
// Reads the session JSON from stdin (see https://code.claude.com/docs/en/statusline)
// and prints short, color-coded lines so nothing needs a wide terminal:
//
//   ▍ model   <display name (context % used) · live reasoning effort>
//   ▍ usage   <5-hour % · 7-day %>
//   ▍ billing <monthly subscription plan / per-token API>
//   ▍ folders <every directory this session can reach, one per line>
//
// Colors are 256-color ANSI chosen to stand out on a black background, with a
// different hue per metric. Every field is treated as possibly absent/null.
//
// The folders block is LAST on purpose. It is the only section whose height
// varies with the session (one line per directory), so putting it at the end
// keeps every fixed metric above it at a stable offset — the lines you glance
// at most do not move when you /add-dir.

const fs = require('fs');
const os = require('os');
const path = require('path');

// ---- ANSI helpers ----
const ESC = '\x1b[';
const RESET = ESC + '0m';
const BOLD = ESC + '1m';
const fg = (n) => `${ESC}38;5;${n}m`;

const COLOR = {
  model: 51,        // bright cyan
  label: 245,       // dim gray (labels)
  // magenta→green by level. xhigh sits between max and high and needs its own
  // entry: it is a real level (/effort xhigh, and what ultracode reports as),
  // so without one it fell back to the dim label gray — indistinguishable from
  // "no effort reported", which is the one thing this palette exists to avoid.
  effort: { max: 201, xhigh: 205, high: 208, medium: 220, low: 46 },
  billing: { monthly: 39, api: 214, unknown: 245 },      // blue / amber / gray
  folderCwd: 111,   // light steel blue — where you are now
  folderRoot: 109,  // muted teal — where the session was launched
  folderAdded: 103, // slate gray-blue — reachable, but not the focus
  error: 196,       // red
};

// Usage-severity color: green → yellow → orange → red.
function severityColor(pct) {
  if (pct >= 90) return 196;
  if (pct >= 75) return 208;
  if (pct >= 50) return 220;
  return 46;
}

// Width the labels are padded to. Every label must fit, or the value column
// steps right on that line alone and the block stops reading as a column.
const LABEL_WIDTH = 7;

// One line: a colored left bar, a fixed-width dim label, then the value.
function line(barColor, label, value) {
  const leftBar = `${fg(barColor)}▍${RESET}`;               // ▍
  const lbl = `${fg(COLOR.label)}${label.padEnd(LABEL_WIDTH)}${RESET}`;
  return `${leftBar} ${lbl} ${value}`;
}

// A continuation line: same geometry, no label. Used by the folders block so
// its rows line up under the first one instead of restating "folders".
function contLine(barColor, value) {
  return line(barColor, '', value);
}

// Both rate-limit windows on one line: "35% 5h · 6% 7d".
//
// No progress bars. Two ten-cell bars cost two whole lines to say what two
// numbers say, and the number is the part you actually read — the bar was only
// ever a second encoding of it. Each percentage keeps its OWN severity color,
// which is the part worth preserving: the windows fill at different rates, and
// a red 7-day next to a green 5-hour is exactly the case a shared color would
// hide.
//
// The left bar takes the WORSE of the two, since a line summarising two numbers
// should be as loud as its loudest one.
function usageLine(data) {
  const read = (key) => {
    const w = data.rate_limits && data.rate_limits[key];
    return w && typeof w.used_percentage === 'number' ? Math.round(w.used_percentage) : null;
  };
  const five = read('five_hour');
  const seven = read('seven_day');

  // Absent for every non-subscription session, and for the first call of a
  // subscription one — so this is a normal state, not an error.
  if (five === null && seven === null) {
    return line(COLOR.label, 'usage', `${fg(COLOR.label)}— n/a (Pro/Max, after 1st call)${RESET}`);
  }

  const part = (p, tag) => (p === null
    ? `${fg(COLOR.label)}n/a ${tag}${RESET}`
    : `${BOLD}${fg(severityColor(p))}${p}%${RESET} ${fg(COLOR.label)}${tag}${RESET}`);

  const worst = Math.max(five === null ? 0 : five, seven === null ? 0 : seven);
  return line(severityColor(worst), 'usage',
    `${part(five, '5h')} ${fg(COLOR.label)}·${RESET} ${part(seven, '7d')}`);
}

// ---- context window ------------------------------------------------------

// Claude Code bakes the window's *ceiling* into the display name on
// extended-context models ("Opus 4.8 (1M context)"). That slot is the most
// valuable one on the line and the ceiling never moves during a session, so it
// spent it saying the same thing on every render. Strip it, and put the live
// fill there instead — how full the window is now is the number you act on.
const MAX_CONTEXT_SUFFIX = /\s*\([^)]*context[^)]*\)\s*$/i;

// Percentage of the context window in use, or null if the session hasn't
// reported it yet (no API response so far).
function contextPct(data) {
  const cw = data.context_window;
  if (!cw) return null;
  if (typeof cw.used_percentage === 'number') return Math.round(cw.used_percentage);
  // Pre-calculated field absent: derive it. total_input_tokens already includes
  // cache reads and writes, so the two counts together are what's in the window.
  const used = (cw.total_input_tokens || 0) + (cw.total_output_tokens || 0);
  const size = cw.context_window_size;
  if (used > 0 && typeof size === 'number' && size > 0) {
    return Math.round((used / size) * 100);
  }
  return null;
}

// Rendered as a parenthetical straight after the model name, in the slot the
// ceiling used to hold. Carries its own severity color for the same reason the
// usage percentages do: a context window filling up is precisely the thing you
// want to catch before it forces a compaction, and a fixed color would hide it.
function contextText(data) {
  const pct = contextPct(data);
  if (pct === null) return `${fg(COLOR.label)}(n/a ctx)${RESET}`;
  return `${fg(COLOR.label)}(${RESET}${BOLD}${fg(severityColor(pct))}${pct}%${RESET}`
    + ` ${fg(COLOR.label)}ctx)${RESET}`;
}

const HOME = os.homedir();
const CLAUDE_DIR = path.join(HOME, '.claude');

function readStdin() {
  try { return fs.readFileSync(0, 'utf8'); } catch (_) { return ''; }
}

// Persisted effort default, used only if the live stdin field is absent
// (e.g. a model that doesn't support the reasoning-effort parameter).
function settingsEffort() {
  try {
    const s = JSON.parse(fs.readFileSync(path.join(CLAUDE_DIR, 'settings.json'), 'utf8'));
    return s.effortLevel || null;
  } catch (_) { return null; }
}

// Non-secret plan tier from the auth file (e.g. "max", "pro"). Never logs the token.
function subscriptionType() {
  try {
    const c = JSON.parse(fs.readFileSync(path.join(CLAUDE_DIR, '.credentials.json'), 'utf8'));
    return (c.claudeAiOauth && c.claudeAiOauth.subscriptionType) || null;
  } catch (_) { return null; }
}

// Is this session on the monthly subscription plan or per-token API billing?
// Billing isn't in the status-line stdin, so infer it from auth signals.
function detectBilling(data) {
  const env = process.env;
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const api = (note) => ({ color: COLOR.billing.api, value: 'API', note });
  const monthly = (tier) => ({ color: COLOR.billing.monthly, value: 'MONTHLY', note: cap(tier) });

  // Cloud gateways bill per token through the provider.
  if (env.CLAUDE_CODE_USE_BEDROCK) return api('Bedrock');
  if (env.CLAUDE_CODE_USE_VERTEX) return api('Vertex');

  const sub = subscriptionType();

  // rate_limits are reported only when the session actually runs on the
  // Claude.ai subscription — the strongest per-session "monthly plan" signal.
  if (data.rate_limits && sub) return monthly(sub);

  // Explicit key/token → per-token API billing.
  if (env.ANTHROPIC_API_KEY || env.ANTHROPIC_AUTH_TOKEN) return api('per-token');

  // Subscription creds present but rate_limits not populated yet (early session).
  if (sub) return monthly(sub);

  return { color: COLOR.billing.unknown, value: '—', note: 'detecting' };
}

// ---- folders -------------------------------------------------------------

// Longest path we will print. Past this the HEAD is dropped, not the tail:
// the leaf folder is what identifies a directory at a glance, and it is the
// part a left-truncating elision would throw away first.
const MAX_PATH = 46;

// Windows paths are case-insensitive, so the same directory can arrive spelled
// two ways — this session was launched at "C:\Users\Matt Lamon\Desktop\..." and
// had "C:\users\matt lamon\desktop\..." added, which are one folder. Comparing
// raw strings would list it twice.
function dirKey(p) {
  const norm = path.normalize(p).replace(/[\\/]+$/, '');
  return process.platform === 'win32' ? norm.toLowerCase() : norm;
}

// Home-relative and length-capped, because the interesting part of a path is
// almost never its first 30 characters.
function prettyPath(p) {
  let out = path.normalize(p).replace(/[\\/]+$/, '');
  const home = path.normalize(HOME).replace(/[\\/]+$/, '');
  const cmp = (s) => (process.platform === 'win32' ? s.toLowerCase() : s);
  if (cmp(out).startsWith(cmp(home))) {
    out = '~' + out.slice(home.length);
  }
  if (out.length > MAX_PATH) out = '…' + out.slice(out.length - (MAX_PATH - 1));
  return out;
}

// Every directory this session can actually read or write, in the order that
// answers "where am I?" before "what else is in scope?":
//
//   ● cwd       — the working directory right now
//   ◇ launched  — where the session started, when it is somewhere else
//   ○ added     — each /add-dir or --add-dir directory
//
// All three glyphs come from Geometric Shapes, which is an alignment decision
// rather than a stylistic one: a fullwidth mark such as ＋ (U+FF0B) occupies
// TWO terminal columns where ● occupies one, so mixing them steps every path
// in the block one column right of the row above it. Filled reads as "you are
// here", hollow as "also in scope".
//
// There is deliberately no count. It would have to live on the first row,
// which is the one row that already carries the label, so it pushed that
// row's glyph two columns right of the rows below — and the rows are the
// count anyway.
//
// Sourced from workspace.{current_dir,project_dir,added_dirs}; added_dirs is
// documented as an empty array when nothing has been added, but it is treated
// as possibly absent anyway, like every other field here.
function folderLines(data) {
  const ws = data.workspace || {};
  const cwd = ws.current_dir || data.cwd || ws.project_dir || '';
  const root = ws.project_dir || '';
  const added = Array.isArray(ws.added_dirs) ? ws.added_dirs : [];

  const rows = [];
  const seen = new Set();
  const push = (dir, color, glyph) => {
    if (!dir) return;
    const key = dirKey(dir);
    if (seen.has(key)) return;      // cwd is usually also project_dir
    seen.add(key);
    rows.push({ color, glyph, text: prettyPath(dir) });
  };

  push(cwd, COLOR.folderCwd, '●');
  push(root, COLOR.folderRoot, '◇');
  for (const d of added) push(d, COLOR.folderAdded, '○');

  if (!rows.length) {
    return [line(COLOR.label, 'folders', `${fg(COLOR.label)}— none reported${RESET}`)];
  }

  return rows.map((r, i) => {
    const value = `${fg(r.color)}${r.glyph}${RESET} ${i === 0 ? BOLD : ''}${fg(r.color)}${r.text}${RESET}`;
    return i === 0
      ? line(r.color, 'folders', value)
      : contLine(r.color, value);
  });
}

function main() {
  const raw = readStdin();
  let data = {};
  try { data = JSON.parse(raw); } catch (_) { data = {}; }

  // Opt-in capture of the real stdin, so its exact shape can be inspected once.
  // Enabled only while the flag file ~/.claude/.statusline-debug exists.
  try {
    if (raw && fs.existsSync(path.join(CLAUDE_DIR, '.statusline-debug'))) {
      fs.writeFileSync(path.join(CLAUDE_DIR, '.statusline-last-stdin.json'), raw);
    }
  } catch (_) { /* ignore */ }

  const out = [];

  // 1) MODEL (CONTEXT %) · EFFORT
  //
  // One line, because both the context fill and the effort are properties of
  // the model this session is running, not separate metrics — the same
  // relationship "billing" already shows as "MONTHLY · Max". Hence the label
  // stays "model": the leading term names the line, the qualifiers refine it,
  // which is the convention every multi-value line here now follows.
  //
  // The two qualifiers are attached differently on purpose. Context fill is a
  // parenthetical hugging the name because it occupies the slot the model's own
  // "(1M context)" ceiling used to, and it reads as a property of that name.
  // Effort stays a "· suffix" because it is set independently of the model.
  //
  // Each keeps its own color: cyan identifies the model, context runs the
  // shared green→red severity scale, and the effort palette runs magenta→green
  // by level. The left bar stays the model's cyan, because the model is the
  // subject and the rest qualifies it.
  const rawName = (data.model && data.model.display_name) || 'unknown';
  const model = rawName.replace(MAX_CONTEXT_SUFFIX, '') || rawName;
  const effortRaw = (data.effort && data.effort.level) || settingsEffort();
  const effKey = String(effortRaw || '').toLowerCase();
  const effColor = COLOR.effort[effKey] || COLOR.label;
  // An absent effort still prints "· —" rather than vanishing: dropping the
  // suffix would make "no effort reported" look identical to a model that has
  // no effort setting at all, and those are different things.
  const effText = effortRaw ? String(effortRaw).toUpperCase() : '—'; // —
  out.push(line(COLOR.model, 'model',
    `${BOLD}${fg(COLOR.model)}${model}${RESET} ${contextText(data)}`
    + ` ${fg(COLOR.label)}·${RESET} ${BOLD}${fg(effColor)}${effText}${RESET}`));

  // 2) USAGE — both rolling rate-limit windows on one line
  out.push(usageLine(data));

  // 3) BILLING (monthly subscription plan vs per-token API; see detectBilling)
  const b = detectBilling(data);
  const billValue = `${BOLD}${fg(b.color)}${b.value}${RESET}`
    + (b.note ? ` ${fg(COLOR.label)}· ${b.note}${RESET}` : '');
  out.push(line(b.color, 'billing', billValue));

  // 4) FOLDERS — variable height, so it goes last (see the header note)
  for (const l of folderLines(data)) out.push(l);

  process.stdout.write(out.join('\n'));
}

try {
  main();
} catch (e) {
  process.stdout.write(`${fg(COLOR.error)}statusline error: ${e && e.message}${RESET}`);
}
