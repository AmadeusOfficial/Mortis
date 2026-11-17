# 打 Windows 包：dist\Mortis\ 和 dist\Mortis-v<版本>-windows-x64.zip（附 SHA256SUMS）。
# 依次构建前端、后端、托盘，组装后跑一遍冒烟检查。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = if ($env:MORTIS_PYTHON) { $env:MORTIS_PYTHON } else { "python" }
$Dist = Join-Path $Root "dist"
$App = Join-Path $Dist "Mortis"

$Version = (& $Python -c "import tomllib; print(tomllib.load(open(r'$Root\pyproject.toml', 'rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "读不到 pyproject.toml 里的版本号" }
Write-Output "版本 $Version"

& (Join-Path $PSScriptRoot "build_web.ps1")
& (Join-Path $PSScriptRoot "build_server.ps1")
& (Join-Path $PSScriptRoot "build_tray.ps1")

Remove-Item -Recurse -Force $App -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $App "web") | Out-Null
Copy-Item (Join-Path $Dist "tray\Mortis.exe") $App
Copy-Item -Recurse (Join-Path $Dist "server\Mortis.Server") (Join-Path $App "onedir")
Copy-Item -Recurse (Join-Path $Root "web\dist") (Join-Path $App "web\dist")
Copy-Item -Recurse (Join-Path $Root "plugins") (Join-Path $App "plugins")
Get-ChildItem (Join-Path $App "plugins") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Copy-Item (Join-Path $Root "LICENSE") $App

foreach ($path in "Mortis.exe", "onedir\Mortis.Server.exe", "web\dist\index.html", "plugins\potplayer\plugin.toml") {
    if (-not (Test-Path (Join-Path $App $path))) { throw "组装后缺 $path" }
}

# 冒烟检查不传 --web-dist、--plugins，验证后端自己能在 onedir 旁边找到它们。
$ErrorActionPreference = "Continue"
& $Python -X utf8 (Join-Path $PSScriptRoot "smoke_package.py") (Join-Path $App "onedir\Mortis.Server.exe")
$Code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($Code -ne 0) { throw "冒烟检查失败" }

$Zip = Join-Path $Dist "Mortis-v$Version-windows-x64.zip"
Remove-Item -Force $Zip, (Join-Path $Dist "SHA256SUMS") -ErrorAction SilentlyContinue
Compress-Archive -Path $App -DestinationPath $Zip
$Hash = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
[IO.File]::WriteAllText((Join-Path $Dist "SHA256SUMS"), "$Hash  $(Split-Path -Leaf $Zip)`n")

Write-Output ("ZIP={0} ({1:N1} MB)" -f $Zip, ((Get-Item $Zip).Length / 1MB))
Write-Output "SHA256=$Hash"
