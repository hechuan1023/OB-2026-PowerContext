param(
    [Parameter(Mandatory = $true)][string]$InputDir,
    [string]$CasesFile = "",
    [string]$OutputDir = "",
    [string]$ProductDir = "",
    [string]$Image = "powercontext-competition-selftest:2026"
)

$ErrorActionPreference = "Stop"
$runnerRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProductDir) { $ProductDir = $runnerRoot }
if (-not $CasesFile) { $CasesFile = Join-Path $InputDir "cases.json" }
if (-not $OutputDir) { $OutputDir = Join-Path $InputDir "results" }
$inputPath = (Resolve-Path -LiteralPath $InputDir).Path
$casesPath = (Resolve-Path -LiteralPath $CasesFile).Path
$productPath = (Resolve-Path -LiteralPath $ProductDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $productPath "src/powercontext"))) {
    throw "ProductDir must contain src/powercontext: $productPath"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$outputPath = (Resolve-Path -LiteralPath $OutputDir).Path
foreach ($path in @($inputPath, $casesPath, $productPath, $outputPath, $PSScriptRoot)) {
    if ($path.Contains(",")) { throw "Docker bind-mount paths must not contain commas: $path" }
}
& docker image inspect $Image *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker image '$Image' is unavailable. Start Docker Desktop using the Linux engine and select the existing selftest image."
}
$candidateCommit = (& git -C $productPath rev-parse HEAD).Trim()
$candidateChanges = & git -C $productPath status --porcelain -- src
$candidateDirty = if ($candidateChanges) { "true" } else { "false" }
$dockerArgs = @(
    "run", "--rm", "--network", "none", "--cpus", "1", "--memory", "2g",
    "--pids-limit", "512", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
    "--mount", "type=bind,source=$productPath/src,target=/candidate/src,readonly",
    "--mount", "type=bind,source=$PSScriptRoot/pdf_selfcheck.py,target=/runner.py,readonly",
    "--mount", "type=bind,source=$inputPath,target=/input,readonly",
    "--mount", "type=bind,source=$casesPath,target=/cases.json,readonly",
    "--mount", "type=bind,source=$outputPath,target=/output",
    "-e", "PYTHONPATH=/candidate/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
    "-e", "PYTHONHASHSEED=0", "-e", "OMP_NUM_THREADS=1", "-e", "OPENBLAS_NUM_THREADS=1",
    "-e", "CANDIDATE_COMMIT=$candidateCommit", "-e", "CANDIDATE_DIRTY=$candidateDirty",
    "--entrypoint", "/opt/selftest-venv/bin/python", $Image,
    "/runner.py", "--input", "/input", "--cases", "/cases.json", "--output", "/output"
)
Write-Host "Candidate: $candidateCommit; modified product source: $candidateDirty"
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw "PDF selfcheck failed. See error.json under $outputPath" }
Write-Host "Open report.html in the newest run folder under: $outputPath"
