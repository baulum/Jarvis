<#
.SYNOPSIS
  J.A.R.V.I.S. auf Windows einrichten: prüft Abhängigkeiten, lädt Modelle, richtet Client, Device Agent und/oder
  Core ein. Alle Parameter werden an den Installer (installer/jarvis_installer) weitergereicht.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1
  powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1 --check
  powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1 --roles device --core-url https://100.101.102.103:8767 --code <Code> --fingerprint <SHA-256>
#>
$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..\..')

$build = [Environment]::OSVersion.Version.Build
if ($build -lt 17763) {
  Write-Host "Windows 10 1809 oder neuer wird benötigt (gefunden: Build $build)." -ForegroundColor Red
  exit 1
}

function Find-Uv {
  $cmd = Get-Command uv -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  foreach ($p in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

$uv = Find-Uv
if (-not $uv) {
  Write-Host "uv (Python-Umgebungen) fehlt – der Installer braucht es." -ForegroundColor Yellow
  $answer = Read-Host "Jetzt installieren? Quelle: https://astral.sh/uv/install.ps1 (J/n)"
  if ($answer -and $answer -notmatch '^(j|ja|y|yes)$') { exit 1 }
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id astral-sh.uv --exact --silent --accept-package-agreements --accept-source-agreements
  } else {
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  }
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
  $uv = Find-Uv
  if (-not $uv) { Write-Host "uv konnte nicht installiert werden." -ForegroundColor Red; exit 1 }
}

$env:PYTHONPATH = Join-Path $Root 'installer'
& $uv run --no-project --python 3.12 python -m jarvis_installer --repo $Root @args
exit $LASTEXITCODE
