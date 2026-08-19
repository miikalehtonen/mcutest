---
name: mcutest-firmware-regression
description: Develop or debug Arduino, PlatformIO, and other Wokwi-supported MCU firmware in repositories that use mcutest project-owned test files. Preserves the existing build system and runs Docker-contained firmware tests without flashing hardware.
---

# MCU firmware regression workflow

Use the repository's existing build contract. Never initialize or migrate PlatformIO merely for testing.

1. Run `mcutest inspect` at the firmware repository root.
2. Treat `.mcutest/project.toml` as build configuration only. `mcutest adopt --write` may create it when authorized, but must not generate tests.
3. Store each test independently at `.mcutest/tests/<name>.toml`. The relative path is the command name; for example `tests/network/fallback.toml` runs as `mcutest test network/fallback`.
4. Define only behavior relevant to that repository. Do not add generic boot, panic, sensor, network, or access-point tests unless the project requirements call for them.
5. Let each test explicitly define its serial expectations and rejected text. Model external inputs with generic `[[wokwi.part]]` and `[[wokwi.connection]]` entries, or reference a project-owned diagram and Wokwi automation scenario.
6. Run the narrowest relevant test with `mcutest test <name>`. Run every project test with `mcutest test` before handing off when the change warrants a full regression pass.
7. Read the reported serial log on failure. Do not claim physical RF, electrical, power, or unsupported-peripheral behavior was proven by simulation.

Building and inspection do not require `WOKWI_CLI_TOKEN`; simulation does.

