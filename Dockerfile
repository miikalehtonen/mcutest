FROM python:3.12-slim-bookworm

ARG ARDUINO_CLI_VERSION=1.5.1
ARG PLATFORMIO_VERSION=6.1.19
ARG WOKWI_CLI_VERSION=0.26.1

ENV DEBIAN_FRONTEND=noninteractive \
    ARDUINO_UPDATER_ENABLE_NOTIFICATION=false \
    ARDUINO_DIRECTORIES_DATA=/cache/arduino/data \
    ARDUINO_DIRECTORIES_DOWNLOADS=/cache/arduino/downloads \
    ARDUINO_DIRECTORIES_USER=/cache/arduino/user \
    PLATFORMIO_CORE_DIR=/cache/platformio \
    MCUTEST_CACHE=/cache/workspaces \
    PATH=/root/bin:/root/.wokwi/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
    | BINDIR=/usr/local/bin sh -s "$ARDUINO_CLI_VERSION"

RUN python -m pip install --no-cache-dir "platformio==$PLATFORMIO_VERSION"

RUN curl -fsSL https://wokwi.com/ci/install.sh | sh -s "$WOKWI_CLI_VERSION" \
    && install -m 0755 /root/.wokwi/bin/wokwi-cli /usr/local/bin/wokwi-cli

WORKDIR /opt/mcutest
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

WORKDIR /workspace
ENTRYPOINT ["mcutest"]
