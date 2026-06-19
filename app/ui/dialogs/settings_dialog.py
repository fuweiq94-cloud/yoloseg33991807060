"""设置对话框：独立窗口，左侧分类导航 + 右侧表单。

四个分区：
- 检测：置信度 / IoU / 设备 / 输入尺寸 / 显示开关
- 报警：四通道开关 / 声音文件 / 冷却时间 / 截图时长 / 弹窗
- 外观：主题 / 字号 / 主题色
- 路径：截图 / 录像 / 日志保存目录

所有修改点击「应用」后写回 ConfigManager（autosave），并即时生效。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
    QListWidget, QListWidgetItem, QStackedWidget, QPushButton,
    QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox, QLineEdit,
    QFileDialog, QSlider, QDialogButtonBox,
)

from app.core.config_manager import ConfigManager


class SettingsDialog(QDialog):
    """设置对话框。settings_applied 信号在应用并保存后发出。"""

    settings_applied = pyqtSignal()

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = config
        self.setWindowTitle("设置")
        self.setMinimumSize(640, 480)

        self._build()
        self._load_values()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 左侧导航
        self._nav = QListWidget()
        self._nav.setFixedWidth(140)
        self._nav.setCurrentRowChanged.connect(self._on_nav)
        for key, text in (
            ("detection", "检测"), ("alarm", "报警"),
            ("appearance", "外观"), ("paths", "路径"),
        ):
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, key)
            self._nav.addItem(item)
        layout.addWidget(self._nav)

        # 右侧分页
        right = QVBoxLayout()
        right.setContentsMargins(12, 12, 12, 12)
        right.setSpacing(8)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_detection())
        self._stack.addWidget(self._page_alarm())
        self._stack.addWidget(self._page_appearance())
        self._stack.addWidget(self._page_paths())
        right.addWidget(self._stack, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel | QDialogButtonBox.Reset
        )
        buttons.button(QDialogButtonBox.Apply).setText("应用")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.Reset).setText("恢复默认")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.button(QDialogButtonBox.Reset).clicked.connect(self._reset_defaults)
        right.addWidget(buttons)
        layout.addLayout(right, 1)

        self._nav.setCurrentRow(0)

    # ---- 检测页 ----
    def _page_detection(self) -> QWidget:
        page = QWidget()
        g = QGridLayout(page)
        g.setContentsMargins(4, 4, 4, 4)
        g.setVerticalSpacing(10)
        g.setHorizontalSpacing(12)
        row = 0

        g.addWidget(QLabel("模型路径"), row, 0)
        self.det_model = QLineEdit()
        self.det_model_btn = QPushButton("浏览")
        self.det_model_btn.setProperty("role", "flat")
        self.det_model_btn.clicked.connect(self._pick_model)
        g.addWidget(self.det_model, row, 1)
        g.addWidget(self.det_model_btn, row, 2)
        row += 1

        g.addWidget(QLabel("置信度 (Conf)"), row, 0)
        self.det_conf = QDoubleSpinBox()
        self.det_conf.setRange(0.0, 1.0)
        self.det_conf.setSingleStep(0.05)
        self.det_conf.setDecimals(2)
        g.addWidget(self.det_conf, row, 1)
        row += 1

        g.addWidget(QLabel("IoU 阈值"), row, 0)
        self.det_iou = QDoubleSpinBox()
        self.det_iou.setRange(0.0, 1.0)
        self.det_iou.setSingleStep(0.05)
        self.det_iou.setDecimals(2)
        g.addWidget(self.det_iou, row, 1)
        row += 1

        g.addWidget(QLabel("设备 (Device)"), row, 0)
        self.det_device = QComboBox()
        self.det_device.addItems(["cpu", "cuda:0", "cuda:1"])
        g.addWidget(self.det_device, row, 1)
        row += 1

        g.addWidget(QLabel("输入尺寸 (imgsz)"), row, 0)
        self.det_imgsz = QSpinBox()
        self.det_imgsz.setRange(320, 1280)
        self.det_imgsz.setSingleStep(32)
        g.addWidget(self.det_imgsz, row, 1)
        row += 1

        self.det_show_masks = QCheckBox("显示掩码")
        self.det_show_boxes = QCheckBox("显示检测框")
        self.det_show_labels = QCheckBox("显示标签")
        g.addWidget(self.det_show_masks, row, 1)
        g.addWidget(self.det_show_boxes, row, 2)
        row += 1
        g.addWidget(self.det_show_labels, row, 1)

        g.setColumnStretch(1, 1)
        return page

    # ---- 报警页 ----
    def _page_alarm(self) -> QWidget:
        page = QWidget()
        g = QGridLayout(page)
        g.setContentsMargins(4, 4, 4, 4)
        g.setVerticalSpacing(10)
        g.setHorizontalSpacing(12)
        row = 0

        self.alm_visual = QCheckBox("界面高亮 + 弹窗")
        self.alm_sound = QCheckBox("声音")
        self.alm_snapshot = QCheckBox("截图 / 录像")
        self.alm_log = QCheckBox("日志记录")
        g.addWidget(self.alm_visual, row, 1)
        g.addWidget(self.alm_sound, row, 2)
        row += 1
        g.addWidget(self.alm_snapshot, row, 1)
        g.addWidget(self.alm_log, row, 2)
        row += 1

        self.alm_popup = QCheckBox("报警时弹窗提示")
        g.addWidget(self.alm_popup, row, 1)
        row += 1

        g.addWidget(QLabel("声音文件"), row, 0)
        self.alm_sound_file = QLineEdit()
        self.alm_sound_btn = QPushButton("浏览")
        self.alm_sound_btn.setProperty("role", "flat")
        self.alm_sound_btn.clicked.connect(self._pick_sound)
        g.addWidget(self.alm_sound_file, row, 1)
        g.addWidget(self.alm_sound_btn, row, 2)
        row += 1

        g.addWidget(QLabel("冷却时间 (秒)"), row, 0)
        self.alm_cooldown = QDoubleSpinBox()
        self.alm_cooldown.setRange(0.0, 600.0)
        self.alm_cooldown.setSingleStep(0.5)
        self.alm_cooldown.setDecimals(1)
        g.addWidget(self.alm_cooldown, row, 1)
        row += 1

        g.addWidget(QLabel("录像前录 (秒)"), row, 0)
        self.alm_pre = QDoubleSpinBox()
        self.alm_pre.setRange(0.0, 60.0)
        self.alm_pre.setSingleStep(0.5)
        self.alm_pre.setDecimals(1)
        g.addWidget(self.alm_pre, row, 1)
        row += 1

        g.addWidget(QLabel("录像后录 (秒)"), row, 0)
        self.alm_post = QDoubleSpinBox()
        self.alm_post.setRange(0.0, 60.0)
        self.alm_post.setSingleStep(0.5)
        self.alm_post.setDecimals(1)
        g.addWidget(self.alm_post, row, 1)

        g.setColumnStretch(1, 1)
        return page

    # ---- 外观页 ----
    def _page_appearance(self) -> QWidget:
        page = QWidget()
        g = QGridLayout(page)
        g.setContentsMargins(4, 4, 4, 4)
        g.setVerticalSpacing(10)
        g.setHorizontalSpacing(12)
        row = 0

        g.addWidget(QLabel("主题"), row, 0)
        self.app_theme = QComboBox()
        self.app_theme.addItems(["dark", "light"])
        g.addWidget(self.app_theme, row, 1)
        row += 1

        g.addWidget(QLabel("字号"), row, 0)
        self.app_font = QSpinBox()
        self.app_font.setRange(10, 20)
        g.addWidget(self.app_font, row, 1)
        row += 1

        g.addWidget(QLabel("主题色"), row, 0)
        self.app_primary = QLineEdit()
        self.app_primary.setInputMask(">#HHHHHH;")
        g.addWidget(self.app_primary, row, 1)
        row += 1

        g.setColumnStretch(1, 1)
        return page

    # ---- 路径页 ----
    def _page_paths(self) -> QWidget:
        page = QWidget()
        g = QGridLayout(page)
        g.setContentsMargins(4, 4, 4, 4)
        g.setVerticalSpacing(10)
        g.setHorizontalSpacing(12)
        row = 0

        g.addWidget(QLabel("截图目录"), row, 0)
        self.p_snap = QLineEdit()
        self.p_snap_btn = QPushButton("浏览")
        self.p_snap_btn.setProperty("role", "flat")
        self.p_snap_btn.clicked.connect(lambda: self._pick_dir(self.p_snap))
        g.addWidget(self.p_snap, row, 1)
        g.addWidget(self.p_snap_btn, row, 2)
        row += 1

        g.addWidget(QLabel("录像目录"), row, 0)
        self.p_clip = QLineEdit()
        self.p_clip_btn = QPushButton("浏览")
        self.p_clip_btn.setProperty("role", "flat")
        self.p_clip_btn.clicked.connect(lambda: self._pick_dir(self.p_clip))
        g.addWidget(self.p_clip, row, 1)
        g.addWidget(self.p_clip_btn, row, 2)
        row += 1

        g.addWidget(QLabel("日志目录"), row, 0)
        self.p_log = QLineEdit()
        self.p_log_btn = QPushButton("浏览")
        self.p_log_btn.setProperty("role", "flat")
        self.p_log_btn.clicked.connect(lambda: self._pick_dir(self.p_log))
        g.addWidget(self.p_log, row, 1)
        g.addWidget(self.p_log_btn, row, 2)

        g.setColumnStretch(1, 1)
        return page

    # ---- 导航 ----
    def _on_nav(self, row: int) -> None:
        self._stack.setCurrentIndex(row)

    # ---- 文件/目录选择 ----
    def _pick_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择模型", "", "Models (*.pt *.onnx)")
        if path:
            self.det_model.setText(path)

    def _pick_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择声音文件", "", "Audio (*.wav *.mp3)")
        if path:
            self.alm_sound_file.setText(path)

    def _pick_dir(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择目录")
        if d:
            edit.setText(d)

    # ---- 加载 / 应用 / 重置 ----
    def _load_values(self) -> None:
        cfg = self._cfg
        self.det_model.setText(cfg.get("detection.model_path", ""))
        self.det_conf.setValue(cfg.get("detection.conf", 0.45))
        self.det_iou.setValue(cfg.get("detection.iou", 0.5))
        self.det_device.setCurrentText(cfg.get("detection.device", "cpu"))
        self.det_imgsz.setValue(cfg.get("detection.imgsz", 640))
        self.det_show_masks.setChecked(bool(cfg.get("detection.show_masks", True)))
        self.det_show_boxes.setChecked(bool(cfg.get("detection.show_boxes", True)))
        self.det_show_labels.setChecked(bool(cfg.get("detection.show_labels", True)))

        self.alm_visual.setChecked(bool(cfg.get("alarm.enabled_visual", True)))
        self.alm_sound.setChecked(bool(cfg.get("alarm.enabled_sound", True)))
        self.alm_snapshot.setChecked(bool(cfg.get("alarm.enabled_snapshot", True)))
        self.alm_log.setChecked(bool(cfg.get("alarm.enabled_log", True)))
        self.alm_popup.setChecked(bool(cfg.get("alarm.popup", True)))
        self.alm_sound_file.setText(cfg.get("alarm.sound_file", ""))
        self.alm_cooldown.setValue(cfg.get("alarm.cooldown_seconds", 3.0))
        self.alm_pre.setValue(cfg.get("alarm.clip_pre_seconds", 2.0))
        self.alm_post.setValue(cfg.get("alarm.clip_post_seconds", 2.0))

        self.app_theme.setCurrentText(cfg.get("appearance.theme", "dark"))
        self.app_font.setValue(cfg.get("appearance.font_size", 13))
        self.app_primary.setText(cfg.get("appearance.primary_color", "#2D6CDF"))

        self.p_snap.setText(cfg.get("paths.snapshots_dir", "data/snapshots"))
        self.p_clip.setText(cfg.get("paths.clips_dir", "data/clips"))
        self.p_log.setText(cfg.get("paths.logs_dir", "data/logs"))

    def _apply(self) -> bool:
        """把表单写回 ConfigManager 并保存。返回是否成功。"""
        cfg = self._cfg
        cfg.set("detection.model_path", self.det_model.text().strip())
        cfg.set("detection.conf", self.det_conf.value())
        cfg.set("detection.iou", self.det_iou.value())
        cfg.set("detection.device", self.det_device.currentText())
        cfg.set("detection.imgsz", self.det_imgsz.value())
        cfg.set("detection.show_masks", self.det_show_masks.isChecked())
        cfg.set("detection.show_boxes", self.det_show_boxes.isChecked())
        cfg.set("detection.show_labels", self.det_show_labels.isChecked())

        cfg.set("alarm.enabled_visual", self.alm_visual.isChecked())
        cfg.set("alarm.enabled_sound", self.alm_sound.isChecked())
        cfg.set("alarm.enabled_snapshot", self.alm_snapshot.isChecked())
        cfg.set("alarm.enabled_log", self.alm_log.isChecked())
        cfg.set("alarm.popup", self.alm_popup.isChecked())
        cfg.set("alarm.sound_file", self.alm_sound_file.text().strip())
        cfg.set("alarm.cooldown_seconds", self.alm_cooldown.value())
        cfg.set("alarm.clip_pre_seconds", self.alm_pre.value())
        cfg.set("alarm.clip_post_seconds", self.alm_post.value())

        cfg.set("appearance.theme", self.app_theme.currentText())
        cfg.set("appearance.font_size", self.app_font.value())
        cfg.set("appearance.primary_color", self.app_primary.text().strip())

        cfg.set("paths.snapshots_dir", self.p_snap.text().strip() or "data/snapshots")
        cfg.set("paths.clips_dir", self.p_clip.text().strip() or "data/clips")
        cfg.set("paths.logs_dir", self.p_log.text().strip() or "data/logs")

        cfg.save()
        self.settings_applied.emit()
        return True

    def _reset_defaults(self) -> None:
        self._cfg.reset_defaults(autosave=True)
        self._load_values()
        self.settings_applied.emit()

    def accept(self) -> None:
        self._apply()
        super().accept()
