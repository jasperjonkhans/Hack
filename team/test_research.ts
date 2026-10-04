import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setImmediate as tick } from "node:timers/promises";
import { afterEach, test } from "node:test";
import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import research, { describe, projectRoot, serverUrl } from "./research.ts";

const cleanups: (() => void)[] = [];
afterEach(() => { for (const cleanup of cleanups.splice(0)) cleanup(); });

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "research-command-"));
  cleanups.push(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "team"));
  mkdirSync(join(root, ".venv/bin"), { recursive: true });
  writeFileSync(join(root, "team/config.yaml"), "");
  writeFileSync(join(root, "team/loop.py"), "");
  writeFileSync(join(root, ".venv/bin/python"), "");
  const entries: any[] = [], messages: any[] = [], notifications: string[] = [], spawned: any[] = [], executed: string[][] = [];
  const events = new Map<string, (...args: any[]) => any>();
  let command: any, approve = false, idle = true, ui = true;
  const ctx = {
    cwd: root,
    get hasUI() { return ui; },
    isIdle: () => idle,
    sessionManager: { getBranch: () => entries },
    ui: {
      notify: (text: string) => notifications.push(text),
      setStatus: () => {},
      confirm: async () => approve,
      input: async () => "A prompted topic",
    },
  } as unknown as ExtensionCommandContext;
  const pi = {
    on: (name: string, callback: any) => events.set(name, callback),
    registerCommand: (name: string, definition: any) => { assert.equal(name, "research"); command = definition; },
    appendEntry: (customType: string, data: any) => entries.push({ type: "custom", customType, data }),
    sendMessage: async (message: any, options: any) => { assert.equal(options.triggerTurn, false); messages.push(message); },
    exec: async (_python: string, args: string[]) => {
      executed.push(args);
      const directory = args[args.indexOf("--run") + 1];
      const state = JSON.parse(readFileSync(join(directory, "state.json"), "utf8"));
      if (args.includes("approve")) {
        assert.equal(args.at(-1), "current-plan-id");
        state.phase = "experiments";
        writeFileSync(join(directory, "state.json"), JSON.stringify(state));
      }
      return { code: 0, stdout: JSON.stringify({ ...state, plan_id: "current-plan-id" }), stderr: "" };
    },
  } as unknown as ExtensionAPI;
  const spawn = (_python: string, args: string[]) => {
    const child = new EventEmitter() as any;
    child.stderr = new EventEmitter();
    child.kill = (signal: string) => { child.signal = signal; queueMicrotask(() => child.emit("close", null)); return true; };
    spawned.push({ child, args });
    return child;
  };
  const controller = research(pi, { spawn: spawn as any, pollMs: 1000 });
  return {
    root, entries, messages, notifications, spawned, executed, events, controller, ctx,
    invoke: (text: string) => command.handler(text, ctx),
    allow: () => { approve = true; },
    busy: () => { idle = false; },
    noUI: () => { ui = false; },
    seed: (phase = "awaiting_approval") => {
      const directory = join(root, "team/.runs/test");
      mkdirSync(directory, { recursive: true });
      const state = { phase, scope: "Scope", summary: "Evidence reviewed", todos: [], gaps: [],
        research_round: 1, experiment_round: 0,
        proposal: { question: "Does X help?", hypothesis: "X helps", plan: ["Compare with baseline"] } };
      writeFileSync(join(directory, "state.json"), JSON.stringify(state));
      entries.push({ type: "custom", customType: "research-run", data: { directory } });
      return directory;
    },
    finish: async () => { spawned.at(-1).child.emit("close", 0); await tick(); },
  };
}

test("a topic starts the Python controller, without shell interpolation", async () => {
  const f = fixture();
  const topic = 'Compare X; $(touch unsafe) "quoted"';
  await f.invoke(topic);
  assert.equal(f.spawned.length, 1);
  assert.deepEqual(f.spawned[0].args.slice(-2), ["start", topic]);
  assert.equal(f.spawned[0].args[0], join(f.root, "team/loop.py"));
  assert.ok(f.entries[0].data.directory.startsWith(join(f.root, "team/.runs/")));
  await f.finish();
});

test("the model starter runs during a Pi turn without treating a topic as approval", async () => {
  const f = fixture();
  f.seed(); f.busy();
  const directory = f.controller.start("approve", f.ctx);
  assert.deepEqual(f.spawned[0].args.slice(-2), ["start", "approve"]);
  assert.equal(f.executed.length, 0);
  assert.equal(f.entries.at(-1).data.directory, directory);
  await f.finish();
});

