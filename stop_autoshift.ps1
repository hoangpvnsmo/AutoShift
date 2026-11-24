$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot '.runtime\server-state.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No owned server state.'; exit 0 }
$serverState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
try {
    $runningJobs = Invoke-RestMethod "http://127.0.0.1:$($serverState.port)/api/jobs"
    foreach ($solverJob in $runningJobs) {
        if ($solverJob.status -in @('QUEUED','RUNNING','CANCELLING')) {
            Invoke-RestMethod "http://127.0.0.1:$($serverState.port)/api/jobs/$($solverJob.id)/cancel" -Method Post -ContentType 'application/json' -Body '{}' | Out-Null
        }
    }
} catch { Write-Host 'Server is already offline; the next startup invalidates unfinished jobs.' }
foreach ($ownedId in $serverState.process_ids) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedId" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -and ($process.CommandLine.Contains('autoshift5.api:app') -or $process.CommandLine.Contains('node_modules/vite/bin/vite.js'))) {
        Stop-Process -Id $ownedId -ErrorAction SilentlyContinue
    }
}
Remove-Item -LiteralPath $statePath
Write-Host 'Owned AutoShift servers stopped.'
