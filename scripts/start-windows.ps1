param(
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 18790,
  [int]$MaxRepairAttempts = 3
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $env:USERPROFILE ".copenclaw\.logs"

function Ensure-CopilotCli {
  if (Get-Command copilot -ErrorAction SilentlyContinue) {
    return
  }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Warning "Copilot CLI not found and winget unavailable; skipping Copilot auto-heal session."
    return
  }
  Write-Host "Installing GitHub Copilot CLI via winget..."
  & winget install GitHub.Copilot --accept-source-agreements --accept-package-agreements | Out-Host
}

function Test-CopilotAuthReady {
  if ($env:GH_TOKEN -or $env:GITHUB_TOKEN) {
    return $true
  }
  if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
    return $false
  }
  & copilot auth status *> $null
  return ($LASTEXITCODE -eq 0)
}

function Invoke-CopilotAutohealSession {
  param([string]$IssueDescription)

  if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
    Write-Warning "Copilot CLI unavailable; skipping Copilot auto-heal session."
    return
  }
  if (-not (Test-CopilotAuthReady)) {
    Write-Warning "Copilot auth not ready; skipping Copilot auto-heal session."
    return
  }

  $prompt = "You are a COpenClaw Windows startup auto-repair agent. Project path: $ProjectDir. Workspace path: $env:copenclaw_WORKSPACE_DIR. Issue: $IssueDescription. Diagnose startup issues, apply safe fixes, run validation checks, and summarize what you changed."
  Write-Host "Running Copilot auto-heal session..."
  & copilot --add-dir $ProjectDir --add-dir $env:copenclaw_WORKSPACE_DIR --add-dir $LogDir --autopilot --yolo --no-ask-user -s -p $prompt | Out-Host
}

function Invoke-CopenclawRepair {
  param(
    [string]$IssueDescription,
    [string]$PythonExe
  )
  Write-Host "Running built-in COpenClaw repair..."
  & $PythonExe -m copenclaw.cli repair --description $IssueDescription | Out-Host
}

Push-Location $ProjectDir
try {
  if (-not $env:copenclaw_WORKSPACE_DIR -or [string]::IsNullOrWhiteSpace($env:copenclaw_WORKSPACE_DIR)) {
    $env:copenclaw_WORKSPACE_DIR = Join-Path $env:USERPROFILE ".copenclaw"
  }
  if (-not (Test-Path $env:copenclaw_WORKSPACE_DIR)) {
    New-Item -ItemType Directory -Path $env:copenclaw_WORKSPACE_DIR | Out-Null
  }
  if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
  }

  if (-Not (Test-Path ".venv")) {
    Write-Host "Creating venv..."
    python -m venv .venv
  }

  $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
  Write-Host "Installing/refreshing dependencies..."
  & $venvPython -m pip install -e . --quiet
  if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
      throw "Dependency installation failed"
    }
  }

  Ensure-CopilotCli

  $attempt = 0
  while ($true) {
    $attempt++
    Write-Host "Starting COpenClaw (attempt $attempt)..."
    $env:copenclaw_AUTO_REPAIR_ON_STARTUP = "1"
    & $venvPython -m copenclaw.cli serve --accept-risks --host $BindHost --port $Port
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    if ($exitCode -eq 0) {
      break
    }
    if ($attempt -ge [Math]::Max(1, $MaxRepairAttempts)) {
      throw "COpenClaw exited with code $exitCode after $attempt attempts."
    }
    $issue = "Auto-heal launcher saw copenclaw.cli serve exit code $exitCode on attempt $attempt."
    Invoke-CopilotAutohealSession -IssueDescription $issue
    Invoke-CopenclawRepair -IssueDescription $issue -PythonExe $venvPython
    Start-Sleep -Seconds 2
  }
} finally {
  Pop-Location
}
