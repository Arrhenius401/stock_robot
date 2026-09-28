<#
.SYNOPSIS
按 Git 改动范围或全量执行项目校验。

.DESCRIPTION
Changed 模式根据已改文件选择最小的 Ruff、Pyright 与 Pytest 集合；无法安全归类
的代码或配置改动自动退回 Full 模式。所有 pytest 运行产物都落在 tmp/pytest/，
成功后自动删除，失败时保留以便诊断。
#>
[CmdletBinding()]
param(
    [ValidateSet("Changed", "Full")]
    [string]$Scope = "Changed",

    [ValidateSet("Worktree", "Staged")]
    [string]$Source = "Worktree",

    [string[]]$Files = @(),

    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = "Stop"

function Add-UniqueTarget {
    param([System.Collections.Generic.List[string]]$Targets, [string]$Target)
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
$venvRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $VenvPath))
$repositoryPrefix = "$repositoryRoot$([IO.Path]::DirectorySeparatorChar)"
if (-not $venvRoot.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "虚拟环境必须位于仓库目录内。"
}
$venvScripts = Join-Path $venvRoot "Scripts"
$ruffPath = Join-Path $venvScripts "ruff.exe"
$pyrightPath = Join-Path $venvScripts "pyright.exe"
$pythonPath = Join-Path $venvScripts "python.exe"
foreach ($toolPath in @($ruffPath, $pyrightPath, $pythonPath)) {
    if (-not (Test-Path $toolPath)) {
        throw "未找到开发工具：$toolPath。请先执行 uv sync --extra dev。"
    }
}

if ($Scope -eq "Full") {
    $changedFiles = @()
} elseif ($Files.Count -gt 0) {
    $changedFiles = $Files
} elseif ($Source -eq "Staged") {
    $changedFiles = & git diff --cached --name-only
} else {
    $changedFiles = & git diff --name-only HEAD
}

if ($Scope -eq "Changed" -and $changedFiles.Count -eq 0) {
    Write-Host "未发现待验证的改动。"
    exit 0
}

$pythonTargets = [System.Collections.Generic.List[string]]::new()
$testTargets = [System.Collections.Generic.List[string]]::new()
$requiresFullSuite = $Scope -eq "Full"

foreach ($file in $changedFiles) {
    $path = $file.Replace("/", "\")
    if ($path.EndsWith(".py", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $pythonTargets $path
    }

    if ($path -eq "pyproject.toml" -or $path -eq "uv.lock" -or
        $path.StartsWith("config\", [StringComparison]::OrdinalIgnoreCase) -or
        $path.StartsWith("scripts\", [StringComparison]::OrdinalIgnoreCase) -or
        $path.StartsWith("src\stock_robot\", [StringComparison]::OrdinalIgnoreCase)) {
        $requiresFullSuite = $true
        continue
    }
    if ($path.StartsWith("tests\", [StringComparison]::OrdinalIgnoreCase)) {
        Add-UniqueTarget $testTargets $path
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
    if ($path.StartsWith("src\", [StringComparison]::OrdinalIgnoreCase)) {
        $requiresFullSuite = $true
    }
}

if ($requiresFullSuite) {
    $pythonTargets.Clear()
    $testTargets.Clear()
    $pythonTargets.Add(".")
    # 全量 pytest 仅收集项目测试，避免已忽略的 tmp/ 缓存被误当作第三方测试源码。
    $testTargets.Add("tests")
    Write-Host "使用全量校验：改动包含 CLI、配置、依赖或无法安全归类的路径。"
} else {
    Write-Host "按改动范围校验：$($changedFiles.Count) 个文件。"
}

if ($pythonTargets.Count -gt 0) {
    & $ruffPath check @pythonTargets
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if ($requiresFullSuite) {
        # 不传 "."，让 pyproject.toml 的 include 排除 tmp/ 与虚拟环境。
        & $pyrightPath
    } else {
        & $pyrightPath @pythonTargets
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "本次没有 Python 改动，跳过 Ruff 与 Pyright。"
}

if ($testTargets.Count -eq 0) {
    Write-Host "本次没有可执行测试目标。"
    exit 0
}

$runPrefix = if ($Scope -eq "Full") { "full" } else { "changed-$($Source.ToLowerInvariant())" }
# 每次校验使用独立目录，避免先前被系统占用的失败产物阻塞后续校验。
$runName = "$runPrefix-$PID"
$runDirectory = Get-RunDirectory $repositoryRoot $runName
if (Test-Path $runDirectory) {
    Remove-Item -LiteralPath $runDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null

$baseTemp = Join-Path $runDirectory "basetemp"
$cacheDirectory = Join-Path $runDirectory "cache"
& $pythonPath -m pytest @testTargets -q "--basetemp=$baseTemp" "-o" "cache_dir=$cacheDirectory"
$testExitCode = $LASTEXITCODE
if ($testExitCode -eq 0) {
    Remove-Item -LiteralPath $runDirectory -Recurse -Force
    Write-Host "校验通过，已清理测试临时目录。"
} else {
    Write-Warning "校验失败，已保留测试临时目录：$runDirectory"
}
exit $testExitCode
