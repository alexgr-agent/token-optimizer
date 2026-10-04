// The plugin's published state contract (types/index.d.ts) is written out by
// hand because the engine ships it to other plugins as is. These assignments
// fail `npm run typecheck` the moment a reducer's state and its published
// shape drift apart. Type-only: nothing here runs.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { Handoff, UiState } from '../../src/actions.ts'
import type { ClockState } from '../../src/clock.ts'
import type { PoseState } from '../../src/pose.ts'
import type {
  TokenOptimizerDesktopClock,
  TokenOptimizerDesktopHandoff,
  TokenOptimizerDesktopPose,
  TokenOptimizerDesktopUi,
} from '../../types/index.d.ts'

type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false
const same = <A, B>(proof: Same<A, B>): Same<A, B> => proof

test('the published state shapes match the reducers that write them', () => {
  assert.equal(same<ClockState, TokenOptimizerDesktopClock>(true), true)
  assert.equal(same<PoseState, TokenOptimizerDesktopPose>(true), true)
  assert.equal(same<UiState, TokenOptimizerDesktopUi>(true), true)
  assert.equal(same<Handoff, NonNullable<TokenOptimizerDesktopHandoff>>(true), true)
})
