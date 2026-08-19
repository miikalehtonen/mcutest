from __future__ import annotations

import tomllib
from pathlib import Path

from .model import Manifest, Project, TestCase, WokwiConnection, WokwiPart


class ConfigError(ValueError):
    pass


CONFIG_PATH = Path(".mcutest/project.toml")
TESTS_PATH = Path(".mcutest/tests")


def find_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_PATH).is_file():
            return candidate
        if (candidate / "platformio.ini").is_file():
            return candidate
        if (candidate / "sketch.yaml").is_file():
            return candidate
        if any(candidate.glob("*.ino")):
            return candidate
    return current


def detect_project(root: Path, data: dict | None = None) -> Project:
    data = data or {}
    _reject_unknown(root / CONFIG_PATH, data, {"project"}, "top level")
    section = data.get("project", {})
    if not isinstance(section, dict):
        raise ConfigError(f"{root / CONFIG_PATH}: [project] must be a table")
    _reject_unknown(
        root / CONFIG_PATH,
        section,
        {
            "adapter", "sketch", "fqbn", "profile", "platformio_env", "board",
            "serial_tx", "serial_rx", "core", "board_urls", "library_dirs",
        },
        "project",
    )
    requested = str(section.get("adapter", "auto"))
    adapter = requested
    if requested == "auto":
        if (root / "platformio.ini").is_file():
            adapter = "platformio"
        elif (root / "sketch.yaml").is_file() or any(root.glob("*.ino")):
            adapter = "arduino-cli"
        else:
            raise ConfigError("No platformio.ini, sketch.yaml, or .ino sketch found")
    if adapter not in {"platformio", "arduino-cli"}:
        raise ConfigError(f"Unsupported adapter: {adapter}")

    sketch_value = section.get("sketch")
    sketch = (root / str(sketch_value)).resolve() if sketch_value else None
    if adapter == "arduino-cli" and sketch is None:
        sketches = sorted(root.glob("*.ino"))
        if len(sketches) == 1:
            sketch = sketches[0]
        elif (root / root.name).with_suffix(".ino").is_file():
            sketch = (root / root.name).with_suffix(".ino")
        elif not (root / "sketch.yaml").is_file():
            raise ConfigError("Set project.sketch when the project has zero or multiple root .ino files")

    serial_tx = _optional_text(section.get("serial_tx"))
    serial_rx = _optional_text(section.get("serial_rx"))
    if bool(serial_tx) != bool(serial_rx):
        raise ConfigError(f"{root / CONFIG_PATH}: project.serial_tx and project.serial_rx must be set together")

    return Project(
        root=root,
        adapter=adapter,
        sketch=sketch,
        fqbn=_optional_text(section.get("fqbn")),
        profile=_optional_text(section.get("profile")),
        platformio_env=_optional_text(section.get("platformio_env")),
        board=_optional_text(section.get("board")),
        serial_tx=serial_tx,
        serial_rx=serial_rx,
        core=_optional_text(section.get("core")),
        board_urls=_string_list(root / CONFIG_PATH, section.get("board_urls", []), "project.board_urls"),
        library_dirs=tuple(
            (root / value).resolve()
            for value in _string_list(root / CONFIG_PATH, section.get("library_dirs", []), "project.library_dirs")
        ),
    )


def load_manifest(start: Path) -> Manifest:
    root = find_root(start)
    config_path = root / CONFIG_PATH
    data: dict = {}
    if config_path.is_file():
        data = _load_toml(config_path)
    project = detect_project(root, data)
    tests = tuple(_parse_test(root, path) for path in _test_files(root))
    return Manifest(project=project, tests=tests, raw=data)


def _test_files(root: Path) -> list[Path]:
    tests_root = root / TESTS_PATH
    return sorted(path for path in tests_root.rglob("*.toml") if path.is_file()) if tests_root.is_dir() else []


