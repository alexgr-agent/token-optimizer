# Changelog

## [Unreleased]

- Tool-call fatigue warnings now scale with the session's effective context window instead of staying at the 200K thresholds (25/40 calls). A 1M window uses 202/324 calls; smaller session windows and explicit compact-window limits keep earlier warnings.

## [5.13.46] - 2026-10-15

- `git log` output now compresses: for the default format, every commit becomes one line (10-character hash, date, author name, subject), with bodies, trailers and author emails condensed to counts and Co-authored-by names kept. `git log -60` on this repo drops from about 6,100 to 2,300 estimated tokens, so the automatic Devin path now delivers it compressed instead of raw. A commit cut off by `| head` is listed as truncated instead of guessed. Every other shape is delivered exactly as git printed it: `--oneline`, `--format`, `--pretty`, `-z`, `--porcelain`, `--show-signature`, `--stat`, `--name-status`, `--name-only`, `--numstat`, `--graph`, `-p`, `-L` and output that does not parse cleanly.
- `find` path lists now compress: grouped by directory with every name kept for small directories and a count, type breakdown and example names for larger ones. In a full-tree listing the `.git`, `node_modules` and `__pycache__` trees collapse to a count with examples; they are never collapsed when the find root is inside them or the command is a `-name`/`-path` search. The total path count is always stated and absolute roots keep their real prefix. `find . -type f` on this repo drops from about 23,700 to 4,000 estimated tokens.
- Summaries apply only when `find` or `git log` is the command, or is followed only by `head`, `tail` or `sort` (through `|` or `|&`, with or without spaces). Pipelines through `cut`, `uniq`, `xargs`, `awk`, `sed` and similar, `find` with `-print0`, `-printf`, `-exec`, `-delete`, `-ls` or `-fprint`, error lines, names containing newlines or other ambiguous separators, and lists over 50,000 paths are delivered untouched. Failed commands still pass raw.
- Claude Code and Devin recover the full original exactly as before (archive pointer and capture ID).
- Devin automatic mode is steadier under load: when many commands finished at once, a compressed result could miss the cleanup list and stay on disk until `uninstall --purge`. Checks of Token Optimizer's own state files now run under the same lock their writers hold, a failed capture preparation leaves nothing behind, and a cleanup failure is reported instead of swallowed.
- While an install, refresh or uninstall is running, Devin hooks wait at most 2 seconds for it, then let the command run normally without compression; nothing hangs.

## [5.13.45] - 2026-10-14

- New for Devin CLI and Devin Desktop's agent: automatic mode. `bash install.sh --devin --auto` wires two hooks into the `hooks` key of `~/.config/devin/config.json`, so large command output is compressed in interactive sessions and `devin -p` with no new prompts. `bash install.sh --devin` alone stays manual-only (the `/token-optimizer-output` skill remains the fallback), `--manual` removes the hooks and keeps the skill, and a plain reinstall keeps the active mode. Restart running Devin sessions after installing.
- Only commands your own Devin allow rules already run without a prompt are compressed; commands Devin asks about still ask. The allow rule must be in your user-level Devin config (`~/.config/devin/config.json` or `config.local.json`); a project's `.devin/` rules can only make it stricter. Commands with unquoted globs, `~` or `#` (`ls *.txt`) run natively, untouched. `git status --short`, `--porcelain` and `-z` run untouched, and failures, stderr and credential-looking output pass through raw.
- Every automatic result carries a capture ID and a recover command that returns the exact original. Automatic captures are kept 7 days or 200 entries.
- `--auto` records your statement that no Devin team or enterprise permission policy applies, because those policies are not stored locally and cannot be checked.
- Uninstall and purge remove only Token Optimizer's own hook entries and keep every other hook and key. A JSONC config with comments is refused; the skill still installs. Mac and Linux only.
- `devin_doctor.py --probe` now proves the round trip (rewrite, one-time approval, other calls still prompt, sizes) and reads Devin's log for loaded global hooks. If Python is upgraded to a new Homebrew versioned path, doctor flags it; rerun the install.
- Measured live on Devin CLI 3000.11.3 (`-p` and interactive) and over ACP with the CLI agent and Desktop's bundled agent (3000.10.48): `grep -rn` on a real repo 5,054 to 1,846 tokens, a `pytest -v` run 4,932 to 1,323 tokens, no new prompts; `docker ps -a` (not in the allow rules) still prompted. As on Claude Code, `git log` and `find` mostly pass through uncompressed. Over ACP, Desktop's tool card shows the wrapper command.

