---
name: mortis-release
description: >-
  发布莫提斯的 Windows 版本：确认工作区干净、定版本号、打包和冒烟检查、
  打 tag、推送、用 gh 建 GitHub Release 并上传 zip 和 SHA256SUMS。
  Use when 发版、打包、出安装包、上传 Release、打 tag，or when asked how to publish Mortis.
---

# 发布 Windows 版本

命令都在仓库根目录 `D:\personal开发\mortis` 的 PowerShell 里执行。仓库是 `AmadeusOfficial/Mortis`。

push、打 tag、建 Release 都会改远程，**每次发版都要用户明确同意**，不能沿用上一次的同意。

## 1. 发版前

- `git status --short` 应为空；有未提交的改动先问用户。
- 跑一遍检查，全部通过才往下走：

```powershell
D:\python\envs\python314\python.exe -m ruff check src tests plugins
D:\python\envs\python314\python.exe -m pytest -q -p no:cacheprovider
```

- 版本号只在 `pyproject.toml` 的 `[project] version`。新版本先问用户版本号，改完单独提交 `chore: 版本 x.y.z`。`git tag --list` 里已有的版本不能再用。

## 2. 打包

打包会删掉重建 `dist\Mortis\`。打包版托盘（`Mortis.exe`、`onedir\Mortis.Server.exe`）如果正从这个目录运行，要先请用户退出托盘，或确认后按进程树结束。查运行中的进程：

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "*mortis\dist*" } | Select-Object ProcessId,Name,ExecutablePath
```

打包，要几分钟（前端、PyInstaller 后端、托盘，然后组装、冒烟、压缩）：

```powershell
$env:MORTIS_PYTHON="D:\python\envs\python314\python.exe"; $env:npm_config_cache="D:\dev\cache\npm"; powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1 2>&1 | Select-String -NotMatch "INFO " | Select-Object -Last 10
```

- 退出码不可信：`2>&1` 会把构建工具写到 stderr 的正常输出当成错误，退出码可能是 1。以输出为准：要有「冒烟通过」和最后的 `ZIP=...`、`SHA256=...`。
- 冒烟检查失败时单独重跑，看是哪条断言：

```powershell
D:\python\envs\python314\python.exe -X utf8 scripts\smoke_package.py dist\Mortis\onedir\Mortis.Server.exe 2>&1 | Select-String -NotMatch "INFO "
```

  `scripts\smoke_package.py` 写死了插件清单（顶层插件、galgame 下的来源和播放插件）。增删内置插件后要同步改这里；是脚本过时就修脚本并提交，是包真的有问题就停下报告。

产物：`dist\Mortis-v<版本>-windows-x64.zip` 和 `dist\SHA256SUMS`。

## 3. tag 和推送

```powershell
git tag -a v<版本> -m "莫提斯 v<版本>"
git push origin main
git push origin v<版本>
```

PowerShell 会把 git push 的进度输出显示成 `NativeCommandError`，看到 `main -> main` 或 `[new tag]` 就是成功。

## 4. GitHub Release

gh 是免安装版，在 `D:\dev\tools\gh\bin\gh.exe`，配置目录放 D 盘。登录由用户自己做（浏览器授权），不要替用户处理令牌：

```powershell
$env:GH_CONFIG_DIR="D:\dev\tools\gh\config"; D:\dev\tools\gh\bin\gh.exe auth status
```

没登录时请用户在自己的终端运行：

```powershell
$env:GH_CONFIG_DIR="D:\dev\tools\gh\config"; D:\dev\tools\gh\bin\gh.exe auth login --hostname github.com --git-protocol https --web
```

更新说明写成文件 `D:\dev\tmp\release-notes.md`（中文，按用户能感知的功能分组，不列提交哈希，不写密钥、本机路径和库内容），先给用户看过再发：

```powershell
$env:GH_CONFIG_DIR="D:\dev\tools\gh\config"; D:\dev\tools\gh\bin\gh.exe release create v<版本> dist\Mortis-v<版本>-windows-x64.zip dist\SHA256SUMS -R AmadeusOfficial/Mortis --title "莫提斯 v<版本>" --notes-file D:\dev\tmp\release-notes.md
```

发完用 `gh release view v<版本> -R AmadeusOfficial/Mortis` 确认两个附件都在，把 Release 网址给用户。

托盘的自动更新靠附件名找文件：压缩包必须叫 `Mortis-v<版本>-windows-x64.zip`，校验文件必须叫 `SHA256SUMS`，里面要有这个压缩包的一行。不要改名，也不要只传其中一个。tag 必须是 `v` 加三段数字，托盘按它比较版本。

## 不要做

- 不把 `.dev-data`、设置文件、密钥文件、`dist\` 提交进 git。
- 不在 C 盘放 gh 或缓存。
- 不删除、不覆盖已发布的 Release 和 tag；发错了先问用户。
