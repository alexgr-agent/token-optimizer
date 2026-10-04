// Clawd's pose reducer. Pure: every step takes `now` explicitly.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  DEBOUNCE_MS,
  MIN_DWELL_MS,
  HOLD_MS,
  NAP_AFTER_MS,
  initialPose,
  reducePose,
  toolPose,
  type PoseEvent,
  type PoseState,
} from '../../src/pose.ts'
import type { Pose } from '../../src/contracts.ts'

const T0 = 1_000_000

/** Feed [time, event] pairs; return the final state and every pose shown, in order. */
function run(steps: Array<[number, PoseEvent]>, start = initialPose(T0)) {
  let s: PoseState = start
  const shown: Pose[] = [s.pose]
  for (const [now, e] of steps) {
    s = reducePose(s, e, now)
    if (shown[shown.length - 1] !== s.pose) shown.push(s.pose)
  }
  return { s, shown }
}

/** A main turn already running and showing `type`, settled past the debounce. */
function typingTurn(t = T0): Array<[number, PoseEvent]> {
  return [
    [t, { type: 'turn-start' }],
    [t, { type: 'working-changed', working: true }],
    [t + 10, { type: 'tool-call', tool: 'Edit' }],
    // The first working pose (think) holds for its dwell before typing replaces it.
    [t + MIN_DWELL_MS, { type: 'tick', now: t + MIN_DWELL_MS }],
  ]
}

test('starts watching', () => {
  const s = initialPose(T0)
  assert.equal(s.pose, 'idle')
  assert.equal(s.since, T0)
})

test('tool names map to read, type, or lift', () => {
  for (const t of ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch', 'NotebookRead', 'LS']) {
    assert.equal(toolPose(t), 'read', t)
  }
  for (const t of ['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash', 'mcp__x__y', 'TodoWrite']) {
    assert.equal(toolPose(t), 'type', t)
  }
  for (const t of ['Task', 'Agent']) assert.equal(toolPose(t), 'lift', t)
})

test('session start plays wake for 2 s, then watching', () => {
  const { s, shown } = run([
    [T0, { type: 'session-start' }],
    [T0 + HOLD_MS.wake - 1, { type: 'tick', now: T0 + HOLD_MS.wake - 1 }],
  ])
  assert.equal(s.pose, 'wake')
  const after = reducePose(s, { type: 'tick', now: T0 + HOLD_MS.wake }, T0 + HOLD_MS.wake)
  assert.equal(after.pose, 'idle')
  assert.deepEqual(shown, ['idle', 'wake'])
})

test('turn start shows thinking; text shows writing; turn end shows done then watching', () => {
  let { s } = run([
    [T0, { type: 'turn-start' }],
    [T0, { type: 'working-changed', working: true }],
  ])
  assert.equal(s.pose, 'think')
  s = reducePose(s, { type: 'text' }, T0 + 1000)
  s = reducePose(s, { type: 'tick', now: T0 + 1000 + DEBOUNCE_MS }, T0 + 1000 + DEBOUNCE_MS)
  assert.equal(s.pose, 'write')
  const end = T0 + 5000
  s = reducePose(s, { type: 'turn-complete', reason: 'answer' }, end)
  s = reducePose(s, { type: 'working-changed', working: false }, end)
  assert.equal(s.pose, 'done')
  s = reducePose(s, { type: 'tick', now: end + HOLD_MS.done }, end + HOLD_MS.done)
  assert.equal(s.pose, 'idle')
})

test('permission open while a subagent runs and a compaction starts: needs you', () => {
  const { s } = run([
    ...typingTurn(),
    [T0 + 400, { type: 'agent-spawn', agentId: 'a1', background: false }],
    [T0 + 450, { type: 'compact-start' }],
    [T0 + 500, { type: 'permission-open' }],
  ])
  assert.equal(s.pose, 'ask')
  const closed = reducePose(s, { type: 'permission-closed' }, T0 + 600)
  assert.equal(closed.pose, 'compact', 'compacting beats heavy lifting once answered')
})

