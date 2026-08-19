# mcutest

`mcutest` is a Docker-contained build and Wokwi test runner for MCU firmware. The host needs only Docker. The runner detects an existing PlatformIO project, Arduino CLI sketch profile, or raw `.ino` sketch without initializing or migrating the project.

`mcutest` has no built-in application tests, peripherals, Wi-Fi assumptions, serial markers, or crash patterns. Every firmware repository owns its test definitions and explicitly states what each test constructs and asserts.

## Project layout

```text
firmware-repository/
├── .mcutest/
│   ├── project.toml
│   ├── tests/
│   │   ├── boot.toml
│   │   └── network/
│   │       └── fallback.toml
│   └── fixtures/
│       ├── circuit.json
│       └── actions.yaml
├── platformio.ini / sketch.yaml / controller.ino
└── source files and libraries
```

- `.mcutest/project.toml` contains only project-wide build and simulated-board settings.
- Every `.mcutest/tests/**/*.toml` file is one independent test.
- A test name is its relative path without `.toml`: `boot` or `network/fallback`.
- Referenced diagrams and automation files are resolved relative to the test file.

## Commands

Run commands from the firmware repository root:

```sh
mcutest inspect
mcutest build
mcutest test                    # all test files, up to 8 simulations in parallel
mcutest test -j 8               # override simulation parallelism
mcutest test boot               # one test
mcutest test network/fallback   # one nested test
```

There is no special `all` test name. Omitting the name means all tests.

## Installation on Linux

```sh
sh ./install.sh
export PATH="$HOME/.local/bin:$PATH"
export WOKWI_CLI_TOKEN='...'
```

The installer builds `mcutest:0.3.0` once and installs a small Docker launcher to `~/.local/bin`. Normal `mcutest` commands only start an ephemeral container from that ready image; they do not rebuild the image or reinstall tools. Arduino CLI, PlatformIO, Python, board packages, and Wokwi CLI remain inside the image and the bounded external cache.

Override locations when needed:

```sh
MCUTEST_IMAGE=registry.example/mcutest:0.3.0 \
MCUTEST_BIN_DIR=/usr/local/bin \
sh ./install.sh
```

## Project configuration

Preview a detected configuration with `mcutest adopt`. Writing is explicit:

```sh
mcutest adopt --write --fqbn arduino:avr:uno
```

`adopt` creates only `.mcutest/project.toml`. It never creates tests. A raw `.ino` project requires `--fqbn`; existing PlatformIO and `sketch.yaml` projects retain their own build contracts.

Illustrative Arduino CLI configuration:

```toml
[project]
adapter = "arduino-cli"
sketch = "controller.ino"
fqbn = "arduino:avr:uno"
board = "wokwi-arduino-uno"
library_dirs = ["libraries"]
```

Illustrative existing PlatformIO configuration:

```toml
[project]
adapter = "platformio"
platformio_env = "release"
board = "wokwi-arduino-uno"
```

`board` is a Wokwi diagram part type. It is required only when a test asks `mcutest` to generate `diagram.json`. A test that supplies its own diagram does not need it. Boards whose UART is not attached automatically should also declare their Wokwi pin names; mcutest then wires the generated board to `$serialMonitor`:

```toml
board = "wokwi-esp32-devkit-v1"
serial_tx = "TX0"
serial_rx = "RX0"
```

## Test files

The runner understands generic execution, serial assertions, Wokwi parts, connections, custom diagrams, and Wokwi automation. It does not attach meaning such as “sensor” or “network” to any part.

Minimal test:

```toml
[test]
timeout = 10
wall_timeout = 60
expect = ["READY"]
reject = ["FATAL"]
ordered = true
```

All fields except `[test]` are optional. `timeout` is simulated time in seconds. The optional `wall_timeout` caps real elapsed time; its default is the larger of 30 seconds and six times `timeout`. `expect` is checked in order unless `ordered = false`. Only strings explicitly listed in `reject` fail the test. The simulator is stopped as soon as every expectation matches or any rejected text appears.

