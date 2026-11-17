# 发布托盘程序：dist\tray\Mortis.exe（.NET 8 自包含单文件，裁剪，不用 Native AOT）。
# 版本号取 pyproject.toml，检查更新拿它和 GitHub Release 比。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Out = Join-Path $Root "dist\tray"
$Project = Join-Path $Root "tray\Mortis.csproj"

$Dotnet = if ($env:MORTIS_DOTNET) { $env:MORTIS_DOTNET }
          elseif (Test-Path "D:\dev\dotnet\dotnet.exe") { "D:\dev\dotnet\dotnet.exe" }
          else { "dotnet" }

$Match = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $Match) { throw "pyproject.toml 里没有找到 version" }
$Version = $Match.Matches[0].Groups[1].Value

if (-not (Test-Path (Join-Path $Root "assets\app.ico"))) { throw "缺 assets\app.ico，先运行 scripts\make_icon.py" }

Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue

# dotnet 会往 stderr 写东西，Windows PowerShell 会把它当错误中断，所以这一段只看退出码。
$ErrorActionPreference = "Continue"
& $Dotnet publish $Project -c Release -o $Out "/p:Version=$Version" --nologo
$Code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($Code -ne 0) { throw "dotnet publish 失败（退出码 $Code）" }

$Exe = Join-Path $Out "Mortis.exe"
if (-not (Test-Path $Exe)) { throw "没有生成 $Exe" }
$Size = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Output "TRAY=$Exe ($Size MB, 版本 $Version)"
