from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Project:
    root: Path
    adapter: str
    sketch: Path | None = None
    fqbn: str | None = None
    profile: str | None = None
    platformio_env: str | None = None
    board: str | None = None
    serial_tx: str | None = None
    serial_rx: str | None = None
    core: str | None = None
    board_urls: tuple[str, ...] = ()
    library_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class WokwiPart:
    type: str
    id: str
    top: int | float = 0
    left: int | float = 0
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WokwiConnection:
    from_pin: str
    to_pin: str
    color: str = "green"
    route: tuple[str | int | float, ...] = ()


@dataclass(frozen=True)
class TestCase:
    name: str
    source: Path
    timeout: int = 30
    wall_timeout: int | None = None
    expect: tuple[str, ...] = ()
    reject: tuple[str, ...] = ()
    ordered: bool = True
    diagram: Path | None = None
    automation: Path | None = None
    include_project_board: bool = True
    parts: tuple[WokwiPart, ...] = ()
    connections: tuple[WokwiConnection, ...] = ()


@dataclass(frozen=True)
class Manifest:
    project: Project
    tests: tuple[TestCase, ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Artifact:
    firmware: Path
    elf: Path | None
    environment: str | None = None
