function Resolve-DockerExecutable {
  $command = Get-Command docker -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  $candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
    'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return $candidate
    }
  }
  throw 'Docker CLI was not found. Start a new terminal after installing Docker Desktop.'
}