test('a question on screen is needs you; answered returns to the working pose', () => {
  let { s } = run([...typingTurn(), [T0 + 400, { type: 'question-open' }]])
  assert.equal(s.pose, 'ask')
  s = reducePose(s, { type: 'question-closed' }, T0 + 500)
  assert.equal(s.pose, 'type')
})

test('transients arriving while needs you is up are dropped, not queued', () => {
  let { s } = run([...typingTurn(), [T0 + 400, { type: 'permission-open' }]])
  s = reducePose(s, { type: 'turn-complete', reason: 'error' }, T0 + 500)
  assert.equal(s.pose, 'ask')
  s = reducePose(s, { type: 'permission-closed' }, T0 + 600)
  assert.notEqual(s.pose, 'error')
})

test("a subagent's turn end does not trigger done; the main turn's does", () => {
  let { s } = run([
    ...typingTurn(),
    [T0 + 400, { type: 'agent-spawn', agentId: 'a1', background: false }],
  ])
  assert.equal(s.pose, 'lift')
  s = reducePose(s, { type: 'turn-complete', reason: 'answer', agentId: 'a1' }, T0 + 2000)
  assert.notEqual(s.pose, 'done')
  assert.equal(s.pose, 'type', 'last subagent done returns to the working pose')
  s = reducePose(s, { type: 'turn-complete', reason: 'answer' }, T0 + 3000)
  s = reducePose(s, { type: 'working-changed', working: false }, T0 + 3000)
  assert.equal(s.pose, 'done')
})

test('subagent tool calls only feed heavy lifting, never the working sub-pose', () => {
  let { s } = run([
    ...typingTurn(),
    [T0 + 400, { type: 'agent-spawn', agentId: 'a1', background: false }],
    [T0 + 500, { type: 'tool-call', tool: 'Read', agentId: 'a1' }],
    [T0 + 600, { type: 'text' }],
    [T0 + 700, { type: 'tool-call', tool: 'Grep', agentId: 'a1' }],
    [T0 + 800, { type: 'tool-done', agentId: 'a1' }],
  ])
  s = reducePose(s, { type: 'agent-done', agentId: 'a1' }, T0 + 2000)
  s = reducePose(s, { type: 'tick', now: T0 + 2000 + DEBOUNCE_MS }, T0 + 2000 + DEBOUNCE_MS)
  // The main thread's own text (T0+600) moved it to writing; the agent's Read and Grep did not.
  assert.equal(s.pose, 'write')
})

test('aborted then error within a second: dizzy', () => {
  let { s } = run([...typingTurn()])
  s = reducePose(s, { type: 'turn-complete', reason: 'aborted' }, T0 + 1000)
  s = reducePose(s, { type: 'working-changed', working: false }, T0 + 1000)
  assert.equal(s.pose, 'stop')
  s = reducePose(s, { type: 'turn-complete', reason: 'error' }, T0 + 1800)
  assert.equal(s.pose, 'error')
})

test('error then aborted stays dizzy (dizzy beats stopped) for its 4 s hold', () => {
  let { s } = run([...typingTurn()])
  s = reducePose(s, { type: 'working-changed', working: false }, T0 + 1000)
  s = reducePose(s, { type: 'turn-complete', reason: 'error' }, T0 + 1000)
  s = reducePose(s, { type: 'turn-complete', reason: 'aborted' }, T0 + 1200)
  assert.equal(s.pose, 'error')
  s = reducePose(s, { type: 'tick', now: T0 + 1000 + HOLD_MS.error - 1 }, T0 + 1000 + HOLD_MS.error - 1)
  assert.equal(s.pose, 'error')
  s = reducePose(s, { type: 'tick', now: T0 + 1000 + HOLD_MS.error }, T0 + 1000 + HOLD_MS.error)
  assert.equal(s.pose, 'idle')
})

test('refusal ends the turn as done', () => {
  let { s } = run([...typingTurn()])
  s = reducePose(s, { type: 'turn-complete', reason: 'refusal' }, T0 + 1000)
  s = reducePose(s, { type: 'working-changed', working: false }, T0 + 1000)
  assert.equal(s.pose, 'done')
})

