# 安装 VideoLingo 全部依赖（在仓库根目录执行）
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py -m pip install -e ".[pipeline,content-hub]"
& $py -m pip install -r "pipeline\requirements.txt"
Write-Host "Running VideoLingo install.py (PyTorch, WhisperX, Demucs)..."
Set-Location (Join-Path $Root "pipeline")
& $py install.py
