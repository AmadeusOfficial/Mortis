# 构建前端，再由后端托管 web\dist。只占用一个端口，和发布后的形态一致。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
$Port = if ($env:MORTIS_PORT) { $env:MORTIS_PORT } else { "8100" }
if (-not $env:MORTIS_DATA_DIR) { $env:MORTIS_DATA_DIR = Join-Path $Root ".dev-data" }

# Windows 上第二个 uvicorn 仍能绑定已被占用的端口，请求会落到旧进程上，所以启动前必须确认端口空闲。
$owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { $_.OwningProcess } | Sort-Object -Unique)
if ($owners.Count -gt 0) {
    Write-Host "端口 $Port 已被占用，占用进程："
    foreach ($id in $owners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
        Write-Host "  PID $id：$($proc.CommandLine)"
    }
    Write-Host "多半是上次直接关窗口留下的后端。确认后用 taskkill /PID <pid> /T /F 结束，再重新启动。"
    throw "端口 $Port 已被占用，没有启动。"
}

& (Join-Path $PSScriptRoot "build_web.ps1")
$env:MORTIS_WEB_DIST = Join-Path $Root "web\dist"
& $Python -m mortis.server
