$domain = "smtihtesla"
$fqdn = "$domain.duckdns.org"
$duckdnsToken = (Get-Content "$PSScriptRoot\.env" | Where-Object { $_ -match '^DUCKDNS_TOKEN=' }) -replace 'DUCKDNS_TOKEN=', ''
$certsDir = "$PSScriptRoot\certs"

Write-Host "=== Fleet Telemetry TLS Certificate Setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Domain: $fqdn"
Write-Host "DuckDNS Token: $($duckdnsToken.Substring(0,8))..."
Write-Host ""

# Step 1: Request cert with manual DNS challenge using hooks
# We'll use certbot's manual auth hook to automate the DNS challenge

$authHook = @"
`$challenge = `$env:CERTBOT_VALIDATION
`$url = "https://www.duckdns.org/update?domains=$domain&token=$duckdnsToken&txt=`$challenge"
Invoke-RestMethod -Uri `$url | Out-Null
Write-Host "Set DuckDNS TXT record, waiting 30s for propagation..."
Start-Sleep -Seconds 30
"@

$cleanupHook = @"
`$url = "https://www.duckdns.org/update?domains=$domain&token=$duckdnsToken&txt=&clear=true"
Invoke-RestMethod -Uri `$url | Out-Null
"@

$authHookFile = "$env:TEMP\certbot-auth-hook.ps1"
$cleanupHookFile = "$env:TEMP\certbot-cleanup-hook.ps1"

Set-Content -Path $authHookFile -Value $authHook
Set-Content -Path $cleanupHookFile -Value $cleanupHook

Write-Host "Running certbot..." -ForegroundColor Yellow
certbot certonly `
    --manual `
    --preferred-challenges dns `
    --manual-auth-hook "pwsh.exe -File $authHookFile" `
    --manual-cleanup-hook "pwsh.exe -File $cleanupHookFile" `
    --agree-tos `
    --no-eff-email `
    --email smith.w.da@gmail.com `
    -d $fqdn `
    --non-interactive

if ($LASTEXITCODE -ne 0) {
    Write-Host "Certbot failed! Check the output above." -ForegroundColor Red
    exit 1
}

# Step 2: Copy certs to project
Write-Host ""
Write-Host "Copying certificates..." -ForegroundColor Yellow

if (-not (Test-Path $certsDir)) {
    New-Item -ItemType Directory -Path $certsDir | Out-Null
}

$liveDir = "C:\Certbot\live\$fqdn"
if (-not (Test-Path $liveDir)) {
    # Try alternate path on Windows
    $liveDir = "$env:LOCALAPPDATA\certbot\live\$fqdn"
}

Copy-Item "$liveDir\fullchain.pem" "$certsDir\fullchain.pem" -Force
Copy-Item "$liveDir\privkey.pem" "$certsDir\privkey.pem" -Force

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host "Certificates saved to: $certsDir"
Write-Host "  fullchain.pem"
Write-Host "  privkey.pem"
