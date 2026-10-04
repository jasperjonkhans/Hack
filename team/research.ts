import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

type State = {
  phase: string; scope: string; summary: string; todos: string[]; gaps: string[];
  research_round: number; experiment_round: number; error?: string; plan_id?: string;
  proposal?: { question: string; hypothesis: string; plan: string[] };
};
type Job = { child: ChildProcess; done: Promise<void>; poll: ReturnType<typeof setInterval>;
             kill?: ReturnType<typeof setTimeout>; cancelled: boolean };
const ENTRY = "research-run";

export function projectRoot(cwd: string): string {
  for (let root = resolve(cwd); ; root = dirname(root)) {
    if (existsSync(join(root, "team/config.yaml")) && existsSync(join(root, "team/loop.py"))) return root;
    if (dirname(root) === root) throw new Error("Run /research from this research project's directory");
  }
}

export function serverUrl(env = process.env): string {
  const config = env.OMNIGENT_PI_NATIVE_CONFIG;
  if (!config) return "http://localhost:6767";
  const url = JSON.parse(readFileSync(config, "utf8")).serverUrl;
  if (typeof url !== "string") throw new Error("Omnigent's Pi bridge has no server URL");
  return url; // Never copy or display bridge credentials.
}

export function describe(state: State, directory: string): string {
  const lines = [`Research: ${state.phase}`, `Scope: ${state.scope}`,
    `Rounds: research ${state.research_round}/3, experiments ${state.experiment_round}/3`];
  if (state.summary) lines.push(state.summary);
  if (state.error) lines.push(`Blocked: ${state.error}`);
  for (const todo of state.todos) lines.push(`Todo: ${todo}`);
  for (const gap of state.gaps) lines.push(`Gap: ${gap}`);
  if (state.proposal) {
    lines.push(`Question: ${state.proposal.question}`, `Hypothesis: ${state.proposal.hypothesis}`);
    for (const step of state.proposal.plan) lines.push(`Plan: ${step}`);
  }
  if (state.phase === "awaiting_approval") lines.push("Use /research approve to approve this plan and start experiments.");
  lines.push(`Reports/artifacts: ${directory}`);
  return lines.join("\n");
}

