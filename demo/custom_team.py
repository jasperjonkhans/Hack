"""Example: your own two-role team, still using the native Omnigent runtime."""
from pathlib import Path

from framework import Role, Runtime, Team
from roles import RESEARCHER

ROOT = Path(__file__).resolve().parent

report_lead = Role(
    name="report_lead",
    description="Produce an evidence-based explanation of a codebase.",
    instructions="""You explain the workspace without modifying files.
Delegate code investigation to researcher using sys_session_send with a
specific title, args.purpose explore, and the question in args.input.
Collect its findings through sys_read_inbox. If it is still working, end
your turn and wait for completion; do not busy-poll.
Then answer the user with file references, uncertainties, and conclusions.
Do not edit files, install dependencies, access secrets, or push changes.
""",
)

if __name__ == "__main__":
    team = Team(report_lead, (RESEARCHER,)).on(Runtime("claude-sdk"))
    path = team.export(ROOT / "generated" / "custom-report", ROOT / "workspace")
    print(f"Built {path}\nRun: uv run omnigent run {path}")
