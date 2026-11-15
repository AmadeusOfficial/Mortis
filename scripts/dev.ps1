# 开发模式：后端 uvicorn 热重载 + 前端 Vite。Ctrl+C 同时停掉两边。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
$Port = if ($env:MORTIS_PORT) { $env:MORTIS_PORT } else { "8100" }
$env:MORTIS_PORT = $Port
if (-not $env:MORTIS_DATA_DIR) { $env:MORTIS_DATA_DIR = Join-Path $Root ".dev-data" }

$ErrorActionPreference = "Continue"
& $Python -c "import mortis, uvicorn" 2>$null | Out-Null
$importOk = $LASTEXITCODE -eq 0
$ErrorActionPreference = "Stop"
if (-not $importOk) {
    throw "解释器 $Python 里没有装莫提斯。把环境变量 MORTIS_PYTHON 设成装了莫提斯的 python.exe（例如 conda 环境 python314），再重新启动。"
}

function Get-PortOwners([string]$P) {
    @(Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { $_.OwningProcess } | Sort-Object -Unique)
}

function Show-PortOwners([string]$P, $Owners) {
    Write-Host "端口 $P 已被占用，占用进程："
    foreach ($id in $Owners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
        Write-Host "  PID $id：$($proc.CommandLine)"
    }
    Write-Host "多半是上次直接关窗口留下的后端或 Vite。确认后用 taskkill /PID <pid> /T /F 结束，再重新启动。"
}

# Windows 上第二个 uvicorn 仍能绑定已被占用的端口，请求会落到旧进程上，所以启动前必须确认端口空闲。
foreach ($p in @($Port, "5174")) {
    $owners = Get-PortOwners $p
    if ($owners.Count -gt 0) {
        Show-PortOwners $p $owners
        throw "端口 $p 已被占用，没有启动。"
    }
}

function Test-InProcessTree([int]$RootId, $Ids) {
    $children = @{}
    foreach ($proc in Get-CimInstance Win32_Process) {
        $children[[int]$proc.ParentProcessId] += @([int]$proc.ProcessId)
    }
    $tree = @{}
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($RootId)
    while ($queue.Count -gt 0) {
        $id = $queue.Dequeue()
        if ($tree.ContainsKey($id)) { continue }
        $tree[$id] = $true
        foreach ($c in @($children[$id])) { if ($null -ne $c) { $queue.Enqueue($c) } }
    }
    foreach ($id in $Ids) { if (-not $tree.ContainsKey([int]$id)) { return $false } }
    return $true
}

$LogDir = Join-Path $env:MORTIS_DATA_DIR "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$ApiErrLog = Join-Path $LogDir "dev-api.err.log"
Write-Host "后端日志：$ApiErrLog"

function Show-ApiErrLog {
    if (Test-Path $ApiErrLog) {
        Write-Host "---- $ApiErrLog 最后 20 行 ----"
        Get-Content $ApiErrLog -Tail 20 -Encoding UTF8 | Write-Host
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
    # 200 也可能来自别的进程，确认端口上的监听全部属于这次启动的后端。
    $owners = Get-PortOwners $Port
    if (-not (Test-InProcessTree $api.Id $owners)) {
        Show-PortOwners $Port $owners
        throw "端口 $Port 上有不属于这次启动的监听，没有启动 Vite。"
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
