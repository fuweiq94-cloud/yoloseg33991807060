"""单图推理工作线程：一次性读取图片、推理、ROI 判定、统计，发信号后退出。"""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from app.core.detector import Detector
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector
from app.core.alarm import AlarmEngine
from app.utils.logger import get_logger
from app.workers._frame_pipeline import process_frame

logger = get_logger()


class ImageWorker(QThread):
    """单图推理。信号与 VideoWorker 一致，便于 UI 统一处理。"""

    frame_ready = pyqtSignal(np.ndarray, list, object)
    stats_ready = pyqtSignal(dict)
    alarm_ready = pyqtSignal(int, list, list)
    error_occurred = pyqtSignal(str)
    finished_source = pyqtSignal()

    def __init__(
        self,
        image_path: str,
        detector: Detector,
        roi_manager: RoiManager,
        stats: StatsCollector,
        alarm: AlarmEngine | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._image_path = image_path
        self._detector = detector
        self._roi = roi_manager
        self._stats = stats
        self._alarm = alarm

    def run(self) -> None:
        frame = cv2.imdecode(np.fromfile(self._image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            self.error_occurred.emit(f"无法读取图片: {self._image_path}")
            return
        try:
            result = self._detector.predict_frame(frame)
        except Exception as e:
            logger.exception("图片推理异常: %s", e)
            self.error_occurred.emit(f"推理失败: {e}")
            return

        # ROI 判定 + 报警 + 统计采样（与 VideoWorker 共享同一流水线）
        outcome = process_frame(
            result, self._roi, self._stats, self._alarm, use_tracking=False,
        )
        for rid, cls_ids, confs in outcome.fired_alarms:
            self.alarm_ready.emit(rid, cls_ids, confs)
        self.stats_ready.emit({"counts": outcome.counts, "alarms": outcome.alarms_this_frame})

        self.frame_ready.emit(result.annotated, outcome.violator_indices, outcome.centers)
        self.finished_source.emit()
