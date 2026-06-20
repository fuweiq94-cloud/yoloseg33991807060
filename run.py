"""安全识别系统启动入口。

运行方式（任选其一）：
    python run.py                                # 自动查找并切换到项目虚拟环境
    .venv\\Scripts\\python.exe run.py             # 直接用虚拟环境运行

启动时若检测到当前解释器不在项目虚拟环境（.venv）内，会自动定位
.venv 并用其中的解释器重新执行本脚本，确保依赖齐全、避免 torch/PyQt5
DLL 冲突（必须在 PyQt5 之前导入 torch）。
"""
import os
import sys

# 让 app 包可被导入（run.py 位于项目根目录）
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)


def _auto_use_venv() -> None:
    """若当前不在项目虚拟环境内，定位 .venv 并用其解释器重启本脚本。

    判定依据：sys.executable 是否位于 .venv 目录下。
    若已在虚拟环境内（或找不到 .venv），直接返回不处理。
    """
    exe = sys.executable
    # 已经在虚拟环境里（路径含项目根下的 .venv）
    venv_dir = os.path.join(_PROJECT_ROOT, ".venv")
    if os.path.normcase(exe).startswith(os.path.normcase(venv_dir)):
        return

    # 定位虚拟环境的 python 解释器
    if os.name == "nt":
        candidates = [os.path.join(venv_dir, "Scripts", "python.exe")]
    else:
        candidates = [
            os.path.join(venv_dir, "bin", "python"),
            os.path.join(venv_dir, "bin", "python3"),
        ]
    py_exe = next((p for p in candidates if os.path.isfile(p)), None)
    if py_exe is None:
        print(
            "[run.py] 未找到项目虚拟环境 .venv。\n"
            "请先运行环境配置脚本：python setup_env.py\n"
            f"（期望位置：{venv_dir}）"
        )
        sys.exit(1)

    # os.execv 前立即 flush，否则 print 会随进程被替换而丢失
    sys.stdout.write(f"[run.py] 切换到虚拟环境解释器：{py_exe}\n")
    sys.stdout.flush()
    # 用虚拟环境的 python 重新执行本脚本，替换当前进程
    os.execv(py_exe, [py_exe] + sys.argv)


# 关键：切换到正确解释器后再导入 torch（必须在 PyQt5 之前）。
# Windows 下若 PyQt5 的 Qt DLL 先加载，会占用 loader lock 导致 torch 的 c10.dll
# DllMain 初始化失败（WinError 1114）。先加载 torch 即可规避。
_auto_use_venv()
import torch  # noqa: F401,E402
from ultralytics import YOLO  # noqa: F401,E402


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
