// The cache clock. Pure: time is passed in.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  KEEP_WARM_MARGIN_MS,
  canKeepWarm,
  initialClock,
  reduceClock,
  view,
  type ClockEvent,
  type ClockState,
} from '../../src/clock.ts'

const T = 5_000_000
const MIN = 60_000
const S = 1000

function feed(events: Array<[number, ClockEvent]>, start: ClockState = initialClock()) {
  return events.reduce((s, [now, e]) => reduceClock(s, e, now), start)
}

/** A finished turn whose last main-thread request landed at `at`. */
function turnEndedAt(at: number, lifetime: '1h' | '5m' | null, contextTokens = 80_000) {
  return feed([
    [at - 5 * S, { type: 'working-changed', working: true }],
    [at, { type: 'request-done', at, lifetime, contextTokens }],
    [at + S, { type: 'working-changed', working: false }],
  ])
}

test('starts unknown with no countdown', () => {
  const v = view(initialClock(), T, 3600)
  assert.equal(v.state, 'unknown')
  assert.equal(v.secondsLeft, null)
  assert.equal(v.tokensAtStake, null)
  assert.equal(v.measured, false)
  assert.equal(canKeepWarm(initialClock(), T), false)
})

test('a running turn is refreshing and shows the full lifetime', () => {
  const s = feed([
    [T, { type: 'working-changed', working: true }],
    [T + 3 * S, { type: 'request-done', at: T + 3 * S, lifetime: '1h', contextTokens: 50_000 }],
  ])
  const v = view(s, T + 30 * S, 3600)
  assert.equal(v.state, 'refreshing')
  assert.equal(v.secondsLeft, 3600)
  assert.equal(canKeepWarm(s, T + 30 * S), false, 'refused while a turn runs')
})

test('one-hour cache, turn ends at T: warm until T+55 min, warning until T+60 min, then cold', () => {
  const s = turnEndedAt(T, '1h')
  assert.equal(view(s, T + 55 * MIN - 1, 3600).state, 'warm')
  const w = view(s, T + 55 * MIN, 3600)
  assert.equal(w.state, 'warning')
  assert.equal(w.secondsLeft, 300)
  assert.equal(w.measured, true)
  assert.equal(w.lifetime, 3600)
  assert.equal(w.tokensAtStake, 80_000)
  assert.equal(view(s, T + 60 * MIN - 1, 3600).state, 'warning')
  const c = view(s, T + 60 * MIN, 3600)
  assert.equal(c.state, 'cold')
  assert.equal(c.secondsLeft, 0)
  assert.equal(c.tokensAtStake, 80_000)
})

test('the countdown is measured from the last request, not the turn end', () => {
  const s = turnEndedAt(T, '1h')
  assert.equal(view(s, T + 10 * MIN, 3600).secondsLeft, 50 * 60)
})

test('five-minute cache: warning from T+4 min, cold at T+5 min', () => {
  const s = turnEndedAt(T, '5m')
  assert.equal(view(s, T + 4 * MIN - 1, 300).state, 'warm')
  assert.equal(view(s, T + 4 * MIN, 300).state, 'warning')
  assert.equal(view(s, T + 5 * MIN, 300).state, 'cold')
  assert.equal(view(s, T + 4 * MIN, 3600).lifetime, 300, 'a measurement beats the plan default')
})

test('canKeepWarm: false at deadline minus 10 s, true at deadline minus 200 s', () => {
  const s = turnEndedAt(T, '1h')
  const deadline = T + 60 * MIN
  assert.equal(canKeepWarm(s, deadline - 200 * S), true)
  assert.equal(canKeepWarm(s, deadline - 10 * S), false)
  assert.equal(canKeepWarm(s, deadline - KEEP_WARM_MARGIN_MS), false)
  assert.equal(canKeepWarm(s, deadline - KEEP_WARM_MARGIN_MS - 1), true)
  assert.equal(canKeepWarm(s, T + 2 * MIN), true, 'offered while warm, not only in warning')
})

test('no Keep warm the instant the cache expires, or after', () => {
  const s = turnEndedAt(T, '5m')
  const deadline = T + 5 * MIN
  assert.equal(canKeepWarm(s, deadline), false)
  assert.equal(canKeepWarm(s, deadline + 1), false)
  assert.equal(canKeepWarm(s, deadline + 30 * MIN), false)
})

