# One-time Gmail OAuth setup for JobBee INSTALLERS (not end users).
#
# End users only see "Sign in with Google" in the app — like any normal product.
# You (the person deploying JobBee) run this once, then every user gets one-click connect.
#
# Prerequisites: Google account, ~5 minutes.
#
# Steps this script walks you through:
#   1. Create a Google Cloud project (or reuse one)
#   2. Enable Gmail API
#   3. Create OAuth "Web application" credentials
#   4. Add redirect URI: http://127.0.0.1:8000/api/email/gmail/callback
#   5. Write GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to backend/.env
#
# Usage (recommended — works on Windows without changing execution policy):
#   cd backend
#   .\.venv\Scripts\python.exe scripts\setup_gmail_oauth.py
#
# Alternative (PowerShell; if blocked, use: powershell -ExecutionPolicy Bypass -File .\scripts\setup_gmail_oauth.ps1):
#   .\scripts\setup_gmail_oauth.ps1

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $BackendDir ".env"
$RedirectUri = "http://127.0.0.1:8000/api/email/gmail/callback"

Write-Host ""
Write-Host "JobBee Gmail OAuth — installer setup (one time only)" -ForegroundColor Cyan
Write-Host "End users will never see this. They only click 'Sign in with Google'." -ForegroundColor DarkGray
Write-Host ""

Write-Host "1. Open Google Cloud Console:" -ForegroundColor Yellow
Write-Host "   https://console.cloud.google.com/" -ForegroundColor White
Write-Host ""
Write-Host "2. Create or select a project, then enable Gmail API:" -ForegroundColor Yellow
Write-Host "   https://console.cloud.google.com/apis/library/gmail.googleapis.com" -ForegroundColor White
Write-Host ""
Write-Host "3. Create OAuth credentials (Web application):" -ForegroundColor Yellow
Write-Host "   https://console.cloud.google.com/apis/credentials/oauthclient" -ForegroundColor White
Write-Host "   Authorized redirect URI (copy exactly):" -ForegroundColor DarkGray
Write-Host "   $RedirectUri" -ForegroundColor Green
Write-Host ""
Write-Host "4. OAuth consent screen - add test users (required while app is in Testing):" -ForegroundColor Yellow
Write-Host "   https://console.cloud.google.com/apis/credentials/consent" -ForegroundColor White
Write-Host "   Audience / Test users -> Add users -> add the Gmail you'll sign in with" -ForegroundColor DarkGray
Write-Host "   Unverified apps can only sign in test users (fine for personal JobBee use)." -ForegroundColor DarkGray
Write-Host ""
Write-Host "5. Paste your credentials below." -ForegroundColor Yellow
Write-Host ""

$ClientId = Read-Host "Client ID"
$ClientSecret = Read-Host "Client Secret" -AsSecureString
$ClientSecretPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ClientSecret)
)

if (-not $ClientId.Trim() -or -not $ClientSecretPlain.Trim()) {
    Write-Host "Both Client ID and Client Secret are required." -ForegroundColor Red
    exit 1
}

function Set-EnvLine {
    param([string]$Path, [string]$Key, [string]$Value)
    $lines = if (Test-Path $Path) { Get-Content $Path } else { @() }
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $newLine = "$Key=$Value"
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match $pattern) {
            $found = $true
            $newLine
        } else {
            $line
        }
    }
    if (-not $found) { $out += $newLine }
    $out | Set-Content -Path $Path -Encoding utf8
}

Set-EnvLine -Path $EnvFile -Key "GMAIL_CLIENT_ID" -Value $ClientId.Trim()
Set-EnvLine -Path $EnvFile -Key "GMAIL_CLIENT_SECRET" -Value $ClientSecretPlain.Trim()
Set-EnvLine -Path $EnvFile -Key "GMAIL_OAUTH_REDIRECT_URI" -Value $RedirectUri
Set-EnvLine -Path $EnvFile -Key "FRONTEND_URL" -Value "http://localhost:5173"

Write-Host ""
Write-Host "Saved to $EnvFile" -ForegroundColor Green
Write-Host "Restart the backend, then users can click Sign in with Google." -ForegroundColor Green
Write-Host ""
