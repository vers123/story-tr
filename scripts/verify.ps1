#Requires -Version 5.1
<#
.SYNOPSIS
    一键质量校验：ruff check、ruff format --check、mypy、pytest --cov 四步串联。
.DESCRIPTION
    自动优先使用仓库内 .venv 的解释器（缺失时回退到 python）。
    每步打印标题，任何一步失败都不会中断后续步骤，最后统一汇总并以非零退出码结束。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
#>

$ErrorActionPreference = "Continue"

$script:failedSteps = @()
$exitCode = 1

function Invoke-VerifyStep {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$StepArgs
    )

    Write-Host ""
    Write-Host ("=== " + $Label + " ===") -ForegroundColor Cyan

    & $py @StepArgs
    $code = $LASTEXITCODE

    if ($code -ne 0) {
        Write-Host ("[失败] " + $Label + " (exit " + $code + ")") -ForegroundColor Red
        $script:failedSteps += $Label
    }
    else {
        Write-Host ("[通过] " + $Label) -ForegroundColor Green
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot

try {
    $venvPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $py = (Resolve-Path -LiteralPath $venvPython).Path
    }
    else {
        $py = "python"
    }

    Write-Host ("解释器: " + $py) -ForegroundColor Cyan

    Invoke-VerifyStep -Label "[1/4] ruff check" -StepArgs @("-m", "ruff", "check", ".")
    Invoke-VerifyStep -Label "[2/4] ruff format --check" -StepArgs @("-m", "ruff", "format", "--check", ".")
    Invoke-VerifyStep -Label "[3/4] mypy src/story_tr" -StepArgs @("-m", "mypy", "src/story_tr")
    Invoke-VerifyStep -Label "[4/4] pytest --cov" -StepArgs @("-m", "pytest", "-q", "--cov=story_tr", "--cov-report=term-missing")

    Write-Host ""
    if ($script:failedSteps.Count -eq 0) {
        Write-Host "全部检查通过。" -ForegroundColor Green
        $exitCode = 0
    }
    else {
        Write-Host ("校验失败，共 " + $script:failedSteps.Count + " 项：") -ForegroundColor Red
        foreach ($step in $script:failedSteps) {
            Write-Host ("  - " + $step) -ForegroundColor Red
        }
        $exitCode = 1
    }
}
finally {
    Pop-Location
}

exit $exitCode
