@echo off
chcp 65001
cls
cd /d %~dp0
echo yoshi式・70点奪取戦略アプリを起動しています...
streamlit run study_app.py --server.address 192.168.10.131
pause