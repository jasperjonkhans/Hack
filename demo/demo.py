#!/usr/bin/env python3
"""Inspect, build, validate, and launch a native Omnigent development team."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from roles import OPTIONS, make_team

ROOT = Path(__file__).resolve().parent
HARNESSES = ("claude-sdk", "codex", "pi", "openai-agents")


def options() -> None:
    print("OMNIGENT / development starter\n")
    for name, description in OPTIONS.items():
        print(f"{name:12} {description}")
    print("\nHarnesses: " + ", ".join(HARNESSES))
    print("\nTry: python demo.py show team")
    print("     python demo.py build team --workspace ./workspace")
    print("     python demo.py run ./generated/team --prompt-file TASK.md")
    print("\nInspect/build need no dependencies or credentials. Run uses real Omnigent.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("options", help="Compare the available setups")
    for name in ("show", "build"):
        command = commands.add_parser(name, help=f"{name.title()} a native agent bundle")
        command.add_argument("preset", choices=OPTIONS)
        command.add_argument("--workspace", type=Path, default=ROOT / "workspace")
        command.add_argument("--harness", choices=HARNESSES, default="claude-sdk")
        command.add_argument("--model", help="Harness-specific model or endpoint ID")
        command.add_argument("--profile", help="Existing Databricks CLI profile")
        command.add_argument("--reviewer-harness", choices=HARNESSES)
        command.add_argument("--reviewer-model")
        command.add_argument("--sandbox", choices=("auto", "none"), default="auto")
        if name == "build":
            command.add_argument("--output", type=Path, help="New bundle directory; never overwritten")
    validate = commands.add_parser("validate", help="Use Omnigent's real parser/validator, offline")
    validate.add_argument("bundle", type=Path)
    run = commands.add_parser("run", help="Launch a built bundle via the real Omnigent CLI")
    run.add_argument("bundle", type=Path)
    prompts = run.add_mutually_exclusive_group()
    prompts.add_argument("--prompt")
    prompts.add_argument("--prompt-file", type=Path)
    run.add_argument("--dry-run", action="store_true", help="Print command; never launch an agent")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "options"):
            options()
        elif args.command in ("show", "build"):
            team = make_team(args.preset, harness=args.harness, model=args.model,
                             profile=args.profile, reviewer_harness=args.reviewer_harness,
                             reviewer_model=args.reviewer_model)
            if args.sandbox == "none":
                print("WARNING: no OS isolation; read-only role instructions are not enforcement.",
                      file=sys.stderr)
            if args.command == "show":
                for path, content in team.documents(args.workspace, args.sandbox).items():
                    print(f"\n--- {path} ---\n{content}")
            else:
                bundle = team.export(args.output or ROOT / "generated" / args.preset,
                                     args.workspace, args.sandbox)
                print(f"Built {bundle}")
                print("Run: " + shlex.join([sys.executable, str(ROOT / "demo.py"),
                                            "run", str(bundle)]))
        elif args.command == "validate":
            from omnigent.spec.parser import parse
            from omnigent.spec.validator import validate as validate_spec
            result = validate_spec(parse(args.bundle.resolve()))
            if result.errors:
                for error in result.errors:
                    print(error, file=sys.stderr)
                return 1
            print(f"Omnigent accepted {args.bundle.resolve()}")
        elif args.command == "run":
            bundle = args.bundle.resolve()
            if not (bundle / "config.yaml").is_file():
                raise ValueError("Bundle not found. Build one first with: python demo.py build team")
            prompt = (args.prompt_file.read_text(encoding="utf-8")
                      if args.prompt_file else args.prompt)
            project_executable = ROOT.parent / ".venv" / ("Scripts/omnigent.exe" if sys.platform == "win32" else "bin/omnigent")
            executable = (str(project_executable) if project_executable.is_file()
                          else shutil.which("omnigent"))
            command = [executable or "omnigent", "run", str(bundle)]
            if prompt is not None:
                if not prompt.strip():
                    raise ValueError("Prompt must not be empty")
                command += ["-p", prompt]
            print(shlex.join(command), flush=True)
            if not args.dry_run:
                if not executable:
                    raise ValueError("Omnigent is not installed. Run uv sync at the project root, then uv run python demo/demo.py run ...")
                config = json.loads((bundle / "config.yaml").read_text(encoding="utf-8"))
                return subprocess.call(command, cwd=config["os_env"]["cwd"])
    except (ValueError, OSError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
