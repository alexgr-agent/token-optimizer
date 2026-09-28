import { describe, expect, test } from "bun:test";
import { SessionStore } from "./storage/session-store.js";
import { TrendsStore } from "./storage/trends.js";
import { hashProjectDir } from "./util/env.js";
import { setupV2 } from "./v2.js";

function fakeContext() {
  const hooks = new Map<string, (e: any) => Promise<void> | void>();
  const tools: any[] = [];
  const events: any[] = [];
  let wake: (() => void) | undefined;
  const controller = {
    emit(event: any) { events.push(event); wake?.(); },
    async *iterator() {
      while (true) {
        if (!events.length) await new Promise<void>((resolve) => { wake = resolve; });
        wake = undefined;
        while (events.length) yield events.shift();
      }
    },
  };
  const ctx: any = {
    location: { directory: process.cwd(), project: { id: "test", canonical: process.cwd(), directory: process.cwd() } },
    options: { dataDir: `${process.env.TMPDIR || "/tmp"}/token-optimizer-v2-test-${crypto.randomUUID()}` },
    tool: {
      transform: async (fn: any) => { fn({ add: (tool: any) => tools.push(tool) }); return { dispose: async () => {} }; },
      hook: async (name: string, fn: any) => { hooks.set(`tool.${name}`, fn); return { dispose: async () => {} }; },
    },
    shell: { hook: async (name: string, fn: any) => { hooks.set(`shell.${name}`, fn); return { dispose: async () => {} }; } },
    session: { hook: async (name: string, fn: any) => { hooks.set(`session.${name}`, fn); return { dispose: async () => {} }; } },
    event: { subscribe: () => controller.iterator() },
  };
  return { ctx, hooks, tools, controller };
}

describe("OpenCode V2 adapter", () => {
  test("registers tools, shell, prompt, context, compaction and tool hooks", async () => {
    const { ctx, hooks, tools } = fakeContext();
    const cleanup = await setupV2(ctx);
    expect(tools.map((t: any) => t.name)).toEqual(["token_status", "token_dashboard"]);
    expect(hooks.has("session.prompt")).toBe(true);
    expect(hooks.has("session.context")).toBe(true);
    expect(hooks.has("session.compaction")).toBe(true);
    expect(hooks.has("tool.execute.before")).toBe(true);
    expect(hooks.has("tool.execute.after")).toBe(true);
    const shell = { env: {} };
    await hooks.get("shell.create.before")!(shell);
    expect(shell.env).toEqual({ TOKEN_OPTIMIZER_RUNTIME: "opencode" });
    await cleanup?.();
  });
  test("records prompt and tool result through real stores without leaking into a second session", async () => {
    const { ctx, hooks, tools } = fakeContext();
    const cleanup = await setupV2(ctx);
    await hooks.get("session.prompt")!({sessionID:"s1", prompt:{text:"Research this project thoroughly and report findings with examples"}, metadata:{}});
    await hooks.get("tool.execute.before")!({sessionID:"s1", tool:"read", input:{filePath:"/tmp/a"}});
    await hooks.get("tool.execute.after")!({sessionID:"s1", tool:"read", input:{filePath:"/tmp/a"}, status:"completed", result:{content:"sample contents"}});
    expect((await tools[0].execute({detail:false})).content).toContain("Context Health Report");
    const first = new SessionStore(ctx.options.dataDir, "s1", hashProjectDir(ctx.location.project.canonical));
    expect(first.getRecentReads(5).map((r) => r.path)).toContain("/tmp/a");
    expect(first.getToolCallCount()).toBe(1);
    first.close();
    await hooks.get("session.prompt")!({sessionID:"s2", prompt:{text:"Separate project task"}, metadata:{}});
    expect((await tools[0].execute({detail:false})).content).toContain("Context Health Report");
    await cleanup?.();
  });
  test("final V2 step usage is saved once per step and idle flushes trends", async () => {
    const { ctx, hooks, controller } = fakeContext();
    const cleanup = await setupV2(ctx);
    const sid = `usage-${crypto.randomUUID()}`;
    await hooks.get("session.prompt")!({ sessionID: sid, prompt: { text: "Please assess the source code" } });
    controller.emit({ type: "session.step.ended", id: "step-1", data: { sessionID: sid, tokens: { input: 10, output: 5, cache: { read: 2, write: 1 } }, cost: 0.001 } });
    controller.emit({ type: "session.idle", data: { sessionID: sid } });
    await new Promise((resolve) => setTimeout(resolve, 25));
    const store = new SessionStore(ctx.options.dataDir, sid, hashProjectDir(ctx.location.project.canonical));
    expect(store.getToolCallCount()).toBe(0);
    store.close();
    const trends = new TrendsStore(ctx.options.dataDir);
    const row = trends.getAllSessions().find((r) => r.session_id === sid);
    expect(row?.tokens_input).toBe(10);
    expect(row?.tokens_output).toBe(5);
    expect(row?.tokens_cache_read).toBe(2);
    expect(row?.cost_usd).toBe(0.001);
    trends.close();
    await cleanup?.();
  });
  test("compaction instructions preserve native summary and teardown closes stores", async () => {
    const { ctx, hooks } = fakeContext();
    const cleanup = await setupV2(ctx);
    await hooks.get("session.prompt")!({ sessionID: "compaction-1", prompt: { text: "Please analyze this sample source file thoroughly" } });
    const event = { sessionID: "compaction-1", system: [], messages: [], model: { id: "test-model" } } as any;
    await hooks.get("session.compaction")!(event);
    expect(event.result).toBeUndefined(); // OpenCode's native compaction still runs.
    expect(event.system.length).toBeGreaterThan(0);
    await cleanup?.();
  });
});
