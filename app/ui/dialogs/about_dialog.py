"""关于对话框：应用信息 + 技术栈。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QFrame,
)


APP_NAME = "安全识别系统 SecurityVisionSystem"
APP_VERSION = "1.0.0"
TECH_STACK = "YOLO26s-seg · PyQt5 · OpenCV · pyqtgraph · shapely"


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        title = QLabel(APP_NAME)
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        ver = QLabel(f"版本 {APP_VERSION}")
        ver.setProperty("role", "sub")
        ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(ver)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        tech = QLabel(TECH_STACK)
        tech.setWordWrap(True)
        tech.setAlignment(Qt.AlignCenter)
        tech.setProperty("role", "sub")
        layout.addWidget(tech)

        desc = QLabel(
            "接入图片 / 视频 / 摄像头，自动检测目标；\n"
            "支持多边形 ROI 划定与多通道报警，\n"
            "实时呈现统计图表与异常帧回看。"
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignRight)
