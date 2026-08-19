from __future__ import annotations

import os
import shutil
from pathlib import Path

from .model import Artifact, Project
from .process import run


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
    firmware = _find_artifact(build_dir, ("*.bin", "*.hex", "*.uf2"), exclude=("*.bootloader.bin", "*.partitions.bin"))
    elf = _find_artifact(build_dir, ("*.elf",))
    if firmware is None:
        raise BuildError(f"Arduino CLI finished but no firmware was found in {build_dir}")
    return Artifact(firmware=firmware, elf=elf)


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
    urls = project.board_urls
    if not urls and core == "esp32:esp32":
        urls = ("https://espressif.github.io/arduino-esp32/package_esp32_index.json",)
    command = ["arduino-cli", "core", "install", core]
    if urls:
        command += ["--additional-urls", ",".join(urls)]
    run(command, cwd=project.root)


def _find_artifact(directory: Path, patterns: tuple[str, ...], exclude: tuple[str, ...] = ()) -> Path | None:
    excluded = {path for pattern in exclude for path in directory.glob(pattern)}
    candidates = [path for pattern in patterns for path in directory.glob(pattern) if path not in excluded]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _first_existing(paths) -> Path | None:
    return next((path for path in paths if path.is_file()), None)
