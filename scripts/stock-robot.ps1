# 在不激活虚拟环境的情况下，使用项目自己的 stock-robot 命令。
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $projectRoot ".venv\Scripts\stock-robot.exe"

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    Write-Error "未找到项目虚拟环境。请先执行：py -3 -m venv .venv；随后执行：.\.venv\Scripts\python.exe -m pip install -e '.[dev]'"
    exit 1
}

& $executable @CommandArgs
exit $LASTEXITCODE
