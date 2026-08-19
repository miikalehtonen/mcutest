from __future__ import annotations

import os
import hashlib
import json
import shutil
from pathlib import Path

from .model import Artifact, Project
from .process import capture, run


class BuildError(RuntimeError):
    pass


def build(project: Project, cache_root: Path) -> Artifact:
    build_dir = cache_root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    if project.adapter == "platformio":
        return _build_platformio(project, build_dir)
    return _build_arduino(project, build_dir)


def _build_platformio(project: Project, build_dir: Path) -> Artifact:
    workspace = build_dir / "platformio"
    environment = os.environ.copy()
    environment["PLATFORMIO_WORKSPACE_DIR"] = str(workspace)
    command = ["pio", "run", "-d", str(project.root)]
    if project.platformio_env:
        command += ["-e", project.platformio_env]
    run(command, env=environment)
    pio_build = workspace / "build"
    environments = [pio_build / project.platformio_env] if project.platformio_env else sorted(
        path for path in pio_build.iterdir() if path.is_dir()
    ) if pio_build.is_dir() else []
    candidates: list[Artifact] = []
    for env_dir in environments:
        firmware = _first_existing(env_dir / name for name in ("firmware.bin", "firmware.hex", "firmware.uf2"))
        elf = env_dir / "firmware.elf"
        if firmware:
            candidates.append(Artifact(firmware=firmware, elf=elf if elf.is_file() else None, environment=env_dir.name))
    if not candidates:
        raise BuildError("PlatformIO finished but no firmware artifact was found in its external build workspace")
    if len(candidates) > 1:
        names = ", ".join(item.environment or "?" for item in candidates)
        raise BuildError(f"Multiple PlatformIO environments produced firmware ({names}); set project.platformio_env")
    return candidates[0]


def _build_arduino(project: Project, build_dir: Path) -> Artifact:
    sketch_target = _stage_arduino_sketch(project, build_dir) if project.sketch else project.root
    fingerprint = _arduino_fingerprint(project)
    cached = _cached_arduino_artifact(build_dir, fingerprint)
    if cached:
        print("+ cached Arduino artifact", flush=True)
        return cached
    command = ["arduino-cli", "compile", "--output-dir", str(build_dir)]
    if project.profile:
        command += ["--profile", project.profile]
    elif project.fqbn:
        _ensure_arduino_core(project)
        command += ["--fqbn", project.fqbn]
    else:
        raise BuildError("Arduino CLI needs project.fqbn or project.profile in .mcutest/project.toml")
    for library_dir in project.library_dirs:
        command += ["--libraries", str(library_dir)]
    command.append(str(sketch_target))
    run(command, cwd=project.root)
    # Board/library archives are only installation staging files. Installed
    # cores and the separate compilation cache remain available.
    run(["arduino-cli", "cache", "clean"], cwd=project.root)
    firmware = _find_arduino_firmware(build_dir)
    elf = _find_artifact(build_dir, ("*.elf",))
    if firmware is None:
        raise BuildError(f"Arduino CLI finished but no firmware was found in {build_dir}")
    artifact = Artifact(firmware=firmware, elf=elf)
    _save_arduino_artifact(build_dir, fingerprint, artifact)
    return artifact


def _arduino_fingerprint(project: Project) -> str:
    digest = hashlib.sha256()
    settings = {
        "fqbn": project.fqbn,
        "profile": project.profile,
        "core": project.core,
        "board_urls": project.board_urls,
        "library_dirs": tuple(str(path) for path in project.library_dirs),
        "arduino_cli": capture(["arduino-cli", "version"]),
        "installed_cores": capture(["arduino-cli", "core", "list"]),
        "artifact_format": "arduino-application-v1",
    }
    digest.update(json.dumps(settings, sort_keys=True).encode())
    roots: list[Path] = []
    if project.sketch:
        roots.append(project.sketch.parent)
    else:
        roots.append(project.root)
    roots.extend(project.library_dirs)
    source_suffixes = {
        ".ino", ".pde", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
        ".s", ".S", ".ld", ".csv", ".json", ".yaml", ".yml", ".properties", ".txt",
    }
    for root in roots:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if any(part in {".git", ".mcutest", ".pio", "__pycache__"} for part in path.parts):
                continue
            if path.suffix not in source_suffixes and not path.name.startswith("sdkconfig"):
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _cached_arduino_artifact(build_dir: Path, fingerprint: str) -> Artifact | None:
    metadata = build_dir / ".mcutest-build.json"
    if not metadata.is_file():
        return None
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("fingerprint") != fingerprint:
        return None
    firmware = build_dir / data.get("firmware", "")
    elf_name = data.get("elf")
    elf = build_dir / elf_name if elf_name else None
    if not firmware.is_file() or (elf is not None and not elf.is_file()):
        return None
    return Artifact(firmware=firmware, elf=elf)


def _save_arduino_artifact(build_dir: Path, fingerprint: str, artifact: Artifact) -> None:
    payload = {
        "fingerprint": fingerprint,
        "firmware": artifact.firmware.name,
        "elf": artifact.elf.name if artifact.elf else None,
    }
    temporary = build_dir / ".mcutest-build.json.tmp"
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(build_dir / ".mcutest-build.json")


def _find_arduino_firmware(build_dir: Path) -> Path | None:
    # Wokwi's Arduino flow loads application binaries at the architecture's
    # application offset and supplies its compatible simulated bootloader.
    application = _find_artifact(
        build_dir,
        ("*.bin", "*.hex", "*.uf2"),
        exclude=("*.bootloader.bin", "*.partitions.bin", "*.merged.bin"),
    )
    return application or _find_artifact(build_dir, ("*.merged.bin",))


def _stage_arduino_sketch(project: Project, build_dir: Path) -> Path:
    if not project.sketch:
        return project.root
    source_dir = project.sketch.parent
    stage = build_dir.parent / "sketches" / project.sketch.stem
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ignored = {".git", ".mcutest", ".pio", "__pycache__"}
    for source in source_dir.iterdir():
        if source.name in ignored:
            continue
        (stage / source.name).symlink_to(source, target_is_directory=source.is_dir())
    return stage


def _ensure_arduino_core(project: Project) -> None:
    if not project.fqbn:
        return
    fqbn_parts = project.fqbn.split(":")
    if len(fqbn_parts) < 2:
        raise BuildError(f"Invalid FQBN: {project.fqbn}")
    core = project.core or ":".join(fqbn_parts[:2])
    if _arduino_core_is_installed(core):
        return
    urls = project.board_urls
    if not urls and core == "esp32:esp32":
        urls = ("https://espressif.github.io/arduino-esp32/package_esp32_index.json",)
    command = ["arduino-cli", "core", "install", core]
    if urls:
        command += ["--additional-urls", ",".join(urls)]
    run(command, cwd=project.root)


def _arduino_core_is_installed(core: str) -> bool:
    requested_id, separator, requested_version = core.partition("@")
    listing = capture(["arduino-cli", "core", "list"])
    if listing == "unavailable":
        return False
    for line in listing.splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 2 and columns[0] == requested_id:
            return not separator or columns[1] == requested_version
    return False


def _find_artifact(directory: Path, patterns: tuple[str, ...], exclude: tuple[str, ...] = ()) -> Path | None:
    excluded = {path for pattern in exclude for path in directory.glob(pattern)}
    candidates = [path for pattern in patterns for path in directory.glob(pattern) if path not in excluded]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _first_existing(paths) -> Path | None:
    return next((path for path in paths if path.is_file()), None)
