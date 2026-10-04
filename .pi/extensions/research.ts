import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import research, { describe } from "../../team/research.ts";

export default function (pi: ExtensionAPI) {
  const controller = research(pi);
  pi.registerTool({
    name: "research",
    label: "Research",
    description: "Start research using the YAML lead and deterministic controller, as /research does. This does not approve experiments.",
    promptSnippet: "Start a research workflow with the YAML lead",
    promptGuidelines: ["When the user asks you to research a topic, invoke research with that direction instead of explaining CLI commands or doing the team's research yourself. Ask if the direction is unclear. Never approve experiments through shell/file tools; the user must confirm /research approve."],
    parameters: Type.Object({ topic: Type.String({ minLength: 1, description: "The user's research direction" }) }),
    async execute(_id, params, signal, _update, ctx) {
      if (signal?.aborted) throw new Error("Research request cancelled");
      const directory = controller.start(params.topic, ctx);
      return { content: [{ type: "text", text: `Research lead started. Use /research status. Reports/artifacts: ${directory}` }],
               details: { directory } };
    },
  });
  pi.registerTool({
    name: "research_status",
    label: "Research status",
    description: "Read the current research run, including its scope, pending approval, gaps and artifact directory. Does not approve or advance it.",
    parameters: Type.Object({}),
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    async execute(_id, _params, _signal, _update, ctx) {
      const { directory, state } = controller.inspect(ctx);
      return { content: [{ type: "text", text: describe(state, directory) }], details: { directory, phase: state.phase } };
    },
  });
}
