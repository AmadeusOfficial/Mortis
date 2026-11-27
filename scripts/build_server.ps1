# 用 PyInstaller 把后端打成 onedir：dist\server\Mortis.Server\Mortis.Server.exe。
# 参数改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）的 scripts/build_windows_app.ps1。
# 前端和插件不收进这里，由 build_windows.ps1 放在 onedir 旁边。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
$Work = Join-Path $Root "dist\server-work"
$Out = Join-Path $Root "dist\server"

& $Python -c "import mortis, PyInstaller" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "解释器 $Python 里没有装莫提斯或 PyInstaller。先 pip install -e .[dev]，或把 MORTIS_PYTHON 设成对应的 python.exe。"
}

# conda 的 Python 把 sqlite3、OpenSSL 等扩展依赖的 DLL 放在 Library\bin，不在 PATH 上 PyInstaller 就收不到。
$Prefix = (& $Python -c "import sys; print(sys.prefix)").Trim()
$CondaBin = Join-Path $Prefix "Library\bin"
if (Test-Path $CondaBin) { $env:PATH = "$CondaBin;$env:PATH" }

Remove-Item -Recurse -Force $Work, $Out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$StdlibArgs = @()
& $Python (Join-Path $PSScriptRoot "stdlib_modules.py") | ForEach-Object {
    if ($_ -ne "") { $StdlibArgs += @("--collect-submodules", $_) }
}
if ($LASTEXITCODE -ne 0) { throw "列标准库模块失败" }

# --console：uvicorn 要有 stdout；托盘用不显示窗口的方式启动它。
# PyInstaller 把进度写到 stderr，Windows PowerShell 会把它当错误中断，所以这一段只看退出码。
$ErrorActionPreference = "Continue"
& $Python -m PyInstaller `
    --noconfirm --clean --onedir --console --log-level WARN `
    --name "Mortis.Server" `
    --paths (Join-Path $Root "src") `
    --add-data "$(Join-Path $Root 'src\mortis\db\migrations');mortis/db/migrations" `
    --collect-submodules mortis `
    --collect-submodules uvicorn `
    --collect-submodules alembic `
    --collect-submodules watchdog `
    --copy-metadata mortis `
    --exclude-module playwright `
    --exclude-module pytest `
    --exclude-module PyInstaller `
    @StdlibArgs `
    --distpath $Out `
    --workpath (Join-Path $Work "build") `
    --specpath $Work `
    (Join-Path $PSScriptRoot "pyinstaller_entry.py")
$Code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($Code -ne 0) { throw "PyInstaller 失败（退出码 $Code）" }

$Exe = Join-Path $Out "Mortis.Server\Mortis.Server.exe"
if (-not (Test-Path $Exe)) { throw "没有生成 $Exe" }
foreach ($dll in "sqlite3.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll") {
    if (-not (Get-ChildItem (Join-Path $Out "Mortis.Server") -Recurse -Filter $dll -ErrorAction SilentlyContinue) -and
        (Test-Path (Join-Path $CondaBin $dll))) {
        throw "打包结果里缺 $dll"
    }
}
Write-Output "SERVER=$Exe"
