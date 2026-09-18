<#
.SYNOPSIS
同步项目锁定的开发工具链。

.DESCRIPTION
将 uv 缓存放入项目已忽略的 tmp/uv-cache，避免用户目录缓存的权限或损坏问题。
同步完成后，.venv 中会包含 Ruff、Pyright 与 pytest。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repositoryRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repositoryRoot) {
    throw "当前目录不在 Git 仓库中。"
}
$repositoryRoot = [IO.Path]::GetFullPath($repositoryRoot)
Set-Location $repositoryRoot

$env:UV_CACHE_DIR = Join-Path $repositoryRoot "tmp\uv-cache"
& uv sync --extra dev
exit $LASTEXITCODE
