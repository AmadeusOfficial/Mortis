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
$env:npm_config_cache = "D:\dev\cache\npm"
```

- `MORTIS_PYTHON`：conda 环境 python314。不设时脚本用 PATH 里的 `python`，那个解释器没装 mortis，会报 `No module named ...`。
- `npm_config_cache`：Cursor 代理的终端会注入 C 盘的 npm 缓存路径，跑 npm 前改回 D 盘。
- `MORTIS_DATA_DIR`：不用设。`dev.ps1` 和 `start.ps1` 在它未设或为空时用仓库内的 `.dev-data`（已在 .gitignore）。要换位置就显式设置，脚本会尊重已有的值。

## 启动

| 方式 | 脚本 | 端口 | 网址 |
|---|---|---|---|
| 开发 | `scripts\dev.ps1` | 后端 8100 + Vite 5174 | http://localhost:5174 |
| 托管 | `scripts\start.ps1`（先构建 `web\dist`） | 只有 8100 | http://127.0.0.1:8100 |

两个脚本都是长期进程，后台运行，输出写到 `D:\dev\tmp`（命令里先建好），记下 pid 以便停止。开发模式：

```powershell
New-Item -ItemType Directory -Force D:\dev\tmp | Out-Null; $env:MORTIS_PYTHON = "D:\python\envs\python314\python.exe"; $env:npm_config_cache = "D:\dev\cache\npm"
$p = Start-Process powershell.exe -ArgumentList "-NoProfile","-File","scripts\dev.ps1" -PassThru -NoNewWindow -RedirectStandardOutput D:\dev\tmp\dev-out.log -RedirectStandardError D:\dev\tmp\dev-err.log
$p.Id | Set-Content D:\dev\tmp\dev.pid
```

托管模式：

```powershell
New-Item -ItemType Directory -Force D:\dev\tmp | Out-Null; $env:MORTIS_PYTHON = "D:\python\envs\python314\python.exe"; $env:npm_config_cache = "D:\dev\cache\npm"
$p = Start-Process powershell.exe -ArgumentList "-NoProfile","-File","scripts\start.ps1" -PassThru -NoNewWindow -RedirectStandardOutput D:\dev\tmp\start-out.log -RedirectStandardError D:\dev\tmp\start-err.log
$p.Id | Set-Content D:\dev\tmp\start.pid
```

只构建不启动用 `powershell -NoProfile -File scripts\build_web.ps1`。npm 失败时脚本报中文错误、退出码非零；`start.ps1` 构建失败就不启动后端。

`dev.ps1` 先起 uvicorn（`--reload-dir` 只监视 `src`，数据目录里的写入不会触发重启），轮询 `/api/health` 最多约 20 秒拿到 200 才起 Vite。后端提前退出或超时会先打印后端日志最后 20 行，再报中文错误、结束后端并非零退出；Vite 非零退出时脚本也非零退出。

开发模式的后端跑在一个隐藏的独立控制台里（经 `cmd.exe` 启动）：uvicorn 热重载时会向同一控制台的所有进程发 Ctrl+C，和 Vite 共用控制台会把 Vite 一起停掉。

- 后端运行日志（启动、重新加载、报错）在 `<数据目录>\logs\dev-api.err.log`，默认 `.dev-data\logs\dev-api.err.log`，每次真正起后端时重写（端口预检没通过时脚本直接退出，不起后端，这个文件保持上次的内容）。用 `Get-Content .dev-data\logs\dev-api.err.log -Encoding UTF8 -Tail 30` 查看。改了 `src` 后应能看到 `Reloading...` 和新的 `Started server process`。
- 访问日志（每条请求）不落文件：reloader 的 stdout 必须是控制台，Ctrl+C 才会被处理，stdout 重定向到文件后热重载会卡住不重启。
- `dev-out.log` / `dev-err.log` 里只有 dev.ps1 自己和 Vite 的输出。

后端只监听 127.0.0.1。访问后端一律写 `127.0.0.1`：用 `localhost` 会先试 IPv6，每次请求慢约 2 秒。Vite 只监听 `::1`，开发页面要用 `localhost:5174`。

## 就绪

以 HTTP 为准，不等 Vite 的 `ready` 输出。一般几秒内就绪，托管模式要先构建，多等几秒：

```powershell
(Invoke-WebRequest http://127.0.0.1:8100/api/health -UseBasicParsing -TimeoutSec 5).StatusCode
(Invoke-WebRequest http://localhost:5174/ -UseBasicParsing -TimeoutSec 5).StatusCode
```

两个都应是 200（第二个只在开发模式有）。没就绪时：开发模式看 `D:\dev\tmp\dev-out.log` 和 `.dev-data\logs\dev-api.err.log`；托管模式看 `D:\dev\tmp\start-err.log`（uvicorn 日志在 stderr）。

托管模式另外确认 http://127.0.0.1:8100/library/1 返回页面（200，text/html），http://127.0.0.1:8100/api/unknown 返回 404。

端口已被占用时先确认是不是已经在跑，不要再起第二份：

```powershell
Get-NetTCPConnection -LocalPort 8100,5174 -State Listen -ErrorAction SilentlyContinue
```

Windows 上第二个 uvicorn 仍能绑定已被占用的 8100，请求会落到旧进程上，所以两个脚本启动前都会预检端口（`dev.ps1` 查 8100 和 5174，`start.ps1` 查 8100）：被占就列出占用进程的 PID 和命令行，提示用 `taskkill /PID <pid> /T /F` 结束，然后非零退出，不起后端也不起 Vite。`dev.ps1` 拿到 200 后还会确认 8100 的监听进程属于自己起的进程树。

直接关掉 dev.ps1 的窗口时 `finally` 不会执行，隐藏控制台里的后端会留在后台占着 8100；下次启动就会报端口被占，并给出要结束的 PID（是 uvicorn reloader，按进程树结束会连同服务进程和外层 cmd 一起退出）。

## 页面自查

有浏览器工具时直接打开网址看。没有浏览器工具时，用无头 Edge 截图代替（会执行页面脚本，能证明页面真的从接口取到了数据）：

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --user-data-dir=D:\dev\tmp\edge-prof --window-size=1280,800 --virtual-time-budget=5000 --screenshot=D:\dev\tmp\<名字>.png <网址>
```

用 Read 查看截图：首页会跳到媒体库页，应显示「莫提斯」、左侧导航「媒体库」「设置」和库卡片（没有库时是「还没有媒体库。」）；`/settings` 的「关于」里有版本和数据目录（默认是 `<仓库>\.dev-data`）。页面上不应出现「连不上后端」或「读取失败」。托管模式的 `D:\dev\tmp\start-out.log` 里应出现 `GET /api/system/info ... 200`；开发模式没有访问日志，改用 `Invoke-WebRequest http://localhost:5174/api/system/info -UseBasicParsing` 确认经 Vite 代理能取到 JSON。

### 页面自动化测试

用 Playwright 驱动本机 Edge（`channel="msedge"`），不下载浏览器，不要运行 `playwright install`。默认 `pytest` 不跑（`addopts` 排除了 e2e，直接指定文件也会全部跳过），要先构建前端再用 `-m e2e` 运行：

```powershell
$env:npm_config_cache = "D:\dev\cache\npm"; Push-Location web; npm run build; Pop-Location
& $env:MORTIS_PYTHON -m pytest -m e2e -q
```

测试自己在随机端口起一个后端、用临时数据目录，不占 8100，也不碰 `.dev-data`。

## 交给用户

只给 Web 地址，用纯文本，不放进代码块。要用户看某个页面时给完整路径。请用户复验时，无论服务是否已在运行，都再给一次地址。

## 停止

后台运行时按进程树结束。开发模式（独立控制台里的后端仍是 dev.ps1 的子进程：powershell → cmd → uvicorn reloader → 服务进程，这一条会一起结束，8100 随之释放）：

```powershell
taskkill /PID (Get-Content D:\dev\tmp\dev.pid) /T /F
```

托管模式：

```powershell
taskkill /PID (Get-Content D:\dev\tmp\start.pid) /T /F
```

前台运行时按 Ctrl+C，脚本的 `finally` 会按进程树结束后端（这条未实测）。npm 若问「终止批处理作业 (Y/N)?」回 Y。

无论哪种方式，停完都确认端口已释放、没有遗留进程（两条都应无输出）：

```powershell
Get-NetTCPConnection -LocalPort 8100,5174 -State Listen -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn|mortis\.server|mortis\\web|vite|scripts\\(dev|start|build_web)\.ps1' -and $_.ProcessId -ne $PID } | Select-Object ProcessId,Name,CommandLine
```

托管模式不会随源码更新，改了前端要停掉后重新运行 `scripts\start.ps1`。