A test can construct arbitrary Wokwi hardware:

```toml
[test]
timeout = 15
expect = ["INPUT_SEEN"]

[[wokwi.part]]
type = "wokwi-clock-generator"
id = "input-source"
left = -180
attrs = { frequency = "2k" }

[[wokwi.connection]]
from = "input-source:CLK"
to = "mcu:7"
color = "green"
```

The generated project board has id `mcu`. Part types, attributes, pin names, colors, and routes are copied generically into `diagram.json`.

For a complete custom circuit or timed interactions:

```toml
[test]
timeout = 30

[wokwi]
diagram = "../fixtures/circuit.json"
automation = "../fixtures/actions.yaml"
```

`diagram` cannot be combined with inline parts or connections. `automation` is passed to Wokwi CLI using `--scenario`. Tests may rely entirely on Wokwi automation for their assertions, entirely on serial `expect`/`reject`, or combine both.

To create a diagram without the project board:

```toml
[test]

[wokwi]
include_project_board = false

[[wokwi.part]]
type = "wokwi-attiny85"
id = "target"
```

## Build behavior

Detection order is conservative:

1. Existing `platformio.ini` uses PlatformIO without `pio init`.
2. Existing `sketch.yaml` uses its Arduino CLI profile.
3. A raw root `.ino` uses Arduino CLI and the FQBN from `.mcutest/project.toml`.

Sketch-folder C/C++ files compile with the sketch. Repository-local Arduino library parents can be listed in `library_dirs`.

`mcutest test` performs at most one build, then shares that immutable artifact between all selected simulations. A source/configuration fingerprint reuses the completed Arduino artifact without invoking the compiler when relevant inputs are unchanged; changed inputs fall back to Arduino's incremental cache. Tests run in parallel (`-j 8` by default) in separate workspaces. An already installed Arduino core is detected locally, so normal runs do not invoke `core install` or contact the board index.

Transient Wokwi transport failures are retried twice by default without rebuilding firmware or rerunning successful tests. Set `MCUTEST_WOKWI_RETRIES=0` to disable this.

The Docker cache has bounded retention:

- Arduino's persistent compilation cache expires unused entries after 7 days and checks for stale entries every 10 compilations.
- Downloaded board/library installation archives are deleted after a successful full compile; installed cores and libraries remain.
- Project workspaces not used for 30 days are deleted automatically.
- Each test name reuses one workspace; repeated runs do not create versioned directories.
- Containers are removed after every command and therefore do not accumulate writable layers.

Tune concurrency and retention with `MCUTEST_JOBS`, `MCUTEST_BUILD_CACHE_TTL`, and `MCUTEST_WORKSPACE_TTL_DAYS`.

## Limits

Wokwi tests firmware behavior, simulated peripherals, and the observable contracts defined by each project. It does not by itself prove RF range, physical client association, electrical signal integrity, power-supply behavior, or behavior of unsupported peripherals. Custom Wokwi Wi-Fi access points and some network functionality may require a paid Wokwi plan.

## Windows development

```powershell
.\build-image.ps1
$env:WOKWI_CLI_TOKEN = '...'
.\bin\mcutest.ps1 doctor
```

Docker Desktop must use Linux containers.

## Development checks

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
docker build -t mcutest:0.3.0 .
docker run --rm mcutest:0.3.0 doctor
```

## Upstream references

- [Wokwi CLI usage](https://docs.wokwi.com/wokwi-ci/cli-usage)
- [Wokwi automation scenarios](https://docs.wokwi.com/wokwi-ci/automation-scenarios)
- [Wokwi project configuration](https://docs.wokwi.com/vscode/project-config)
- [Wokwi supported hardware](https://docs.wokwi.com/getting-started/supported-hardware)
- [Arduino CLI sketch profiles](https://arduino.github.io/arduino-cli/latest/sketch-project-file/)
- [PlatformIO external workspace directory](https://docs.platformio.org/en/stable/projectconf/sections/platformio/options/directory/workspace_dir.html)
