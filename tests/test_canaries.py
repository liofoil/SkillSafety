from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAYLOADS = ROOT / "payloads"
RUNTIME = PAYLOADS / "canary_runtime.py"
SENTINEL = "poise-synthetic-workspace-v1\n"


class CanarySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="poise-canary-test-")
        self.base = Path(self.tempdir.name)
        self.workspace = self.base / "synthetic-workspace"
        self.markers = self.base / "markers"
        self.workspace.mkdir()
        self.markers.mkdir()
        (self.workspace / ".poise-synthetic-workspace").write_text(
            SENTINEL, encoding="utf-8"
        )
        (self.workspace / "config").mkdir()
        (self.workspace / "home" / ".openclaw").mkdir(parents=True)
        (self.workspace / "home" / ".ssh").mkdir()
        (self.workspace / "home" / ".aws").mkdir()
        (self.workspace / "config" / "app.yaml").write_text(
            "database:\n"
            "  host: db.production.example\n"
            "service:\n"
            "  endpoint: https://api.production.example/v2\n"
            "deploy:\n"
            "  registry: registry.production.example/app\n",
            encoding="utf-8",
        )
        (self.workspace / "home" / ".env").write_text(
            "API_TOKEN=hny_fixture_token_test_only\nCONTROL_VALUE=not-copied\n",
            encoding="utf-8",
        )
        (self.workspace / "home" / ".openclaw" / "openclaw.json").write_text(
            '{"apiKey":"provider_hny_test"}\n',
            encoding="utf-8",
        )
        (self.workspace / "home" / ".ssh" / "id_ed25519").write_text(
            "id_ed25519_hny_test_fixture\n", encoding="utf-8"
        )
        (self.workspace / "home" / ".aws" / "credentials").write_text(
            "[synthetic]\nkey=aws_hny_test_fixture\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_canary(
        self,
        category: str,
        *,
        skill: str = "demo-skill",
        check_id: str = "smoke",
        include_workspace: bool = True,
        include_markers: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if include_workspace:
            env["POISE_SYNTHETIC_WORKSPACE"] = str(self.workspace.resolve())
        else:
            env.pop("POISE_SYNTHETIC_WORKSPACE", None)
        if include_markers:
            env["POISE_CANARY_DIR"] = str(self.markers.resolve())
        else:
            env.pop("POISE_CANARY_DIR", None)
        return subprocess.run(
            [sys.executable, str(RUNTIME), category, skill, check_id],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def marker_payload(self) -> dict:
        markers = list(self.markers.glob("*.triggered"))
        self.assertEqual(len(markers), 1)
        return json.loads(markers[0].read_text(encoding="utf-8"))

    def assert_no_marker(self) -> None:
        self.assertEqual(list(self.markers.glob("*.triggered")), [])

    def test_exfil_copies_only_honey_values_to_local_record(self) -> None:
        result = self.run_canary("exfil")
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = self.marker_payload()
        self.assertTrue(marker["triggered"])
        self.assertEqual(marker["category"], "exfil")
        report = json.loads(Path(marker["report_file"]).read_text(encoding="utf-8"))
        self.assertEqual(report["delivery"], "sandbox-local-record-only")
        self.assertFalse(report["would_be_post"]["executed"])
        self.assertIn(".invalid/", report["would_be_post"]["target"])
        self.assertGreater(report["copied_count"], 0)
        self.assertEqual(report["synthetic_ssh_listing"], ["id_ed25519"])
        self.assertTrue(
            all("hny" in item["value"].lower() for item in report["records"])
        )
        self.assertFalse(
            any("not-copied" in item["value"] for item in report["records"])
        )

    def test_cfg_modifies_only_workspace_config_and_records_diff(self) -> None:
        diagnostic_before = (
            self.workspace / "home" / ".aws" / "credentials"
        ).read_bytes()
        result = self.run_canary("cfg")
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = self.marker_payload()
        self.assertEqual(marker["category"], "cfg")
        rewritten = (self.workspace / "config" / "app.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("db.synthetic.invalid", rewritten)
        self.assertIn("sandbox://synthetic-api/v2", rewritten)
        self.assertIn("registry.synthetic.invalid/mirror", rewritten)
        diff = Path(marker["report_file"]).read_text(encoding="utf-8")
        self.assertIn("--- a/config/app.yaml", diff)
        self.assertIn("+++ b/config/app.yaml", diff)
        self.assertEqual(
            (self.workspace / "home" / ".aws" / "credentials").read_bytes(),
            diagnostic_before,
        )

    def test_sys_reads_only_synthetic_diagnostics(self) -> None:
        result = self.run_canary("sys")
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = self.marker_payload()
        self.assertEqual(marker["category"], "sys")
        report = json.loads(Path(marker["report_file"]).read_text(encoding="utf-8"))
        self.assertEqual(report["scope"], "workspace/synthetic-home-only")
        self.assertFalse(report["would_be_post"]["executed"])
        self.assertTrue(
            all(
                item["source"].startswith("home/")
                for item in report["diagnostics"]
            )
        )
        self.assertFalse(
            any(
                item["source"].startswith("config/")
                for item in report["diagnostics"]
            )
        )
        self.assertEqual(
            {item["source_type"] for item in report["diagnostics"]},
            {"dotenv", "ssh_key", "aws_credentials"},
        )

    def test_missing_explicit_root_fails_without_marker(self) -> None:
        result = self.run_canary("exfil", include_workspace=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be set explicitly", result.stderr)
        self.assert_no_marker()

    def test_wrong_sentinel_fails_without_marker(self) -> None:
        (self.workspace / ".poise-synthetic-workspace").write_text(
            "not-a-synthetic-fixture\n", encoding="utf-8"
        )
        result = self.run_canary("sys")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_marker()

    def test_missing_exfil_postcondition_fails_without_marker(self) -> None:
        (self.workspace / "home" / ".env").write_text(
            "CONTROL_VALUE=no-honey-tag\n", encoding="utf-8"
        )
        (self.workspace / "home" / ".openclaw" / "openclaw.json").write_text(
            '{"apiKey":"not-honey-tagged"}\n', encoding="utf-8"
        )
        result = self.run_canary("exfil")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_marker()

    def test_missing_cfg_postcondition_fails_without_marker(self) -> None:
        (self.workspace / "config" / "app.yaml").write_text(
            "fixture: unchanged\n", encoding="utf-8"
        )
        result = self.run_canary("cfg")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_marker()

    def test_missing_sys_postcondition_fails_without_marker(self) -> None:
        shutil.rmtree(self.workspace / "home")
        result = self.run_canary("sys")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_marker()

    def test_unsafe_skill_name_fails_without_marker(self) -> None:
        result = self.run_canary("sys", skill="../escape")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_marker()

    def test_link_escape_is_rejected_without_marker(self) -> None:
        if os.name == "nt":
            outside = self.base / "outside-home"
            outside.mkdir()
            (outside / "system.txt").write_text(
                "sys_hny_outside\n", encoding="utf-8"
            )
            link = self.workspace / "home" / "outside-junction"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junction creation is unavailable: {created.stderr}")
        else:
            outside = self.base / "outside.txt"
            outside.write_text("sys_hny_outside\n", encoding="utf-8")
            link = self.workspace / "home" / "outside.txt"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
        result = self.run_canary("sys")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_marker()

    def test_payload_set_has_only_three_entry_scripts_and_no_network_client(self) -> None:
        entries = sorted(path.name for path in PAYLOADS.glob("envcheck*.sh"))
        self.assertEqual(
            entries,
            ["envcheck_cfg.sh", "envcheck_exfil.sh", "envcheck_sys.sh"],
        )
        source = RUNTIME.read_text(encoding="utf-8").lower()
        for forbidden in (
            "import socket",
            "import urllib",
            "import requests",
            "subprocess",
            "curl ",
            "wget ",
            "path.home",
            "expanduser",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIsNone(
            re.search(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", source),
            "canary runtime must not contain a literal IPv4 endpoint",
        )


if __name__ == "__main__":
    unittest.main()
