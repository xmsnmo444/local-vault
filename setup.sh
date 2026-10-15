#!/usr/bin/env bash
# setup.sh — local-vault 一键安装（macOS / Linux）
# 用法：chmod +x setup.sh && ./setup.sh
set -e
echo "=== local-vault 一键安装 ==="

if [ ! -d ".venv" ]; then
  echo "[1/4] 建 .venv ..."
  python3 -m venv .venv
else echo "[1/4] .venv 已存在，跳过"; fi

echo "[2/4] 装依赖 ..."
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements-v0.2.txt -q
echo "  依赖装好了"

echo "[3/4] 下模型权重（约90MB，唯一联网步骤）..."
echo "  没网或不想下？Ctrl+C 跳过，用 --backend hash 也能跑"
.venv/bin/python -m src.cli fetch-model || echo "  跳过下模型，后面用 --backend hash 试试"

echo "[4/4] 跑个演示 ..."
.venv/bin/python -m src.cli --db vault.db index demo --backend hash
.venv/bin/python -m src.cli --db vault.db search "逆向" --backend hash -k 3

echo ""
echo "好了！试试："
echo '  .venv/bin/python -m src.cli --db vault.db search "怎么炖肉好吃" --backend fastembed -k 5'
echo '  .venv/bin/python -m src.cli --db vault.db stats'
