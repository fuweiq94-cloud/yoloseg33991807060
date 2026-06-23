"""多摄像头并发 worker：方案 A（单线程串行推理 + 多路采集 + 多画面）。

设计要点：
- 1 个 worker 线程，N 个 _CaptureThread（每路一个）并行采集。
- 主循环轮流取各路最新帧，**串行**调用同一个 Detector 推理（满足 detector.py
  的"同一实例仅在调用方线程内使用"线程安全约束，不增加显存）。
- 对每路帧分别调 process_frame，传入该路各自的 roi/stats/alarm 实例——
  process_frame 是无状态纯函数，per-source 隔离天然成立。
- 串行推理的代价：N 路 CPU 推理时每路 FPS ≈ 单路的 1/N。这是方案 A 固有权衡。

仅支持摄像头源（int）。视频文件/图片仍走单源 VideoWorker/ImageWorker 路径，
本 worker 不涉及，保持单源路径零风险。
"""
from __future__ import annotations

import time

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from app.core.detector import Detector
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector
from app.core.alarm import AlarmEngine
from app.utils.fps_counter import FpsCounter
from app.utils.logger import get_logger
from app.workers._frame_pipeline import process_frame
# _CaptureThread 是 video_worker 内的私有类，但接口稳定（take/stop/is_open），
# 直接复用避免重复实现采集逻辑。摄像头源走覆盖式单槽，满足低延迟需求。
from app.workers.video_worker import _CaptureThread

logger = get_logger()


class MultiSourceVideoWorker(QThread):
    """多摄像头并发推理（方案 A：串行推理）。

    信号（全部带 source_id 0..N-1 路由）：
    - frame_ready(int source_id, ndarray annotated, list violator_indices, object centers)
    - details_ready(int source_id, object details)
    - fps_updated(int source_id, float fps)
    - alarm_ready(int roi_id, list cls_ids, list confs)  # 报警不带 source_id（与单源兼容）
    - finished_source(int source_id)
    - error_occurred(int source_id, str msg)
    - all_finished()  # 所有路都结束
    """

    frame_ready = pyqtSignal(int, np.ndarray, list, object)
    details_ready = pyqtSignal(int, object)
    fps_updated = pyqtSignal(int, float)
    alarm_ready = pyqtSignal(int, list, list)
    error_occurred = pyqtSignal(int, str)
    finished_source = pyqtSignal(int)
    all_finished = pyqtSignal()

    def __init__(
        self,
        sources: list[int],
        detector: Detector,
        roi_managers: list[RoiManager],
        stats_list: list[StatsCollector],
        alarms: list[AlarmEngine | None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        assert len(sources) == len(roi_managers) == len(stats_list) == len(alarms), \
            "sources / roi_managers / stats_list / alarms 数量必须一致"
        self._sources = list(sources)
        self._detector = detector
        self._roi_managers = list(roi_managers)
        self._stats_list = list(stats_list)
        self._alarms = list(alarms)
        self._n = len(sources)
        self._stop_flag = False
        self._pause_flag = False
        # 每路一个采集线程 + FPS 计数器
        self._captures: list[_CaptureThread | None] = [None] * self._n
        self._fps_counters = [FpsCounter() for _ in range(self._n)]
        # 每路是否已结束（采集线程退出即标记，全部结束则 all_finished）
        self._done = [False] * self._n

    def pause(self) -> None:
        self._pause_flag = True

    def resume(self) -> None:
        self._pause_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self._pause_flag = False
        for cap in self._captures:
            if cap is not None:
                cap.stop()

    def run(self) -> None:
        # 启动所有路的采集线程
        for i, src in enumerate(self._sources):
            cap = _CaptureThread(src)
            cap.start()
            self._captures[i] = cap

        # 等待所有路打开源（给一段窗口）
        for _ in range(100):
            if self._stop_flag:
                break
            if all(cap is not None and (cap.is_open or not cap.isRunning()) for cap in self._captures):
                break
            self.msleep(10)

        # 检查每路是否打开成功；失败的路标记结束并报错，不影响其他路
        for i, cap in enumerate(self._captures):
            if cap is None or not cap.is_open:
                self._done[i] = True
                if not self._stop_flag:
                    self.error_occurred.emit(i, f"无法打开摄像头 {self._sources[i]}")
                if cap is not None:
                    cap.wait()

        active = [i for i in range(self._n) if not self._done[i]]
        if active:
            logger.info("MultiSourceWorker 启动，源=%s", [self._sources[i] for i in active])

        # 主循环：轮流取各路最新帧，串行推理
        while not self._stop_flag and active:
            if self._pause_flag:
                # 暂停时消费各路帧防缓冲膨胀
                for i in active:
                    if self._captures[i] is not None:
                        self._captures[i].take()
                self.msleep(30)
                continue

            still_active = []
            for i in active:
                if self._stop_flag:
                    break
                cap = self._captures[i]
                if cap is None:
                    continue
                ok, frame = cap.take()
                if frame is None:
                    if cap.isRunning():
                        # 暂无新帧，稍后重试
                        still_active.append(i)
                        continue
                    else:
                        # 该路采集线程退出（摄像头断开）
                        self._done[i] = True
                        self.finished_source.emit(i)
                        continue

                # 串行推理（同一 Detector 实例，单线程内调用，满足线程安全）
                try:
                    result = self._detector.track_frame(frame)
                except Exception as e:
                    logger.exception("路 %d 推理异常: %s", i, e)
                    still_active.append(i)
                    continue

                # per-source 处理：用该路的 roi/stats/alarm
                outcome = process_frame(
                    result, self._roi_managers[i], self._stats_list[i],
                    self._alarms[i], use_tracking=True,
                )
                for rid, cls_ids, confs in outcome.fired_alarms:
                    self.alarm_ready.emit(rid, cls_ids, confs)

                # FPS（每路独立）
                self._fps_counters[i].tick()

                # 发帧 + 详情（带 source_id 路由）
                self.frame_ready.emit(
                    i, result.annotated.copy(), outcome.violator_indices, outcome.centers,
                )
                self.details_ready.emit(i, outcome.details)
                self.fps_updated.emit(i, self._fps_counters[i].fps())
                still_active.append(i)

            active = still_active

        # 收尾：停所有采集线程
        for cap in self._captures:
            if cap is not None:
                cap.stop()
                cap.wait()

        if not self._stop_flag:
            self.all_finished.emit()
        logger.info("MultiSourceWorker 结束")

    @property
    def source_ids(self) -> list[int]:
        """有效的路 id 列表（0..N-1）。"""
        return list(range(self._n))

    def source_label(self, source_id: int) -> str:
        """路的可读标签（如「摄像头 0」）。"""
        return f"摄像头{self._sources[source_id]}" if 0 <= source_id < self._n else "?"