export default function research(pi: ExtensionAPI, services = { spawn, pollMs: 1000 }) {
  let active: Job | undefined;
  let generation = 0;
  const readState = (directory: string): State => JSON.parse(readFileSync(join(directory, "state.json"), "utf8"));
  const notify = (ctx: ExtensionContext, text: string, level: "info" | "warning" | "error" = "info") => {
    if (ctx.hasUI) ctx.ui.notify(text, level);
    else void pi.sendMessage({ customType: "research", content: text, display: true,
                               details: { level } }, { triggerTurn: false }).catch(() => {});
  };
  const publish = (state: State, directory: string) => pi.sendMessage({
    customType: "research", content: describe(state, directory), display: true,
    details: { directory, phase: state.phase },
  }, { triggerTurn: false });

  function currentRun(root: string, ctx: ExtensionContext): string {
    for (const entry of [...ctx.sessionManager.getBranch()].reverse()) {
      if (entry.type !== "custom" || entry.customType !== ENTRY) continue;
      const directory = (entry.data as { directory?: string })?.directory;
      if (!directory) continue;
      const path = resolve(directory);
      const local = relative(join(root, "team/.runs"), path);
      if (!local || local.startsWith("..") || isAbsolute(local)) throw new Error("Research run belongs to another project");
      return path;
    }
    throw new Error("Start a run with /research <topic> first");
  }

  function argumentsFor(root: string, directory: string, command: string[]): string[] {
    return [join(root, "team/loop.py"), "--run", directory, "--server", serverUrl(), "--json", ...command];
  }

  async function status(root: string, directory: string): Promise<State> {
    const reply = await pi.exec(join(root, ".venv/bin/python"), argumentsFor(root, directory, ["status"]));
    if (reply.code !== 0) throw new Error(reply.stderr || "Cannot read research state");
    return JSON.parse(reply.stdout);
  }

  function launch(root: string, directory: string, command: string[], ctx: ExtensionContext) {
    if (active) throw new Error("Research is already running");
    if (!existsSync(join(root, ".venv/bin/python"))) throw new Error("Set up this project's .venv first");
    const started = generation;
    const child = services.spawn(join(root, ".venv/bin/python"), argumentsFor(root, directory, command), {
      cwd: root, stdio: ["ignore", "ignore", "pipe"], env: process.env,
    });
    let stderr = "";
    child.stderr?.on("data", data => { stderr = (stderr + data.toString()).slice(-4000); });
    const poll = setInterval(() => {
      if (!ctx.hasUI || generation !== started) return;
      try {
        const state = readState(directory);
        ctx.ui.setStatus("research", `Research: ${state.phase}, ${state.research_round}/3 + ${state.experiment_round}/3 rounds`);
      } catch { /* The first lead checkpoint may not exist yet. */ }
    }, services.pollMs);
    let finish!: () => void;
    const done = new Promise<void>(resolve => { finish = resolve; });
    const job: Job = { child, done, poll, cancelled: false };
    active = job;
    let settled = false;
    async function complete(error?: string) {
      if (settled) return;
      settled = true;
      clearInterval(poll);
      clearTimeout(job.kill);
      if (active === job) active = undefined;
      try {
        if (generation !== started) return;
        if (ctx.hasUI) ctx.ui.setStatus("research", undefined);
        if (error && !job.cancelled) notify(ctx, error, "error");
        if (job.cancelled) notify(ctx, "Research cancelled. Inspect the checkpoint before starting another run.", "warning");
        if (existsSync(join(directory, "state.json"))) await publish(readState(directory), directory);
      } finally { finish(); }
    }
    child.once("error", error => { void complete(error.message).catch(() => {}); });
    child.once("close", code => { void complete(code === 0 ? undefined : stderr || `Controller exited ${code}`).catch(() => {}); });
    if (ctx.hasUI) ctx.ui.setStatus("research", "Research: lead starting");
    notify(ctx, "Research lead started. Use /research status or /research cancel.");
  }

  function start(topic: string, ctx: ExtensionContext): string {
    if (!topic.trim()) throw new Error("Research direction cannot be empty");
    const root = projectRoot(ctx.cwd);
    const directory = join(root, "team/.runs", randomUUID());
    if (active) throw new Error("Research is already running");
    launch(root, directory, ["start", topic], ctx);
    pi.appendEntry(ENTRY, { directory });
    return directory;
  }

  function inspect(ctx: ExtensionContext) {
    const directory = currentRun(projectRoot(ctx.cwd), ctx);
    return { directory, state: readState(directory) };
  }

  function cancel(job: Job) {
    if (job.cancelled) return;
    job.cancelled = true;
    clearInterval(job.poll);
    job.child.kill("SIGINT"); // asyncio cancels active Omnigent invocations before exiting.
    job.kill = setTimeout(() => job.child.kill("SIGKILL"), 5000);
    job.kill.unref();
  }

  pi.on("session_shutdown", async () => {
    generation++;
    if (active) {
      const job = active;
      cancel(job);
      await job.done;
    }
  });

  pi.registerCommand("research", {
    description: "Research a topic with the YAML lead; status, approve, resume, cancel",
    getArgumentCompletions: prefix => ["status", "approve", "resume", "cancel"]
      .filter(value => value.startsWith(prefix)).map(value => ({ value, label: value })),
    handler: async (args, ctx) => {
      try {
        const root = projectRoot(ctx.cwd);
        const commandGeneration = generation;
        let command = args.trim();
        if (command === "cancel") {
          if (active) cancel(active);
          else notify(ctx, "No research controller is running.");
          return;
        }
        if (command === "status") {
          const { directory, state } = inspect(ctx);
          await publish(state, directory);
          return;
        }
        if (active) throw new Error("Research is already running; use /research status or /research cancel");
        if (!ctx.isIdle()) throw new Error("Wait for the current Pi turn to finish before starting research");
        if (command === "approve") {
          if (!ctx.hasUI) throw new Error("Approval requires an interactive confirmation");
          const directory = currentRun(root, ctx);
          const state = await status(root, directory);
          if (generation !== commandGeneration) return;
          if (state.phase !== "awaiting_approval" || !state.plan_id) throw new Error("No research plan is awaiting approval");
          if (!await ctx.ui.confirm("Approve experiments?", describe(state, directory))) return;
          if (generation !== commandGeneration) return;
          const approval = await pi.exec(join(root, ".venv/bin/python"), argumentsFor(root, directory, ["approve", state.plan_id]));
          if (approval.code !== 0) throw new Error(approval.stderr || "Plan changed; inspect /research status");
          if (generation !== commandGeneration) return;
          launch(root, directory, ["resume"], ctx);
        } else if (command === "resume") {
          launch(root, currentRun(root, ctx), ["resume"], ctx);
        } else {
          if (!command && ctx.hasUI) command = (await ctx.ui.input("Research direction", "What should the lead investigate?"))?.trim() || "";
          if (!command || generation !== commandGeneration) return;
          start(command, ctx);
        }
      } catch (error) {
        notify(ctx, error instanceof Error ? error.message : String(error), "error");
      }
    },
  });
  return { start, inspect };
}
