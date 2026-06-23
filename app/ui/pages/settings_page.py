"""设置页：把检测/报警/外观/路径四组配置内置为一个可滚动页面。

布局：顶部一行操作按钮（应用/恢复默认/重新载入），下方用 GroupBox 分四组。
字段与逻辑与原 SettingsDialog 一致，去掉了对话框外壳与内部左侧导航
（外层页面导航已承担切换职责）。
应用后发 settings_applied 信号，由 MainWindow 触发热重载。
"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QGroupBox,
    QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox, QLineEdit,
    QFileDialog, QMessageBox,
)

from app.core.config_manager import ConfigManager
from app.ui.pages.base_page import BasePage
from app.ui.widgets.svg_icon import load_svg_icon


class SettingsPage(BasePage):
    title = "设置"
    icon_name = "settings"

    settings_applied = pyqtSignal()

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        self._cfg = config
        super().__init__(parent)

    def _build_content(self) -> None:
        # 顶部操作行
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_export = QPushButton("导出")
        self.btn_export.setProperty("role", "flat")
        self.btn_export.setIcon(load_svg_icon("export", self.palette.fg_main, 16))
        self.btn_export.clicked.connect(self._export_config)
        self.btn_import = QPushButton("导入")
        self.btn_import.setProperty("role", "flat")
        self.btn_import.setIcon(load_svg_icon("import", self.palette.fg_main, 16))
        self.btn_import.clicked.connect(self._import_config)
        self.btn_reload = QPushButton("重新载入")
        self.btn_reload.setProperty("role", "flat")
        self.btn_reload.setIcon(load_svg_icon("refresh", self.palette.fg_main, 16))
        self.btn_reload.clicked.connect(self._reload)
        self.btn_reset = QPushButton("恢复默认")
        self.btn_reset.setProperty("role", "flat")
        self.btn_reset.setIcon(load_svg_icon("undo", self.palette.fg_main, 16))
        self.btn_reset.clicked.connect(self._reset_defaults)
        self.btn_apply = QPushButton("应用")
        self.btn_apply.setIcon(load_svg_icon("check", "#FFFFFF", 16))
        self.btn_apply.clicked.connect(self._apply)
        bar.addWidget(self.btn_export)
        bar.addWidget(self.btn_import)
        bar.addWidget(self.btn_reload)
        bar.addWidget(self.btn_reset)
        bar.addWidget(self.btn_apply)
        self._root_layout.addLayout(bar)

        # 可滚动表单
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QFrame()
        form = QVBoxLayout(container)
        form.setContentsMargins(2, 2, 2, 2)
        form.setSpacing(10)

        form.addWidget(self._group_detection())
        form.addWidget(self._group_alarm())
        form.addWidget(self._group_appearance())
        form.addWidget(self._group_paths())
        form.addStretch(1)
        scroll.setWidget(container)
        self._root_layout.addWidget(scroll, 1)

        self._load_values()

    # ---- 检测 ----
    def _group_detection(self) -> QGroupBox:
        g = QGroupBox("检测")
        f = QGridLayout(g)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        row = 0

        f.addWidget(QLabel("模型路径"), row, 0)
        self.det_model = QLineEdit()
        self.det_model_btn = QPushButton("浏览")
        self.det_model_btn.setProperty("role", "flat")
        self.det_model_btn.setIcon(load_svg_icon("folder_open", self.palette.fg_main, 16))
        self.det_model_btn.clicked.connect(self._pick_model)
        f.addWidget(self.det_model, row, 1)
        f.addWidget(self.det_model_btn, row, 2)
        row += 1

        f.addWidget(QLabel("置信度 (Conf)"), row, 0)
        self.det_conf = QDoubleSpinBox()
        self.det_conf.setRange(0.0, 1.0)
        self.det_conf.setSingleStep(0.05)
        self.det_conf.setDecimals(2)
        f.addWidget(self.det_conf, row, 1)
        row += 1

        f.addWidget(QLabel("IoU 阈值"), row, 0)
        self.det_iou = QDoubleSpinBox()
        self.det_iou.setRange(0.0, 1.0)
        self.det_iou.setSingleStep(0.05)
        self.det_iou.setDecimals(2)
        f.addWidget(self.det_iou, row, 1)
        row += 1

        f.addWidget(QLabel("设备 (Device)"), row, 0)
        self.det_device = QComboBox()
        self.det_device.addItems(["cpu", "cuda:0", "cuda:1"])
        f.addWidget(self.det_device, row, 1)
        row += 1

        f.addWidget(QLabel("输入尺寸 (imgsz)"), row, 0)
        self.det_imgsz = QSpinBox()
        self.det_imgsz.setRange(320, 1280)
        self.det_imgsz.setSingleStep(32)
        f.addWidget(self.det_imgsz, row, 1)
        row += 1

        self.det_show_masks = QCheckBox("显示掩码")
        self.det_show_boxes = QCheckBox("显示检测框")
        self.det_show_labels = QCheckBox("显示标签")
        f.addWidget(self.det_show_masks, row, 1)
        f.addWidget(self.det_show_boxes, row, 2)
        row += 1
        f.addWidget(self.det_show_labels, row, 1)

        f.setColumnStretch(1, 1)
        return g

    # ---- 报警 ----
    def _group_alarm(self) -> QGroupBox:
        g = QGroupBox("报警")
        f = QGridLayout(g)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        row = 0

        self.alm_visual = QCheckBox("界面高亮 + 弹窗")
        self.alm_sound = QCheckBox("声音")
        self.alm_snapshot = QCheckBox("截图保存")
        self.alm_log = QCheckBox("日志记录")
        f.addWidget(self.alm_visual, row, 1)
        f.addWidget(self.alm_sound, row, 2)
        row += 1
        f.addWidget(self.alm_snapshot, row, 1)
        f.addWidget(self.alm_log, row, 2)
        row += 1

        self.alm_popup = QCheckBox("报警时弹窗提示")
        self.alm_clip = QCheckBox("报警片段录像")
        f.addWidget(self.alm_popup, row, 1)
        f.addWidget(self.alm_clip, row, 2)
        row += 1

        f.addWidget(QLabel("声音文件"), row, 0)
        self.alm_sound_file = QLineEdit()
        self.alm_sound_btn = QPushButton("浏览")
        self.alm_sound_btn.setProperty("role", "flat")
        self.alm_sound_btn.setIcon(load_svg_icon("folder_open", self.palette.fg_main, 16))
        self.alm_sound_btn.clicked.connect(self._pick_sound)
        f.addWidget(self.alm_sound_file, row, 1)
        f.addWidget(self.alm_sound_btn, row, 2)
        row += 1

        f.addWidget(QLabel("冷却时间 (秒)"), row, 0)
        self.alm_cooldown = QDoubleSpinBox()
        self.alm_cooldown.setRange(0.0, 600.0)
        self.alm_cooldown.setSingleStep(0.5)
        self.alm_cooldown.setDecimals(1)
        f.addWidget(self.alm_cooldown, row, 1)
        row += 1

        f.addWidget(QLabel("驻留时间 (秒)"), row, 0)
        self.alm_dwell = QDoubleSpinBox()
        self.alm_dwell.setRange(0.0, 300.0)
        self.alm_dwell.setSingleStep(0.5)
        self.alm_dwell.setDecimals(1)
        self.alm_dwell.setToolTip("目标在 ROI 内连续停留 ≥ 此值才报警（0=关闭，进 ROI 即报）。抗误报。")
        f.addWidget(self.alm_dwell, row, 1)
        row += 1

        f.addWidget(QLabel("录像前录 (秒)"), row, 0)
        self.alm_pre = QDoubleSpinBox()
        self.alm_pre.setRange(0.0, 60.0)
        self.alm_pre.setSingleStep(0.5)
        self.alm_pre.setDecimals(1)
        f.addWidget(self.alm_pre, row, 1)
        row += 1

        f.addWidget(QLabel("录像后录 (秒)"), row, 0)
        self.alm_post = QDoubleSpinBox()
        self.alm_post.setRange(0.0, 60.0)
        self.alm_post.setSingleStep(0.5)
        self.alm_post.setDecimals(1)
        f.addWidget(self.alm_post, row, 1)

        f.setColumnStretch(1, 1)
        return g

    # ---- 外观 ----
    def _group_appearance(self) -> QGroupBox:
        g = QGroupBox("外观")
        f = QFormLayout(g)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)

        self.app_theme = QComboBox()
        self.app_theme.addItems(["dark", "light"])
        f.addRow(QLabel("主题"), self.app_theme)

        self.app_font = QSpinBox()
        self.app_font.setRange(10, 20)
        f.addRow(QLabel("字号"), self.app_font)

        self.app_primary = QLineEdit()
        self.app_primary.setInputMask(">#HHHHHH;")
        f.addRow(QLabel("主题色"), self.app_primary)
        return g

    # ---- 路径 ----
    def _group_paths(self) -> QGroupBox:
        g = QGroupBox("路径")
        f = QGridLayout(g)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        row = 0

        f.addWidget(QLabel("截图目录"), row, 0)
        self.p_snap = QLineEdit()
        self.p_snap_btn = QPushButton("浏览")
        self.p_snap_btn.setProperty("role", "flat")
        self.p_snap_btn.setIcon(load_svg_icon("folder_open", self.palette.fg_main, 16))
        self.p_snap_btn.clicked.connect(lambda: self._pick_dir(self.p_snap))
        f.addWidget(self.p_snap, row, 1)
        f.addWidget(self.p_snap_btn, row, 2)
        row += 1

        f.addWidget(QLabel("录像目录"), row, 0)
        self.p_clip = QLineEdit()
        self.p_clip_btn = QPushButton("浏览")
        self.p_clip_btn.setProperty("role", "flat")
        self.p_clip_btn.setIcon(load_svg_icon("folder_open", self.palette.fg_main, 16))
        self.p_clip_btn.clicked.connect(lambda: self._pick_dir(self.p_clip))
        f.addWidget(self.p_clip, row, 1)
        f.addWidget(self.p_clip_btn, row, 2)
        row += 1

        f.addWidget(QLabel("日志目录"), row, 0)
        self.p_log = QLineEdit()
        self.p_log_btn = QPushButton("浏览")
        self.p_log_btn.setProperty("role", "flat")
        self.p_log_btn.setIcon(load_svg_icon("folder_open", self.palette.fg_main, 16))
        self.p_log_btn.clicked.connect(lambda: self._pick_dir(self.p_log))
        f.addWidget(self.p_log, row, 1)
        f.addWidget(self.p_log_btn, row, 2)

        f.setColumnStretch(1, 1)
        return g

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
        self.alm_clip.setChecked(bool(cfg.get("alarm.enabled_clip", True)))
        self.alm_sound_file.setText(cfg.get("alarm.sound_file", ""))
        self.alm_cooldown.setValue(cfg.get("alarm.cooldown_seconds", 3.0))
        self.alm_dwell.setValue(cfg.get("alarm.dwell_seconds", 0.0))
        self.alm_pre.setValue(cfg.get("alarm.clip_pre_seconds", 2.0))
        self.alm_post.setValue(cfg.get("alarm.clip_post_seconds", 2.0))

        self.app_theme.setCurrentText(cfg.get("appearance.theme", "dark"))
        self.app_font.setValue(cfg.get("appearance.font_size", 13))
        self.app_primary.setText(cfg.get("appearance.primary_color", "#2D6CDF"))

        self.p_snap.setText(cfg.get("paths.snapshots_dir", "data/snapshots"))
        self.p_clip.setText(cfg.get("paths.clips_dir", "data/clips"))
        self.p_log.setText(cfg.get("paths.logs_dir", "data/logs"))

    def _reload(self) -> None:
        self._cfg.reload()
        self._load_values()

    def _apply(self) -> None:
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
        cfg.set("alarm.enabled_clip", self.alm_clip.isChecked())
        cfg.set("alarm.sound_file", self.alm_sound_file.text().strip())
        cfg.set("alarm.cooldown_seconds", self.alm_cooldown.value())
        cfg.set("alarm.dwell_seconds", self.alm_dwell.value())
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

    def _reset_defaults(self) -> None:
        self._cfg.reset_defaults(autosave=True)
        self._load_values()
        self.settings_applied.emit()

    def _export_config(self) -> None:
        """导出当前配置全量为 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "config_export.json", "JSON (*.json)")
        if not path:
            return
        if self._cfg.export_to_file(path):
            QMessageBox.information(self, "导出成功", f"配置已导出到：\n{path}")
        else:
            QMessageBox.warning(self, "导出失败", "写入文件失败，请检查路径权限。")

    def _import_config(self) -> None:
        """从 JSON 文件导入配置。

        导入时校验：非法字段用默认值静默替换，并提示哪些被重置。
        成功后刷新表单 + emit settings_applied 触发热重载。
        """
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON (*.json)")
        if not path:
            return
        ok, reset_keys = self._cfg.import_from_file(path)
        if not ok:
            QMessageBox.warning(self, "导入失败", "无法读取配置文件（文件不存在或不是合法 JSON）。")
            return
        # 刷新表单 + 热重载
        self._load_values()
        self.settings_applied.emit()
        if reset_keys:
            QMessageBox.warning(
                self, "导入完成（含校正）",
                "配置已导入，以下字段值非法，已恢复默认：\n" + "\n".join(reset_keys),
            )
        else:
            QMessageBox.information(self, "导入成功", "配置已导入并应用。")
