$ErrorActionPreference = 'Stop'
$trackerRoot = $PSScriptRoot
$trackerPython = 'C:\Users\Chawewo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe'
if (-not (Test-Path -LiteralPath $trackerPython)) { throw 'Python runtime not found' }
$trackerUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trackerAction = New-ScheduledTaskAction -Execute $trackerPython -Argument ('"' + (Join-Path $trackerRoot 'dispatch_workflow.py') + '"') -WorkingDirectory $trackerRoot
$trackerTimer = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$trackerLogon = New-ScheduledTaskTrigger -AtLogOn -User $trackerUser
$trackerPrincipal = New-ScheduledTaskPrincipal -UserId $trackerUser -LogonType Interactive -RunLevel Limited
$trackerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
Register-ScheduledTask -TaskName 'Roblox Tracker - GitHub Collection' -Action $trackerAction -Trigger @($trackerTimer, $trackerLogon) -Principal $trackerPrincipal -Settings $trackerSettings -Description 'Dispatch Roblox collection every 15 minutes while signed in. Uses existing GitHub Git credentials; skips fresh or active collections.' -Force | Select-Object TaskName, State
