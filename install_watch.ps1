# Registers (or removes) the Windows scheduled task that runs the Career Brain search.
param([switch]$Remove)
$name = 'Career Brain Watch'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$name'."
    exit 0
}
$run = Join-Path $PSScriptRoot 'Run Watch.cmd'
if (-not (Test-Path $run)) { Write-Host "Run Watch.cmd not found next to this script."; exit 1 }
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$run`" quiet`"" -WorkingDirectory $PSScriptRoot
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At '08:30'),
    (New-ScheduledTaskTrigger -Daily -At '14:30')
)
# StartWhenAvailable: if the PC was off at 08:30, the run starts as soon as it is on again.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings `
    -Description 'Career Brain: daily job search, new postings only' -Force | Out-Null
$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "OK. Task '$name' runs daily at 08:30 and 14:30."
    Write-Host "Next run: $((Get-ScheduledTaskInfo -TaskName $name).NextRunTime)"
} else {
    Write-Host "Task was not created. Run this file again as the same Windows user."
    exit 1
}