## [5.13.44] - 2026-10-14

- Up-to-date model prices: gemini-4-argon.

## [5.13.43] - 2026-10-14

- Devin Desktop: the `/token-optimizer-output` skill now has install guidance for Desktop's Devin Local agent. Checked on macOS with Devin Desktop 1.126.0: the app's bundled agent listed the skill from the user skills folder and, when it was invoked over the agent's ACP interface, ran prepare, capture, seal and filter, compressed a repetitive output and recovered the original. The Desktop window itself, Linux and Windows were not exercised; native Windows remains unsupported.
- Devin dry-runs now tell the truth: `--dry-run` for install, uninstall and purge refuses, with exit 1 and no writes, in the same unsafe states where the real command refuses.
- Devin purge is all-or-nothing on the states it cannot finish: a directory-shaped state file, a symlinked or loose-mode captures folder, or unchanged adapter files in a read-only folder now refuse before anything is removed, instead of stopping halfway.
- Devin purge now completes after an interrupted update. Files a half-finished refresh wrote are recognised as the adapter's own and removed; a file you edited still stops the purge and is named.
- Devin refusals name the file or folder at fault and how to fix it (the symlink, the pending record, the missing payload file, the loose-mode path with its `chmod`).
- Unsafe optional metrics state (`metrics.db`, its sidecars, `session-salt`) no longer blocks install or readiness on Devin: install and doctor warn and name the file, and doctor reports it as `numeric_state_ok: false`.
- On macOS, an immutable installed file now refuses Devin install, uninstall and purge before anything changes. Elsewhere, uninstall keeps its ownership record when one file cannot be removed, names that file, and finishes on a rerun. Uninstall with nothing installed writes nothing. A foreign folder it would never touch is no longer a reason to refuse.
- Devin doctor reports pending recovery (exit 1, no local probe), warns when the install lock would make install or uninstall refuse, names an unsafe data folder instead of reporting consent as missing, and adds a best-effort Desktop app check from files on disk.

## [5.13.42] - 2026-10-13

- On Devin, `git status --short` output keeps its file names: the `git-status` hint now applies to the long form only, and short or porcelain listings get generic compression.

## [5.13.41] - 2026-10-13

- On Devin, a named format hint is now only trusted when the capture actually looks like that command's output; foreign output like `git log` hinted as `git-status` gets the generic treatment instead of collapsing to a few mislabeled lines.

## [5.13.40] - 2026-10-13

- The Devin output filter now verifies every installed payload file against the install manifest before it can run, and the doctor checks that the whole payload still imports.
- Devin install, refresh and uninstall keep working across home overrides and adapter versions, and purge clears stale install records.
- The Devin installer runs isolated like the rest of the adapter, and the install docs match the doctor and capture cleanup contract.
- On Devin, a wrong or unhelpful format hint no longer loses output: the filter falls back to generic compression, named formats never deliver more bytes than generic, and `git log` output now compresses.
- Colour-highlighted output containing a credential still passes through raw on Devin, even when the colour escapes split the token.

## [5.13.39] - 2026-10-13

- Memory health no longer reports every topic file as orphaned when MEMORY.md is a flat list of links.
- Session health only flags a session as outdated when its version is really older than the installed one.
- Doctor and the Hooks tab now recognise the hooks that plugin installs ship, so Smart Compaction and Session Tracking show as on.
- The desktop status bar finds Python on Windows machines where the Store alias reports a shortened exit code.
- On Codex, the optional command rewrite no longer approves `tree -o`, which writes a file. It now uses the same inspection-only check as every other host.
- On Windows, cleaning up a stale lock no longer fails when several sessions reach for it at once.
- New for Devin CLI: an optional skill you trigger yourself that shrinks large command output, with the original kept privately on your machine and recoverable. No automatic hooks, no permission changes.
- On Codex, quality and state now report the session that asked, and say "unresolved" instead of guessing when Codex provides no thread ID.

## [5.13.38] - 2026-10-12

