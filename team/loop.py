"""Small, checkpointed research state machine; agents supply decisions, not control flow."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
from pathlib import Path
from uuid import uuid4

MAX_ROUNDS = 3
MAX_SCOUTS = 5
TEAM = Path(__file__).resolve().parent
RESULT = {
    "status": "completed | blocked | failed",
    "summary": "string",
    "output_refs": ["database or artifact reference"],
    "limitations": ["string"],
}
PROPOSAL = {"question": "string", "hypothesis": "string", "plan": ["experiment objective"]}
REVIEW = {"complete": "boolean", "summary": "string", "gaps": ["string"],
          "todos": ["next objective"]}


def strings(value, *, nonempty=False, limit=None):
    if not isinstance(value, list) or any(not isinstance(s, str) or not s.strip() for s in value):
        raise ValueError("Expected a list of nonempty strings")
    if nonempty and not value:
        raise ValueError("At least one todo is required")
    if limit is not None and len(value) > limit:
        raise ValueError(f"At most {limit} scout todos are allowed")
    return value


def proposal(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a research proposal")
    for key in ("question", "hypothesis"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Proposal needs {key}")
    return {"question": value["question"], "hypothesis": value["hypothesis"],
            "plan": strings(value.get("plan"), nonempty=True)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class Loop:
    def __init__(self, directory, invoke, preflight=None):
        self.directory = Path(directory).resolve()
        self.invoke = invoke
        self.preflight = preflight or (lambda phase: None)
        self.path = self.directory / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self):
        write_json(self.path, self.state)

    def start(self, prompt):
        if self.state is not None:
            raise ValueError("Run already exists; resume it or use a new directory")
        if not prompt.strip():
            raise ValueError("Research direction cannot be empty")
        self.state = {"run_id": uuid4().hex, "direction": prompt, "scope": prompt,
                      "phase": "research", "research_round": 0, "experiment_round": 0,
                      "todos": [], "reports": [], "summary": "", "gaps": [],
                      "proposal": None, "approved_plan": None}
        self.save()

    def plan_id(self):
        content = {"scope": self.state["scope"], "proposal": self.state["proposal"]}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def approve(self, plan_id):
        if self.state is None:
            raise ValueError("Start a run first")
        if self.state["phase"] != "awaiting_approval" or plan_id != self.plan_id():
            raise ValueError("Approval must match the currently pending plan ID")
        self.state.update(phase="experiments", approved_plan=plan_id)
        self.save()

    async def ask(self, operation, contract, **context):
        reply = await self.invoke("lead", self.request(operation, contract,
                                                       prior_reports=self.state["reports"], **context), self.directory)
        if not isinstance(reply, dict):
            raise ValueError("Lead must return a JSON object")
        return reply

    def request(self, operation, contract, **context):
        return {"run_id": self.state["run_id"], "memory_namespace": self.state["run_id"],
                "scope": self.state["scope"], "proposal": self.state["proposal"],
                "operation": operation, "contract": contract, **context}

    async def task(self, role, task_id, objective, *, depth=0, **context):
        artifact_dir = self.directory / "artifacts" / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        request = self.request("run_task", RESULT, task_id=task_id, objective=objective,
                               depth=depth, artifact_dir=str(artifact_dir), **context)
        try:
            result = await self.invoke(role, request, artifact_dir)
            if isinstance(result, dict) and "subtasks" in result:
                if role != "worker" or depth != 0:
                    raise ValueError("Only an experiment worker may request one child level")
                objectives = strings(result["subtasks"], nonempty=True)
                write_json(self.directory / "reports" / f"{task_id}-request.json", result)
                children = await asyncio.gather(*(
                    self.task("worker", f"{task_id}-child-{i}", todo, depth=1)
                    for i, todo in enumerate(objectives, 1)
                ))
                result = await self.invoke(role, {**request, "operation": "finish_task",
                                                  "child_reports": children}, artifact_dir)
            if not isinstance(result, dict) or result.get("status") not in ("completed", "blocked", "failed"):
                raise ValueError("Invalid task result status (or repeated delegation request)")
            if not isinstance(result.get("summary"), str):
                raise ValueError("Task result needs a summary")
            strings(result.get("output_refs"))
            strings(result.get("limitations"))
        except Exception as error:
            result = {"status": "failed", "summary": str(error), "output_refs": [],
                      "limitations": ["Task did not produce a valid completed result"]}
        report = {**result, "task_id": task_id, "role": role, "objective": objective}
        report_path = self.directory / "reports" / f"{task_id}.json"
        write_json(report_path, report)
        report["report_ref"] = str(report_path)
        self.state["reports"].append(report)
        return report

    def update_plan(self, decision):
        if "scope" in decision:
            if not isinstance(decision["scope"], str) or not decision["scope"].strip():
                raise ValueError("Scope must be a nonempty string")
            self.state["scope"] = decision["scope"]
        if "proposal" in decision:
            self.state["proposal"] = proposal(decision["proposal"])

    def review(self, decision):
        if type(decision.get("complete")) is not bool or not isinstance(decision.get("summary"), str):
            raise ValueError("Review needs boolean complete and string summary")
        self.state.update(summary=decision["summary"], gaps=strings(decision.get("gaps")))
        self.update_plan(decision)
        return decision["complete"]

    async def research(self):
        if not self.state["todos"]:
            plan = await self.ask("plan_research", {"scope": "string", "todos": ["scout objective"]},
                                  direction=self.state["direction"], max_scouts=MAX_SCOUTS)
            if not isinstance(plan.get("scope"), str) or not plan["scope"].strip():
                raise ValueError("Lead must define a scope")
            self.state.update(scope=plan["scope"], todos=strings(plan.get("todos"), nonempty=True,
                                                               limit=MAX_SCOUTS))
        todos = strings(self.state["todos"], nonempty=True, limit=MAX_SCOUTS)
        self.save()  # Expose the lead's scope/todos even when external roles are absent.
        self.preflight("research")
        self.state["research_round"] += 1
        number = self.state["research_round"]
        self.state["phase"] = "running"
        self.save()  # Consume the round before dispatch; never replay an interrupted batch.
        scouts = await asyncio.gather(*(
            self.task("scout", f"r{number}-scout-{i}", todo,
                      instructions="Persist cited findings in shared memory without verification flags")
            for i, todo in enumerate(todos, 1)
        ))
        verifiers = await asyncio.gather(*(
            self.task("verifier", f"r{number}-verifier-{i}", f"Verify: {report['objective']}",
                      scout_report=report,
                      instructions="Read cited findings from memory; set verified/disputed flags with reasons; retain disputes")
            for i, report in enumerate(scouts, 1) if report["status"] == "completed"
        ))
        decision = await self.ask("review_research", {**REVIEW, "scope": "string", "proposal": PROPOSAL},
                                  scouts=scouts, verifiers=verifiers, todos=todos,
                                  round=number, max_rounds=MAX_ROUNDS)
        complete = self.review(decision)
        self.state["proposal"] = proposal(decision.get("proposal"))
        if complete or number == MAX_ROUNDS:
            self.state.update(phase="awaiting_approval", todos=[])
            if not complete:
                self.state["gaps"].append("Research round limit reached; coverage remains incomplete")
        else:
            self.state.update(phase="research", todos=strings(decision.get("todos"), nonempty=True,
                                                             limit=MAX_SCOUTS))
        self.save()

    async def experiments(self):
        self.preflight("experiments")
        if self.state["approved_plan"] != self.plan_id():
            raise ValueError("The current scope and proposal have not been approved")
        if not self.state["todos"]:
            plan = await self.ask("plan_experiments", {"todos": ["approved experiment objective"]},
                                  instructions="Stay within the approved plan; include scope/proposal only if a change is needed")
            self.update_plan(plan)
            if self.plan_id() != self.state["approved_plan"]:
                self.state.update(phase="awaiting_approval", todos=[])
                self.save()
                return
            self.state["todos"] = strings(plan.get("todos"), nonempty=True)
        todos = strings(self.state["todos"], nonempty=True)
        self.state["experiment_round"] += 1
        number = self.state["experiment_round"]
        self.state["phase"] = "running"
        self.save()
        results = await asyncio.gather(*(
            self.task("worker", f"e{number}-worker-{i}", todo)
            for i, todo in enumerate(todos, 1)
        ))
        decision = await self.ask("review_experiments", REVIEW,
                                  results=results, todos=todos, round=number, max_rounds=MAX_ROUNDS,
                                  instructions="Assess against the approved scope/question. Include scope/proposal if a change is needed")
        complete = self.review(decision)
        changed = self.plan_id() != self.state["approved_plan"]
        if number == MAX_ROUNDS or (complete and not changed):
            self.state.update(phase="complete", todos=[])
            if not complete or changed:
                self.state["gaps"].append("Experiment round limit reached; approved question remains unresolved")
        elif changed:
            self.state.update(phase="awaiting_approval", todos=[])
        else:
            self.state.update(phase="experiments", todos=strings(decision.get("todos"), nonempty=True))
        self.save()

    async def advance(self):
        if self.state is None:
            raise ValueError("Start a run first")
        if self.state["phase"] in ("running", "failed"):
            raise ValueError("Run interrupted or failed; inspect state/reports before starting a new run")
        self.state.pop("error", None)
        try:
            while self.state["phase"] in ("research", "experiments"):
                phase = self.state["phase"]
                counter = "research_round" if phase == "research" else "experiment_round"
                if self.state[counter] >= MAX_ROUNDS:
                    raise ValueError("Round limit already reached")
                await (self.research() if phase == "research" else self.experiments())
        except FileNotFoundError as error:
            # Missing external roles block before dispatch; keep the lead's plan resumable.
            self.state["error"] = str(error)
            self.save()
            raise
        except Exception as error:
            self.state.update(phase="failed", error=str(error))
            self.save()
            raise
        return self.state


def show(loop):
    state = loop.state
    if state is None:
        raise ValueError("Start a run first")
    print(f"Run: {state['run_id']}\nPhase: {state['phase']}")
    print(f"Rounds: research {state['research_round']}/{MAX_ROUNDS}, experiments {state['experiment_round']}/{MAX_ROUNDS}")
    print(f"Scope: {state['scope']}")
    print(state["summary"])
    if state.get("error"):
        print(f"Blocked: {state['error']}")
    for todo in state["todos"]:
        print(f"Todo: {todo}")
    for gap in state["gaps"]:
        print(f"Gap: {gap}")
    if state["proposal"]:
        print(f"Question: {state['proposal']['question']}\nHypothesis: {state['proposal']['hypothesis']}")
        for todo in state["proposal"]["plan"]:
            print(f"Plan: {todo}")
    if state["phase"] == "awaiting_approval":
        print(f"Approval ID: {loop.plan_id()}")
    print(f"Reports/artifacts: {loop.directory}")


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Checkpoint and artifact directory")
    parser.add_argument("--server", default="http://localhost:6767")
    parser.add_argument("--runner", help="Online Omnigent runner ID (otherwise discover one supporting Pi)")
    parser.add_argument("--timeout", type=float, default=900, help="Seconds per agent invocation")
    parser.add_argument("--json", action="store_true", help="Return machine-readable state for the Pi command")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start").add_argument("prompt")
    commands.add_parser("approve").add_argument("plan_id")
    commands.add_parser("resume")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    args.run.mkdir(parents=True, exist_ok=True)
    with (args.run / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another controller is using this run")
        loop = Loop(args.run, None)
        if args.command == "start":
            loop.start(args.prompt)
        if args.command == "approve":
            loop.approve(args.plan_id)  # Approval is an explicit local command, never an agent response.
        if args.command in ("start", "resume"):
            if loop.state is None:
                raise ValueError("Start a run first")
            if loop.state["phase"] in ("research", "experiments"):
                if __package__:
                    from .runtime import OmnigentRuntime
                else:
                    from runtime import OmnigentRuntime
                async with OmnigentRuntime(TEAM, args.server, args.runner, args.timeout) as runtime:
                    runtime.preflight("lead")
                    loop.preflight = runtime.preflight
                    loop.invoke = runtime.invoke
                    await loop.advance()
            else:
                await loop.advance()
        if args.json:
            if loop.state is None:
                raise ValueError("Start a run first")
            print(json.dumps({**loop.state, "run_directory": str(loop.directory),
                              "plan_id": loop.plan_id() if loop.state["proposal"] else None}))
        else:
            show(loop)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        raise SystemExit(str(error)) from error
