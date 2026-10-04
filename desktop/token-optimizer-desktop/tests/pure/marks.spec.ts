import { test } from 'node:test'
import assert from 'node:assert/strict'

import { marks, cards, row, type Card } from '../../src/marks.ts'
import { iconSvg, ICONS } from '../../src/icons.ts'
import { snap, withQuality, TZ } from './fixtures.ts'
import type { CacheView } from '../../src/contracts.ts'

const body = (c: Card) => c.body.map((r) => r.text).join('')
const cacheCard = (cache: CacheView) => cards(snap({ cache }), TZ).find((c) => c.id === 'cache')!
const actionIds = (c: Card) => c.actions.map((a) => a.id)

test('five marks for a healthy subscriber, each with an icon and alt text', () => {
  const m = marks(snap(), TZ)
  assert.deepEqual(m.map((x) => x.id), ['quality', 'context', 'cache', 'fiveHour', 'week'])
  for (const x of m) {
    assert.ok(x.icon in ICONS, `${x.id} icon`)
    assert.ok(x.alt.length > 0, `${x.id} alt`)
  }
  const [q, ctx, cache, five, week] = m
  assert.equal(q!.value, '92')
  assert.equal(q!.badge, 'S')
  assert.equal(q!.tone, 'good')
  assert.equal(ctx!.value, '34%')
  assert.equal(ctx!.ringPercent, 34)
  assert.equal(cache!.value, '42m') // minutes: the band redraws once a minute, never per second
  assert.equal(cache!.tone, 'good')
  assert.equal(five!.value, '41%')
  assert.equal(five!.label, '5 hours')
  assert.match(five!.alt, /renews today at 3:20 PM/)
  assert.equal(week!.label, 'week')
})

test('quality missing: the mark shows "--"', () => {
  const q = marks(snap({ quality: null }), TZ)[0]!
  assert.equal(q.value, '--')
  assert.equal(q.badge, '--')
  assert.equal(q.tone, 'none')
})

test('context missing: "--"', () => {
  const c = marks(snap({ contextPercent: null }), TZ)[1]!
  assert.equal(c.value, '--')
  assert.equal(c.ringPercent, 0)
})

test('API user with no rate limits: no limit marks', () => {
  assert.deepEqual(marks(snap({ fiveHour: null, week: null }), TZ).map((x) => x.id), ['quality', 'context', 'cache'])
})

test('cache mark per state', () => {
  const cold = marks(snap({ cache: { state: 'cold', secondsLeft: 0, lifetime: 3600, measured: true, tokensAtStake: 1 } }), TZ)[2]!
  assert.equal(cold.value, 'cold')
  assert.equal(cold.tone, 'cold')
  assert.equal(cold.icon, 'cold')
  const unknown = marks(snap({ cache: { state: 'unknown', secondsLeft: null, lifetime: 300, measured: false, tokensAtStake: null } }), TZ)[2]!
  assert.equal(unknown.value, '--')
  const warn = marks(snap({ cache: { state: 'warning', secondsLeft: 42, lifetime: 300, measured: false, tokensAtStake: 1 } }), TZ)[2]!
  assert.equal(warn.value, '1m')
  assert.equal(warn.tone, 'caution')
  assert.equal(warn.ringPercent, 14)
  assert.match(warn.alt, /estimate/)
  assert.match(warn.alt, /not measured/)
})

test('tones follow the thresholds', () => {
  const m = marks(withQuality(snap({ contextPercent: 81, fiveHour: { percentUsed: 76, resetsAt: null } }), { score: 61 }), TZ)
  assert.equal(m[0]!.tone, 'bad')
  assert.equal(m[0]!.badge, 'C')
  assert.equal(m[1]!.tone, 'bad')
  assert.equal(m[3]!.tone, 'caution')
})

test('cache card offers Keep warm only when warm or warning and measured', () => {
  const base = { secondsLeft: 900, lifetime: 3600, tokensAtStake: 720_000 }
  assert.deepEqual(actionIds(cacheCard({ ...base, state: 'warm', measured: true })), ['warm'])
  assert.deepEqual(actionIds(cacheCard({ ...base, state: 'warning', secondsLeft: 40, measured: true })), ['warm'])
  assert.deepEqual(actionIds(cacheCard({ ...base, state: 'warm', measured: false })), [])
  assert.deepEqual(actionIds(cacheCard({ ...base, state: 'warning', secondsLeft: 40, measured: false })), [])
  for (const state of ['unknown', 'refreshing', 'warming'] as const) {
    assert.deepEqual(actionIds(cacheCard({ ...base, state, measured: true })), [], state)
  }
  const cold = cacheCard({ ...base, state: 'cold', secondsLeft: 0, measured: true })
  assert.deepEqual(actionIds(cold), ['clean-first'])
  assert.deepEqual(cold.body.filter((r) => r.lose), [{ text: '720k tokens', lose: true }])
  assert.match(body(cold), /makes later messages cheaper/)
})

