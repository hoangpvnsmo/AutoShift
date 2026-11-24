param([switch]$Dev, [int]$Port = 8765, [switch]$NoImport)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup_autoshift.ps1 first.' }
$existingServer = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($existingServer) { Write-Host "Port $Port already in use. Check http://127.0.0.1:$Port"; exit 0 }
& $pythonPath -m autoshift5 init
if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
if (-not $NoImport) {
    & $pythonPath -m autoshift5.bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Legacy import failed. See the report above.' }
}
if (-not $Dev -and -not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Run setup_autoshift.ps1 to build the frontend, or use -Dev.' }
New-Item -ItemType Directory -Force -Path '.runtime' | Out-Null
$backendProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8','-m','uvicorn','autoshift5.api:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput '.runtime\backend.log' -RedirectStandardError '.runtime\backend-error.log' -PassThru
$ownedProcesses = @($backendProcess.Id)
if ($Dev) {
    $portableNode = Get-ChildItem -LiteralPath '.runtime' -Filter node.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($portableNode) { $env:Path = $portableNode.DirectoryName + ';' + $env:Path }
    $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
    $devProcess = Start-Process -FilePath $nodePath -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173') -WorkingDirectory (Join-Path $PSScriptRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput '.runtime\frontend.log' -RedirectStandardError '.runtime\frontend-error.log' -PassThru
    $ownedProcesses += $devProcess.Id
}
@{process_ids=$ownedProcesses; port=$Port; started=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath '.runtime\server-state.json' -Encoding UTF8
$serverReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { $response = Invoke-RestMethod "http://127.0.0.1:$Port/api/health"; $serverReady = $true; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $serverReady) { throw 'Backend did not start. Read .runtime\backend-error.log.' }
if ($Dev) { Write-Host 'Frontend: http://127.0.0.1:5173' } else { Write-Host "AutoShift: http://127.0.0.1:$Port" }
Write-Host "API docs: http://127.0.0.1:$Port/docs"
Write-Host 'Stop only these owned servers with: .\stop_autoshift.ps1'