test('background subagent still running after the main turn ends: heavy lifting', () => {
  let { s } = run([
    ...typingTurn(),
    [T0 + 400, { type: 'agent-spawn', agentId: 'bg', background: true }],
    [T0 + 450, { type: 'agent-spawn', agentId: 'fg', background: false }],
  ])
  s = reducePose(s, { type: 'turn-complete', reason: 'answer' }, T0 + 3000)
  s = reducePose(s, { type: 'working-changed', working: false }, T0 + 3000)
  s = reducePose(s, { type: 'tick', now: T0 + 3000 + HOLD_MS.done }, T0 + 3000 + HOLD_MS.done)
  assert.equal(s.pose, 'lift')
  s = reducePose(s, { type: 'agent-done', agentId: 'bg' }, T0 + 9000)
  assert.equal(s.pose, 'idle', 'foreground agent was cleared with the main turn')
})

test('reading, typing, reading within 200 ms: one pose change after the debounce', () => {
  const { s: settled } = run([
    [T0, { type: 'turn-start' }],
    [T0, { type: 'working-changed', working: true }],
  ])
  assert.equal(settled.pose, 'think')
  const t = T0 + 1000
  const { s, shown } = run(
    [
      [t, { type: 'tool-call', tool: 'Read' }],
      [t + 100, { type: 'tool-call', tool: 'Edit' }],
      [t + 200, { type: 'tool-call', tool: 'Grep' }],
      [t + 300, { type: 'tick', now: t + 300 }],
      [t + 200 + DEBOUNCE_MS - 1, { type: 'tick', now: t + 200 + DEBOUNCE_MS - 1 }],
      [t + 200 + DEBOUNCE_MS, { type: 'tick', now: t + 200 + DEBOUNCE_MS }],
      [t + 2000, { type: 'tick', now: t + 2000 }],
    ],
    settled,
  )
  assert.deepEqual(shown, ['think', 'read'])
  assert.equal(s.since, t + 200 + DEBOUNCE_MS)
})

test('a burst that returns to the shown pose causes no change at all', () => {
  const { s: settled } = run([...typingTurn()])
  const t = T0 + 1000
  const { shown } = run(
    [
      [t, { type: 'tool-call', tool: 'Read' }],
      [t + 100, { type: 'tool-call', tool: 'Bash' }],
      [t + 1000, { type: 'tick', now: t + 1000 }],
    ],
    settled,
  )
  assert.deepEqual(shown, ['type'])
})

test('Task tool call on the main thread shows heavy lifting', () => {
  const { s } = run([
    ...typingTurn(),
    [T0 + 2000, { type: 'tool-call', tool: 'Task' }],
    [T0 + 2000 + MIN_DWELL_MS, { type: 'tick', now: T0 + 2000 + MIN_DWELL_MS }],
  ])
  assert.equal(s.pose, 'lift')
})

test('compaction shows compacting, then back to watching', () => {
  let { s } = run([[T0, { type: 'compact-start' }]])
  assert.equal(s.pose, 'compact')
  s = reducePose(s, { type: 'compact-end' }, T0 + 5000)
  assert.equal(s.pose, 'idle')
})

test('idle 10 minutes with a warm cache: napping; with a cold cache: cold', () => {
  const end = T0 + 1000
  const { s: idle } = run([
    ...typingTurn(),
    [end, { type: 'turn-complete', reason: 'answer' }],
    [end, { type: 'working-changed', working: false }],
  ])
  const nearly = reducePose(idle, { type: 'tick', now: end + NAP_AFTER_MS - 1 }, end + NAP_AFTER_MS - 1)
  assert.equal(nearly.pose, 'idle')
  const napping = reducePose(idle, { type: 'tick', now: end + NAP_AFTER_MS }, end + NAP_AFTER_MS)
  assert.equal(napping.pose, 'sleep')
  const cold = reducePose(napping, { type: 'cache-cold-changed', cold: true }, end + NAP_AFTER_MS)
  assert.equal(cold.pose, 'cold')
})

