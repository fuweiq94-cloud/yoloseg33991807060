@echo off
chcp 65001 >nul
REM 一键创建虚拟环境并安装依赖（Windows 批处理封装）
REM 用法：双击运行 或 在命令行执行 setup_env.bat
python setup_env.py %*
pause
