param([string]$Image = 'mcutest:0.2.0')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$here\bin\docker-path.ps1"
$docker = Resolve-DockerExecutable
& $docker build --pull -t $Image $here
exit $LASTEXITCODE
