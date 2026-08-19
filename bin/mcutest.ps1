param([Parameter(ValueFromRemainingArguments = $true)][string[]]$McutestArgs)

. "$PSScriptRoot\docker-path.ps1"
$docker = Resolve-DockerExecutable
$image = if ($env:MCUTEST_IMAGE) { $env:MCUTEST_IMAGE } else { 'mcutest:0.3.0' }
$cache = if ($env:MCUTEST_HOST_CACHE) { $env:MCUTEST_HOST_CACHE } else { Join-Path $env:LOCALAPPDATA 'mcutest-cache' }
New-Item -ItemType Directory -Force -Path $cache | Out-Null

& $docker run --rm -i --init `
  -e WOKWI_CLI_TOKEN `
  -e ARDUINO_DIRECTORIES_DATA=/cache/arduino/data `
  -e ARDUINO_DIRECTORIES_DOWNLOADS=/cache/arduino/downloads `
  -e ARDUINO_DIRECTORIES_USER=/cache/arduino/user `
  -e ARDUINO_BUILD_CACHE_PATH=/cache/arduino/build-cache `
  -e ARDUINO_BUILD_CACHE_TTL=168h `
  -e ARDUINO_BUILD_CACHE_COMPILATIONS_BEFORE_PURGE=10 `
  -e PLATFORMIO_CORE_DIR=/cache/platformio `
  -e MCUTEST_CACHE=/cache/workspaces `
  -e MCUTEST_JOBS=8 `
  -e MCUTEST_WORKSPACE_TTL_DAYS=30 `
  -e MCUTEST_WOKWI_RETRIES=2 `
  -e "MCUTEST_PROJECT_KEY=$PWD" `
  -v "${PWD}:/workspace" `
  -v "${cache}:/cache" `
  -w /workspace `
  $image @McutestArgs
exit $LASTEXITCODE
