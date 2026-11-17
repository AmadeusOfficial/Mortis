# 构建前端到 web\dist。
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location (Join-Path $Root "web")
try {
    # npm 和 Vite 会往 stderr 写提示，输出被重定向时 Windows PowerShell 会把它当错误中断，所以只看退出码。
    $ErrorActionPreference = "Continue"
    if (-not (Test-Path "node_modules")) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "安装前端依赖失败（npm ci 退出码 $LASTEXITCODE）" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败（npm run build 退出码 $LASTEXITCODE）" }
}
finally {
    $ErrorActionPreference = "Stop"
    Pop-Location
}

if (-not (Test-Path (Join-Path $Root "web\dist\index.html"))) {
    throw "前端构建后没有 web\dist\index.html"
}
