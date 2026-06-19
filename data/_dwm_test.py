import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ctypes
from ctypes import wintypes

import torch  # noqa: F401
from ultralytics import YOLO  # noqa: F401
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

app = QApplication([])
from app.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
app.processEvents()

hwnd = int(w.winId())
print("HWND:", hwnd, hex(hwnd))

dwmapi = ctypes.WinDLL("dwmapi")

# probe each attribute's HRESULT
for attr, name, val in [
    (35, "DWMWA_CAPTION_COLOR", 0x00312D2A),  # BGR of #2A2D31
    (36, "DWMWA_TEXT_COLOR", 0x00FFFFFF),
    (20, "DWMWA_USE_IMMERSIVE_DARK_MODE", 1),
]:
    h = dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), attr,
        ctypes.byref(ctypes.c_uint(val)), ctypes.sizeof(ctypes.c_uint))
    print(f"{name} ({attr}) -> HRESULT 0x{h & 0xFFFFFFFF:08X} ({'OK' if h==0 else 'FAIL'})")

# force redraw
user32 = ctypes.WinDLL("user32")
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_FRAMECHANGED = 0x0020
user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)


def done():
    app.quit()


QTimer.singleShot(3000, done)
app.exec_()