test("an empty command asks for a direction and starts the lead", async () => {
  const f = fixture();
  await f.invoke("");
  assert.equal(f.spawned[0].args.at(-1), "A prompted topic");
  await f.finish();
});

test("status restores the current branch's run without spawning or approving", async () => {
  const f = fixture();
  f.seed();
  await f.invoke("status");
  assert.equal(f.spawned.length, 0);
  assert.equal(f.executed.length, 0);
  assert.match(f.messages[0].content, /Question: Does X help\?/);
  assert.match(f.messages[0].content, /\/research approve/);
  const snapshot = f.controller.inspect(f.ctx);
  assert.equal(describe(snapshot.state, snapshot.directory), f.messages[0].content);
});

test("approval is not granted when the user declines", async () => {
  const f = fixture();
  f.seed();
  await f.invoke("approve");
  assert.equal(f.executed.length, 1);
  assert.equal(f.executed[0].at(-1), "status");
  assert.equal(f.spawned.length, 0);
});

test("explicit approval passes the exact plan ID and resumes experiments", async () => {
  const f = fixture();
  f.seed(); f.allow();
  await f.invoke("approve");
  assert.deepEqual(f.executed[1].slice(-2), ["approve", "current-plan-id"]);
  assert.equal(f.spawned[0].args.at(-1), "resume");
  await f.finish();
});

test("headless approval is refused", async () => {
  const f = fixture();
  f.seed(); f.allow(); f.noUI();
  await f.invoke("approve");
  assert.equal(f.executed.length, 0);
  assert.equal(f.spawned.length, 0);
  assert.match(f.messages[0].content, /interactive confirmation/);
});

test("resume reuses the run without implicitly approving it", async () => {
  const f = fixture();
  const directory = f.seed();
  await f.invoke("resume");
  const args = f.spawned[0].args;
  assert.equal(args[args.indexOf("--run") + 1], directory);
  assert.equal(args.at(-1), "resume");
  assert.equal(f.executed.length, 0);
  await f.finish();
});

test("a busy Pi turn and concurrent research runs are refused", async () => {
  const f = fixture();
  await f.invoke("First topic");
  await f.invoke("Second topic");
  assert.equal(f.spawned.length, 1);
  assert.match(f.notifications.at(-1)!, /already running/);
  await f.finish();
  f.busy();
  await f.invoke("Third topic");
  assert.equal(f.spawned.length, 1);
  assert.match(f.notifications.at(-1)!, /current Pi turn/);
});

test("cancellation interrupts the controller and shutdown suppresses stale results", async () => {
  const f = fixture();
  await f.invoke("A topic");
  await f.invoke("cancel");
  await tick();
  assert.equal(f.spawned[0].child.signal, "SIGINT");
  await f.invoke("Another topic");
  await f.events.get("session_shutdown")!();
  assert.equal(f.spawned[1].child.signal, "SIGINT");
  assert.equal(f.messages.length, 0);
});

test("the command reports blocked roles and checkpoint paths on completion", async () => {
  const f = fixture();
  await f.invoke("A topic");
  const directory = f.entries[0].data.directory;
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, "state.json"), JSON.stringify({ phase: "research", scope: "Scope", summary: "", gaps: [],
    todos: ["Check cited evidence"], research_round: 0, experiment_round: 0, error: "External scout/verifier configs are missing" }));
  f.spawned[0].child.stderr.emit("data", "External scout/verifier configs are missing");
  f.spawned[0].child.emit("close", 1);
  await tick();
  assert.match(f.messages[0].content, /Blocked: External scout\/verifier/);
  assert.match(f.messages[0].content, /Todo: Check cited evidence/);
  assert.ok(f.messages[0].content.includes(directory));
});

test("project discovery and bridge URL preserve the Omnigent launch context", () => {
  const f = fixture();
  assert.equal(projectRoot(join(f.root, "team")), f.root);
  const path = join(f.root, "bridge.json");
  writeFileSync(path, JSON.stringify({ serverUrl: "http://127.0.0.1:7000", headers: { secret: "not-used" } }));
  assert.equal(serverUrl({ OMNIGENT_PI_NATIVE_CONFIG: path }), "http://127.0.0.1:7000");
  assert.equal(serverUrl({}), "http://localhost:6767");
});

test("run metadata cannot access another project's checkpoint", async () => {
  const f = fixture();
  f.entries.push({ type: "custom", customType: "research-run", data: { directory: join(tmpdir(), "another-project") } });
  await f.invoke("status");
  assert.equal(f.messages.length, 0);
  assert.match(f.notifications.at(-1)!, /another project/);
});
