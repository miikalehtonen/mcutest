import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcutest.adopt import render_project_config
from mcutest.build import _arduino_core_is_installed, _arduino_fingerprint, _find_arduino_firmware
from mcutest.cli import _cache_for, _prune_stale_workspaces, select_tests
from mcutest.config import ConfigError, detect_project, load_manifest
from mcutest.model import Artifact, Manifest, Project, TestCase
from mcutest.sim import SimulationError, _missing_expectations, _write_diagram, simulate


class ConfigTests(unittest.TestCase):
    def test_detects_platformio_without_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "platformio.ini").write_text("[env:uno]\n", encoding="utf-8")
            self.assertEqual(detect_project(root).adapter, "platformio")

    def test_detects_ino_and_cpp_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "device.ino").write_text('#include "library.h"\n', encoding="utf-8")
            (root / "library.cpp").write_text("void function() {}\n", encoding="utf-8")
            project = detect_project(root)
            self.assertEqual(project.adapter, "arduino-cli")
            self.assertEqual(project.sketch.name, "device.ino")

    def test_discovers_one_test_per_file_and_uses_relative_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(Path(directory))
            tests = root / ".mcutest" / "tests"
            (tests / "network").mkdir(parents=True)
            (tests / "boot.toml").write_text('[test]\nexpect=["READY"]\n', encoding="utf-8")
            (tests / "network" / "fallback.toml").write_text(
                '[test]\nreject=["FATAL"]\n', encoding="utf-8"
            )
            manifest = load_manifest(root)
            self.assertEqual([test.name for test in manifest.tests], ["boot", "network/fallback"])
            self.assertEqual(manifest.tests[0].reject, ())
            self.assertEqual(manifest.tests[1].reject, ("FATAL",))

    def test_test_file_defines_generic_wokwi_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(Path(directory))
            tests = root / ".mcutest" / "tests"
            tests.mkdir(parents=True)
            (tests / "input.toml").write_text(
                """[test]
timeout = 4

[[wokwi.part]]
type = "wokwi-clock-generator"
id = "source"
attrs = { frequency = "2k" }

[[wokwi.connection]]
from = "source:CLK"
to = "mcu:7"
color = "blue"
""",
                encoding="utf-8",
            )
            manifest = load_manifest(root)
            test = manifest.tests[0]
            self.assertEqual(test.parts[0].type, "wokwi-clock-generator")
            self.assertEqual(test.connections[0].to_pin, "mcu:7")
            _write_diagram(root, manifest.project, test)
            diagram = json.loads((root / "diagram.json").read_text(encoding="utf-8"))
            self.assertEqual(diagram["parts"][1]["attrs"]["frequency"], "2k")
            self.assertEqual(diagram["connections"][0][0:3], ["source:CLK", "mcu:7", "blue"])

    def test_referenced_automation_is_passed_to_wokwi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "firmware.hex"
            firmware.write_text("firmware", encoding="utf-8")
            automation = root / "input.yaml"
            automation.write_text("steps: []\n", encoding="utf-8")
            test = TestCase("automated", root / "automated.toml", automation=automation, include_project_board=False)
            completed = subprocess.CompletedProcess([], 0)
            with patch("mcutest.sim._run_wokwi", return_value=(completed, False)) as command:
                result = simulate(Project(root, "platformio"), Artifact(firmware, None), test, root / "cache")
            argv = command.call_args.args[0]
            self.assertEqual(argv[argv.index("--scenario") + 1], "automation.yaml")
            self.assertTrue(result.passed)

    def test_transport_failure_retries_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "firmware.bin"
            firmware.write_text("firmware", encoding="utf-8")
            failed = subprocess.CompletedProcess([], 7)
            passed = subprocess.CompletedProcess([], 0)
            test = TestCase("retry", root / "retry.toml", include_project_board=False)
            with patch.dict("os.environ", {"MCUTEST_WOKWI_RETRIES": "2"}), patch(
                "mcutest.sim._run_wokwi", side_effect=[(failed, False), (passed, False)]
            ) as runner, patch("mcutest.sim.time.sleep"):
                result = simulate(Project(root, "platformio"), Artifact(firmware, None), test, root / "cache")
            self.assertEqual(runner.call_count, 2)
            self.assertTrue(result.passed)

    def test_generated_diagram_wires_explicit_project_serial_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Project(
                root, "arduino-cli", board="wokwi-esp32-devkit-v1", serial_tx="TX0", serial_rx="RX0"
            )
            _write_diagram(root, project, TestCase("boot", root / "boot.toml"))
            diagram = json.loads((root / "diagram.json").read_text(encoding="utf-8"))
            self.assertIn(["mcu:TX0", "$serialMonitor:RX", "", []], diagram["connections"])
            self.assertIn(["mcu:RX0", "$serialMonitor:TX", "", []], diagram["connections"])

    def test_generated_diagram_requires_explicit_project_board(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test = TestCase("empty", root / "empty.toml")
            with self.assertRaises(SimulationError):
                _write_diagram(root, Project(root, "platformio"), test)

    def test_adopt_creates_only_project_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "device.ino").write_text("", encoding="utf-8")
            config = render_project_config(root, "arduino:avr:uno")
            self.assertIn("[project]", config)
            self.assertNotIn("[test]", config)
            self.assertNotIn("wifi", config.lower())
            self.assertNotIn("sensor", config.lower())

    def test_raw_arduino_adopt_requires_fqbn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "device.ino").write_text("", encoding="utf-8")
            with self.assertRaises(ConfigError):
                render_project_config(root)

    def test_select_without_name_runs_all_and_name_runs_one(self):
        root = Path("/project")
        tests = (TestCase("one", root / "one.toml"), TestCase("group/two", root / "two.toml"))
        manifest = Manifest(Project(root, "platformio"), tests)
        self.assertEqual(select_tests(manifest, None), tests)
        self.assertEqual(select_tests(manifest, "group/two"), (tests[1],))
        with self.assertRaises(ConfigError):
            select_tests(manifest, "all")

    def test_ordered_expectations(self):
        self.assertEqual(_missing_expectations("A B C", ("A", "C"), True), ())
        self.assertEqual(_missing_expectations("C A", ("A", "C"), True), ("C",))

    def test_unknown_test_field_fails_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(Path(directory))
            tests = root / ".mcutest" / "tests"
            tests.mkdir(parents=True)
            (tests / "typo.toml").write_text('[test]\nexpext=["READY"]\n', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "expext"):
                load_manifest(root)

    def test_expect_must_be_an_array_of_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(Path(directory))
            tests = root / ".mcutest" / "tests"
            tests.mkdir(parents=True)
            (tests / "invalid.toml").write_text('[test]\nexpect="READY"\n', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "array of strings"):
                load_manifest(root)

    def test_cache_is_keyed_by_host_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"MCUTEST_CACHE": directory, "MCUTEST_PROJECT_KEY": "/host/repo-a"}):
                first = _cache_for(Path("/workspace"))
            with patch.dict("os.environ", {"MCUTEST_CACHE": directory, "MCUTEST_PROJECT_KEY": "/host/repo-b"}):
                second = _cache_for(Path("/workspace"))
            self.assertNotEqual(first, second)

    def test_stale_workspace_is_pruned_but_current_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            current = base / "current"
            stale = base / "stale"
            current.mkdir()
            stale.mkdir()
            marker = stale / ".last_used"
            marker.touch()
            old = time.time() - 31 * 86400
            marker.touch()
            import os
            os.utime(marker, (old, old))
            with patch.dict("os.environ", {"MCUTEST_WORKSPACE_TTL_DAYS": "30"}):
                _prune_stale_workspaces(base, current)
            self.assertTrue(current.exists())
            self.assertFalse(stale.exists())

    def test_local_core_version_skips_install(self):
        listing = "ID Installed Latest Name\nesp32:esp32 2.0.17 3.3.11 esp32\n"
        with patch("mcutest.build.capture", return_value=listing):
            self.assertTrue(_arduino_core_is_installed("esp32:esp32@2.0.17"))
            self.assertTrue(_arduino_core_is_installed("esp32:esp32"))
            self.assertFalse(_arduino_core_is_installed("esp32:esp32@3.3.11"))

    def test_arduino_fingerprint_changes_with_source_but_not_readme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sketch = root / "device.ino"
            sketch.write_text("void setup() {}\n", encoding="utf-8")
            project = Project(root, "arduino-cli", sketch=sketch, fqbn="arduino:avr:uno")
            with patch("mcutest.build.capture", return_value="arduino-cli 1.5.1"):
                first = _arduino_fingerprint(project)
                (root / "README.md").write_text("documentation", encoding="utf-8")
                self.assertEqual(first, _arduino_fingerprint(project))
                sketch.write_text("void setup() { pinMode(1, 1); }\n", encoding="utf-8")
                self.assertNotEqual(first, _arduino_fingerprint(project))

    def test_arduino_firmware_prefers_application_image_and_falls_back_to_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = root / "device.ino.bin"
            application.write_text("application", encoding="utf-8")
            merged = root / "device.ino.merged.bin"
            merged.write_text("merged", encoding="utf-8")
            (root / "device.ino.bootloader.bin").write_text("bootloader", encoding="utf-8")
            (root / "device.ino.partitions.bin").write_text("partitions", encoding="utf-8")
            self.assertEqual(_find_arduino_firmware(root), application)
            application.unlink()
            self.assertEqual(_find_arduino_firmware(root), merged)

    @staticmethod
    def _project(root: Path) -> Path:
        (root / "device.ino").write_text("", encoding="utf-8")
        config = root / ".mcutest" / "project.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            '[project]\nfqbn="arduino:avr:uno"\nboard="wokwi-arduino-uno"\n', encoding="utf-8"
        )
        return root


if __name__ == "__main__":
    unittest.main()
