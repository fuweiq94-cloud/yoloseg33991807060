"""页面基类：统一布局约定、标题、主题切换入口。

页面是视图层，不直接持有 detector/worker。所有数据更新由 MainWindow 通过
页面暴露的方法（update_xxx / set_xxx）驱动，用户操作通过页面发出的信号回传 MainWindow。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel

from app.ui.theme import Palette, get_palette


class BasePage(QWidget):
    """所有页面的基类。

    子类需设置类属性 title（导航显示用），并在 _build_content() 中构建具体内容。
    """

    title: str = "页面"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette: Palette = get_palette("dark")
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 8, 8, 8)
        self._root_layout.setSpacing(8)

        # 页面标题（子类可隐藏）
        self._title_label = QLabel(self.title)
        self._title_label.setProperty("role", "title")
        self._root_layout.addWidget(self._title_label)

        self._build_content()
        self._apply_palette()

    # 子类覆盖：构建主体内容，加入 _root_layout
    def _build_content(self) -> None:
        pass

    # 子类可覆盖：主题相关样式刷新
    def _apply_palette(self) -> None:
        pass

    # ---- 公共接口 ----
    @property
    def palette(self) -> Palette:
        return self._palette

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._apply_palette()
