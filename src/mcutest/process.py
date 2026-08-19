from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


class CommandError(RuntimeError):
    def __init__(self, command: list[str], returncode: int):
        super().__init__(f"Command failed ({returncode}): {' '.join(command)}")
        self.command = command
        self.returncode = returncode


def run(
    command: Iterable[str],
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [str(part) for part in command]
    print("+", " ".join(argv), flush=True)
    result = subprocess.run(argv, cwd=cwd, text=True, env=env)
    if check and result.returncode:
        raise CommandError(argv, result.returncode)
    return result


def capture(command: Iterable[str]) -> str:
    result = subprocess.run([str(part) for part in command], text=True, capture_output=True)
    if result.returncode:
        return "unavailable"
    return (result.stdout or result.stderr).strip()
