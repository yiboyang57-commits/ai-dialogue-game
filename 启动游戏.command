#!/bin/bash
# 双击启动：自动启动游戏并打开浏览器（macOS）
cd "$(dirname "$0")" || exit 1

# 首次运行：创建本地环境并安装 streamlit（之后秒开）
if [ ! -d ".venv" ]; then
  echo "首次运行：正在安装运行环境（约 1-2 分钟，仅一次）…"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt || {
    echo "安装失败，请检查网络后重试。"
    read -r -p "按回车退出…"
    exit 1
  }
fi

# 已有环境也需要支持新版聊天区的自动滚动。
if ! .venv/bin/python -c 'import streamlit; from packaging.version import Version; assert Version("1.62") <= Version(streamlit.__version__) < Version("2")' 2>/dev/null; then
  echo "正在更新网页运行环境…"
  .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt || {
    echo "更新失败，请检查网络后重试。"
    read -r -p "按回车退出…"
    exit 1
  }
fi

# 启动服务（后台）并打开浏览器
.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false &
SERVER_PID=$!
sleep 3
open "http://localhost:8501"

echo ""
echo "✅ 游戏已在浏览器中打开。"
echo "   玩完后，关闭本窗口（Terminal）即可结束游戏。"
wait $SERVER_PID
