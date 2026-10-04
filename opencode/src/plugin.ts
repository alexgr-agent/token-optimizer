import type { Plugin as V2Plugin } from "@opencode/plugin";
import { TokenOptimizerPlugin } from "./index.js";
import { setupV2 } from "./v2.js";

export const id = "token-optimizer-opencode";
export { TokenOptimizerPlugin };
// V2 calls setup(), V1 (1.18.29+) calls server(). Both share the scoring engine.
// A plain object: V2's Plugin.define only returns its argument, and importing it
// at runtime would load the whole V2 package for V1 users too.
export default {
  id,
  setup: setupV2,
  server: TokenOptimizerPlugin,
} satisfies ReturnType<typeof V2Plugin.define> & { server: typeof TokenOptimizerPlugin };
