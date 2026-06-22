"""视频/摄像头推理工作线程。

在子线程读取帧 + YOLO 推理 + ROI 判定 + 统计，通过信号通知 UI。
不在此线程画 ROI 叠加（交由 VideoCanvas 用 QPainter 画，更灵活且支持交互）。

性能要点：
- 采集与推理解耦：_CaptureThread 独立读取相机/视频帧，只保留「最新一帧」
  （覆盖式单槽）。VideoWorker 主循环从最新帧消费，旧帧自动丢弃。
  这样即使某帧推理慢，下一帧也是相机当前画面，不会累积滞后。
- 打开后立即设 CAP_PROP_BUFFERSIZE=1，避免 OpenCV 内部缓冲堆积旧帧。
- Windows 下摄像头用 DSHOW 后端，降低打开延迟。
- frame_ready 发射前对 annotated 做 .copy()，避免与 ultralytics 内部
  缓冲跨线程共享导致撕裂。
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from PyQt5.QtCore import QThread, pyqtSignal

from app.core.detector import Detector
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector
from app.core.alarm import AlarmEngine
from app.utils.fps_counter import FpsCounter
from app.utils.logger import get_logger
from app.workers._frame_pipeline import process_frame

logger = get_logger()


class _CaptureThread(QThread):
    """独立的帧采集线程：持续 read，只保留最新一帧（覆盖式）。

    与推理线程解耦：相机/流持续推帧时，OpenCV 内部缓冲会堆积旧帧；
    本线程高频 read 把最新帧写入单槽，旧的被覆盖。推理线程取到的永远
    是「当下」的画面，消除「越来越滞后」的累积延迟。
    """

    def __init__(self, source, parent=None) -> None:
        super().__init__(parent)
        self._source = source
        self._stop_flag = False
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None      # 最新帧（受锁保护）
        self._ok = False                             # 最近一次 read 是否成功
        self._cap: cv2.VideoCapture | None = None

    def run(self) -> None:
        # 摄像头（int 源）在 Windows 下用 DSHOW 后端降低延迟；视频文件/RTSP 用默认。
        is_camera = not isinstance(self._source, str)
        if is_camera:
            cap = cv2.VideoCapture(self._source, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(self._source)
        # 关键：把 OpenCV 内部缓冲压到最小，read() 拿到的就是最新帧而非积压的旧帧。
        # 不是所有后端都支持，忽略失败即可。
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            self._ok = False
            return
        self._cap = cap

        while not self._stop_flag:
            ok, frame = cap.read()
            if not ok:
                # 视频文件读到末尾或流断开
                with self._lock:
                    self._ok = False
                    self._latest = None
                break
            with self._lock:
                self._latest = frame
                self._ok = True

        cap.release()

    def take(self) -> tuple[bool, np.ndarray | None]:
        """取走最新帧（并清空，避免同一帧被推理两次）。返回 (ok, frame)。"""
        with self._lock:
            frame = self._latest
            ok = self._ok
            self._latest = None  # 消费后清空，直到采集线程写入新帧
        return ok, frame

    def stop(self) -> None:
        self._stop_flag = True

    @property
    def is_open(self) -> bool:
        """源是否成功打开（打开尝试过后才准确）。"""
        return self._cap is not None and self._cap.isOpened()


class VideoWorker(QThread):
    """视频/摄像头推理。

    信号：
    - frame_ready(ndarray annotated, list violator_indices, object centers)
    - stats_ready(dict {"counts":..., "alarms":...})
    - fps_updated(float)
    - alarm_ready(int roi_id, list cls_ids, list confs)
    - error_occurred(str)
    - finished_source()  视频文件读到末尾
    - video_recorded(str tmp_path, float duration)  录制完成（带标注的临时视频）
    """

    frame_ready = pyqtSignal(np.ndarray, list, object)
    stats_ready = pyqtSignal(dict)
    fps_updated = pyqtSignal(float)
    alarm_ready = pyqtSignal(int, list, list)
    error_occurred = pyqtSignal(str)
    finished_source = pyqtSignal()
    video_recorded = pyqtSignal(str, float)

    def __init__(
        self,
        source,
        detector: Detector,
        roi_manager: RoiManager,
        stats: StatsCollector,
        alarm: AlarmEngine | None = None,
        record_path: str | None = None,
        record_fps: float = 25.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._detector = detector
        self._roi = roi_manager
        self._stats = stats
        self._alarm = alarm
        self._fps = FpsCounter()
        self._stop_flag = False
        self._pause_flag = False
        self._capture: _CaptureThread | None = None
        # 视频录制：传入 record_path 则把每帧 annotated 写入该文件（mp4v 编码）。
        # 用于「识别完即可回放带框视频」。
        self._record_path = record_path
        self._record_fps = float(record_fps)
        self._writer: cv2.VideoWriter | None = None
        self._record_start_ts: float | None = None

    def pause(self) -> None:
        self._pause_flag = True

    def resume(self) -> None:
        self._pause_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self._pause_flag = False
        if self._capture is not None:
            self._capture.stop()

    @property
    def source(self):
        return self._source

    def update_source(self, source) -> None:
        self._source = source

    def run(self) -> None:
        # 先启动采集线程；它会负责打开源、设置缓冲区、持续 read。
        self._capture = _CaptureThread(self._source)
        self._capture.start()

        # 等待采集线程打开源（给一段窗口）。打开是同步发生在 run() 开头，
        # 所以短时间内 is_open 就会变 True 或采集线程退出。
        for _ in range(100):  # 最多等 ~1s
            if self._stop_flag:
                break
            # 采集线程已确定结果（打开成功继续运行，或打开失败已退出）
            if self._capture.is_open or not self._capture.isRunning():
                break
            self.msleep(10)

        # 判定源是否成功打开
        if not self._capture.is_open:
            if not self._stop_flag:
                self.error_occurred.emit(f"无法打开数据源: {self._source}")
            self._capture.wait()
            return

        logger.info("VideoWorker 启动，源=%s", self._source)

        while not self._stop_flag:
            # 暂停：丢弃新帧，空转等待
            if self._pause_flag:
                # 暂停时也要消费掉采集线程不断推来的帧，避免缓冲膨胀
                self._capture.take()
                self.msleep(30)
                continue

            # 取最新帧（旧的已被采集线程覆盖 / 上次 take 清空）
            ok, frame = self._capture.take()
            if frame is None:
                # 暂时没有新帧：可能是推理比采集快。短暂等待，避免空转烧 CPU。
                # 区分两种情况：采集线程还在跑（等待新帧） vs 已结束（文件 EOF / 流断开）。
                if self._capture.isRunning():
                    self.msleep(1)
                    continue
                else:
                    # 采集线程已退出
                    if isinstance(self._source, str) and not ok:
                        logger.info("视频处理完毕")
                        self.finished_source.emit()
                    break

            try:
                result = self._detector.track_frame(frame)
            except Exception as e:
                logger.exception("推理异常: %s", e)
                continue

            # ROI 判定 + 报警 + 统计采样（与 ImageWorker 共享同一流水线）
            outcome = process_frame(
                result, self._roi, self._stats, self._alarm, use_tracking=True,
            )
            for rid, cls_ids, confs in outcome.fired_alarms:
                self.alarm_ready.emit(rid, cls_ids, confs)
            self.stats_ready.emit({"counts": outcome.counts, "alarms": outcome.alarms_this_frame})

            # FPS
            self._fps.tick()
            self.fps_updated.emit(self._fps.fps())

            # 录制：把带标注的帧写入临时文件（延迟到首帧才知尺寸）
            annotated_for_emit = result.annotated
            if self._record_path is not None:
                if self._writer is None:
                    h, w = annotated_for_emit.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    self._writer = cv2.VideoWriter(self._record_path, fourcc, self._record_fps, (w, h))
                    if self._writer.isOpened():
                        self._record_start_ts = time.time()
                        logger.info("开始录制到: %s (%dx%d@%.0ffps)", self._record_path, w, h, self._record_fps)
                    else:
                        logger.warning("VideoWriter 打开失败，录制取消: %s", self._record_path)
                        self._writer = None
                        self._record_path = None
                if self._writer is not None:
                    self._writer.write(annotated_for_emit)

            # 发帧：annotated 必须 copy，否则跨线程与 ultralytics 内部缓冲共享会撕裂。
            # centers 是本次推理新算出的 ndarray，所有权独占，无需 copy。
            self.frame_ready.emit(
                annotated_for_emit.copy(), outcome.violator_indices, outcome.centers,
            )

        # 收尾：停采集线程
        self._capture.stop()
        self._capture.wait()

        # 收尾：结束录制并通知（仅视频文件源 EOF 时才发，表示「完整结果已就绪」）
        if self._writer is not None:
            duration = time.time() - self._record_start_ts if self._record_start_ts else 0.0
            self._writer.release()
            self._writer = None
            # 仅在自然播完（非用户停止）时通知「录制就绪可保存」
            if isinstance(self._source, str):
                self.video_recorded.emit(self._record_path, duration)

        logger.info("VideoWorker 结束")

    # 不实现 __del__：解释器关闭阶段 C++ 对象可能已销毁，
    # __del__ 调用 self.wait() 会抛 RuntimeError（"wrapped C/C++ object has been deleted"）。
    # 正常停止由 MainWindow._stop_worker() 显式调用 stop()+wait() 处理。
