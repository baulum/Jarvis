<#
.SYNOPSIS
  Startet den J.A.R.V.I.S.-Core auf Windows: Spracherkennung (whisper-server), lokale Stimme (Piper), Core,
  Voice-Daemon. Gegenstück zu scripts/start.sh auf dem Mac; die Desktop-App ruft es beim Start auf.
  Ist der Rechner nur Client (client.json vorhanden), startet die App keine Dienste.

.PARAMETER NoVoice  Voice-Daemon (Mikrofon, "Hey Jarvis") nicht starten.
.PARAMETER NoApp    Desktop-App nicht öffnen.
#>
param([switch]$NoVoice, [switch]$NoApp)
$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$Data = Join-Path $env:APPDATA 'Jarvis'
$Logs = Join-Path $env:LOCALAPPDATA 'Jarvis\Logs'
$Run = Join-Path $Data 'run'
$Models = Join-Path $Data 'models'
New-Item -ItemType Directory -Force -Path $Logs, $Run | Out-Null
$env:Path = "$env:USERPROFILE\.local\bin;$(Join-Path $Data 'bin');$env:Path"

function Test-Running([string]$Name) {
  $pidFile = Join-Path $Run "$Name.pid"
  if (-not (Test-Path $pidFile)) { return $false }
  $procId = [int](Get-Content $pidFile -Raw)
  return [bool](Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

function Start-Service-Process([string]$Name, [string]$File, [string[]]$Arguments, [string]$WorkDir = $Root) {
  if (Test-Running $Name) { Write-Host "✓ $Name läuft bereits"; return }
  $out = Join-Path $Logs "$Name.log"
  $err = Join-Path $Logs "$Name.err.log"
  $p = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $WorkDir -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
  Set-Content -Path (Join-Path $Run "$Name.pid") -Value $p.Id
  Write-Host "→ $Name gestartet (pid $($p.Id))"
}

function Wait-Http([string]$Url, [int]$Seconds) {
  for ($i = 0; $i -lt $Seconds; $i++) {
    try { Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true } catch { Start-Sleep 1 }
  }
  Write-Host "✗ $Url nicht erreichbar"
  return $false
}

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { Write-Host "uv fehlt – scripts\windows\install.ps1 ausführen" -ForegroundColor Red; exit 1 }

# Spracherkennung: großes Modell, sonst das kleine (der Installer lädt eines davon)
$whisper = (Get-Command whisper-server -ErrorAction SilentlyContinue).Source
$model = @('ggml-large-v3-turbo-q5_0.bin', 'ggml-small-q5_1.bin') | ForEach-Object { Join-Path $Models $_ } |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if ($whisper -and $model) {
  Start-Service-Process 'whisper' $whisper @('-m', "`"$model`"", '--host', '127.0.0.1', '--port', '8178', '-l', 'de', '-t', '4', '-ac', '768', '-nf')
} else {
  Write-Host "ℹ whisper-server oder Modell fehlt – Spracherkennung aus (oder voice.stt.server_url auf den Mac)"
}

# Lokale Stimme (Piper) auf dem Port der Mac-Stimme
$ttsPy = Join-Path $Data 'tts-local\.venv\Scripts\python.exe'
$voice = Join-Path $Models 'piper\de_DE-thorsten-high.onnx'
if ((Test-Path $ttsPy) -and (Test-Path $voice)) {
  Start-Service-Process 'tts' $ttsPy @('-I', "`"$(Join-Path $Root 'scripts\local-tts\piper_server.py')`"", '--port', '8179', '--model', "`"$voice`"")
}

Start-Service-Process 'core' $uv @('run', '--frozen', 'jarvisctl', 'serve') (Join-Path $Root 'core')
if (Wait-Http 'http://127.0.0.1:8765/api/health' 90) { Write-Host "✓ Core bereit" }

if (-not $NoVoice) {
  Start-Service-Process 'voice' $uv @('run', '--frozen', 'jarvisctl', 'voice') (Join-Path $Root 'core')
}

if (-not $NoApp) {
  $app = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs", "$env:ProgramFiles" -Filter 'J.A.R.V.I.S..exe' -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($app) { Start-Process $app.FullName; Write-Host "→ Desktop-App geöffnet" } else { Write-Host "ℹ Desktop-App nicht installiert" }
}
Write-Host "Logs: $Logs"
