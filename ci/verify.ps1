$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$Python = if ($env:PDP_PYTHON) { $env:PDP_PYTHON } else { "python" }
$ArtifactRoot = Join-Path $ProjectRoot "dist-package"
New-Item -ItemType Directory -Path $ArtifactRoot -Force | Out-Null
$PytestReport = Join-Path $ArtifactRoot "pytest-results.xml"
$VitestReport = Join-Path $ArtifactRoot "vitest-results.json"
$Vitest = Join-Path $ProjectRoot "node_modules/vitest/vitest.mjs"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

& $Python scripts/generate_tool_contracts.py --root . --check
Assert-LastExitCode "Generated tool contracts"

& $Python scripts/lint_skill.py `
    --skill skills/personal-diet-pantry `
    --cases tests/skill-evals/routing.yaml
Assert-LastExitCode "Skill routing lint"

& $Python scripts/validate_behavior_trace.py `
    tests/fixtures/traces/packaged-soy-one-box.json
Assert-LastExitCode "Packaged pantry behavior trace"

& $Python scripts/scan_sensitive_content.py .
Assert-LastExitCode "Sensitive content scan"

$CoreTestList = Join-Path $ProjectRoot "contracts/v070-core-tests.txt"
$CoreTests = @(
    Get-Content -LiteralPath $CoreTestList |
        Where-Object {
            $_.Trim() -and -not $_.TrimStart().StartsWith("#")
        }
)
$CorePythonTests = @($CoreTests | Where-Object { $_ -like "tests/*" })
$CoreTypeScriptTests = @($CoreTests | Where-Object { $_ -like "src-tests/*" })
& $Python -m pytest -p no:cacheprovider @CorePythonTests -q
Assert-LastExitCode "v0.7.5.4 core behavior gate"

& npm run build
Assert-LastExitCode "TypeScript build"

& node $Vitest run @CoreTypeScriptTests
Assert-LastExitCode "v0.7.5.4 core behavior gate"

& $Python -m pytest -q "--junitxml=$PytestReport"
Assert-LastExitCode "Python tests"

& node $Vitest run --reporter=json "--outputFile=$VitestReport"
Assert-LastExitCode "TypeScript tests"

& $Python -m compileall -q python scripts
Assert-LastExitCode "Python compileall"

& $Python scripts/validate_skill.py
Assert-LastExitCode "Skill validation"

$ReleaseAudit = & $Python scripts/release_audit.py .
$ReleaseAuditExit = $LASTEXITCODE
$ReleaseAudit | Set-Content -LiteralPath (Join-Path $ArtifactRoot "release-audit.json") -Encoding UTF8
$ReleaseAudit | Write-Output
if ($ReleaseAuditExit -ne 0) {
    throw "Release audit failed with exit code $ReleaseAuditExit"
}

& npm pack --dry-run --json | Out-Null
Assert-LastExitCode "npm pack dry run"

& $Python -m pytest `
    tests/integration/test_bridge_e2e.py `
    tests/integration/test_upgrade_e2e.py `
    tests/integration/test_installable_e2e.py `
    -q
Assert-LastExitCode "Integration tests"

& npm audit --omit=dev --audit-level=high
Assert-LastExitCode "Production dependency audit"

$DependencyAuditPath = Join-Path $ArtifactRoot "dependency-audit.json"
& npm audit --json 2>$null |
    Set-Content -LiteralPath $DependencyAuditPath -Encoding UTF8

& $Python scripts/validate_dependency_audit.py `
    --audit $DependencyAuditPath `
    --acceptance contracts/dependency-risk-acceptance.json
Assert-LastExitCode "Development dependency acceptance"
