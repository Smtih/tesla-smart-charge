Stop-ScheduledTask -TaskName 'Tesla Smart-Charge' -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName 'Tesla Smart-Charge'
Write-Host 'Tesla Smart-Charge restarted.'
