<#
.SYNOPSIS
    Installs douyin-mcp into a project-local Python virtual environment.

.DESCRIPTION
    This script is idempotent: it reuses .venv and never overwrites an existing
    .env file. It does not open Chrome, start the MCP server, or change any MCP
    client configuration.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\easy-install.ps1

.EXAMPLE
    .\easy-install.ps1 -Dev -InstallChromium
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$InstallChromium
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Resolve-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) {
            return @($py.Source, '-3.11')
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) {
            return @($python.Source)
        }
    }

    throw 'Python 3.11+ was not found. Install Python 3.11 or later, then run this script again.'
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

$PythonCommand = Resolve-PythonCommand
$PythonExe = $PythonCommand[0]
$PythonArgs = @($PythonCommand | Select-Object -Skip 1)
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

Write-Host "[1/5] Using Python: $(& $PythonExe @PythonArgs --version)"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host '[2/5] Creating .venv virtual environment...'
    Invoke-CheckedNative -FilePath $PythonExe -Arguments (@($PythonArgs) + @('-m', 'venv', '.venv'))
} else {
    Write-Host '[2/5] Reusing existing .venv virtual environment.'
}

Write-Host '[3/5] Installing douyin-mcp runtime dependencies...'
Invoke-CheckedNative -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
if ($Dev) {
    Invoke-CheckedNative -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '-e', '.[dev]')
} else {
    Invoke-CheckedNative -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '-e', '.')
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host '[4/5] Created .env from .env.example.'
} else {
    Write-Host '[4/5] Keeping existing .env; it will not be overwritten.'
}

if ($InstallChromium) {
    Write-Host 'Installing Playwright Chromium...'
    Invoke-CheckedNative -FilePath $VenvPython -Arguments @('-m', 'playwright', 'install', 'chromium')
}

Write-Host '[5/5] Initializing and running diagnostics...'
Invoke-CheckedNative -FilePath $VenvPython -Arguments @('-m', 'douyin_creator_mcp.cli', 'init')
Invoke-CheckedNative -FilePath $VenvPython -Arguments @('-m', 'douyin_creator_mcp.cli', 'doctor')

Write-Host ''
Write-Host 'Installation complete. Next steps:'
Write-Host '1. Add the mcp_config from the init output to your MCP client configuration.'
Write-Host '2. For first login run: .\.venv\Scripts\douyin-mcp.exe login --timeout 180'
Write-Host '3. Read README.md for installation and MCP configuration guidance.'