test('canKeepWarm false while a turn runs', () => {
  const s = reduceClock(turnEndedAt(T, '1h'), { type: 'working-changed', working: true }, T + MIN)
  assert.equal(canKeepWarm(s, T + MIN), false)
})

test('Keep warm: warming while in flight, refuses a second press, warm again from fork completion', () => {
  let s = turnEndedAt(T, '1h')
  s = reduceClock(s, { type: 'warm-start' }, T + 56 * MIN)
  assert.equal(view(s, T + 56 * MIN, 3600).state, 'warming')
  assert.equal(canKeepWarm(s, T + 56 * MIN), false, 'refused while warming')
  const doneAt = T + 56 * MIN + 8 * S
  s = reduceClock(s, { type: 'warm-done', at: doneAt, cacheReadTokens: 79_000, contextTokens: 81_000 }, doneAt)
  const v = view(s, doneAt, 3600)
  assert.equal(v.state, 'warm')
  assert.equal(v.secondsLeft, 3600)
  assert.equal(v.tokensAtStake, 81_000)
})

test('warm-start is refused at press time when the guard fails (stale button handle)', () => {
  const s = turnEndedAt(T, '1h')
  const pressed = reduceClock(s, { type: 'warm-start' }, T + 60 * MIN - 5 * S)
  assert.deepEqual(pressed, s)
  assert.equal(view(pressed, T + 60 * MIN - 5 * S, 3600).state, 'warning')
})

test('a warm-up that read under half the context had lapsed: cold', () => {
  let s = turnEndedAt(T, '5m')
  s = reduceClock(s, { type: 'warm-start' }, T + 4 * MIN)
  const doneAt = T + 4 * MIN + 10 * S
  s = reduceClock(s, { type: 'warm-done', at: doneAt, cacheReadTokens: 30_000, contextTokens: 80_000 }, doneAt)
  assert.equal(view(s, doneAt, 300).state, 'cold')
  assert.equal(canKeepWarm(s, doneAt), false)
})

test('a failed warm-up recomputes the state from the old deadline', () => {
  let s = turnEndedAt(T, '1h')
  s = reduceClock(s, { type: 'warm-start' }, T + 56 * MIN)
  s = reduceClock(s, { type: 'warm-failed' }, T + 57 * MIN)
  assert.equal(view(s, T + 57 * MIN, 3600).state, 'warning')
  assert.equal(view(s, T + 61 * MIN, 3600).state, 'cold')
})

test('cold returns to refreshing when the next turn starts, and warm after it', () => {
  let s = turnEndedAt(T, '5m')
  assert.equal(view(s, T + 10 * MIN, 300).state, 'cold')
  s = feed(
    [
      [T + 10 * MIN, { type: 'working-changed', working: true }],
      [T + 10 * MIN + 4 * S, { type: 'request-done', at: T + 10 * MIN + 4 * S, lifetime: '5m', contextTokens: 90_000 }],
    ],
    s,
  )
  assert.equal(view(s, T + 10 * MIN + 5 * S, 300).state, 'refreshing')
  s = reduceClock(s, { type: 'working-changed', working: false }, T + 10 * MIN + 6 * S)
  assert.equal(view(s, T + 10 * MIN + 6 * S, 300).state, 'warm')
})

test('a lapsed warm-up stays cold until a real request lands', () => {
  let s = turnEndedAt(T, '1h')
  s = reduceClock(s, { type: 'warm-start' }, T + 2 * MIN)
  s = reduceClock(s, { type: 'warm-done', at: T + 3 * MIN, cacheReadTokens: 0, contextTokens: 80_000 }, T + 3 * MIN)
  assert.equal(view(s, T + 3 * MIN, 3600).state, 'cold')
  s = feed(
    [
      [T + 4 * MIN, { type: 'working-changed', working: true }],
      [T + 4 * MIN + S, { type: 'request-done', at: T + 4 * MIN + S, lifetime: '1h', contextTokens: 80_000 }],
      [T + 4 * MIN + 2 * S, { type: 'working-changed', working: false }],
    ],
    s,
  )
  assert.equal(view(s, T + 5 * MIN, 3600).state, 'warm')
})

