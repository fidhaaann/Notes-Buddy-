#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Start the Google Drive Bot with an ngrok public tunnel for OAuth.
    Use this when running locally but letting other users authenticate.

.USAGE
    .\start_with_ngrok.ps1
#>

# ── 1. Check ngrok is installed ───────────────────────────────────────────────
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "❌ ngrok not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install it from: https://ngrok.com/download" -ForegroundColor Yellow
    Write-Host "  1. Download the Windows zip"
    Write-Host "  2. Extract ngrok.exe to this folder (or anywhere in PATH)"
    Write-Host "  3. Run: ngrok config add-authtoken <your-token>"
    Write-Host "     (Get a free token at https://dashboard.ngrok.com/get-started/your-authtoken)"
    Write-Host ""
    exit 1
}

# ── 2. Start ngrok tunnel in background ───────────────────────────────────────
Write-Host "🚇 Starting ngrok tunnel on port 8000..." -ForegroundColor Cyan
$ngrokJob = Start-Job -ScriptBlock { ngrok http 8000 --log=stdout }
Start-Sleep -Seconds 3

# ── 3. Get the public URL from ngrok API ─────────────────────────────────────
try {
    $tunnels   = (Invoke-WebRequest "http://localhost:4040/api/tunnels" -UseBasicParsing).Content | ConvertFrom-Json
    $publicUrl = ($tunnels.tunnels | Where-Object { $_.proto -eq "https" }).public_url
} catch {
    Write-Host "❌ Could not read ngrok tunnel URL. Is ngrok running?" -ForegroundColor Red
    Stop-Job $ngrokJob
    exit 1
}

if (-not $publicUrl) {
    Write-Host "❌ No HTTPS tunnel found." -ForegroundColor Red
    Stop-Job $ngrokJob
    exit 1
}

$callbackUrl = "$publicUrl/oauth/callback"

Write-Host ""
Write-Host "✅ ngrok tunnel active!" -ForegroundColor Green
Write-Host "   Public URL : $publicUrl" -ForegroundColor White
Write-Host "   Callback   : $callbackUrl" -ForegroundColor White
Write-Host ""
Write-Host "⚠️  ACTION REQUIRED:" -ForegroundColor Yellow
Write-Host "   1. Go to Google Cloud Console → APIs & Services → Credentials"
Write-Host "   2. Edit your OAuth 2.0 Client ID"
Write-Host "   3. Add this to Authorized Redirect URIs:"
Write-Host "      $callbackUrl" -ForegroundColor Cyan
Write-Host "   4. Click Save"
Write-Host ""
Write-Host "   Then press ENTER here to start the bot." -ForegroundColor Green
Read-Host "Press ENTER when ready"

# ── 4. Update .env with the real callback URL ─────────────────────────────────
$envContent = Get-Content ".env" -Raw
$envContent = $envContent -replace "OAUTH_REDIRECT_URI=.*", "OAUTH_REDIRECT_URI=$callbackUrl"
Set-Content ".env" $envContent -NoNewline
Write-Host "✅ .env updated: OAUTH_REDIRECT_URI=$callbackUrl" -ForegroundColor Green

# ── 5. Start the bot ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "🤖 Starting bot..." -ForegroundColor Cyan
venv\Scripts\python.exe main.py