test('cold cache while idle shows cold; a new turn shows thinking', () => {
  let { s } = run([[T0, { type: 'cache-cold-changed', cold: true }]])
  assert.equal(s.pose, 'cold')
  s = reducePose(s, { type: 'turn-start' }, T0 + 100)
  s = reducePose(s, { type: 'working-changed', working: true }, T0 + 100)
  assert.equal(s.pose, 'think')
})

test('napping wakes into thinking when a turn starts', () => {
  let s = reducePose(initialPose(T0), { type: 'tick', now: T0 + NAP_AFTER_MS }, T0 + NAP_AFTER_MS)
  assert.equal(s.pose, 'sleep')
  s = reducePose(s, { type: 'turn-start' }, T0 + NAP_AFTER_MS + 1)
  s = reducePose(s, { type: 'working-changed', working: true }, T0 + NAP_AFTER_MS + 1)
  assert.equal(s.pose, 'think')
})

test('done does not show over a working sub-pose; wake does not show over done', () => {
  let { s } = run([[T0, { type: 'session-start' }]])
  s = reducePose(s, { type: 'turn-complete', reason: 'answer' }, T0 + 100)
  assert.equal(s.pose, 'done')
  s = reducePose(s, { type: 'turn-start' }, T0 + 200)
  s = reducePose(s, { type: 'working-changed', working: true }, T0 + 200)
  assert.equal(s.pose, 'think')
})

test('since only moves when the pose changes', () => {
  let { s } = run([...typingTurn()])
  const since = s.since
  s = reducePose(s, { type: 'tick', now: T0 + 5000 }, T0 + 5000)
  s = reducePose(s, { type: 'tool-call', tool: 'Write' }, T0 + 6000)
  assert.equal(s.since, since)
})

test('tick without an explicit now uses the event time; other events default to the last time seen', () => {
  let s = reducePose(initialPose(T0), { type: 'session-start' }, T0)
  s = reducePose(s, { type: 'tick', now: T0 + HOLD_MS.wake })
  assert.equal(s.pose, 'idle')
  const at = s.since
  s = reducePose(s, { type: 'compact-start' })
  assert.equal(s.since, at)
})

test('session start resets counters left over from before', () => {
  let { s } = run([
    ...typingTurn(),
    [T0 + 400, { type: 'agent-spawn', agentId: 'bg', background: true }],
    [T0 + 500, { type: 'permission-open' }],
    [T0 + 550, { type: 'compact-start' }],
  ])
  s = reducePose(s, { type: 'working-changed', working: false }, T0 + 600)
  s = reducePose(s, { type: 'session-start' }, T0 + 700)
  assert.equal(s.pose, 'wake')
  s = reducePose(s, { type: 'tick', now: T0 + 700 + HOLD_MS.wake }, T0 + 700 + HOLD_MS.wake)
  assert.equal(s.pose, 'idle')
})

test('a stray close never drives a counter negative', () => {
  let s = reducePose(initialPose(T0), { type: 'permission-closed' }, T0)
  s = reducePose(s, { type: 'agent-done', agentId: 'nobody' }, T0)
  s = reducePose(s, { type: 'permission-open' }, T0 + 1)
  assert.equal(s.pose, 'ask')
  s = reducePose(s, { type: 'permission-closed' }, T0 + 2)
  assert.equal(s.pose, 'idle')
})

test('a working pose holds its dwell: a quick type-write-type flicker never shows', () => {
  const { s: settled } = run([...typingTurn()])
  const t = settled.since
  const { shown } = run(
    [
      [t + 200, { type: 'text' }],
      [t + 200 + DEBOUNCE_MS, { type: 'tick', now: t + 200 + DEBOUNCE_MS }],
      [t + 1300, { type: 'tool-call', tool: 'Edit' }],
      [t + 1300 + MIN_DWELL_MS, { type: 'tick', now: t + 1300 + MIN_DWELL_MS }],
    ],
    settled,
  )
  assert.deepEqual(shown, ['type'])
})
