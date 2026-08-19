from __future__ import annotations

from pathlib import Path

from .config import ConfigError, detect_project


def render_project_config(root: Path, fqbn: str | None = None) -> str:
    project = detect_project(root)
    lines = ["[project]", f'adapter = "{project.adapter}"']
    if project.sketch:
        lines.append(f'sketch = "{project.sketch.relative_to(root).as_posix()}"')
    if project.adapter == "arduino-cli" and not (root / "sketch.yaml").is_file():
        if not fqbn:
            raise ConfigError("A raw Arduino sketch needs --fqbn when creating .mcutest/project.toml")
        lines.append(f'fqbn = "{fqbn}"')
        if fqbn.startswith("esp32:esp32:"):
            lines.append('core = "esp32:esp32"')
            lines.append('board_urls = ["https://espressif.github.io/arduino-esp32/package_esp32_index.json"]')
        lines.append('# library_dirs = ["libraries"]')
    else:
        lines.append('# platformio_env = "environment-name"')
    lines += [
        "",
        "# Required only for tests that ask mcutest to generate diagram.json.",
        '# board = "wokwi-board-part-name"',
    ]
    return "\n".join(lines) + "\n"

