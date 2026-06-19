"""单图推理工作线程：一次性读取图片、推理、ROI 判定、统计，发信号后退出。"""
from __future__ import annotations

import time

import cv2
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from app.core.detector import Detector
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector
from app.core.alarm import AlarmEngine, AlarmEvent
from app.utils.logger import get_logger

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

        centers = result.centers()
        violators = self._roi.violators(centers, result.clss)
        violator_indices = [bi for bi, _ in violators]

        alarms_this_frame = 0
        if violators and self._alarm is not None:
            by_roi: dict[int, list[int]] = {}
            for bi, rid in violators:
                by_roi.setdefault(rid, []).append(bi)
            for rid, idxs in by_roi.items():
                cls_ids = [int(result.clss[bi]) for bi in idxs]
                confs = [float(result.confs[bi]) for bi in idxs]
                event = AlarmEvent(
                    timestamp=time.time(),
                    frame=result.annotated.copy(),
                    cls_ids=cls_ids,
                    confs=confs,
                    roi_id=rid,
                    box_indices=list(idxs),
                )
                if self._alarm.trigger(event):
                    alarms_this_frame += 1
                    self.alarm_ready.emit(rid, cls_ids, confs)

        counts: dict[int, int] = {}
        for cid in result.clss:
            counts[int(cid)] = counts.get(int(cid), 0) + 1
        self._stats.record(time.time(), counts, alarms_this_frame)
        self.stats_ready.emit({"counts": counts, "alarms": alarms_this_frame})

        self.frame_ready.emit(result.annotated, violator_indices, centers)
        self.finished_source.emit()