test('clear: back to unknown', () => {
  let s = turnEndedAt(T, '1h')
  s = reduceClock(s, { type: 'clear' }, T + MIN)
  const v = view(s, T + MIN, 3600)
  assert.equal(v.state, 'unknown')
  assert.equal(v.secondsLeft, null)
  assert.equal(v.tokensAtStake, null)
  assert.equal(canKeepWarm(s, T + MIN), false)
  const cold = reduceClock(turnEndedAt(T, '5m'), { type: 'clear' }, T + 20 * MIN)
  assert.equal(view(cold, T + 20 * MIN, 300).state, 'unknown')
})

// An unmeasured lifetime counts against the plan default for display only.

test('unmeasured on a Claude plan counts down from an hour but warns and goes cold on five minutes', () => {
  const s = turnEndedAt(T, null)
  const early = view(s, T + MIN, 3600)
  assert.equal(early.state, 'warm')
  assert.equal(early.measured, false)
  assert.equal(early.lifetime, 3600)
  assert.equal(early.secondsLeft, 59 * 60)
  assert.equal(view(s, T + 4 * MIN, 3600).state, 'warning')
  const lapsed = view(s, T + 5 * MIN, 3600)
  assert.equal(lapsed.state, 'cold', 'a possibly lapsed cache is never shown as warm')
  assert.equal(lapsed.secondsLeft, 0)
})

test('unmeasured on the API is the five-minute clock', () => {
  const s = turnEndedAt(T, null)
  const v = view(s, T + MIN, 300)
  assert.equal(v.lifetime, 300)
  assert.equal(v.secondsLeft, 240)
  assert.equal(v.measured, false)
})

test('Keep warm is never offered while unmeasured', () => {
  const s = turnEndedAt(T, null)
  assert.equal(canKeepWarm(s, T + MIN), false)
  const pressed = reduceClock(s, { type: 'warm-start' }, T + MIN)
  assert.equal(view(pressed, T + MIN, 3600).state, 'warm')
})

test('lifetime reading changes from unknown to 1h: deadline recomputed from the same anchor', () => {
  let s = turnEndedAt(T, null)
  assert.equal(view(s, T + 10 * MIN, 3600).state, 'cold')
  s = reduceClock(s, { type: 'lifetime-measured', lifetime: '1h' }, T + 10 * MIN)
  const v = view(s, T + 10 * MIN, 3600)
  assert.equal(v.state, 'warm')
  assert.equal(v.measured, true)
  assert.equal(v.secondsLeft, 50 * 60)
  assert.equal(canKeepWarm(s, T + 10 * MIN), true)
})

test('a later request without a lifetime reading keeps the earlier measurement', () => {
  let s = turnEndedAt(T, '1h')
  s = feed(
    [
      [T + MIN, { type: 'working-changed', working: true }],
      [T + MIN + S, { type: 'request-done', at: T + MIN + S, lifetime: null, contextTokens: 90_000 }],
      [T + MIN + 2 * S, { type: 'working-changed', working: false }],
    ],
    s,
  )
  const v = view(s, T + 2 * MIN, 3600)
  assert.equal(v.measured, true)
  assert.equal(v.tokensAtStake, 90_000)
})

test('an out-of-order older request never moves the anchor back', () => {
  let s = turnEndedAt(T, '1h')
  s = reduceClock(s, { type: 'request-done', at: T - 30 * MIN, lifetime: '1h', contextTokens: 70_000 }, T + MIN)
  assert.equal(view(s, T + MIN, 3600).secondsLeft, 59 * 60)
})

test('tick changes nothing in the state; time lives in view and canKeepWarm', () => {
  const s = turnEndedAt(T, '1h')
  assert.deepEqual(reduceClock(s, { type: 'tick', now: T + 59 * MIN }), s)
})

test('an older request changes neither the lifetime nor the tokens at stake', () => {
  let s = turnEndedAt(T, '1h', 800_000)
  s = reduceClock(s, { type: 'request-done', at: T - 30 * MIN, lifetime: '5m', contextTokens: 70_000 }, T + MIN)
  const v = view(s, T + 59 * MIN, 3600)
  assert.equal(v.state, 'warning')
  assert.equal(v.secondsLeft, 60)
  assert.equal(v.tokensAtStake, 800_000)
})
