# setup.ps1 — local-vault 一键安装（Windows）
# 双击运行或：powershell -ExecutionPolicy Bypass -File setup.ps1

# 1. 切到 UTF-8，避免中文乱码
chcp 65001 >$null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ErrorActionPreference = "Stop"
Write-Host "=== local-vault 一键安装 ===" -ForegroundColor Green

# 2. 建独立虚拟环境（锁死 onnxruntime 版本，不动全局）
if (-not (Test-Path ".venv")) {
    Write-Host "[1/4] 建 .venv ..."
    python -m venv .venv
} else { Write-Host "[1/4] .venv 已存在，跳过" }

# 3. 装依赖
Write-Host "[2/4] 装依赖 ..."
& .\.venv\Scripts\python -m pip install --upgrade pip -q
# 想只用 hash 后端？改成 requirements.txt 就行
& .\.venv\Scripts\pip install -r requirements-v0.2.txt -q
Write-Host "  依赖装好了"

# 4. 下模型（唯一联网步骤，可跳过）
Write-Host "[3/4] 下模型权重（约90MB，唯一联网步骤）..."
Write-Host "  没网或不想下？直接回车跳过，用 --backend hash 也能跑"
try {
    & .\.venv\Scripts\python -m src.cli fetch-model
} catch { Write-Host "  跳过下模型，后面用 --backend hash 试试" -ForegroundColor Yellow }

# 5. 跑个小演示
Write-Host "[4/4] 跑个演示 ..."
& .\.venv\Scripts\python -m src.cli --db vault.db index demo --backend hash
& .\.venv\Scripts\python -m src.cli --db vault.db search "逆向" --backend hash -k 3

Write-Host ""
Write-Host "好了！试试：" -ForegroundColor Green
Write-Host '  .\.venv\Scripts\python -m src.cli --db vault.db search "怎么炖肉好吃" --backend fastembed -k 5'
Write-Host '  .\.venv\Scripts\python -m src.cli --db vault.db stats'
