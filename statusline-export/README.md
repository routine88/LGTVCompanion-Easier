# Claude Code Status Line

A multi-line, color-coded status line for Claude Code. One *subject* per line — related values
share a line as `leading term · qualifier` — kept narrow on purpose so nothing needs a wide
terminal:

```
▍ model   Opus 4.8 (37% ctx) · MAX        model + context used + effort    (cyan · green→red · magenta→green)
▍ usage   35% 5h · 6% 7d                  both rate-limit windows          (each % green→yellow→orange→red)
▍ billing MONTHLY · Max                   subscription plan vs API billing (blue / amber)
▍ folders ● ~/code/app                    every directory in scope         (steel blue / teal / slate)
▍         ◇ ~/code                          ◇ = launch dir, if you cd'd away
▍         ○ ~/notes                         ○ = added with /add-dir
```

## Requirements

- **Node.js** on your PATH (`node --version`). No other dependencies — it's plain Node, no npm install.
- The **usage** line only populates on a Claude.ai **Pro/Max** subscription, and only after the
  first API response in a session (before that it shows `n/a`).

## Install on the new machine

1. **Copy `statusline.js`** into your Claude config directory:
   - Windows: `C:\Users\<you>\.claude\statusline.js`
   - macOS / Linux: `~/.claude/statusline.js`

   The script has **no hardcoded paths** — it resolves your home directory at runtime, so the
   same file works on any machine or OS.

2. **Wire it into `~/.claude/settings.json`.** Merge this block into the existing JSON
   (keep your other settings — just add `statusLine`):

   ```json
   "statusLine": {
     "type": "command",
     "command": "node -e \"require(require('os').homedir()+'/.claude/statusline.js')\""
   }
   ```

   This command is **path-independent**: Node computes your home directory itself, so you can
   copy it verbatim to any computer. Tested under cmd.exe, PowerShell, and bash.

   *Prefer an explicit path?* Use this instead (edit the path for the new machine):

   ```json
   "command": "node \"C:/Users/<you>/.claude/statusline.js\""
   ```

   If `node` isn't found when the status line runs, use Node's absolute path in place of `node`.

3. **Restart Claude Code** (or just wait for the next render).

## How each line is sourced

- **model** / **context** / **effort** / **usage** come straight from the JSON Claude Code pipes
  to the status line. `effort` is the **live** session value (`/effort`), falling back to
  `effortLevel` in `settings.json` if the model doesn't expose it.

- **model** carries two qualifiers, because both are properties of the model this session is
  running rather than separate metrics — the same relationship `billing` already shows as
  `MONTHLY · Max`. That is why the label stays `model`: across every multi-value line here, the
  leading term names the line and what follows qualifies it.

  They attach differently on purpose. **Context fill** is a parenthetical hugging the name,
  because it occupies the slot the model's own `(1M context)` ceiling used to and reads as a
  property of that name. **Effort** stays a `·` suffix, because it is set independently of the
  model.

  Each part keeps its own color — cyan identifies the model, context runs the same green→red
  severity scale as `usage`, and the effort palette runs magenta→green by level — so the only
  things that move are the parts that changed. The left edge bar stays cyan: the model is the
  subject, and unlike `usage` there is no severity ordering for a "worst of the two" rule to
  read. An unset effort still prints `· —` rather than vanishing, since a missing suffix would
  make "no effort reported" look identical to a model that has no effort setting at all.

- **context** replaces the window's **ceiling**, which Claude Code bakes into the display name on
  extended-context models (`Opus 4.8 (1M context)`). That parenthetical is stripped and the live
  fill goes in its place: the ceiling never moves during a session, so it was spending the most
  valuable slot on the line to say the same thing on every render, while how full the window is
  *now* is the number you actually act on. It is colored by severity for the same reason the
  usage percentages are — a window filling towards a compaction is exactly what you want to catch
  early, and a fixed color would hide it.

  Read from `context_window.used_percentage`, falling back to
  `(total_input_tokens + total_output_tokens) / context_window_size` if that pre-calculated field
  is ever absent, and to `(n/a ctx)` before the session's first API response.

- **usage** puts both rolling windows on one line — `35% 5h · 6% 7d` — with **no progress bars**.
  Two ten-cell bars cost two whole lines to say what two numbers say, and the number is the part
  you read; the bar was only ever a second encoding of it. Each percentage keeps its **own**
  severity color, which is the part worth having: the windows fill at different rates, and a red
  7-day beside a green 5-hour is exactly the case one shared color would hide. The left edge bar
  takes the worse of the two, so the line is as loud as its loudest number. If only one window is
  reported the other reads `n/a 7d`; if neither is, the whole line falls back to the
  `— n/a (Pro/Max, after 1st call)` note.

- **billing** isn't in that JSON, so it's inferred from how the session is authenticated,
  in priority order:
  1. `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX` env → `API · Bedrock` / `API · Vertex`
  2. Active subscription (rate limits present **and** `subscriptionType` in
     `~/.claude/.credentials.json`) → `MONTHLY · <tier>`
  3. `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` env → `API · per-token`
  4. Otherwise `— detecting` (e.g. before first login/API call)

  It only ever reads the **non-secret `subscriptionType`** field — never the token.

- **folders** lists every directory the session can actually reach, from
  `workspace.current_dir`, `workspace.project_dir` and `workspace.added_dirs` — so `/add-dir`
  and `--add-dir` show up the moment they're added, and nothing is inferred. Paths are shortened
  to `~` and capped at 46 chars, elided from the **front** so the leaf folder always survives.
  Duplicates collapse case-insensitively on Windows, where `C:\Users\Me\x` and `c:\users\me\x`
  are one folder and would otherwise be listed twice.

  Three notes on how it's drawn, each of which was a bug first:

  - It is **last** because it's the only block whose height varies. Anything below it would
    shift every time you `/add-dir`; the fixed metrics above it never move.
  - There is deliberately **no count**. It only fits on the first row — the one row that already
    carries the `folders` label — so it pushed that row's glyph two columns right of the rows
    beneath it. The rows *are* the count.
  - The markers are `●` `◇` `○`, all from Geometric Shapes. That's alignment, not taste: a
    fullwidth mark such as `＋` (U+FF0B) takes **two** terminal columns where `●` takes one, so
    mixing them steps every path one column right of the row above.

## Customize

- Colors and per-level palettes: the `COLOR` object near the top of `statusline.js`
  (256-color ANSI codes).
- Line order / which metrics show: the `main()` function.
- Labels are padded to `LABEL_WIDTH` (7) for alignment — keep new labels ≤ 7 to stay aligned,
  and if you raise it, raise the constant rather than padding by hand.
- Longest folder path before eliding: `MAX_PATH` in `statusline.js`.
- Prefer only the current folder? Delete the `push(root, …)` and `for (const d of added)` lines
  in `folderLines()`; the rest of the block already handles a single row.
