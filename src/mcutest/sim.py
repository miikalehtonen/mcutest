from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .model import Artifact, Project, TestCase
class SimulationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    name: str
    passed: bool
    missing: tuple[str, ...]
    rejected: tuple[str, ...]
    log: Path


def simulate(project: Project, artifact: Artifact, test: TestCase, cache_root: Path) -> Result:
    workspace = cache_root / "tests" / test.name
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    firmware = workspace / artifact.firmware.name
    shutil.copy2(artifact.firmware, firmware)
    elf = None
    if artifact.elf:
        elf = workspace / artifact.elf.name
        shutil.copy2(artifact.elf, elf)
    _write_wokwi_toml(workspace, firmware, elf)
    if test.diagram:
        shutil.copy2(test.diagram, workspace / "diagram.json")
    else:
        _write_diagram(workspace, project, test)
    log = workspace / "serial.log"
    command = [
        "wokwi-cli", str(workspace),
        "--timeout", str(test.timeout * 1000),
        "--timeout-exit-code", "0",
        "--serial-log-file", str(log),
    ]
    if test.automation:
        automation = workspace / "automation.yaml"
        shutil.copy2(test.automation, automation)
        command += ["--scenario", automation.name]
    retries = int(os.environ.get("MCUTEST_WOKWI_RETRIES", "2"))
    completed = subprocess.CompletedProcess(command, 1)
    stopped_early = False
    for attempt in range(retries + 1):
        if log.exists():
            log.unlink()
        completed, stopped_early = _run_wokwi(command, log, test)
        if completed.returncode == 0 or stopped_early:
            break
        if attempt < retries:
            print(
                f"Wokwi attempt {attempt + 1} failed with exit code "
                f"{completed.returncode}; retrying test {test.name!r}",
                flush=True,
            )
            time.sleep(0.5 * (attempt + 1))
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    missing = _missing_expectations(text, test.expect, test.ordered)
    rejected = tuple(value for value in test.reject if value.lower() in text.lower())
    passed = (completed.returncode == 0 or stopped_early) and not missing and not rejected
    return Result(test.name, passed, missing, rejected, log)


def _write_wokwi_toml(workspace: Path, firmware: Path, elf: Path | None) -> None:
    lines = ["[wokwi]", "version = 1", f"firmware = '{firmware.name}'"]
    if elf:
        lines.append(f"elf = '{elf.name}'")
    (workspace / "wokwi.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_diagram(workspace: Path, project: Project, test: TestCase) -> None:
    parts: list[dict] = []
    if test.include_project_board:
        if not project.board:
            raise SimulationError(
                f"Test {test.name!r} generates a diagram, so project.board must be set in .mcutest/project.toml"
            )
        parts.append({"type": project.board, "id": "mcu", "top": 0, "left": 0, "attrs": {}})
    parts.extend(
        {"type": part.type, "id": part.id, "top": part.top, "left": part.left, "attrs": part.attrs}
        for part in test.parts
    )
    connections = [
        [connection.from_pin, connection.to_pin, connection.color, list(connection.route)]
        for connection in test.connections
    ]
    if test.include_project_board and project.serial_tx and project.serial_rx:
        connections[0:0] = [
            [f"mcu:{project.serial_tx}", "$serialMonitor:RX", "", []],
            [f"mcu:{project.serial_rx}", "$serialMonitor:TX", "", []],
        ]
    diagram = {"version": 1, "author": "mcutest", "editor": "mcutest", "parts": parts, "connections": connections}
    (workspace / "diagram.json").write_text(json.dumps(diagram, indent=2) + "\n", encoding="utf-8")


def _run_wokwi(command: list[str], log: Path, test: TestCase) -> tuple[subprocess.CompletedProcess[str], bool]:
    print("+", " ".join(command), flush=True)
    process = subprocess.Popen(command, start_new_session=True)
    deadline = time.monotonic() + (test.wall_timeout or max(30, test.timeout * 6))
    stopped_early = False
    while process.poll() is None:
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        missing = _missing_expectations(text, test.expect, test.ordered)
        rejected = any(value.lower() in text.lower() for value in test.reject)
        if rejected or (test.expect and not missing):
            stopped_early = True
            _terminate_process_group(process)
            break
        if time.monotonic() >= deadline:
            _terminate_process_group(process)
            raise SimulationError(
                f"Test {test.name!r} exceeded wall timeout of "
                f"{test.wall_timeout or max(30, test.timeout * 6)} seconds"
            )
        time.sleep(0.05)
    return subprocess.CompletedProcess(command, process.wait()), stopped_early


def _terminate_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _missing_expectations(log: str, expected: tuple[str, ...], ordered: bool) -> tuple[str, ...]:
    if not ordered:
        return tuple(value for value in expected if value not in log)
    cursor = 0
    missing: list[str] = []
    for value in expected:
        position = log.find(value, cursor)
        if position < 0:
            missing.append(value)
        else:
            cursor = position + len(value)
    return tuple(missing)