- Your permission prompts and auto mode checks now apply to every Bash command. Token Optimizer no longer approves any command on your behalf.
- Bash output is still compressed, after the command has run.
- On Cursor, Copilot CLI and Grok, only inspection commands such as `ls`, `git status` and `rg` are compressed before they run.

## [5.13.37] - 2026-10-11

- Slim desktop status bar: one compact line by default, unfold for detail, with a link to the full dashboard.
- Advice from your own usage: the dashboard measures whether a different compact window or a 1 hour subagent cache would pay on your history, and says so either way. Nothing is changed for you.
- Context % you can trust: the desktop bar follows `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and compaction counts match what happened.
- Lighter and clearer on Windows: hooks start fewer helper processes, the health check counts real sessions only, and printed commands paste into cmd, PowerShell and Git Bash.
- Stronger secret redaction on every platform, including credentials typed in a command, and restored tool results come back intact.
- `kill-stale` only acts when it can prove a session is not the one it runs in, and only on your own sessions.

Thanks to @rexplx, @Reetryyy, @hexsprite and @reach4thelasers for the reports.

## [5.13.36] - 2026-10-08

- Up-to-date model prices: haiku_5_5, sonnet_5_5.

## [5.13.35] - 2026-10-05

- Browser and screenshot tools can be called again with the same input, even when the server's name doesn't say "browser". Clicks, snapshots and screenshots go through.
- Name any other live tools in `TOKEN_OPTIMIZER_LIVE_STATE_TOOLS`, set in your shell or in your settings `env`.

## [5.13.34] - 2026-10-05

- Works with the new OpenCode 2 plugin system, alongside the current one. Thanks to the contributor who built it.
- OpenCode tracking counts compactions and failed steps once each, and stays accurate when events repeat.
- The OpenCode plugin downloads at less than half its old size.

## [5.13.33] - 2026-10-04

- The "start a fresh session" tip shows once per session, as intended, instead of returning on later prompts.
- Clean up in the desktop status bar says when Claude Code skips the compaction, instead of reporting "Cleaned up."

## [5.13.32] - 2026-10-04

- Every session now keeps its own checkpoints, so running several sessions at once no longer clears out the quiet ones. Applies on every platform that saves checkpoints through Token Optimizer's engine.
- The desktop status bar only shows a checkpoint that is still there to restore.
- Passes the stricter plugin check in Claude Code 2.1.289.

## [5.13.31] - 2026-10-04

- The desktop status bar comes alive again: Clawd follows what your session is doing, and the arrow, Clean up, Start fresh and Keep warm respond to a click.

## [5.13.30] - 2026-10-03

- The desktop status bar now comes with Token Optimizer itself: install or update the plugin and it appears above your prompt, nothing extra to add.
- Hide it any time with `TOKEN_OPTIMIZER_STATUS_BAR=0` in your settings `env`.
- Older Claude Code versions keep every Token Optimizer feature and simply skip the bar.

## [5.13.29] - 2026-10-03

- New: a Token Optimizer status bar for the Claude desktop app, as the separate `token-optimizer-desktop` plugin: `/plugin install token-optimizer-desktop@alexgreensh-token-optimizer`.
- Quality, context, cache countdown and both usage limits at a glance, with a one-click Clean up, Start fresh or Keep warm when you need one.
- One more line shows your session, its last checkpoint, and the tokens Token Optimizer saved you in the last 30 days.
- Clawd acts out what your session is doing. Needs Claude Code 2.1.287 or newer; the terminal status line is unchanged.
- The status line now counts compactions the moment they happen.

## [5.13.28] - 2026-09-30

- Up-to-date model prices: gpt-6.1-sol.

## [5.13.27] - 2026-09-29

- Up-to-date model prices: sonnet_5_5.

## [5.13.26] - 2026-09-27

- Keep detached dashboard self-heals single-flight for the entire rebuild, even on large histories, and record the rebuild child's PID.
- Back off repeated Stop-hook dashboard attempts for one hour after a successful background refresh, while retrying promptly after a failed refresh or version upgrade. Fixes #204.
- Keep OpenCode and OpenClaw core dashboard labels aligned with 5.13.26; correct the OpenClaw adapter fallback to its current 2.4.24 package version.

## [5.13.25] - 2026-09-27

- Add opt-in native Pi diagnostics, active-branch usage, local archives, and review-before-use continuity checkpoints.
- Keep OpenCode and OpenClaw core version labels aligned with the release. Fixes #201.
- Protect Pi's local data from shared directories and symlinked files, reject authorization and cookie headers in archives, and keep optional checkpoint failures from interrupting compaction.
- Bound Pi archives to 1,024 files and pin OpenCode's transitive TOML parser to a patched release.
- Reject malformed Cowork telemetry requests, keep concurrent capture records intact, read capture lines with a firm memory bound, skip bad records without blocking later sessions, reject negative or imprecise token counts, and remove stale Cowork copies on the next ingest after a Claude row for the same session arrives. Restrict capture paths, keep captures private, and stop storing authentication headers. Document protected HTTPS ingress for cloud senders.
- Exit with an error when Cowork ingestion writes only some database rows, including in quiet mode.
- Keep Codex hooks working when an optional launcher is missing, validate Cowork collector URLs, and keep test child processes from inheriting host secrets.
- Update docs and VS Code development dependencies to audited versions. Install release CI dependencies without lifecycle scripts.
- Bound VS Code transcript tail reads so opening a long session cannot allocate memory for the entire file.
- Label compact-instruction dry runs as previews.

## [5.13.24] - 2026-09-25

- **Savings report matches the dashboard.** Measured savings, repeat reads avoided, estimates, and one all-in total.
- **Exact rates for repeat reads** on every model, including Opus 5.5 and Fable 5.1.
- **Every number checked against known answers** on each release.

## [5.13.23] - 2026-09-25

- **Full savings in the report.** Measured savings now include the context you no longer re-read on every later turn, often the biggest share.
- **Your own baseline, pinned.** Savings compare against your sessions from before you installed Token Optimizer, and that baseline no longer drifts as old history ages out.
- **Weekly card always shows what was measured.**

## [5.13.22] - 2026-09-25

- **Accurate daily cost.** Each day on the dashboard shows exactly what that day's requests cost, subagents included. Thanks @asaarela-bw (#200).
- **Prices that stay current.** New Claude, OpenAI/Codex and Gemini models are priced automatically with each update. The plugin still makes no network calls.
- **Exact rates for Opus 5.5 and Fable 5.1** in every engine.
- **Clean dashboard launch from OpenCode.** (#199)
- Faster dashboard, tidier logs.

## [5.13.16] - 2026-09-17

- Fix: the Hermes context-fill nudge measured the session-CUMULATIVE input tally instead of the live
  prompt, so it reported a context emergency that did not exist. Every host re-sends the whole
  conversation on each turn, so that sum climbs past the model window regardless of real occupancy:
  on a 129-call Hermes session the cumulative figure reached 1,285,803 against a 1,000,000 window
  ("Context ~100% full ... Grade: F", the percentage being capped at 100) while Hermes itself
  reported 278,545 / 1,000,000 = 28% for the same session. The nudge now uses the prompt the last
  call actually sent -- the full prompt_tokens (fresh input + cache-read + cache-write), since every
  prompt token occupies the window -- and says so in the message ("last request prompt ~N tokens vs
  model window M"). The cumulative tally is unchanged for cost and usage reporting. Regression tests:
  `tests/test_hermes_context_fill_nudge.py` (3 of the 5 original cases fail on the previous code,
  plus a cache-write case so the live figure is not undercounted on a cache-creation turn).

- Fix: Bash cross-turn output dedup and the repeat-command thrash nudge were keyed only by session, so
  a subagent could be told its output was "identical to your previous output", or that a command "has
  run N times this session", for work only the main agent had done. Both are now scoped to session plus
  agent identity: a supplied agent id is treated as an opaque identity (never normalized or merged),
  while a missing, empty, or whitespace-only id falls back to the historical session-only identity so
  the main-agent case is unchanged. The thrash guard's edit-detection reads the shared session activity
  log, so a subagent that edits a file between two identical runs still suppresses the false "stuck in a
  loop" nudge. (issue #189)

- Fix: the Windows hook launcher could select an incomplete or unreadable runtime version. It now admits
  only versioned install dirs whose `run.py` actually opens, choosing the newest that does and skipping
  incomplete, unreadable, or zero-byte candidates before falling back to the baked install. This replaces
  a readability check that was a no-op on Windows and removes a case where an unreadable candidate could
  cause the wrong version to be selected on some Python versions. Argv, stdin, and environment
  forwarding are unchanged. (issue #188)

- Fix: `health` and `kill-stale` found no running sessions when Claude Code is launched by the Claude
  Desktop app under WSL2, because the desktop starts a versioned `ccd-cli` binary rather than a `claude`
  binary, so session detection never matched. Detection now also recognizes the ccd-cli launcher via an
  anchored, version-shaped path match on the process command, which excludes bundled helpers and
  unrelated processes so no false sessions are counted. (issue #192)

## [5.13.15] - 2026-09-16

- Fix: `codex_install.py` no longer emits a base64 `python -c` exec-bootstrap as the Windows hook command on versioned marketplace installs (issue #183). The encoded-exec string trips generic-loader antivirus signatures (SentinelOne flagged it as a Metasploit variant, once per shipped copy of the file), even though the payload was fixed, readable, and decode-auditable. The installer now copies a plain, auditable `windows-launcher.py` next to the versioned install dirs -- a stable path that survives marketplace upgrades -- and bakes a command that invokes it by quoted path with plainly quoted argv. Version resolution (newest semver sibling, fail-open to the baked install, TOKEN_OPTIMIZER_DEBUG-gated resolver log), stdin/argv/env passthrough, legacy-command recognition on reinstall/uninstall, and upgrade trust semantics are unchanged: the command signature now normalizes only the `--baked-root` version leaf, legacy base64 commands compare verbatim so the next install replaces them (the one-time review that ships this fix), and `--decode-launcher` still decodes legacy commands while pointing launcher-file commands at their plain source. Regression coverage: an AV-safe source-string guard (the generator and the launcher never contain or emit `exec(`/base64/bootstrap patterns), launcher install/idempotence/stability tests, cross-platform resolver execution tests, and cmd.exe execution tests on Windows CI.
- Fix: the Codex command-compression shell resolver no longer crashes on unreadable PATH entries. A directory the user cannot stat (e.g. another account's private `~/.cargo/bin` on a shared machine) raised PermissionError out of `_default_shell()` and failed the hook; unreadable entries are now skipped like any other non-match.
- Fix: the generated Windows hook command now force-quotes every path/value token. `list2cmdline` only wraps a token containing whitespace, so a space-free install path carrying a cmd.exe metacharacter (`& | < > ^`) was emitted bare and `cmd` parsed it as a command separator; every token is now quoted (cmd treats those as literal inside quotes). A `%` in the install path remains the one documented, unsupported case.

## [5.13.14] - 2026-09-14

- Fix: the Codex log-index now self-heals on Windows instead of crashing on a locked or corrupt database. The open path closes the broken connection before rebuilding (Windows refuses to unlink an open file, so the rebuild previously reconnected to the same corrupt DB), retries each unlink briefly to ride out transient share locks, and no longer misdiagnoses a merely-locked database as corruption and deletes it out from under a live process. Transient lock contention (concurrent first-opens racing journal-mode/schema setup, a writer mid-commit, or a lock-upgrade deadlock that returns BUSY without consulting the busy handler) now retries on a deadline at both the connect and write-transaction stages instead of surfacing "database is locked" to callers -- follow-up hardening on #175.
- Fix: the release installable check no longer races the signing workflow. A fresh release missing CHECKSUMS.sha256 is polled through the signing grace window (measured from published_at) instead of failing instantly -- v5.13.13's check ran at publish+2s while the asset landed ~20s later. An old unsigned release still fails immediately.
- Fix: Codex hooks on native Windows failed with "hook exited with code 1" on versioned marketplace installs. The generated cmd.exe command assigned TOKEN_OPTIMIZER_RUNTIME_ROOT inside a `for /f` loop and read it back with `!TOKEN_OPTIMIZER_RUNTIME_ROOT!` on the same line; cmd parses a /C line once and `setlocal EnableDelayedExpansion` only applies from the next line, so Python received the literal placeholder path. The runner path is now built from the FOR variable `%R` inside the do-body, and the version resolver always prints one directory (newest semver install, else the baked install) so the fallback still runs. Regression tests execute the generated command through `%COMSPEC% /D /C` with spaces in the install path. Reinstall and uninstall now also recognize those broken commands: they carry no `token-optimizer/scripts` path marker (backslash install paths and `hooks/<name>_runner.py` args), so `_is_token_optimizer_group` additionally matches the full signature of our generated command -- the quoted `TOKEN_OPTIMIZER_RUNTIME_ROOT=` assignment together with our `hooks\run.py` runner invocation under a token-optimizer path (or via our own FOR/delayed-expansion variable) -- so a user's own hook that merely references the env var is never swept up. When the version resolver falls back to the baked install it now appends a line to `token-optimizer-codex-resolver.log` next to the version dirs if `TOKEN_OPTIMIZER_DEBUG` is set, making the previously silent fallback observable.

- Fix: the token-saving hooks now reach every supported harness. Codex, Cowork, and manual installs get the same savings as Claude Code -- startup diagnostics stay out of the model's context, and the command-failure and long-output nudges reach the model through each host's supported channel.
- Fix: SessionStart no longer adds anything to the model's context. Startup diagnostics (health checks, dashboard setup, daemon status) now write to a local log file instead of stdout/stderr, both of which the host captures into the session context. Sessions begin at their true baseline, so the token savings start on the first turn.
- Add: burn nudge. When the same command fails 3 times in a row with different output, a nudge suggests changing approach instead of re-running. Catches the edit-compile-fail cycle that the existing identical-output streak guard cannot see. Tunable with `TOKEN_OPTIMIZER_FAIL_STREAK_THRESHOLD` (default `3`).
- Add: inline-script repeat nudge. When a command with a heredoc body >= 300 chars has been run 8 times in a session, a nudge suggests saving the script to a file and running that instead, so the body is not re-sent as input tokens every turn. Tunable with `TOKEN_OPTIMIZER_INLINE_SCRIPT_THRESHOLD` (default `8`).

## [5.13.13] - 2026-09-14

- Add: first-class Codex support, extracted and hardened from external PR #175 by @dormancygrace. Codex sessions get real model pricing (gpt-6-astra, gpt-5.6-sol) through a versioned model catalog, delta-based token accounting that stops the over-count on incremental log writes, canonical session-ids, a SQLite log-index for fast session discovery, native Windows process handling, a security-hardened command-compression hook, and a base64 Windows launcher that survives cmd.exe quoting.
- Add: Codex Token-Coach port with full runtime isolation. Context-window detection, model config, and savings accounting now resolve per-runtime across Claude, Codex, and the five other supported harnesses, so a foreign runtime never inherits Claude's model env vars, ~/.claude config, or the 1M default.
- Fix: coach-integrity hardening across the measurement pipeline. Safe-int/type/size guards reject malformed session records, ANSI/VT escape sequences are stripped before terminal output, log-index reads are confined to the runtime home, concurrent writers no longer corrupt the index, and the consent gate narrows its exception handling and logs unexpected errors so a corrupt config is visible, while still failing open by design.
- Docs: new benchmarks category (Overview, Terminal-Bench floor, Controlled A/B, One real month) with a refreshed real-savings.svg built from current measurements.

## [5.13.10] - 2026-09-08

- Prevent sandbox dashboard tests from replacing the real background service. Isolate hook test homes and cached modules, and keep capped transformation percentages and older marker history consistent.

- Include modeled repeat-read savings in the action card for removals made during the selected period. Retain logged setup, output, routing and unmatched-event savings without counting initial removals twice.
- Make Savings easier to scan: compact transformation and action summaries, explicit periods and estimates, matching percentage and dollar comparisons, and expandable methods that stay open during live refresh.
- Compare lifetime context savings with its matching period subtotal. Preserve previously verified history when transcripts rotate, and remove duplicate delta-read entries from the derived ledger.
- Retain other logged savings in the weekly fallback calculation and show the previously omitted concise-output estimate. Supported runtimes without repeat-read evidence keep their logged totals.

## [5.13.9] - 2026-09-08

- Fix growing session logs being skipped after their first collection. Refresh parent and child activity without duplicating totals or overwriting newer collector results.
- Show the full Savings-tab estimate for the actual subscription week, counting overlapping savings once and labeling the amount as estimated savings accrued so far. Include new sessions before background collection catches up.
- Recover the workload comparison after history backfill or rebuild changes its baseline month. Cache weekly results for up to 60 seconds and retain weekly dollars when quota readings are unavailable.
