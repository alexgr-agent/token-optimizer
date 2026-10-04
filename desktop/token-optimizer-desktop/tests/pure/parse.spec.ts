// Parsing Token Optimizer's files and the engine's usage into the band's
// shapes. Pure: every input is passed in.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  parseQualityCache,
  parseStatusBar,
  parseUsage,
  resolveTokenOptimizerRoot,
} from '../../src/parse.ts'

const NOW = 1_790_986_000

const QUALITY = {
  score: 31.6,
  grade: 'F',
  resource_health: 77.6,
  resource_health_grade: 'B',
  breakdown: {
    stale_reads: { score: 100, estimated_waste_tokens: 0 },
    bloated_results: { score: 100, estimated_waste_tokens: 123_338 },
    duplicates: { score: 100, estimated_waste_tokens: 0 },
    reread_loops: { estimated_waste_tokens: 6000 },
    total_estimated_waste_tokens: 123_338,
  },
  tool_calls: 349,
  compactions: 2,
  session_start_ts: 1_790_879_584,
  last_checkpoint_epoch: 1_790_985_684,
}

test('quality: resource health wins over the legacy score, with its own grade', () => {
  const q = parseQualityCache(JSON.stringify(QUALITY), NOW)
  assert.deepEqual(q, {
    score: 77.6,
    grade: 'B',
    drag: 'bloated tool results',
    toolCalls: 349,
    compactions: 2,
    checkpointEpoch: 1_790_985_684,
    sessionStartEpoch: 1_790_879_584,
    fillPct: q!.fillPct, // whatever the fixture's fill_pct holds, checked on its own below
  })
})

test('quality: fill_pct comes through as a percent; out-of-range is dropped', () => {
  const base = JSON.parse(JSON.stringify(QUALITY))
  assert.equal(parseQualityCache(JSON.stringify({ ...base, fill_pct: 0.4 }), NOW)!.fillPct, 0.4)
  assert.equal(parseQualityCache(JSON.stringify({ ...base, fill_pct: 140 }), NOW)!.fillPct, null)
})

test('quality: an older cache without resource health falls back to score and grade', () => {
  const q = parseQualityCache({ score: 64, grade: 'C' }, NOW)
  assert.equal(q?.score, 64)
  assert.equal(q?.grade, 'C')
  assert.equal(q?.drag, null)
  assert.equal(q?.toolCalls, null)
  assert.equal(q?.compactions, 0)
  assert.equal(q?.checkpointEpoch, null)
  assert.equal(q?.sessionStartEpoch, null)
})

test('quality: a missing grade is derived with Token Optimizer\'s own bands', () => {
  assert.equal(parseQualityCache({ resource_health: 91 }, NOW)?.grade, 'S')
  assert.equal(parseQualityCache({ resource_health: 55 }, NOW)?.grade, 'C')
  assert.equal(parseQualityCache({ resource_health: 12 }, NOW)?.grade, 'F')
})

test('quality: no waste anywhere means no drag', () => {
  const q = parseQualityCache({ ...QUALITY, breakdown: { stale_reads: { estimated_waste_tokens: 0 } } }, NOW)
  assert.equal(q?.drag, null)
})

test('quality: the biggest waste signal names the drag', () => {
  const q = parseQualityCache(
    { ...QUALITY, breakdown: { stale_reads: { estimated_waste_tokens: 9000 }, duplicates: { estimated_waste_tokens: 200 } } },
    NOW,
  )
  assert.equal(q?.drag, 'stale file reads')
})

test('quality: garbage, half-written or scoreless input reads as nothing', () => {
  assert.equal(parseQualityCache('{"score": 4', NOW), null)
  assert.equal(parseQualityCache('[]', NOW), null)
  assert.equal(parseQualityCache(null, NOW), null)
  assert.equal(parseQualityCache({ grade: 'A' }, NOW), null)
  assert.equal(parseQualityCache({ score: 'high' }, NOW), null)
})

test('quality: epochs in the future (or in milliseconds) are not trusted', () => {
  const q = parseQualityCache({ ...QUALITY, last_checkpoint_epoch: NOW * 1000, session_start_ts: NOW + 3600 }, NOW)
  assert.equal(q?.checkpointEpoch, null)
  assert.equal(q?.sessionStartEpoch, null)
})

