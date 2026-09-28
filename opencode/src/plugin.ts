import { Plugin as V2Plugin } from "@opencode/plugin";
import { TokenOptimizerPlugin } from "./index.js";
import { setupV2 } from "./v2.js";

const id = "token-optimizer-opencode";
// V2 calls setup(), V1 (1.18.29+) calls server(). Both share the scoring engine.
export default {
  ...V2Plugin.define({ id, setup: setupV2 }),
  server: TokenOptimizerPlugin,
} satisfies ReturnType<typeof V2Plugin.define> & { server: typeof TokenOptimizerPlugin };
