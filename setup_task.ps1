$action = New-ScheduledTaskAction `
    -Execute 'C:\Users\smith\scoop\apps\python\current\python.exe' `
    -Argument '"C:\Users\smith\Tesla App\main.py"' `
    -WorkingDirectory 'C:\Users\smith\Tesla App'

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

Unregister-ScheduledTask -TaskName 'Tesla Smart-Charge' -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName 'Tesla Smart-Charge' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Tesla Smart-Charge Daycare-Proof Manager'
