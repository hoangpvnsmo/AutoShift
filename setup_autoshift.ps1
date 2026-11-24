param([switch]$SkipNode)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create project Python environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-web.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
if (-not $SkipNode) {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $nodeMajor = & $nodeCommand.Source -p "Number(process.versions.node.split('.')[0])"
        if ([int]$nodeMajor -lt 22) { $nodeCommand = $null }
    }
    $portableNode = Get-ChildItem -LiteralPath '.runtime' -Filter node.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $nodeCommand -and -not $portableNode) {
        New-Item -ItemType Directory -Force -Path '.runtime' | Out-Null
        $nodeIndex = Invoke-RestMethod 'https://nodejs.org/dist/index.json'
        $nodeRelease = ($nodeIndex | Where-Object { $_.version -like 'v22.*' -and $_.lts } | Select-Object -First 1).version
        $nodeArchive = Join-Path $PSScriptRoot ".runtime\node-$nodeRelease-win-x64.zip"
        Invoke-WebRequest "https://nodejs.org/dist/$nodeRelease/node-$nodeRelease-win-x64.zip" -OutFile $nodeArchive -UseBasicParsing
        $nodeSums = Invoke-WebRequest "https://nodejs.org/dist/$nodeRelease/SHASUMS256.txt" -UseBasicParsing
        $expectedChecksum = (($nodeSums.Content -split "`n" | Where-Object { $_ -match " node-$nodeRelease-win-x64.zip" }) -split '\s+')[0]
        if ((Get-FileHash -LiteralPath $nodeArchive -Algorithm SHA256).Hash.ToLower() -ne $expectedChecksum) { throw 'Node.js checksum mismatch.' }
        Expand-Archive -LiteralPath $nodeArchive -DestinationPath '.runtime' -Force
        $portableNode = Get-ChildItem -LiteralPath '.runtime' -Filter node.exe -Recurse | Select-Object -First 1
    }
    if ($portableNode) { $env:Path = $portableNode.DirectoryName + ';' + $env:Path }
    Push-Location -LiteralPath 'frontend'
    try {
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
& '.\.venv\Scripts\python.exe' -m autoshift5 init
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
Write-Host 'Ready. Run: .\start_autoshift.ps1'
