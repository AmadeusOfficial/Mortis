# 开发模式：后端 uvicorn 热重载 + 前端 Vite。Ctrl+C 同时停掉两边。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
$Port = if ($env:MORTIS_PORT) { $env:MORTIS_PORT } else { "8100" }
$env:MORTIS_PORT = $Port
if (-not $env:MORTIS_DATA_DIR) { $env:MORTIS_DATA_DIR = Join-Path $Root ".dev-data" }

$LogDir = Join-Path $env:MORTIS_DATA_DIR "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$ApiErrLog = Join-Path $LogDir "dev-api.err.log"
Write-Host "后端日志：$ApiErrLog"

function Show-ApiErrLog {
    if (Test-Path $ApiErrLog) {
        Write-Host "---- $ApiErrLog 最后 20 行 ----"
        Get-Content $ApiErrLog -Tail 20 | Write-Host
    }
}

# 后端放在独立的隐藏控制台：uvicorn 在 Windows 上热重载时会向同一控制台的所有进程发 Ctrl+C，会把 Vite 一起停掉。
# 重载用的 Ctrl+C 只有在 reloader 的 stdout 是控制台时才生效，所以只把 stderr（uvicorn 的运行日志）写进文件，
# stdout（访问日志）留在隐藏控制台里。只监视 src，数据目录里的数据库写入不会触发重启。
$Src = Join-Path $Root "src"
$api = Start-Process -FilePath "cmd.exe" -WindowStyle Hidden -PassThru -ArgumentList (
    "/d /c `"`"$Python`" -m uvicorn mortis.api.app:create_app --factory --reload --reload-dir `"$Src`" " +
    "--no-use-colors --host 127.0.0.1 --port $Port 2> `"$ApiErrLog`"`""
)
# 先取一次句柄，进程退出后才能读到 ExitCode。
$null = $api.Handle
$viteExit = 0
try {
    $health = "http://127.0.0.1:$Port/api/health"
    $deadline = (Get-Date).AddSeconds(20)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($api.HasExited) {
            Show-ApiErrLog
            throw "后端启动失败，进程已退出（退出码 $($api.ExitCode)），原因见上方日志。"
        }
        try {
            $r = Invoke-WebRequest $health -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200 -and -not $api.HasExited) { $ready = $true; break }
        }
        catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        Show-ApiErrLog
        throw "后端 20 秒内没有就绪：$health"
    }

    Push-Location (Join-Path $Root "web")
    try {
        npm run dev
        $viteExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    # --reload 会再起一个子进程，按进程树结束才不会留下占着端口的后端。
    if (-not $api.HasExited) {
        taskkill /PID $api.Id /T /F | Out-Null
    }
}
if ($viteExit -ne 0) { throw "Vite 异常退出（退出码 $viteExit）" }
