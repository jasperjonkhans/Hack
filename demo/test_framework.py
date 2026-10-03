"""Starter checks; optional integration checks use the installed Omnigent parser."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from demo import main
from framework import Role, Runtime, Team
from roles import make_team


class FrameworkTests(unittest.TestCase):
    def test_solo_has_only_builder(self):
        team = make_team("solo")
        self.assertEqual(team.lead.name, "builder")
        self.assertFalse(team.roles)
        self.assertTrue(team.lead.writable)

    def test_only_builder_can_write(self):
        team = make_team("team")
        docs = team.documents(Path("."))
        configs = [json.loads(text) for path, text in docs.items() if path.suffix == ".yaml"]
        self.assertEqual(len(configs), 5)
        for config in configs:
            self.assertEqual(bool(config["os_env"]["sandbox"]["write_paths"]),
                             config["name"] == "builder")
            self.assertEqual(config["os_env"]["sandbox"]["type"], "auto")

    def test_mixed_does_not_leak_coordinator_model(self):
        team = make_team("mixed", model="claude-model")
        reviewer = next(r for r in team.roles if r.name == "reviewer")
        self.assertEqual(reviewer.runtime.harness, "codex")
        self.assertIsNone(reviewer.runtime.model)
        self.assertEqual(team.lead.runtime.model, "claude-model")

    def test_databricks_auth_reaches_all_roles(self):
        team = make_team("databricks", profile="my-workspace", model="my-endpoint")
        for role in (team.lead, *team.roles):
            self.assertEqual(role.runtime.config()["auth"],
                             {"type": "databricks", "profile": "my-workspace"})
            self.assertEqual(role.runtime.model, "my-endpoint")

    def test_requires_explicit_databricks_settings(self):
        with self.assertRaises(ValueError):
            make_team("databricks")

    def test_bad_and_duplicate_names_rejected(self):
        with self.assertRaises(ValueError):
            Role("../escape", "", "")
        role = Role("valid", "", "")
        with self.assertRaises(ValueError):
            Team(role, (role,))

    def test_export_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            bundle = workspace / "bundle"
            make_team("team").export(bundle, workspace)
            with self.assertRaises(FileExistsError):
                make_team("solo").export(bundle, workspace)
            self.assertEqual(json.loads((bundle / "config.yaml").read_text())["name"],
                             "coordinator")

    def test_dry_run_never_needs_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            bundle = make_team("solo").export(workspace / "bundle", workspace)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["run", str(bundle), "--dry-run", "--prompt", "hello"])
            self.assertEqual(code, 0)
            self.assertIn("-p hello", output.getvalue())

    def test_launcher_uses_parent_project_environment(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            demo_root = project / "demo"
            demo_root.mkdir()
            executable = project / ".venv" / "bin" / "omnigent"
            executable.parent.mkdir(parents=True)
            executable.touch()
            bundle = make_team("solo").export(project / "bundle", project)
            output = io.StringIO()
            with patch("demo.ROOT", demo_root), patch("demo.shutil.which", return_value=None):
                with contextlib.redirect_stdout(output):
                    code = main(["run", str(bundle), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn(str(executable), output.getvalue())

    def test_extending_roles(self):
        role = Role("analyst", "Summarize evidence", "Read and summarize; never edit.",
                    runtime=Runtime("pi"))
        docs = Team(role).documents(Path("."))
        self.assertEqual(json.loads(docs[Path("config.yaml")])["executor"]["config"]["harness"], "pi")

    @unittest.skipUnless(importlib.util.find_spec("omnigent"), "Install Omnigent for integration checks")
    def test_real_omnigent_accepts_all_presets(self):
        from omnigent.spec.parser import parse
        from omnigent.spec.validator import validate
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            for preset in ("solo", "team", "mixed", "databricks"):
                with self.subTest(preset=preset):
                    kwargs = ({"profile": "test", "model": "test-endpoint"}
                              if preset == "databricks" else {})
                    bundle = make_team(preset, **kwargs).export(workspace / preset, workspace)
                    spec = parse(bundle)
                    self.assertFalse(validate(spec).errors)
                    self.assertEqual(len(spec.sub_agents), 0 if preset == "solo" else 4)
                    for role_spec in (spec, *spec.sub_agents):
                        self.assertEqual(role_spec.os_env.cwd, str(workspace.resolve()))
                        self.assertIsNotNone(role_spec.guardrails)
                        self.assertIn(role_spec.executor.config["harness"], ("claude-sdk", "codex"))
                        if preset == "databricks":
                            self.assertEqual(role_spec.executor.model, "test-endpoint")
                            self.assertEqual(role_spec.executor.auth.profile, "test")
                        if preset == "mixed" and role_spec.name == "reviewer":
                            self.assertEqual(role_spec.executor.config["harness"], "codex")


if __name__ == "__main__":
    unittest.main()
