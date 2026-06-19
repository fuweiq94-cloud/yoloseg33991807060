"""页面基类：统一布局约定、标题、主题切换入口。

页面是视图层，不直接持有 detector/worker。所有数据更新由 MainWindow 通过
页面暴露的方法（update_xxx / set_xxx）驱动，用户操作通过页面发出的信号回传 MainWindow。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from app.ui.theme import Palette, get_palette
from app.ui.widgets.svg_icon import load_svg_pixmap


class BasePage(QWidget):
    """所有页面的基类。

    子类需设置类属性 title（导航显示用），并在 _build_content() 中构建具体内容。
    可选设置 icon_name（assets/icons 下的 svg 名）在标题前显示主题色图标。
    """

    title: str = "页面"
    icon_name: str | None = None  # assets/icons/<icon_name>.svg，None 则不显示图标

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette: Palette = get_palette("dark")
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 8, 8, 8)
        self._root_layout.setSpacing(8)

        # 页面标题栏：[图标] + [标题文字]（图标可选）
        self._header = QHBoxLayout()
        self._header.setSpacing(8)
        self._title_icon = QLabel()
        self._title_icon.setFixedSize(26, 26)
        if self.icon_name:
            self._title_icon.setPixmap(load_svg_pixmap(self.icon_name, self._palette.primary, 26))
        else:
            self._title_icon.setVisible(False)
        self._header.addWidget(self._title_icon)
        self._title_label = QLabel(self.title)
        self._title_label.setProperty("role", "title")
        self._header.addWidget(self._title_label)
        self._header.addStretch(1)
        self._root_layout.addLayout(self._header)

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
        # 标题图标刷新主题色
        if self.icon_name:
            self._title_icon.setPixmap(load_svg_pixmap(self.icon_name, palette.primary, 26))
        self._apply_palette()
