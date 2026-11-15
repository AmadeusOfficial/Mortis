# 开发模式：后端 uvicorn 热重载 + 前端 Vite。Ctrl+C 同时停掉两边。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
$Port = if ($env:MORTIS_PORT) { $env:MORTIS_PORT } else { "8100" }
$env:MORTIS_PORT = $Port

$api = Start-Process -FilePath $Python -NoNewWindow -PassThru -ArgumentList @(
    "-m", "uvicorn", "mortis.api.app:create_app", "--factory", "--reload",
    "--host", "127.0.0.1", "--port", $Port
)
try {
    Push-Location (Join-Path $Root "web")
    npm run dev
}
finally {
    Pop-Location
    # --reload 会再起一个子进程，按进程树结束才不会留下占着端口的后端。
    if (-not $api.HasExited) {
        taskkill /PID $api.Id /T /F | Out-Null
    }
}
