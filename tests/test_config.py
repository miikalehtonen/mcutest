import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcutest.adopt import render_project_config
from mcutest.cli import _cache_for, select_tests
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
            with patch("mcutest.sim.run", return_value=SimpleNamespace(returncode=0)) as command:
                result = simulate(Project(root, "platformio"), Artifact(firmware, None), test, root / "cache")
            argv = command.call_args.args[0]
            self.assertEqual(argv[argv.index("--scenario") + 1], "automation.yaml")
            self.assertTrue(result.passed)

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
