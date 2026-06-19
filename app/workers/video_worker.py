"""视频/摄像头推理工作线程。

在子线程读取帧 + YOLO 推理 + ROI 判定 + 统计，通过信号通知 UI。
不在此线程画 ROI 叠加（交由 VideoCanvas 用 QPainter 画，更灵活且支持交互）。
"""
from __future__ import annotations

import time
import numpy as np
import cv2

from PyQt5.QtCore import QThread, pyqtSignal

from app.core.detector import Detector
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector
from app.core.alarm import AlarmEngine
from app.utils.fps_counter import FpsCounter
from app.utils.logger import get_logger

logger = get_logger()


class VideoWorker(QThread):
    """摄像头/视频文件的推理循环线程。

    信号：
        frame_ready(ndarray annotated, list violator_box_indices, ndarray centers)
            —— 标注帧 + 进入ROI的框索引 + 所有框中心点（供画布画ROI高亮）
        stats_ready(dict)
            —— 采样统计 {counts: {cls:n}, alarms: n}
        alarm_ready(int roi_id, list cls_ids, list confs)
            —— 触发报警
        fps_updated(float)
        error_occurred(str)
        finished_source()  —— 视频/图片处理完毕
    """

    frame_ready = pyqtSignal(np.ndarray, list, object)
    stats_ready = pyqtSignal(dict)
    alarm_ready = pyqtSignal(int, list, list)
    fps_updated = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    finished_source = pyqtSignal()

    def __init__(
        self,
        source,                       # int(摄像头) 或 str(视频路径)
        detector: Detector,
        roi_manager: RoiManager,
        stats: StatsCollector,
        alarm: AlarmEngine | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._detector = detector
        self._roi = roi_manager
        self._stats = stats
        self._alarm = alarm
        self._fps = FpsCounter(window=30)

        # 控制标志（主线程写、工作线程读；单一写者，无需加锁）
        self._stop_flag = False
        self._pause_flag = False

    # ---- 控制 ----
    def pause(self) -> None:
        self._pause_flag = True

    def resume(self) -> None:
        self._pause_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self._pause_flag = False

    @property
    def source(self):
        return self._source

    def update_source(self, source) -> None:
        self._source = source

    # ---- 主循环 ----
    def run(self) -> None:
        cap = cv2.VideoCapture(self._source)
        if not cap.isOpened():
            self.error_occurred.emit(f"无法打开数据源: {self._source}")
            return

        logger.info("VideoWorker 启动，源=%s", self._source)
        while not self._stop_flag:
            # 暂停：空转等待
            if self._pause_flag:
                self.msleep(30)
                continue

            ok, frame = cap.read()
            if not ok:
                # 视频文件读到末尾
                if isinstance(self._source, str):
                    logger.info("视频处理完毕")
                    self.finished_source.emit()
                break

            try:
                result = self._detector.track_frame(frame)
            except Exception as e:
                logger.exception("推理异常: %s", e)
                continue

            # ROI 判定
            centers = result.centers()
            violators = self._roi.violators(centers, result.clss)  # [(box_idx, roi_id), ...]
            violator_indices = [bi for bi, _ in violators]

            # 报警（若有违反且注入了 alarm 引擎）
            alarms_this_frame = 0
            if violators and self._alarm is not None:
                # 按 roi 聚合
                by_roi: dict[int, list[int]] = {}
                for bi, rid in violators:
                    by_roi.setdefault(rid, []).append(bi)
                for rid, idxs in by_roi.items():
                    cls_ids = [int(result.clss[bi]) for bi in idxs]
                    confs = [float(result.confs[bi]) for bi in idxs]
                    from app.core.alarm import AlarmEvent
                    event = AlarmEvent(
                        timestamp=time.time(),
                        frame=result.annotated.copy(),
                        cls_ids=cls_ids,
                        confs=confs,
                        roi_id=rid,
                        box_indices=list(idxs),
                    )
                    fired = self._alarm.trigger(event)
                    if fired:
                        alarms_this_frame += 1
                        self.alarm_ready.emit(rid, cls_ids, confs)

            # 统计采样
            counts: dict[int, int] = {}
            for cid in result.clss:
                counts[int(cid)] = counts.get(int(cid), 0) + 1
            # 传入 track_ids 用于去重累计（同目标多帧只计一次）
            tids = [int(t) for t in result.track_ids] if len(result.track_ids) else []
            clss_list = [int(c) for c in result.clss] if len(result.clss) else []
            self._stats.record(
                time.time(), counts, alarms_this_frame,
                track_ids=tids, clss=clss_list,
            )
            self.stats_ready.emit({"counts": counts, "alarms": alarms_this_frame})

            # FPS
            self._fps.tick()
            self.fps_updated.emit(self._fps.fps())

            # 发帧（annotated + 违反框索引 + 中心点用于画布交互）
            self.frame_ready.emit(result.annotated, violator_indices, centers)

        cap.release()
        logger.info("VideoWorker 结束")

    # 不实现 __del__：解释器关闭阶段 C++ 对象可能已销毁，
    # __del__ 调用 self.wait() 会抛 RuntimeError（"wrapped C/C++ object has been deleted"）。
    # 正常停止由 MainWindow._stop_worker() 显式调用 stop()+wait() 处理。