test('quality: negative or fractional counts are cleaned', () => {
  const q = parseQualityCache({ score: 50, compactions: -1, tool_calls: 12.7 }, NOW)
  assert.equal(q?.compactions, 0)
  assert.equal(q?.toolCalls, 12)
})

const day = (i: number, tokens: number) => ({ date: `2026-09-${String(i + 1).padStart(2, '0')}`, tokens, usd: tokens / 1e6 })

const STATUS = {
  schema: 1,
  session_id: 'sess-1',
  savings: {
    unit: 'tokens',
    session_tokens: 41_000,
    session_usd: 0.6,
    daily: Array.from({ length: 30 }, (_, i) => day(i, i * 100)),
    total_30d_usd: 12.3,
    total_30d_tokens: 2_400_000,
    computed_at: NOW - 5,
  },
  savings_state: 'fresh',
  savings_age_s: 5,
  savings_reason: null,
  refresh_started: false,
  last_request_epoch: 1_790_985_772.132,
  cache_lifetime: '1h',
  last_checkpoint_epoch: 1_790_985_752,
}

test('status bar: savings, clock anchor, lifetime and checkpoint come through', () => {
  const s = parseStatusBar(JSON.stringify(STATUS))
  assert.ok(s)
  assert.equal(s.savings?.sessionTokens, 41_000)
  assert.equal(s.savings?.last30Tokens, 2_400_000)
  assert.equal(s.savings?.daily.length, 30)
  assert.equal(s.savings?.daily[0], 0)
  assert.equal(s.savings?.daily[29], 2900)
  assert.equal(s.savingsState, 'fresh')
  assert.equal(s.savingsReason, null)
  assert.equal(s.lastRequestEpoch, 1_790_985_772.132)
  assert.equal(s.cacheLifetime, '1h')
  assert.equal(s.checkpointEpoch, 1_790_985_752)
})

test('status bar: loading has no savings, keeps the reason, and the transcript facts still land', () => {
  const s = parseStatusBar({ ...STATUS, savings: null, savings_state: 'loading', savings_reason: 'computing savings' })
  assert.ok(s)
  assert.equal(s.savings, null)
  assert.equal(s.savingsState, 'loading')
  assert.equal(s.savingsReason, 'computing savings')
  assert.equal(s.cacheLifetime, '1h')
})

test('status bar: unknown state and lifetime values read as unavailable and unmeasured', () => {
  const s = parseStatusBar({ ...STATUS, savings_state: 'weird', cache_lifetime: '30m', last_request_epoch: 'x' })
  assert.equal(s?.savingsState, 'unavailable')
  assert.equal(s?.cacheLifetime, null)
  assert.equal(s?.lastRequestEpoch, null)
})

test('status bar: a short or long daily series is fitted to 30 days ending today', () => {
  const short = parseStatusBar({ ...STATUS, savings: { ...STATUS.savings, daily: [day(0, 7), day(1, 9)] } })
  assert.equal(short?.savings?.daily.length, 30)
  assert.deepEqual(short?.savings?.daily.slice(27), [0, 7, 9])
  const long = parseStatusBar({ ...STATUS, savings: { ...STATUS.savings, daily: Array.from({ length: 35 }, (_, i) => day(i % 28, i)) } })
  assert.equal(long?.savings?.daily.length, 30)
  assert.equal(long?.savings?.daily[29], 34)
  assert.equal(long?.savings?.daily[0], 5)
})

test('status bar: bad day entries count as zero, missing totals as null', () => {
  const s = parseStatusBar({ ...STATUS, savings: { daily: [{ tokens: 'x' }, null, { tokens: -4 }] } })
  assert.deepEqual(s?.savings?.daily.slice(27), [0, 0, 0])
  assert.equal(s?.savings?.sessionTokens, null)
  assert.equal(s?.savings?.last30Tokens, null)
})

test('status bar: output that is not a JSON object reads as nothing', () => {
  assert.equal(parseStatusBar(''), null)
  assert.equal(parseStatusBar('Traceback (most recent call last):'), null)
  assert.equal(parseStatusBar('[1]'), null)
})