test('warning cache card flags the tokens at stake', () => {
  const c = cacheCard({ state: 'warning', secondsLeft: 40, lifetime: 3600, measured: true, tokensAtStake: 720_000 })
  assert.equal(c.title, 'Drops in 1m')
  assert.deepEqual(c.body.filter((r) => r.lose), [{ text: '720k tokens', lose: true }])
})

test('quality, context and limit cards', () => {
  const all = cards(snap(), TZ)
  const q = all.find((c) => c.id === 'quality')!
  assert.equal(q.title, 'Quality S 92')
  assert.equal(body(q), 'Biggest drag: 3 oversized tool results, about 4k tokens.')
  assert.deepEqual(q.actions, [{ id: 'clean', label: 'Clean up' }, { id: 'fresh', label: 'Start fresh' }])
  const ctx = all.find((c) => c.id === 'context')!
  assert.equal(ctx.title, '340k of 1M tokens')
  const five = all.find((c) => c.id === 'fiveHour')!
  assert.equal(five.title, 'Renews today at 3:20 PM')
  assert.equal(body(five), 'That is in 2h 14m.')
  const week = all.find((c) => c.id === 'week')!
  assert.equal(week.title, 'Renews Thursday, Oct 8 at 9:00 AM')
  assert.deepEqual(cards(snap({ fiveHour: null, week: null }), TZ).map((c) => c.id), ['quality', 'context', 'cache'])
})

test('row: compaction count zero has no compaction fact; three says 3× compacted', () => {
  const zero = row(snap(), TZ)
  assert.equal(zero.facts.find((f) => f.icon === 'compact'), undefined)
  const three = row(withQuality(snap(), { compactions: 3 }), TZ)
  const fact = three.facts.find((f) => f.icon === 'compact')!
  assert.equal(fact.runs.map((r) => r.text).join(''), '3× compacted')
})

test('row facts: branch, session time, tool calls, checkpoint', () => {
  const r = row(snap(), TZ)
  const texts = r.facts.map((f) => [f.icon, f.runs.map((x) => x.text).join('')])
  assert.deepEqual(texts, [
    ['branch', 'feat/billing-tests'],
    ['clock', '1h 5m'],
    ['tool', '86 tools'],
    ['bookmark', 'Checkpoint 3m ago'],
  ])
  const bare = row(snap({ branch: null, quality: null }), TZ)
  assert.deepEqual(bare.facts.map((f) => f.icon), ['bookmark'])
  assert.equal(bare.facts[0]!.runs.map((x) => x.text).join(''), 'No checkpoint yet')
})

test('row savings: ready, loading with last figures, unavailable', () => {
  const ready = row(snap(), TZ).savings
  assert.equal(ready.state, 'ready')
  assert.equal(ready.sessionTokens, 640_000)
  assert.equal(ready.sessionText, '640k')
  assert.equal(ready.last30Text, '48M')
  assert.equal(ready.daily.length, 30)
  assert.equal(ready.bars.length, 30)
  assert.equal(ready.bars[29], 100)
  assert.ok(ready.bars.every((b) => b >= 8))

  const loading = row(snap({ savingsLoading: true }), TZ).savings
  assert.equal(loading.state, 'loading')
  assert.equal(loading.sessionText, '640k')

  const none = row(snap({ savings: null }), TZ).savings
  assert.equal(none.state, 'unavailable')
  assert.equal(none.sessionText, '--')
  assert.equal(none.last30Text, '--')
  assert.deepEqual(none.daily, [])
  assert.deepEqual(none.bars, [])
  assert.ok(none.reason && none.reason.length > 0)
})

test('iconSvg returns a standalone SVG with xmlns, colour and alt', () => {
  const svg = iconSvg('check', '#2f9e55', { alt: 'All clear' })
  assert.match(svg, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/)
  assert.match(svg, /stroke="#2f9e55"/)
  assert.match(svg, /aria-label="All clear"/)
  assert.ok(svg.includes(ICONS.check))
  assert.ok(svg.endsWith('</svg>'))
  assert.match(iconSvg('ask', 'red" onload="x'), /stroke="red&quot; onload=&quot;x"/)
  for (const name of ['branch', 'clock', 'tool', 'compact', 'bookmark', 'cold', 'hourglass', 'gauge', 'check', 'slip', 'ask', 'saved'] as const) {
    assert.ok(ICONS[name].length > 0, name)
  }
})
