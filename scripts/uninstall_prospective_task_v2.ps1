$ErrorActionPreference = "Stop"
schtasks /Delete /TN "BOATRACE-Prospective-Shadow-V2" /F
exit $LASTEXITCODE