test('usage: fill, window and both limits', () => {
  const u = parseUsage({
    startedAt: 0,
    context: { tokens: 620_000, window: 1_000_000, percent: 62 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 80, resetsAt: '2026-10-03T05:00:00Z' },
      { kind: 'seven_day', percentUsed: 12 },
      { kind: 'spend_limit', percentUsed: 99 },
    ],
  })
  assert.deepEqual(u, {
    contextPercent: 62,
    contextTokens: 620_000,
    contextWindow: 1_000_000,
    fiveHour: { percentUsed: 80, resetsAt: '2026-10-03T05:00:00Z' },
    week: { percentUsed: 12, resetsAt: null },
    startedAtMs: null, // startedAt 0 is no start time
  })
  assert.equal(parseUsage({ startedAt: 1_791_000_000_000 }).startedAtMs, 1_791_000_000_000)
})

test('usage: a percent the engine left out is worked out from tokens and window', () => {
  const u = parseUsage({ context: { tokens: 50_000, window: 200_000 }, rateLimits: [] })
  assert.equal(u.contextPercent, 25)
  assert.equal(u.fiveHour, null)
  assert.equal(u.week, null)
})

test('usage: nothing known reads as all nulls', () => {
  assert.deepEqual(parseUsage(null), {
    contextPercent: null,
    contextTokens: null,
    contextWindow: null,
    fiveHour: null,
    week: null,
    startedAtMs: null,
  })
})

const INSTALLED = {
  version: 2,
  plugins: {
    'token-optimizer-desktop@alexgreensh-token-optimizer': [{ scope: 'user', installPath: '/c/desktop/0.1.0' }],
    'token-optimizer-cowork@x': [{ scope: 'user', installPath: '/c/cowork' }],
    'token-optimizer@alexgreensh-token-optimizer': [
      { scope: 'project', installPath: '/c/to/5.13.20', lastUpdated: '2026-09-01T00:00:00Z' },
      { scope: 'user', installPath: '/c/to/5.13.26', lastUpdated: '2026-09-27T10:59:52.599Z' },
    ],
  },
}

test('Token Optimizer root: newest plugin install first, then the skill install', () => {
  const roots = resolveTokenOptimizerRoot(JSON.stringify(INSTALLED), '/home/me/.claude')
  assert.deepEqual(roots, [
    { scriptsDir: '/c/to/5.13.26/skills/token-optimizer/scripts', runner: '/c/to/5.13.26/hooks/module_runner.py' },
    { scriptsDir: '/c/to/5.13.20/skills/token-optimizer/scripts', runner: '/c/to/5.13.20/hooks/module_runner.py' },
    { scriptsDir: '/home/me/.claude/skills/token-optimizer/scripts', runner: null },
  ])
})

test('Token Optimizer root: no registry, or a broken one, still offers the skill install', () => {
  const skill = [{ scriptsDir: '/home/me/.claude/skills/token-optimizer/scripts', runner: null }]
  assert.deepEqual(resolveTokenOptimizerRoot(null, '/home/me/.claude'), skill)
  assert.deepEqual(resolveTokenOptimizerRoot('{nope', '/home/me/.claude'), skill)
  assert.deepEqual(resolveTokenOptimizerRoot({ plugins: { 'token-optimizer@m': 'x' } }, '/home/me/.claude'), skill)
})

test('Token Optimizer root: a Windows home keeps its own separators out of the way', () => {
  const roots = resolveTokenOptimizerRoot(
    { plugins: { 'token-optimizer@m': [{ installPath: 'C:\\Users\\me\\.claude\\plugins\\cache\\to\\5.13.26\\' }] } },
    'C:\\Users\\me/.claude',
  )
  assert.equal(roots[0]?.scriptsDir, 'C:\\Users\\me\\.claude\\plugins\\cache\\to\\5.13.26/skills/token-optimizer/scripts')
  assert.equal(roots[1]?.scriptsDir, 'C:\\Users\\me/.claude/skills/token-optimizer/scripts')
})

test('Token Optimizer root: no home gives only registry entries', () => {
  assert.deepEqual(resolveTokenOptimizerRoot(null, ''), [])
})

test('Token Optimizer root: a relocated Claude folder (CLAUDE_CONFIG_DIR) is where the skill install lives', () => {
  assert.deepEqual(resolveTokenOptimizerRoot(null, '/data/claude'), [{ scriptsDir: '/data/claude/skills/token-optimizer/scripts', runner: null }])
})