def _parse_test(root: Path, path: Path) -> TestCase:
    data = _load_toml(path)
    _reject_unknown(path, data, {"test", "wokwi"}, "top level")
    section = data.get("test")
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: missing [test] table")
    _reject_unknown(path, section, {"timeout", "wall_timeout", "expect", "reject", "ordered"}, "test")
    tests_root = root / TESTS_PATH
    name = path.relative_to(tests_root).with_suffix("").as_posix()
    timeout = int(section.get("timeout", 30))
    if timeout <= 0:
        raise ConfigError(f"{path}: test.timeout must be positive")
    wall_timeout = int(section["wall_timeout"]) if "wall_timeout" in section else None
    if wall_timeout is not None and wall_timeout <= 0:
        raise ConfigError(f"{path}: test.wall_timeout must be positive")

    wokwi = data.get("wokwi", {})
    if not isinstance(wokwi, dict):
        raise ConfigError(f"{path}: [wokwi] must be a table")
    _reject_unknown(
        path,
        wokwi,
        {"diagram", "automation", "include_project_board", "part", "connection"},
        "wokwi",
    )
    diagram = _relative_file(path, wokwi.get("diagram"), "wokwi.diagram")
    automation = _relative_file(path, wokwi.get("automation"), "wokwi.automation")
    parts = tuple(_parse_part(path, item) for item in wokwi.get("part", ()))
    connections = tuple(_parse_connection(path, item) for item in wokwi.get("connection", ()))
    if diagram and (parts or connections):
        raise ConfigError(f"{path}: wokwi.diagram cannot be combined with generated parts or connections")

    return TestCase(
        name=name,
        source=path,
        timeout=timeout,
        wall_timeout=wall_timeout,
        expect=_string_list(path, section.get("expect", []), "test.expect"),
        reject=_string_list(path, section.get("reject", []), "test.reject"),
        ordered=bool(section.get("ordered", True)),
        diagram=diagram,
        automation=automation,
        include_project_board=bool(wokwi.get("include_project_board", True)),
        parts=parts,
        connections=connections,
    )


def _parse_part(path: Path, item: dict) -> WokwiPart:
    if not isinstance(item, dict) or "type" not in item or "id" not in item:
        raise ConfigError(f"{path}: every [[wokwi.part]] needs type and id")
    attrs = item.get("attrs", {})
    if not isinstance(attrs, dict):
        raise ConfigError(f"{path}: wokwi.part.attrs must be a table")
    _reject_unknown(path, item, {"type", "id", "top", "left", "attrs"}, "wokwi.part")
    return WokwiPart(
        type=str(item["type"]),
        id=str(item["id"]),
        top=item.get("top", 0),
        left=item.get("left", 0),
        attrs={str(key): _attribute_value(value) for key, value in attrs.items()},
    )


def _parse_connection(path: Path, item: dict) -> WokwiConnection:
    if not isinstance(item, dict) or "from" not in item or "to" not in item:
        raise ConfigError(f"{path}: every [[wokwi.connection]] needs from and to")
    _reject_unknown(path, item, {"from", "to", "color", "route"}, "wokwi.connection")
    route = item.get("route", [])
    if not isinstance(route, list):
        raise ConfigError(f"{path}: wokwi.connection.route must be an array")
    return WokwiConnection(
        from_pin=str(item["from"]),
        to_pin=str(item["to"]),
        color=str(item.get("color", "green")),
        route=tuple(route),
    )


def _relative_file(test_path: Path, value: object, field: str) -> Path | None:
    if value is None:
        return None
    resolved = (test_path.parent / str(value)).resolve()
    if not resolved.is_file():
        raise ConfigError(f"{test_path}: {field} file not found: {resolved}")
    return resolved


def _attribute_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error


def _string_list(path: Path, value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{path}: {field} must be an array of strings")
    return tuple(value)


def _reject_unknown(path: Path, section: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown {label} field(s): {', '.join(unknown)}")


def _optional_text(value: object) -> str | None:
    return None if value is None or str(value) == "" else str(value)
