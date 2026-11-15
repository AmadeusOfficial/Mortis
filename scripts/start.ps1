# 构建前端，再由后端托管 web\dist。只占用一个端口，和发布后的形态一致。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
if (-not $env:MORTIS_DATA_DIR) { $env:MORTIS_DATA_DIR = Join-Path $Root ".dev-data" }

& (Join-Path $PSScriptRoot "build_web.ps1")
$env:MORTIS_WEB_DIST = Join-Path $Root "web\dist"
& $Python -m mortis.server
