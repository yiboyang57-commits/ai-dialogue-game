@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 首次运行：创建本地环境并安装 streamlit（之后秒开）
if not exist ".venv" (
  echo 首次运行：正在安装运行环境（约 1-2 分钟，仅一次）...
  python -m venv .venv
  .venv\Scripts\pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo 安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

rem 启动服务并打开浏览器
start "" .venv\Scripts\python -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
timeout /t 4 /nobreak >nul
start "" http://localhost:8501

echo.
echo 游戏已在浏览器中打开。玩完后关闭浏览器，再回到此窗口按 Ctrl+C 退出。
pause
