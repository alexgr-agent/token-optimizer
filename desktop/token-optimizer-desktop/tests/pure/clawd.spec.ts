import { test } from 'node:test'
import assert from 'node:assert/strict'

import { clawdAlt, clawdSvg, DARK, LIGHT } from '../../src/clawd.ts'
import type { Pose } from '../../src/contracts.ts'

// Every opened element is closed in order; self-closing tags need no partner.
function balanced(svg: string): boolean {
  const stack: string[] = []
  for (const m of svg.matchAll(/<(\/?)([a-zA-Z]+)[^>]*?(\/?)>/g)) {
    const closing = m[1], name = m[2] ?? '', self = m[3]
    if (self) continue
    if (closing) { if (stack.pop() !== name) return false } else stack.push(name)
  }
  return stack.length === 0
}

const POSES: Pose[] = ['wake', 'idle', 'think', 'read', 'type', 'lift', 'ask', 'write', 'compact', 'done', 'stop', 'error', 'cold', 'sleep']

test('every pose draws well-formed SVG under the Svg size cap, in both palettes and both modes', () => {
  for (const pose of POSES) {
    for (const palette of [LIGHT, DARK]) {
      for (const animate of [true, false]) {
        const svg = clawdSvg(pose, 'calm', { animate, palette })
        assert.ok(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'), pose)
        assert.ok(svg.endsWith('</svg>'), pose)
        assert.ok(svg.length < 131072, `${pose} is ${svg.length} characters`)
        assert.ok(balanced(svg), `${pose} has unbalanced tags`)
      }
    }
  }
})

test('every pose names its moment for readers who cannot see it', () => {
  for (const pose of POSES) {
    assert.match(clawdAlt(pose), /^Clawd: /)
    assert.ok(clawdSvg(pose, 'calm', { animate: true, palette: LIGHT }).includes(`<title>${clawdAlt(pose)}</title>`))
  }
  assert.equal(clawdAlt('ask'), 'Clawd: needs you')
})

test('the still frame carries no animation at all', () => {
  for (const pose of POSES) {
    const svg = clawdSvg(pose, 'worried', { animate: false, palette: LIGHT })
    assert.ok(!svg.includes('<animate'), pose)
  }
})

test('animated poses loop', () => {
  for (const pose of POSES) {
    assert.ok(clawdSvg(pose, 'calm', { animate: true, palette: LIGHT }).includes('repeatCount="indefinite"'), pose)
  }
})

test('needs-you holds up the question sign; compacting has the two arrows', () => {
  const ask = clawdSvg('ask', 'calm', { animate: true, palette: LIGHT })
  assert.ok(ask.includes('M13.95 -0.6q.6-.75 1.25 0'))
  const compact = clawdSvg('compact', 'calm', { animate: true, palette: LIGHT })
  assert.ok(compact.includes('M6 -1.2v2.1') && compact.includes('M11 -1.2v2.1'))
})

test('a worried session sweats; a calm idle one does not', () => {
  assert.ok(clawdSvg('idle', 'worried', { animate: true, palette: LIGHT }).includes('#5aa9e6'))
  assert.ok(!clawdSvg('idle', 'calm', { animate: true, palette: LIGHT }).includes('#5aa9e6'))
})

test('watching, the eyes glance left and right every few seconds; the still frame keeps them centred', () => {
  const live = clawdSvg('idle', 'calm', { animate: true, palette: LIGHT })
  const glance = /<animateTransform attributeName="transform" type="translate" values="([^"]+)" dur="([\d.]+)s"[^>]*\/>/g
  const found = [...live.matchAll(glance)].find(m => (m[1] ?? '').includes('-0.6 0') && (m[1] ?? '').includes('0.6 0'))
  assert.ok(found, 'no left-right eye glance in the watching pose')
  const values = (found[1] ?? '').split(';')
  assert.equal(values[0], '0 0')
  assert.equal(values.at(-1), '0 0')
  const dur = Number(found[2])
  assert.ok(dur >= 6 && dur <= 8, `glance every ${dur}s`)
  const still = clawdSvg('idle', 'calm', { animate: false, palette: LIGHT })
  assert.ok(!still.includes('<animate'))
  assert.ok(still.includes('<g><g><rect x="6" y="5.2"') || still.includes('<g><rect x="6" y="5.2"'), 'still eyes are not centred')
  // Only the watching pose glances.
  assert.ok(![...clawdSvg('think', 'calm', { animate: true, palette: LIGHT }).matchAll(glance)].some(m => (m[1] ?? '').includes('-0.6 0;-0.6 0')))
})
