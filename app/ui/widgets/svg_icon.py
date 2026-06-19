"""SVG 图标渲染工具：从 assets/icons/<name>.svg 加载并按颜色着色。

所有 SVG 用 stroke="currentColor"（与现有 nav 图标一致的线性风格），
通过字符串替换注入目标主题色，再用 QSvgRenderer 渲染到透明 QPixmap。
本模块无 Qt 窗口依赖，可在任意线程调用。
"""
from __future__ import annotations

import os
from functools import lru_cache

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap, QPainter
from PyQt5.QtSvg import QSvgRenderer

from app.utils.logger import get_logger

logger = get_logger()

# 项目根目录（app/ui/widgets/svg_icon.py 上溯三级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _icons_dir() -> str:
    return os.path.join(_PROJECT_ROOT, "assets", "icons")


def _read_svg(name: str) -> str | None:
    """读取 SVG 源文本；缺失返回 None。"""
    path = os.path.join(_icons_dir(), f"{name}.svg")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        logger.warning("SVG 图标缺失: %s", path)
        return None


def _render_pixmap(svg_text: str, color: str, size: int) -> QPixmap:
    """把 SVG 文本着色后渲染到透明 QPixmap。"""
    svg = svg_text.replace("currentColor", color)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pm


def load_svg_pixmap(name: str, color: str, size: int = 24) -> QPixmap:
    """加载 SVG 为指定颜色的 QPixmap。缺失时返回空透明 Pixmap。"""
    svg_text = _read_svg(name)
    if svg_text is None:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        return pm
    return _render_pixmap(svg_text, color, size)


def load_svg_icon(name: str, color: str, size: int = 24) -> QIcon:
    """加载 SVG 为指定颜色的 QIcon。缺失时返回空 QIcon。"""
    return QIcon(load_svg_pixmap(name, color, size))
