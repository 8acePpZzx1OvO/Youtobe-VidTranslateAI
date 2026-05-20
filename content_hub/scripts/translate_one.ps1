# 单条视频译制（需先启动代理，且 pipeline/.env 已配置翻译 Key）
# 用法: .\content_hub\scripts\translate_one.ps1 rFG-Sx-Tz6o

param(
    [Parameter(Mandatory = $true)]
    [string]$VideoId
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Root

& "$Root\.venv\Scripts\python.exe" -m content_hub doctor --video-id $VideoId
if ($LASTEXITCODE -ne 0) {
    Write-Host "doctor failed: start proxy (e.g. 127.0.0.1:13434)" -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Set-Location "$Root\pipeline"
$url = "https://www.youtube.com/watch?v=$VideoId"
Write-Host "Translating: $url"
& "$Root\.venv\Scripts\python.exe" run.py $url --full
$rc = $LASTEXITCODE

Set-Location $Root
& "$Root\.venv\Scripts\python.exe" -m content_hub doctor --video-id $VideoId
exit $rc
