"""安全识别系统启动入口。

使用项目内独立虚拟环境 .venv（Python 3.12，自包含 ultralytics + torch + 全部依赖）。
运行方式：
    C:\\Users\\fu\\Desktop\\ultralytics\\security-vision-system\\.venv\\Scripts\\python.exe run.py
"""
import os
import sys

# 让 app 包可被导入（run.py 位于项目根目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 关键：必须在 PyQt5 之前导入 torch/ultralytics。
# Windows 下若 PyQt5 的 Qt DLL 先加载，会占用 loader lock 导致 torch 的 c10.dll
# DllMain 初始化失败（WinError 1114）。先加载 torch 即可规避。
import torch  # noqa: F401
from ultralytics import YOLO  # noqa: F401


def main() -> int:
    from PyQt5.QtWidgets import QApplication

    from app.utils.logger import setup_logging
    from app.ui.main_window import MainWindow
    from app.ui.theme import apply_theme

    setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("SecurityVisionSystem")
    app.setOrganizationName("SecurityVision")
    apply_theme(app)

    window = MainWindow()
    window.show()

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
