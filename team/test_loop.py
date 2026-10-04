"""Offline orchestration/adapter checks; no live agents or provider charges."""
import asyncio
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

from loop import Loop, MAX_ROUNDS, TEAM, main
from runtime import OmnigentRuntime, assistant_text, bundle

PROPOSAL = {"question": "Does X help?", "hypothesis": "X improves Y", "plan": ["Compare X with baseline"]}


def result(summary="Done"):
    return {"status": "completed", "summary": summary, "output_refs": ["evidence:1"], "limitations": []}


class Agents:
    def __init__(self):
        self.calls = []
        self.scout_count = 2
        self.research_complete = True
        self.experiments_complete = True
        self.override = None

    async def __call__(self, role, request, workspace):
        self.calls.append((role, copy.deepcopy(request), workspace))
        await asyncio.sleep(0)
        if self.override:
            reply = self.override(role, request)
            if reply is not None:
                return reply
        if role != "lead":
            return result()
        operation = request["operation"]
        if operation == "plan_research":
            return {"scope": "Study X", "todos": [f"Find evidence {i}" for i in range(self.scout_count)]}
        if operation == "plan_experiments":
            return {"todos": ["Compare X with baseline"]}
        return {"complete": self.research_complete if operation == "review_research" else self.experiments_complete,
                "summary": "Assessment", "gaps": [], "todos": ["Follow up"],
                **({"proposal": copy.deepcopy(PROPOSAL)} if operation == "review_research" else {})}

    def count(self, role):
        return sum(r == role for r, _, _ in self.calls)


class LoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agents = Agents()
        self.loop = Loop(self.temp.name, self.agents)
        self.loop.start("Study X")

    async def approve_research(self):
        await self.loop.advance()
        self.loop.approve(self.loop.plan_id())

    async def test_flow_pauses_persists_and_resumes_only_after_explicit_approval(self):
        await self.loop.advance()
        self.assertEqual(self.loop.state["phase"], "awaiting_approval")
        self.assertEqual(self.agents.count("worker"), 0)
        calls = len(self.agents.calls)
        self.loop = Loop(self.temp.name, self.agents)
        await self.loop.advance()
        self.assertEqual(len(self.agents.calls), calls)
        with self.assertRaises(ValueError):
            self.loop.approve("wrong-plan")
        self.loop.approve(self.loop.plan_id())
        self.assertEqual(self.agents.count("worker"), 0)
        await self.loop.advance()
        self.assertEqual(self.loop.state["phase"], "complete")
        self.assertEqual(self.loop.state["experiment_round"], 1)
        self.assertEqual(self.agents.count("worker"), 1)
        self.assertTrue(list((Path(self.temp.name) / "reports").glob("*.json")))
        self.assertEqual({p[1]["memory_namespace"] for p in self.agents.calls}, {self.loop.state["run_id"]})

    async def test_all_scouts_finish_before_verification_and_review(self):
        await self.loop.advance()
        roles = [role for role, _, _ in self.agents.calls]
        self.assertLess(max(i for i, role in enumerate(roles) if role == "scout"), roles.index("verifier"))
        review = next(req for role, req, _ in self.agents.calls if req["operation"] == "review_research")
        self.assertEqual(len(review["verifiers"]), 2)
        self.assertIn("without verification flags", self.agents.calls[1][1]["instructions"])

    async def test_research_cap_is_three_total_rounds(self):
        self.agents.research_complete = False
        await self.loop.advance()
        self.assertEqual(self.loop.state["research_round"], MAX_ROUNDS)
        self.assertEqual(self.agents.count("scout"), 4)  # 2 initial + 1 + 1.
        self.assertEqual(self.loop.state["phase"], "awaiting_approval")
        self.assertTrue(self.loop.state["gaps"])

    async def test_initial_scout_limit_checked_before_dispatch(self):
        self.agents.scout_count = 6
        with self.assertRaises(ValueError):
            await self.loop.advance()
        self.assertEqual(self.agents.count("scout"), 0)

    async def test_followup_scout_limit_checked_before_dispatch(self):
        self.agents.override = lambda role, req: (
            {"complete": False, "summary": "Gaps", "gaps": [], "proposal": PROPOSAL, "todos": ["More"] * 6}
            if req["operation"] == "review_research" else None)
        with self.assertRaises(ValueError):
            await self.loop.advance()
        self.assertEqual(self.agents.count("scout"), 2)
        self.assertEqual(self.loop.state["research_round"], 1)

    async def test_experiment_cap_is_three_total_rounds(self):
        await self.approve_research()
        self.agents.experiments_complete = False
        await self.loop.advance()
        self.assertEqual(self.agents.count("worker"), 3)
        self.assertEqual(self.loop.state["experiment_round"], 3)
        self.assertEqual(self.loop.state["phase"], "complete")
        self.assertTrue(self.loop.state["gaps"])

    async def test_helper_work_is_one_child_level_only(self):
        await self.approve_research()
        self.agents.override = lambda role, req: (
            {"subtasks": ["Independent helper"]} if role == "worker" and req["operation"] == "run_task" else None)
        await self.loop.advance()
        workers = [req for role, req, _ in self.agents.calls if role == "worker"]
        self.assertEqual([req["depth"] for req in workers], [0, 1, 0])
        self.assertEqual(workers[-1]["operation"], "finish_task")
        self.assertEqual(workers[-1]["child_reports"][0]["status"], "failed")
        self.assertTrue(any(report["role"] == "worker" and report["status"] == "failed"
                            for report in self.loop.state["reports"]))

    async def test_worker_cannot_request_a_second_batch_of_children(self):
        await self.approve_research()
        self.agents.override = lambda role, req: {"subtasks": ["Helper"]} if role == "worker" and req["depth"] == 0 else None
        await self.loop.advance()
        self.assertEqual(self.agents.count("worker"), 3)
        self.assertEqual(self.loop.state["reports"][-1]["status"], "failed")

    async def test_changed_plan_pauses_before_any_experiment(self):
        await self.approve_research()
        old_id = self.loop.plan_id()
        self.agents.override = lambda role, req: (
            {"todos": ["Changed test"], "proposal": {**PROPOSAL, "plan": ["Different test"]}}
            if req["operation"] == "plan_experiments" else None)
        await self.loop.advance()
        self.assertEqual(self.agents.count("worker"), 0)
        self.assertEqual(self.loop.state["phase"], "awaiting_approval")
        self.assertEqual(self.loop.state["experiment_round"], 0)
        with self.assertRaises(ValueError):
            self.loop.approve(old_id)

    async def test_scope_change_requires_reapproval_without_resetting_rounds(self):
        await self.approve_research()
        self.agents.override = lambda role, req: (
            {"complete": False, "summary": "New scope needed", "gaps": ["Unresolved"], "todos": [], "scope": "Study Z"}
            if req["operation"] == "review_experiments" else None)
        await self.loop.advance()
        self.assertEqual(self.loop.state["phase"], "awaiting_approval")
        self.assertEqual(self.loop.state["experiment_round"], 1)
        self.loop.approve(self.loop.plan_id())
        self.agents.override = None
        self.agents.experiments_complete = False
        await self.loop.advance()
        self.assertEqual(self.loop.state["experiment_round"], 3)
        self.assertEqual(self.agents.count("worker"), 3)

    async def test_interrupted_batch_is_not_replayed(self):
        self.loop.state.update(phase="running", research_round=1)
        self.loop.save()
        reloaded = Loop(self.temp.name, self.agents)
        with self.assertRaisesRegex(ValueError, "interrupted"):
            await reloaded.advance()
        self.assertEqual(self.agents.calls, [])

    async def test_truthy_string_is_not_a_completion_decision(self):
        self.agents.override = lambda role, req: (
            {"complete": "false", "summary": "Invalid", "gaps": [], "proposal": PROPOSAL}
            if req["operation"] == "review_research" else None)
        with self.assertRaises(ValueError):
            await self.loop.advance()
        self.assertEqual(self.loop.state["phase"], "failed")

    async def test_failed_scout_is_retained_and_not_verified(self):
        self.agents.override = lambda role, req: (
            {"status": "failed", "summary": "Search unavailable", "output_refs": [], "limitations": ["No evidence"]}
            if role == "scout" else None)
        await self.loop.advance()
        self.assertEqual(self.agents.count("verifier"), 0)
        self.assertTrue(all(r["status"] == "failed" for r in self.loop.state["reports"]))

    async def test_lead_plans_before_missing_external_roles_block_dispatch(self):
        def missing(phase):
            raise FileNotFoundError("External scout/verifier configs are missing")
        self.loop.preflight = missing
        with self.assertRaises(FileNotFoundError):
            await self.loop.advance()
        self.assertEqual(self.agents.count("lead"), 1)
        self.assertEqual(self.agents.count("scout"), 0)
        saved = Loop(self.temp.name, self.agents)
        self.assertEqual(saved.state["scope"], "Study X")
        self.assertEqual(len(saved.state["todos"]), 2)
        self.assertEqual(saved.state["research_round"], 0)
        await saved.advance()
        plans = [req for role, req, _ in self.agents.calls if req["operation"] == "plan_research"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(saved.state["phase"], "awaiting_approval")

    async def test_json_status_exposes_the_exact_approval_id_without_model_calls(self):
        await self.loop.advance()
        before = len(self.agents.calls)
        output = io.StringIO()
        with redirect_stdout(output):
            await main(["--run", self.temp.name, "--json", "status"])
        snapshot = json.loads(output.getvalue())
        self.assertEqual(snapshot["plan_id"], self.loop.plan_id())
        self.assertEqual(snapshot["run_directory"], self.temp.name)
        self.assertEqual(len(self.agents.calls), before)

    async def test_missing_roles_are_references_not_placeholder_implementations(self):
        runtime = OmnigentRuntime(TEAM, "http://localhost:6767")
        with self.assertRaisesRegex(FileNotFoundError, "scout.*verifier"):
            runtime.preflight("research")
        runtime.preflight("experiments")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self, events, *, timeout=1, delay=0):
        class Chat:
            session_id = "conv_test"
            cancelled = False

            async def send(self, request):
                await asyncio.sleep(delay)
                for event in events:
                    yield event

            async def cancel(self):
                self.cancelled = True

        chat = Chat()
        bound = []

        class Sessions:
            async def resolve_online_runner(self, **kwargs):
                return "runner_pi"

            async def bind_runner(self, session_id, **kwargs):
                bound.append((session_id, kwargs))

        class Client:
            sessions = Sessions()

            async def sessions_chat(self, data):
                return chat

        runtime = OmnigentRuntime(TEAM, "http://localhost:6767", timeout=timeout)
        runtime.client = Client()
        return runtime, chat, bound

    async def test_adapter_uses_last_canonical_message_and_binds_runner(self):
        from omnigent.server.schemas import CompletedEvent
        messages = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
                    for text in ("Intermediate commentary", json.dumps(result()))]
        event = CompletedEvent.model_construct(type="response.completed", response=SimpleNamespace(output=messages))
        runtime, chat, bound = self.make_runtime([event])
        reply = await runtime.invoke("lead", {}, TEAM)
        self.assertEqual(reply["status"], "completed")
        self.assertEqual(reply["session_id"], "conv_test")
        self.assertEqual(bound, [("conv_test", {"runner_id": "runner_pi"})])
        self.assertFalse(chat.cancelled)

    async def test_failed_turn_is_not_treated_as_a_result(self):
        from omnigent.server.schemas import FailedEvent
        runtime, chat, _ = self.make_runtime([FailedEvent.model_construct(type="response.failed")])
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            await runtime.invoke("lead", {}, TEAM)
        self.assertTrue(chat.cancelled)

    async def test_timeout_cancels_server_side_work(self):
        runtime, chat, _ = self.make_runtime([], timeout=0.01, delay=1)
        with self.assertRaises(TimeoutError):
            await runtime.invoke("lead", {}, TEAM)
        self.assertTrue(chat.cancelled)

    def test_bundles_have_no_subagent_grants(self):
        from omnigent.server.bundles import validate_agent_bundle
        for role in ("lead", "worker"):
            data = bundle(OmnigentRuntime(TEAM, "http://localhost:6767").config_path(role), TEAM)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                self.assertFalse(any(name.startswith("agents/") for name in archive.getnames()))
            spec = validate_agent_bundle(data, enforce_handler_allowlist=False)
            self.assertFalse(spec.spawn)
            self.assertEqual(spec.tools.agents, [])
            self.assertEqual(spec.executor.config["harness"], "pi")
            if role == "lead":
                self.assertIsNone(spec.os_env)
            else:
                self.assertNotEqual(spec.os_env.sandbox.type, "none")

    def test_only_local_servers_are_supported(self):
        with self.assertRaisesRegex(ValueError, "local"):
            OmnigentRuntime(TEAM, "https://remote.example")


if __name__ == "__main__":
    unittest.main()
