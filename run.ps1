<#
.SYNOPSIS
    Windows entrypoint for the website-designers cold-email sales pipeline.
    PowerShell twin of run.py, plus one-command setup.

.DESCRIPTION
    Modes:
        .\run.ps1 setup                    # venv + deps + Playwright Chromium + .env scaffold + config check
        .\run.ps1 quick-test               # one lead through the whole pipeline, once (pipeline/quick_run.py)
        .\run.ps1 loop                     # always-on orchestrator + operator console (pipeline/main.py)
        .\run.ps1 dashboard                # Streamlit monitoring UI (dashboard.py)
        .\run.ps1 webhook                  # Flask webhook server: /click, /unsubscribe, /webhook/stripe
        .\run.ps1 test-email you@example.com   # build+deploy a dummy preview and email it to you
        .\run.ps1 ingest path\to\leads.csv     # load a CSV of leads (business_name,niche,location)
        .\run.ps1 test                     # run the offline unit test suite (pytest)
        .\run.ps1 help                     # this text

    If script execution is blocked on your machine, run once per session:
        powershell -ExecutionPolicy Bypass -File .\run.ps1 setup
    or permanently for your user:
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

.NOTES
    Requires Python 3.10+ on PATH (the `py` launcher or `python`).
    All state lives in the repo: venv\, .env, pipeline\leads.db.
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'quick-test', 'loop', 'dashboard', 'webhook', 'test-email', 'ingest', 'test', 'help')]
    [string]$Mode = 'help',

    # Extra argument for modes that need one (test-email <address>, ingest <csv>).
    [Parameter(Position = 1)]
    [string]$ModeArg = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot   = $PSScriptRoot
$VenvDir    = Join-Path $RepoRoot 'venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$EnvFile    = Join-Path $RepoRoot '.env'
$EnvExample = Join-Path $RepoRoot '.env.example'
$PipelineDir = Join-Path $RepoRoot 'pipeline'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
    Write-Host "!!  $Message" -ForegroundColor Yellow
}

function Get-SystemPython {
    # Prefer the Windows py launcher; fall back to python on PATH.
    if (Get-Command py -ErrorAction SilentlyContinue)     { return @('py', '-3') }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @('python') }
    throw 'Python 3 was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and re-run.'
}

function Invoke-Python {
    # Run the venv's python with the given arguments; fail the script on a
    # non-zero exit code (native commands do not throw on their own).
    param([Parameter(Mandatory)][string[]]$Arguments, [string]$WorkingDir = $RepoRoot)
    Push-Location $WorkingDir
    try {
        & $VenvPython @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed (exit $LASTEXITCODE): python $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-Setup {
    if (-not (Test-Path $VenvPython)) {
        Write-Step 'Creating virtual environment (venv\)'
        $sysPy = Get-SystemPython
        $exe   = $sysPy[0]
        $extra = @($sysPy | Select-Object -Skip 1)
        & $exe @extra -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create the virtual environment.' }
    }
    else {
        Write-Step 'Virtual environment already exists (venv\)'
    }

    Write-Step 'Installing dependencies (pipeline\requirements.txt)'
    Invoke-Python @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip')
    Invoke-Python @('-m', 'pip', 'install', '--quiet', '-r', (Join-Path $PipelineDir 'requirements.txt'))

    Write-Step 'Installing Playwright Chromium (preview screenshots)'
    Invoke-Python @('-m', 'playwright', 'install', 'chromium')

    if (-not (Test-Path $EnvFile)) {
        Write-Step 'Creating .env from .env.example'
        Copy-Item $EnvExample $EnvFile
        Write-Warn '.env was just created with EMPTY values. Open it and fill in your real keys before running anything else.'
        Write-Warn "File: $EnvFile"
    }
    else {
        Write-Step '.env already exists -- checking required variables'
        # config.py run directly prints exactly which required variables are missing.
        Push-Location $PipelineDir
        try {
            & $VenvPython 'config.py'
            if ($LASTEXITCODE -ne 0) {
                Write-Warn 'Fill in the missing values above, then re-run: .\run.ps1 setup'
                exit 1
            }
        }
        finally {
            Pop-Location
        }
    }

    Write-Host ''
    Write-Host 'Setup complete. Next steps:' -ForegroundColor Green
    Write-Host '    .\run.ps1 ingest test_lead.csv     # load the 3 sample leads'
    Write-Host '    .\run.ps1 quick-test               # watch one lead go end-to-end'
    Write-Host '    .\run.ps1 loop                     # start the always-on pipeline'
}

function Assert-Ready {
    if (-not (Test-Path $VenvPython)) {
        throw "No virtual environment found. Run '.\run.ps1 setup' first."
    }
    if (-not (Test-Path $EnvFile)) {
        throw "No .env file found. Run '.\run.ps1 setup' first, then fill in your keys."
    }
}

switch ($Mode) {
    'setup' {
        Invoke-Setup
    }
    'quick-test' {
        Assert-Ready
        Invoke-Python @('run.py', 'quick-test')
    }
    'loop' {
        Assert-Ready
        # main.py reads operator commands (takeover / payment ready / transfer)
        # from this same console while the loop runs.
        Invoke-Python @('run.py', 'loop')
    }
    'dashboard' {
        Assert-Ready
        Invoke-Python @('run.py', 'dashboard')
    }
    'webhook' {
        Assert-Ready
        # Dev server on http://localhost:5000 -- pair with ngrok for Stripe
        # webhooks during development (see README "Deployment").
        Invoke-Python @('webhook_server.py') -WorkingDir $PipelineDir
    }
    'test-email' {
        Assert-Ready
        if (-not $ModeArg) {
            Write-Warn 'Usage: .\run.ps1 test-email you@example.com'
            exit 1
        }
        Invoke-Python @('run.py', 'test-email', $ModeArg)
    }
    'ingest' {
        Assert-Ready
        if (-not $ModeArg -or -not (Test-Path $ModeArg)) {
            Write-Warn 'Usage: .\run.ps1 ingest path\to\leads.csv   (columns: business_name,niche,location)'
            exit 1
        }
        $csvFullPath = (Resolve-Path $ModeArg).Path
        Invoke-Python @('-m', 'agents.lead_agent', $csvFullPath) -WorkingDir $PipelineDir
    }
    'test' {
        Assert-Ready
        Invoke-Python @('-m', 'pytest', 'tests', '-q')
    }
    default {
        Get-Help $PSCommandPath -Detailed
    }
}
