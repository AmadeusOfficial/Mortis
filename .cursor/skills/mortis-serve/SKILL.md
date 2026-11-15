---
name: mortis-serve
description: >-
  启动莫提斯的本地服务：开发模式（后端热重载 + Vite）或后端托管前端产物，
  并以 HTTP 判断就绪、把网址交给用户验收。
  Use when 启动开发服务器、起前端、本地预览、让用户在浏览器里验收。
---

# 启动本地服务

命令都在仓库根目录 `D:\personal开发\mortis` 的 PowerShell 里执行。

## 环境变量

每次启动前在**同一条命令里**设好（代理终端转入后台后会换 shell，之前设的环境变量会丢）：

```powershell
$env:MORTIS_PYTHON = "D:\python\envs\python314\python.exe"
$env:MORTIS_DATA_DIR = "D:\dev\mortis-dev-data"
$env:npm_config_cache = "D:\dev\cache\npm"
```

- `MORTIS_PYTHON`：conda 环境 python314。不设时脚本用 PATH 里的 `python`，那个解释器没装 mortis，会报 `No module named 'mortis'`。
- `MORTIS_DATA_DIR`：不设会落到 C 盘 `%LOCALAPPDATA%\Mortis`，开发时不要用。
- `npm_config_cache`：Cursor 代理的终端会注入 C 盘的 npm 缓存路径，跑 npm 前改回 D 盘。

## 启动

| 方式 | 脚本 | 端口 | 网址 |
|---|---|---|---|
| 开发 | `scripts\dev.ps1` | 后端 8100 + Vite 5174 | http://localhost:5174 |
| 托管 | `scripts\start.ps1`（先构建 `web\dist`） | 只有 8100 | http://127.0.0.1:8100 |

两个脚本都是长期进程，后台运行，输出写到 `D:\dev\tmp`，记下 pid 以便停止：

```powershell
$env:MORTIS_PYTHON = "D:\python\envs\python314\python.exe"; $env:MORTIS_DATA_DIR = "D:\dev\mortis-dev-data"; $env:npm_config_cache = "D:\dev\cache\npm"
$p = Start-Process powershell.exe -ArgumentList "-NoProfile","-File","scripts\dev.ps1" -PassThru -NoNewWindow -RedirectStandardOutput D:\dev\tmp\dev-out.log -RedirectStandardError D:\dev\tmp\dev-err.log
$p.Id | Set-Content D:\dev\tmp\dev.pid
```

托管模式把 `dev` 换成 `start`。只构建不启动用 `powershell -NoProfile -File scripts\build_web.ps1`。

后端只监听 127.0.0.1。访问后端一律写 `127.0.0.1`，用 `localhost` 会先走 IPv6 而卡住。Vite 只监听 `::1`，开发页面要用 `localhost:5174`。

## 就绪

以 HTTP 为准，不等 Vite 的 `ready` 输出。一般几秒内就绪，托管模式要先构建，多等几秒：

```powershell
(Invoke-WebRequest http://127.0.0.1:8100/api/health -UseBasicParsing -TimeoutSec 5).StatusCode
(Invoke-WebRequest http://localhost:5174/ -UseBasicParsing -TimeoutSec 5).StatusCode
```

两个都应是 200（第二个只在开发模式有）。没就绪时看 `D:\dev\tmp\*-err.log`，uvicorn 的日志在 stderr。

托管模式另外确认 http://127.0.0.1:8100/library/1 返回页面（200，text/html），http://127.0.0.1:8100/api/unknown 返回 404。

端口已被占用时先确认是不是已经在跑，不要再起第二份：

```powershell
Get-NetTCPConnection -LocalPort 8100,5174 -State Listen -ErrorAction SilentlyContinue
```

## 页面自查

有浏览器工具时直接打开网址看。没有浏览器工具时，用无头 Edge 截图代替（会执行页面脚本，能证明页面真的从接口取到了数据）：

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --user-data-dir=D:\dev\tmp\edge-prof --window-size=1280,800 --virtual-time-budget=5000 --screenshot=D:\dev\tmp\<名字>.png <网址>
```

用 Read 查看截图：应显示「莫提斯」「智能体元数据刮削管理器」「版本 0.0.1」「数据目录 D:\dev\mortis-dev-data」，而不是「连不上后端」。后端日志里应出现 `GET /api/system/info ... 200`。

## 交给用户

只给 Web 地址，用纯文本，不放进代码块。要用户看某个页面时给完整路径。请用户复验时，无论服务是否已在运行，都再给一次地址。

## 停止

开发模式在前台时按 Ctrl+C，脚本的 `finally` 会按进程树结束后端。在后台时按进程树结束：

```powershell
taskkill /PID (Get-Content D:\dev\tmp\dev.pid) /T /F
```

然后确认端口已释放、没有遗留进程（两条都应无输出）：

```powershell
Get-NetTCPConnection -LocalPort 8100,5174 -State Listen -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn|mortis\.server|mortis\\web|vite|scripts\\(dev|start|build_web)\.ps1' -and $_.ProcessId -ne $PID } | Select-Object ProcessId,Name,CommandLine
```

托管模式不会随源码更新，改了前端要停掉后重新运行 `scripts\start.ps1`。
