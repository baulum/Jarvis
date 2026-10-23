<#
.SYNOPSIS
  Stoppt die von scripts\windows\start.ps1 gestarteten J.A.R.V.I.S.-Dienste (samt Kindprozessen).
  PostgreSQL läuft weiter (geteilter Dienst).
#>
$ErrorActionPreference = 'Continue'
$Run = Join-Path $env:APPDATA 'Jarvis\run'
foreach ($name in @('voice', 'core', 'tts', 'whisper')) {
  $pidFile = Join-Path $Run "$name.pid"
  if (-not (Test-Path $pidFile)) { continue }
  $procId = [int](Get-Content $pidFile -Raw)
  if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
    # /T beendet auch die Kindprozesse (uv → python, Claude Code, cloudflared …)
    taskkill /PID $procId /T /F | Out-Null
    Write-Host "■ $name gestoppt"
  }
  Remove-Item $pidFile -Force
}
