<#
.SYNOPSIS
按 Git 改动范围或全量执行项目校验。

.DESCRIPTION
Changed 模式只选择改动相关的检查，不自动转为全量。Full 必须显式指定。
PlanOnly 输出检查计划，不运行检查。测试使用唯一临时目录，成功后尝试清理，
失败时保留以便诊断；清理权限错误不改变检查结果。
#>
[CmdletBinding()]
param(
    [ValidateSet("Changed", "Full")]
    [string]$Scope = "Changed",

    [ValidateSet("Worktree", "Staged")]
    [string]$Source = "Worktree",

    [string[]]$Files = @(),

    [string]$VenvPath = ".venv",

    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"

function Add-UniqueTarget {
    param([System.Collections.Generic.List[string]]$Targets, [string]$Target)
    $Target = $Target.Replace("\", "/")
    if (-not $Targets.Contains($Target)) {
        $Targets.Add($Target)
    }
}

function Get-RunDirectory {
    param([string]$RepositoryRoot, [string]$RunName)
    $temporaryRoot = [IO.Path]::GetFullPath((Join-Path $RepositoryRoot "tmp\pytest"))
    $runDirectory = [IO.Path]::GetFullPath((Join-Path $temporaryRoot $RunName))
    $prefix = "$temporaryRoot$([IO.Path]::DirectorySeparatorChar)"
    if (-not $runDirectory.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "测试临时目录必须位于 $temporaryRoot 下。"
    }
    return $runDirectory
}

$repositoryRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repositoryRoot) {
    throw "当前目录不在 Git 仓库中。"
}
$repositoryRoot = [IO.Path]::GetFullPath($repositoryRoot)
Set-Location $repositoryRoot
$venvRoot = if ([IO.Path]::IsPathRooted($VenvPath)) { [IO.Path]::GetFullPath($VenvPath) } else { [IO.Path]::GetFullPath((Join-Path $repositoryRoot $VenvPath)) }
# 显式指定外部运行时只复用工具和依赖，不安装或修改其 editable 配置。
$venvScripts = Join-Path $venvRoot "Scripts"
$ruffPath = Join-Path $venvScripts "ruff.exe"
$pyrightPath = Join-Path $venvScripts "pyright.exe"
$pythonPath = Join-Path $venvScripts "python.exe"
if ($Scope -eq "Full") {
    $changedFiles = @()
} elseif ($Files.Count -gt 0) {
    $changedFiles = $Files
} elseif ($Source -eq "Staged") {
    $changedFiles = @(& git -c core.quotepath=false diff --cached --name-only)
} else {
    $changedFiles = @(& git -c core.quotepath=false diff --name-only HEAD)
    $changedFiles += @(& git -c core.quotepath=false ls-files --others --exclude-standard)
}

if (-not $PlanOnly -and $Scope -eq "Changed" -and $changedFiles.Count -eq 0) {
    Write-Host "未发现待验证的改动。"
    exit 0
}

$pythonTargets = [System.Collections.Generic.List[string]]::new()
$testTargets = [System.Collections.Generic.List[string]]::new()
$powershellTargets = [System.Collections.Generic.List[string]]::new()
$unmappedFiles = [System.Collections.Generic.List[string]]::new()
$planWarnings = [System.Collections.Generic.List[string]]::new()
$requiresFullSuite = $Scope -eq "Full"

foreach ($file in $changedFiles) {
    $path = $file.Replace("/", "\")
    if ($path.EndsWith(".py", [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $path)) {
        Add-UniqueTarget $pythonTargets $path
    }
    if ($path.EndsWith(".ps1", [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $path)) {
        Add-UniqueTarget $powershellTargets $path
    }

    if ($path -eq "pyproject.toml" -or $path -eq "uv.lock" -or $path -eq "tests\conftest.py") {
        $planWarnings.Add("依赖、检查配置或全局测试夹具发生变化：本次仅运行冒烟检查，合并或发布前需显式执行 -Scope Full。")
        Add-UniqueTarget $testTargets "tests/test_integration.py"
        Add-UniqueTarget $testTargets "tests/test_cli.py"
        Add-UniqueTarget $testTargets "tests/utils"
        continue
    }
    if ($path.StartsWith("config\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/utils"
        Add-UniqueTarget $testTargets "tests/test_cli.py"
        continue
    }
    if ($path -in @("scripts\verify.ps1", "scripts\verify-runtime.py")) {
        Add-UniqueTarget $testTargets "tests/test_verify_script.py"
        continue
    }
    if ($path -eq "scripts\probe-index-sources.py") {
        Add-UniqueTarget $pythonTargets "scripts/probe-index-sources.py"
        Add-UniqueTarget $testTargets "tests/index/test_source_probe.py"
        continue
    }
    if ($path -eq "scripts\run-radar-collector.py") {
        Add-UniqueTarget $testTargets "tests/radar/test_collector_process.py"
        Add-UniqueTarget $testTargets "tests/radar/test_collector_cli.py"
        continue
    }
    if ($path.StartsWith("src\stock_robot\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/test_cli.py"
        Add-UniqueTarget $testTargets "tests/test_cli_index.py"
        Add-UniqueTarget $testTargets "tests/radar/test_collector_cli.py"
        continue
    }
    if ($path.StartsWith("tests\resources\", [StringComparison]::OrdinalIgnoreCase)) {
        # 静态资源变化必须验证所属业务模块，不能按非 Python 文件跳过。
        $resourceParts = $path.Split("\")
        $resourceTests = if ($resourceParts.Count -ge 4) { Join-Path "tests" $resourceParts[2] } else { "" }
        if ($resourceTests -and (Test-Path -LiteralPath $resourceTests -PathType Container)) {
            Add-UniqueTarget $testTargets $resourceTests
        } else {
            $unmappedFiles.Add($path)
        }
        continue
    }
    if ($path.StartsWith("tests\", [StringComparison]::OrdinalIgnoreCase)) {
        if ($path.EndsWith(".py", [StringComparison]::OrdinalIgnoreCase)) {
            # 删除测试或调整局部夹具时验证所在目录，不向 pytest 传不存在的文件。
            $target = if ((Test-Path -LiteralPath $path) -and (Split-Path $path -Leaf) -like "test_*.py") { $path } else { Split-Path $path -Parent }
            if ($target -eq "tests") { $unmappedFiles.Add($path) } else { Add-UniqueTarget $testTargets $target }
        }
        continue
    }
    if ($path.StartsWith("src\api\static\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/api/test_static.py"
        continue
    }
    if ($path.StartsWith("src\api\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/api"
        continue
    }
    if ($path.StartsWith("src\report\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/report"
        Add-UniqueTarget $testTargets "tests/api/test_app.py"
        Add-UniqueTarget $testTargets "tests/api/test_static.py"
        continue
    }
    if ($path.StartsWith("src\index\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/index"
        Add-UniqueTarget $testTargets "tests/api/test_app.py"
        continue
    }
    if ($path.StartsWith("src\radar\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/radar"
        continue
    }
    if ($path.StartsWith("src\data\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/data"
        continue
    }
    if ($path.StartsWith("src\llm\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/llm"
        continue
    }
    if ($path.StartsWith("src\agent\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/agent"
        continue
    }
    if ($path.StartsWith("src\backtest\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/backtest"
        continue
    }
    if ($path.StartsWith("src\analysis\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/analysis"
        continue
    }
    if ($path.StartsWith("src\core\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/core"
        continue
    }
    if ($path.StartsWith("src\utils\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/utils"
        continue
    }
    if ($path.StartsWith("src\push\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/push"
        continue
    }
    if ($path.StartsWith("src\mcp\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/mcp"
        continue
    }
    if ($path.StartsWith("src\rag\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets "tests/rag"
        continue
    }
    if ($path.StartsWith("src\", [StringComparison]::OrdinalIgnoreCase) -or $path.StartsWith("scripts\", [StringComparison]::OrdinalIgnoreCase)) {
        $unmappedFiles.Add($path)
    }
}

if ($requiresFullSuite) {
    $pythonTargets.Clear()
    $testTargets.Clear()
    $pythonTargets.Add(".")
    # 全量 pytest 仅收集项目测试，避免已忽略的 tmp/ 缓存被误当作第三方测试源码。
    $testTargets.Add("tests")
}

# 已选择整个测试目录时，移除目录内的单文件目标，避免重复执行。
$coveredTargets = @($testTargets.ToArray() | Where-Object {
    $candidate = $_
    @($testTargets.ToArray() | Where-Object {
        $_ -ne $candidate -and (Test-Path -LiteralPath $_ -PathType Container) -and $candidate.StartsWith("$_/", [StringComparison]::OrdinalIgnoreCase)
    }).Count -eq 0
})
$testTargets.Clear()
foreach ($target in $coveredTargets) { Add-UniqueTarget $testTargets $target }

if ($PlanOnly) {
    [ordered]@{ SourceRoot = $repositoryRoot; RuntimeRoot = $venvRoot; Scope = $Scope; PythonTargets = @($pythonTargets.ToArray()); PowerShellTargets = @($powershellTargets.ToArray()); TestTargets = @($testTargets.ToArray()); Warnings = @($planWarnings.ToArray()); UnmappedFiles = @($unmappedFiles.ToArray()) } | ConvertTo-Json -Depth 3
    exit 0
}
if ($Source -eq "Staged") { & git diff --cached --check } else { & git diff --check }
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $requiresFullSuite -and $unmappedFiles.Count -gt 0) {
    Write-Warning "未配置相关测试：$($unmappedFiles -join ', ')。请用 -Files 指定相关源码/测试执行定向检查，并记录覆盖范围；不会自动运行全量。"
    exit 2
}
foreach ($warning in $planWarnings) { Write-Warning $warning }
foreach ($path in $powershellTargets) {
    $parseTokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repositoryRoot $path), [ref]$parseTokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -gt 0) { $parseErrors | Write-Error; exit 1 }
}
if ($requiresFullSuite) {
    Write-Host "使用显式全量校验。"
} else {
    Write-Host "按改动范围校验：$($changedFiles.Count) 个文件。"
}

# 所有工具使用当前工作树源码；finally 恢复调用者的环境。
$previousPythonPath = $env:PYTHONPATH
$runName = "verify-$PID-$([guid]::NewGuid().ToString('N'))"
$runDirectory = Get-RunDirectory $repositoryRoot $runName
$verificationPassed = $false
try {
    $env:PYTHONPATH = "$(Join-Path $repositoryRoot 'src');$repositoryRoot"
    New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
    if ($pythonTargets.Count -gt 0 -or $testTargets.Count -gt 0) {
        if (-not (Test-Path -LiteralPath $pythonPath)) { throw "未找到项目 Python：$pythonPath。请先执行 .\scripts\bootstrap-dev.ps1。" }
        $pyrightConfig = Join-Path $runDirectory "pyrightconfig.json"
        & $pythonPath (Join-Path $repositoryRoot "scripts/verify-runtime.py") --root $repositoryRoot --runtime $venvRoot --output $pyrightConfig
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    if ($pythonTargets.Count -gt 0) {
        foreach ($toolPath in @($ruffPath, $pyrightPath)) {
            if (-not (Test-Path -LiteralPath $toolPath)) { throw "未找到开发工具：$toolPath。请先执行 .\scripts\bootstrap-dev.ps1。" }
        }
        & $ruffPath check @pythonTargets
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        if ($requiresFullSuite) { & $pyrightPath --project $pyrightConfig } else { & $pyrightPath --project $pyrightConfig @pythonTargets }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else { Write-Host "本次没有 Python 改动，跳过 Ruff 与 Pyright。" }
    if ($testTargets.Count -gt 0) {
        & $pythonPath -m pytest @testTargets -q "--basetemp=$(Join-Path $runDirectory 'basetemp')" -o "cache_dir=$(Join-Path $runDirectory 'cache')"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else { Write-Host "本次没有可执行测试目标。" }
    $verificationPassed = $true
} finally {
    $env:PYTHONPATH = $previousPythonPath
    if ($verificationPassed) {
        try {
            Remove-Item -LiteralPath $runDirectory -Recurse -Force
            Write-Host "校验通过，已清理测试临时目录。"
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            Write-Warning "校验通过，临时目录暂无法清理：$runDirectory；$($_.Exception.Message)"
        }
    } else { Write-Warning "校验失败，已保留检查临时目录：$runDirectory" }
}
exit 0
