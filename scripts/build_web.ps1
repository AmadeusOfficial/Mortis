# 构建前端到 web\dist。
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location (Join-Path $Root "web")
try {
    if (-not (Test-Path "node_modules")) {
        npm ci
    }
    npm run build
}
finally {
    Pop-Location
}

if (-not (Test-Path (Join-Path $Root "web\dist\index.html"))) {
    throw "前端构建后没有 web\dist\index.html"
}
