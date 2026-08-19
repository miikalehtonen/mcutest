from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .adopt import render_project_config
from .build import BuildError, build
from .config import CONFIG_PATH, ConfigError, find_root, load_manifest
from .process import CommandError, capture
from .sim import SimulationError, simulate


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="mcutest", description="Build and regression-test MCU firmware in Docker")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    result.add_argument("--project", type=Path, default=Path.cwd(), help="project path inside the mounted workspace")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check container tools and credentials")
    commands.add_parser("inspect", help="show detected project configuration")
    adopt = commands.add_parser("adopt", help="print or write .mcutest/project.toml without creating tests")
    adopt.add_argument("--fqbn")
    adopt.add_argument("--write", action="store_true")
    commands.add_parser("build", help="compile firmware")
    test = commands.add_parser("test", help="run all project test files, or one test by name")
    test.add_argument("test_name", nargs="?")
    test.add_argument("--json", action="store_true")
    test.add_argument("-j", "--jobs", type=int, default=int(os.environ.get("MCUTEST_JOBS", "8")))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor()
        if args.command == "adopt":
            return adopt(args.project, args.fqbn, args.write)
        manifest = load_manifest(args.project)
        if args.command == "inspect":
            print(json.dumps(_manifest_summary(manifest), indent=2))
            return 0
        cache = _cache_for(manifest.project.root)
        if args.command == "build":
            artifact = build(manifest.project, cache)
            print(json.dumps({"firmware": str(artifact.firmware), "elf": str(artifact.elf) if artifact.elf else None}, indent=2))
            return 0
        tests = select_tests(manifest, args.test_name)
        if not os.environ.get("WOKWI_CLI_TOKEN"):
            raise ConfigError("WOKWI_CLI_TOKEN is required for simulation")
        artifact = build(manifest.project, cache)
        if args.jobs <= 0:
            raise ConfigError("--jobs must be positive")
        return run_tests(manifest, artifact, cache, tests, args.json, args.jobs)
    except (ConfigError, BuildError, CommandError, SimulationError, OSError) as error:
        print(f"mcutest: {error}", file=sys.stderr)
        return 2


def doctor() -> int:
    checks = {
        "mcutest": __version__,
        "arduino-cli": capture(["arduino-cli", "version"]),
        "platformio": capture(["pio", "--version"]),
        "wokwi-cli": capture(["wokwi-cli", "--version"]),
        "wokwi_token": "set" if os.environ.get("WOKWI_CLI_TOKEN") else "missing",
    }
    print(json.dumps(checks, indent=2))
    return 0 if all(value != "unavailable" for key, value in checks.items() if key != "wokwi_token") else 1


def adopt(path: Path, fqbn: str | None, write: bool) -> int:
    root = find_root(path)
    content = render_project_config(root, fqbn)
    target = root / CONFIG_PATH
    if write:
        if target.exists():
            raise ConfigError(f"Refusing to overwrite {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(target)
    else:
        print(content, end="")
    return 0


def select_tests(manifest, selected: str | None):
    if not manifest.tests:
        raise ConfigError("No test files found under .mcutest/tests")
    if selected is None:
        return manifest.tests
    tests = tuple(item for item in manifest.tests if item.name == selected)
    if not tests:
        raise ConfigError(f"No test named {selected!r}")
    return tests


def run_tests(manifest, artifact, cache: Path, tests, as_json: bool, jobs: int = 8) -> int:
    with ThreadPoolExecutor(max_workers=min(jobs, len(tests))) as executor:
        results = list(executor.map(lambda item: simulate(manifest.project, artifact, item, cache), tests))
    payload = [
        {"name": item.name, "passed": item.passed, "missing": item.missing, "rejected": item.rejected, "log": str(item.log)}
        for item in results
    ]
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        for item in payload:
            status = "PASS" if item["passed"] else "FAIL"
            print(f"{status:4} {item['name']}  log={item['log']}")
            if item["missing"]:
                print("     missing:", ", ".join(item["missing"]))
            if item["rejected"]:
                print("     rejected:", ", ".join(item["rejected"]))
    return 0 if all(item.passed for item in results) else 1


def _cache_for(root: Path) -> Path:
    base = Path(os.environ.get("MCUTEST_CACHE", "/cache/workspaces"))
    # Every repository is mounted at /workspace in the container. The launcher
    # passes the host path so unrelated repositories cannot share build output.
    project_key = os.environ.get("MCUTEST_PROJECT_KEY", str(root))
    digest = hashlib.sha256(project_key.encode()).hexdigest()[:16]
    target = base / digest
    base.mkdir(parents=True, exist_ok=True)
    _prune_stale_workspaces(base, target)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".last_used").touch()
    return target


def _prune_stale_workspaces(base: Path, current: Path) -> None:
    max_age_days = int(os.environ.get("MCUTEST_WORKSPACE_TTL_DAYS", "30"))
    cutoff = time.time() - max_age_days * 86400
    for candidate in base.iterdir():
        if candidate == current or not candidate.is_dir():
            continue
        marker = candidate / ".last_used"
        timestamp = marker.stat().st_mtime if marker.exists() else candidate.stat().st_mtime
        if timestamp < cutoff:
            shutil.rmtree(candidate)


def _manifest_summary(manifest) -> dict:
    project = manifest.project
    return {
        "root": str(project.root),
        "adapter": project.adapter,
        "sketch": str(project.sketch) if project.sketch else None,
        "fqbn": project.fqbn,
        "profile": project.profile,
        "platformio_env": project.platformio_env,
        "board": project.board,
        "serial_tx": project.serial_tx,
        "serial_rx": project.serial_rx,
        "core": project.core,
        "board_urls": list(project.board_urls),
        "library_dirs": [str(path) for path in project.library_dirs],
        "tests": [{"name": item.name, "file": str(item.source)} for item in manifest.tests],
    }


if __name__ == "__main__":
    raise SystemExit(main())
