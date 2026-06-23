"""主题与样式：集中定义配色常量、字体、QSS 样式表。

克制工业风：单一钢蓝主题色 + 中性灰阶，深色为默认。
无表情符号，图标统一线性风格。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """配色方案。深/浅两套。"""

    primary: str        # 主题色：按钮/选中/ROI 线
    primary_hi: str     # 悬停
    primary_lo: str     # 按下
    alarm: str          # 报警：进入ROI框/状态灯
    success: str        # 就绪/正常
    warn: str
    bg_base: str        # 顶层背景
    bg_panel: str       # 面板/对话框
    bg_input: str       # 输入框/下拉
    fg_main: str        # 主文字
    fg_sub: str         # 次文字
    border: str
    canvas: str         # 画布背景（纯黑突出视频/图像）
    canvas_margin: str  # 视频画布等比缩放后的边距色（融于窗口，避免黑边突兀）

    def rgba(self, hex_color: str, alpha: int = 255) -> str:
        """hex(#RRGGBB) 转 rgba 字符串。"""
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"


DARK = Palette(
    primary="#2D6CDF",
    primary_hi="#4A86E8",
    primary_lo="#1E4F9E",
    alarm="#D93025",
    success="#1E8E3E",
    warn="#F9AB00",
    bg_base="#1E1F22",
    bg_panel="#2A2D31",
    bg_input="#383B40",
    fg_main="#E8EAED",
    fg_sub="#9AA0A6",
    border="#3C4043",
    canvas="#000000",
    canvas_margin="#1E1F22",
)

LIGHT = Palette(
    primary="#2D6CDF",
    primary_hi="#4A86E8",
    primary_lo="#1E4F9E",
    alarm="#D93025",
    success="#1E8E3E",
    warn="#F9AB00",
    bg_base="#F8F9FA",
    bg_panel="#FFFFFF",
    bg_input="#FFFFFF",
    fg_main="#202124",
    fg_sub="#5F6368",
    border="#DADCE0",
    canvas="#000000",
    canvas_margin="#F8F9FA",
)

PALETTES = {"dark": DARK, "light": LIGHT}


def get_palette(theme: str = "dark") -> Palette:
    return PALETTES.get(theme, DARK)


def build_qss(p: Palette, font_size: int = 13) -> str:
    """根据 Palette 生成全局 QSS 样式表。"""
    return f"""
    * {{
        font-family: "Microsoft YaHei", "Segoe UI", "JetBrains Mono", sans-serif;
        font-size: {font_size}px;
        color: {p.fg_main};
    }}
    QWidget#MainWindow, QWidget {{
        background-color: {p.bg_base};
    }}
    /* 菜单栏 */
    QMenuBar {{
        background-color: {p.bg_base};
        border-bottom: 1px solid {p.border};
        padding: 2px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 6px 12px;
    }}
    QMenuBar::item:selected {{ background-color: {p.bg_panel}; }}
    QMenu {{
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
    }}
    QMenu::item:selected {{ background-color: {p.primary}; }}
    /* 工具栏 */
    QToolBar {{
        background-color: {p.bg_panel};
        border: none;
        border-top: 1px solid {p.border};
        spacing: 4px;
        padding: 4px;
    }}
    QToolBar QToolButton {{
        background: transparent;
        color: {p.fg_main};
        padding: 6px 10px;
        border-radius: 3px;
    }}
    QToolBar QToolButton:hover {{ background-color: {p.bg_input}; }}
    QToolBar QToolButton:checked {{ background-color: {p.primary}; color: #FFFFFF; }}
    /* 底部快捷工具条：明确覆盖原生背景，避免 Windows 渲染成白色 */
    QToolBar#BottomToolbar {{
        background: {p.bg_panel};
        border: none;
        border-top: 1px solid {p.border};
    }}
    QToolBar#BottomToolbar QToolButton {{
        background: transparent;
        color: {p.fg_main};
    }}
    QToolBar#BottomToolbar QToolButton:hover {{ background-color: {p.bg_input}; }}
    QToolBar#BottomToolbar QToolButton:checked {{
        background-color: {p.primary};
        color: #FFFFFF;
    }}
    QToolBar#BottomToolbar QSeparator {{
        background: {p.border};
        width: 1px; height: 16px; margin: 0 4px;
    }}
    /* 按钮 */
    QPushButton {{
        background-color: {p.primary};
        color: #FFFFFF;
        border: none;
        padding: 6px 16px;
        border-radius: 3px;
    }}
    QPushButton:hover {{ background-color: {p.primary_hi}; }}
    QPushButton:pressed {{ background-color: {p.primary_lo}; }}
    QPushButton:disabled {{ background-color: {p.bg_input}; color: {p.fg_sub}; }}
    QPushButton[role="danger"] {{ background-color: {p.alarm}; }}
    QPushButton[role="flat"] {{
        background-color: transparent;
        color: {p.fg_main};
        border: 1px solid {p.border};
    }}
    QPushButton[role="flat"]:hover {{ background-color: {p.bg_input}; }}
    /* 分组框 */
    QGroupBox {{
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        border-radius: 4px;
        margin-top: 10px;
        padding-top: 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {p.fg_sub};
    }}
    /* 输入控件 */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {p.bg_input};
        border: 1px solid {p.border};
        border-radius: 3px;
        padding: 4px 6px;
        color: {p.fg_main};
    }}
    QComboBox::drop-down {{ border: none; }}
    QComboBox QAbstractItemView {{
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        selection-background-color: {p.primary};
    }}
    /* 复选 */
    QCheckBox {{ spacing: 6px; color: {p.fg_main}; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {p.border};
        border-radius: 2px;
        background-color: {p.bg_input};
    }}
    QCheckBox::indicator:checked {{ background-color: {p.primary}; border: 1px solid {p.primary}; }}
    /* 列表 */
    QListWidget {{
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        border-radius: 3px;
    }}
    QListWidget::item:selected {{ background-color: {p.primary}; }}
    /* 滚动条 */
    QScrollBar:vertical {{
        background: {p.bg_base}; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    /* 标签 */
    QLabel {{ background: transparent; color: {p.fg_main}; }}
    QLabel[role="title"] {{ font-size: {font_size + 3}px; font-weight: bold; }}
    QLabel[role="sub"] {{ color: {p.fg_sub}; font-size: {font_size - 1}px; }}
    QLabel[role="data"] {{ font-family: "JetBrains Mono", monospace; font-weight: bold; }}
    QLabel[role="alarm"] {{ color: {p.alarm}; font-weight: bold; }}
    /* 状态栏 */
    QStatusBar {{
        background-color: {p.bg_panel};
        border-top: 1px solid {p.border};
        color: {p.fg_sub};
    }}
    /* 标签页 */
    QTabWidget::pane {{
        border: 1px solid {p.border};
        background-color: {p.bg_panel};
    }}
    QTabBar::tab {{
        background-color: {p.bg_base};
        color: {p.fg_sub};
        padding: 6px 14px;
        border: 1px solid {p.border};
        border-bottom: none;
        border-top-left-radius: 3px;
        border-top-right-radius: 3px;
    }}
    QTabBar::tab:selected {{ background-color: {p.bg_panel}; color: {p.fg_main}; }}
    /* 滑块 */
    QSlider::groove:horizontal {{
        height: 4px; background: {p.bg_input}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {p.primary}; width: 14px; margin: -6px 0; border-radius: 7px;
    }}
    /* 画布：等比缩放后的边距用主题色，避免纯黑边距突兀 */
    QLabel#VideoCanvas {{ background-color: {p.canvas_margin}; }}
    """


def apply_theme(app, theme: str = "dark", font_size: int = 13) -> None:
    """应用主题到 QApplication。"""
    palette = get_palette(theme)
    app.setStyleSheet(build_qss(palette, font_size))


def set_titlebar_color(window, palette) -> bool:
    """给窗口的原生标题栏上主题色（Windows 10 1809+）。

    window: QMainWindow/QWidget 实例。palette: Palette。
    用面板色 bg_panel 作标题栏背景、纯白作标题文字色，使标题栏与深色工具栏
    视觉连成一体。必须在窗口 show() 之后调用（需要有效的窗口句柄）。
    非 Windows / 旧版本 / 句柄无效时静默忽略，返回 False。
    """
    try:
        handle = int(window.winId())
    except Exception:
        return False
    return _dwm_set_caption_color(handle, palette.bg_panel)


def _dwm_set_caption_color(window_handle: int, hex_color: str) -> bool:
    """Windows 10 1809+ / Windows 11：用 DWM API 给原生标题栏上色。

    关键三步（缺一不可，否则标题栏仍为系统默认白色/灰色）：
    1. DWMWA_USE_IMMERSIVE_DARK_MODE(20)=1：开启沉浸式模式，
       这是 CAPTION_COLOR 生效的前提（Win10 1809+/Win11）。
    2. DWMWA_CAPTION_COLOR(35)：标题栏背景色。
    3. DWMWA_TEXT_COLOR(36)：标题文字色。
    最后调 SetWindowPos(SWP_FRAMECHANGED) 强制重绘非客户区。

    返回是否成功（非 Windows / 旧版本 / 句柄无效时返回 False，静默忽略）。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        dwmapi = ctypes.WinDLL("dwmapi")
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36

        h = wintypes.HWND(window_handle)
        # 1. 开启沉浸式暗色模式（自定义标题色的前提）
        dwmapi.DwmSetWindowAttribute(
            h, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int),
        )
        # 2. 标题栏背景色
        bg = _hex_to_colorref(hex_color)
        dwmapi.DwmSetWindowAttribute(
            h, DWMWA_CAPTION_COLOR,
            ctypes.byref(ctypes.c_uint(bg)), ctypes.sizeof(ctypes.c_uint),
        )
        # 3. 标题文字色：纯白，与深色面板背景对比
        dwmapi.DwmSetWindowAttribute(
            h, DWMWA_TEXT_COLOR,
            ctypes.byref(ctypes.c_uint(0x00FFFFFF)), ctypes.sizeof(ctypes.c_uint),
        )
        # 强制重绘非客户区（标题栏），否则改动不可见
        user32 = ctypes.WinDLL("user32")
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            h, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED,
        )
        return True
    except Exception:
        return False


def _hex_to_colorref(hex_color: str) -> int:
    """#RRGGBB -> Windows COLORREF (0x00BBGGRR，注意 B/G/R 倒序)。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r
