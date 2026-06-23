"""视频/摄像头推理工作线程。

在子线程读取帧 + YOLO 推理 + ROI 判定 + 统计，通过信号通知 UI。
不在此线程画 ROI 叠加（交由 VideoCanvas 用 QPainter 画，更灵活且支持交互）。

性能要点：
- 采集与推理解耦：_CaptureThread 独立读取相机/视频帧，VideoWorker 主循环消费。
- 视频文件用「有界队列 + 背压」：队列满则采集阻塞，绝不丢帧，保证每帧都被
  推理 + 录制 → 回放时长与原片一致，画面严格顺序前进不跳帧。
- 摄像头用「覆盖式单槽」：只保留最新帧，消除累积延迟，旧帧丢弃可接受。
- 打开后立即设 CAP_PROP_BUFFERSIZE=1，避免 OpenCV 内部缓冲堆积旧帧。
- Windows 下摄像头用 DSHOW 后端，降低打开延迟。
- frame_ready 发射前对 annotated 做 .copy()，避免与 ultralytics 内部
  缓冲跨线程共享导致撕裂。
"""
from __future__ import annotations

import threading
import time
from collections import deque

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
    """独立的帧采集线程。

    两种策略（按源类型选择）：
    - 视频文件（str 源）：有界队列 + **背压**。队列满时生产者阻塞等待消费者
      取走，绝不丢帧——这是视频「完整识别」的关键（18s 视频的每一帧都会被
      推理 + 录制，回放时长与原片一致）。消费者按 FIFO 取，画面严格顺序前进，
      不会跳帧/停顿。
    - 摄像头/RTSP（int 源）：覆盖式单槽，只保留最新帧，消除累积延迟
      （实时流场景要求低延迟，丢旧帧可接受）。

    注：cv2.VideoCapture.read() 对本地文件是「尽快读」（不受源帧率约束），
    若用「满则丢最旧」策略，推理只要稍慢于磁盘读取，绝大多数帧就会被丢弃，
    导致 N 秒视频只录到 1~2 秒。背压（满则阻塞）从根上杜绝此问题。
    """

    # 视频文件的有界缓冲帧数（背压语义：满则生产者阻塞，而非丢帧）。
    # 30 帧 × ~1MB ≈ 30MB，足够吸收推理与读取间的短时抖动。
    _FILE_QUEUE_MAX = 30

    def __init__(self, source, parent=None) -> None:
        super().__init__(parent)
        self._source = source
        self._is_file = isinstance(source, str)
        self._stop_flag = False
        # Condition 自带锁：生产者/消费者用同一把锁协调「满则等 / 取走则唤醒」。
        self._cond = threading.Condition()
        # 摄像头模式用单槽
        self._latest: np.ndarray | None = None
        self._ok = False
        # 视频文件模式用有界队列
        self._queue: deque = deque()
        self._cap: cv2.VideoCapture | None = None
        self._source_fps: float = 0.0
        self._frame_count: int = 0
        # seek 请求：VideoWorker 设置后，本线程在下次 read 前「重定位采集位置 +
        # 清空背压队列」。None 表示无待处理的 seek。仅视频文件源有效。
        self._seek_req: int | None = None

    def run(self) -> None:
        # 摄像头（int 源）在 Windows 下用 DSHOW 后端降低延迟；视频文件/RTSP 用默认。
        if self._is_file:
            cap = cv2.VideoCapture(self._source)
        else:
            cap = cv2.VideoCapture(self._source, cv2.CAP_DSHOW)
        # 关键：把 OpenCV 内部缓冲压到最小，read() 拿到的就是最新帧而非积压的旧帧。
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            with self._cond:
                self._ok = False
            return
        self._cap = cap
        # 读源帧率，供 VideoWorker 写录制文件时对齐时长（18s@30fps → 录制也 18s）。
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            self._source_fps = float(fps) if fps and fps > 0 else 0.0
        except Exception:
            self._source_fps = 0.0
        # 读总帧数，供进度条显示总时长 / seek 边界检查。
        try:
            fc = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            self._frame_count = int(fc) if fc and fc > 0 else 0
        except Exception:
            self._frame_count = 0

        while not self._stop_flag:
            # seek 请求处理（仅视频文件源有）：重定位 cv2 指针 + 清空背压队列，
            # 让后续 read 从新位置产出，消费者也不会拿到 seek 前的陈旧帧。
            # 生产者侧处理最干净——读循环是串行的，此时没有并发 read。
            if self._seek_req is not None:
                target = self._seek_req
                self._seek_req = None
                with self._cond:
                    self._queue.clear()
                    self._cond.notify_all()
                if 0 <= target < max(1, self._frame_count):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                # 复位「读到末尾」标记：seek 后即使之前 EOF，也可继续读
                with self._cond:
                    self._ok = True

            ok, frame = cap.read()
            if not ok:
                # 视频文件读到末尾或流断开
                with self._cond:
                    self._ok = False
                    self._latest = None
                break
            with self._cond:
                if self._is_file:
                    # 背压：队列满则等待消费者取走，绝不丢帧。
                    while len(self._queue) >= self._FILE_QUEUE_MAX and not self._stop_flag:
                        self._cond.wait(timeout=0.1)
                    if self._stop_flag:
                        break
                    self._queue.append(frame)
                    self._ok = True
                else:
                    # 摄像头：覆盖式，只留最新
                    self._latest = frame
                    self._ok = True

        cap.release()

    def take(self) -> tuple[bool, np.ndarray | None]:
        """取走下一帧。视频文件按队列顺序（FIFO），摄像头取最新。返回 (ok, frame)。"""
        with self._cond:
            if self._is_file:
                if self._queue:
                    frame = self._queue.popleft()
                    self._cond.notify()  # 唤醒可能因「队列满」而阻塞的生产者
                    return True, frame
                # 队列空但采集线程还在跑：返回 None，主循环会短暂等待
                return self._ok, None
            else:
                frame = self._latest
                ok = self._ok
                self._latest = None  # 消费后清空
                return ok, frame

    def stop(self) -> None:
        with self._cond:
            self._stop_flag = True
            self._cond.notify_all()  # 唤醒因背压阻塞的生产者，使其能及时退出

    def request_seek(self, frame_idx: int) -> None:
        """请求 seek 到指定帧。仅视频文件源有意义。实际重定位由生产者线程在
        下一次 read 前执行（见 run()）。清空背压队列在请求时即完成，避免消费者
        在 seek 落地前又消费到旧帧。"""
        with self._cond:
            self._queue.clear()
            self._cond.notify_all()
            self._seek_req = frame_idx

    @property
    def is_open(self) -> bool:
        """源是否成功打开（打开尝试过后才准确）。"""
        return self._cap is not None and self._cap.isOpened()

    @property
    def source_fps(self) -> float:
        """源视频的帧率（文件打开后才有效）；摄像头为 0.0。"""
        return self._source_fps

    @property
    def frame_count(self) -> int:
        """源视频总帧数（文件打开后才有效，可能为 0 表示读不到）；摄像头为 0。"""
        return self._frame_count


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
    - progress_updated(int cur_frame, int total_frames)  仅视频文件源发，
      用于驱动检测页/ROI 页内联进度条。cur_frame 为已推理帧的序号（从 1 起）。
    """

    frame_ready = pyqtSignal(np.ndarray, list, object)
    stats_ready = pyqtSignal(dict)
    fps_updated = pyqtSignal(float)
    alarm_ready = pyqtSignal(int, list, list)
    error_occurred = pyqtSignal(str)
    finished_source = pyqtSignal()
    video_recorded = pyqtSignal(str, float)
    progress_updated = pyqtSignal(int, int)
    details_ready = pyqtSignal(object)   # 载荷为 FrameDetails，供检测页目标详情面板
    clip_recorded = pyqtSignal(str)      # 报警片段录像完成，载荷为 mp4 路径

    def __init__(
        self,
        source,
        detector: Detector,
        roi_manager: RoiManager,
        stats: StatsCollector,
        alarm: AlarmEngine | None = None,
        record_path: str | None = None,
        record_fps: float = 25.0,
        alarm_clip_config: dict | None = None,
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
        # 报警片段录像配置：{enabled, pre, post, clips_dir}。
        # 录制由本 worker 完成（需回溯触发前的帧，只有持有帧流的 worker 能做到）。
        cfg = alarm_clip_config or {}
        self._clip_enabled = bool(cfg.get("enabled", False))
        self._clip_pre = max(0.0, float(cfg.get("pre", 0.0)))
        self._clip_post = max(0.0, float(cfg.get("post", 0.0)))
        self._clips_dir = str(cfg.get("clips_dir", "data/clips"))
        # 环形帧缓冲：存最近 pre 秒的标注帧（.copy()，避免与 ultralytics 缓冲共享撕裂）。
        # 容量取 pre 秒帧数，帧率在首帧后才稳定，构造时先用 record_fps 估，run() 里校正。
        ring_cap = max(1, int(self._clip_pre * self._record_fps)) if self._clip_enabled else 0
        self._ring: deque = deque(maxlen=ring_cap)
        # 当前正在录的报警片段状态：writer/剩余 post 帧/文件路径
        self._clip_writer: cv2.VideoWriter | None = None
        self._clip_remaining: int = 0
        self._clip_path: str | None = None
        # 进度：已推理的帧序号。seek 时重置为对应位置，保证进度条与画面一致。
        self._frame_idx: int = 0
        # 待应用的 seek 请求（主循环顶部处理）。仅视频文件源有效。
        self._seek_req: int | None = None
        # seek 期间暂停往录制文件写入，避免时间倒流污染录制。
        self._seeking: bool = False

    def pause(self) -> None:
        self._pause_flag = True

    def resume(self) -> None:
        self._pause_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self._pause_flag = False
        if self._capture is not None:
            self._capture.stop()

    def seek(self, frame_idx: int) -> None:
        """请求跳转到指定帧。仅视频文件源有效；相机源 no-op。
        线程安全：只设置请求标志，实际重定位在主循环顶部执行（单消费者，
        避免与背压队列并发）。seek 后重置 tracker，避免 track_id 串台。"""
        if not isinstance(self._source, str):
            return
        self._seek_req = max(0, frame_idx)

    @property
    def source(self):
        return self._source

    @property
    def frame_count(self) -> int:
        """源视频总帧数（采集线程打开源后才有效）；非文件源为 0。"""
        return self._capture.frame_count if self._capture is not None else 0

    @property
    def source_fps(self) -> float:
        """源视频帧率（采集线程打开源后才有效）；非文件源为 0。"""
        return self._capture.source_fps if self._capture is not None else 0.0

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
            # ---- seek 请求处理（主循环顶部 = 唯一消费者，无并发）----
            if self._seek_req is not None:
                target = self._seek_req
                self._seek_req = None
                self._seeking = True
                # 让采集线程重定位 + 清空其背压队列
                self._capture.request_seek(target)
                # 重置 tracker，避免跳转后旧 track_id 串台
                try:
                    self._detector.reset_tracker()
                except Exception:
                    pass
                self._frame_idx = target
                # seek 后丢弃已入队但还没消费的「旧位置」帧（request_seek 已清空，
                # 这里再 take 几次兜底，确保下一帧就是新位置的）
                continue

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

            # seek 落地后的第一帧：清掉 seeking 标志
            self._seeking = False

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

            # 进度：仅视频文件源发，驱动检测页/ROI 页内联进度条
            if isinstance(self._source, str):
                self._frame_idx += 1
                total = self._capture.frame_count
                self.progress_updated.emit(self._frame_idx, total)

            # 录制：把带标注的帧写入临时文件（延迟到首帧才知尺寸）
            annotated_for_emit = result.annotated
            if self._record_path is not None and not self._seeking:
                if self._writer is None:
                    h, w = annotated_for_emit.shape[:2]
                    # 用源帧率写录制文件，使「写入帧数 / 帧率」≈ 原片时长
                    # （背压保证每帧都被写入，故时长对齐）。
                    src_fps = self._capture.source_fps
                    rec_fps = src_fps if src_fps > 0 else self._record_fps
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    self._writer = cv2.VideoWriter(self._record_path, fourcc, rec_fps, (w, h))
                    if self._writer.isOpened():
                        self._record_start_ts = time.time()
                        logger.info("开始录制到: %s (%dx%d@%.1ffps)", self._record_path, w, h, rec_fps)
                    else:
                        logger.warning("VideoWriter 打开失败，录制取消: %s", self._record_path)
                        self._writer = None
                        self._record_path = None
                if self._writer is not None:
                    self._writer.write(annotated_for_emit)

            # 报警片段录像：环形缓冲 + 触发即录（pre 秒历史 + post 秒后续）。
            # 仅非 seek 期执行（seek 会让帧时间倒流，污染片段）。
            if self._clip_enabled and not self._seeking:
                # 1) 每帧把标注帧压入环形缓冲（.copy()，避免与 ultralytics 缓冲共享撕裂）。
                #    首帧才知道尺寸/源帧率，此时按真实源帧率校正环形缓冲容量。
                if self._ring.maxlen == 0 or (
                    self._capture.source_fps > 0
                    and self._ring.maxlen != max(1, int(self._clip_pre * self._capture.source_fps))
                ):
                    src_fps = self._capture.source_fps or self._record_fps
                    self._ring = deque(
                        self._ring,
                        maxlen=max(1, int(self._clip_pre * src_fps)),
                    )
                self._ring.append(annotated_for_emit.copy())

                # 2) 真触发且当前未在录：启动新片段，先把环形缓冲（pre 秒历史）全部写入。
                if outcome.fired_alarms and self._clip_writer is None:
                    self._start_alarm_clip(annotated_for_emit)
                # 3) 正在录：写当前帧并递减剩余帧；归零则关闭片段并通知。
                if self._clip_writer is not None:
                    self._clip_writer.write(annotated_for_emit)
                    self._clip_remaining -= 1
                    if self._clip_remaining <= 0:
                        self._finalize_alarm_clip(notify=True)

            # 发帧：annotated 必须 copy，否则跨线程与 ultralytics 内部缓冲共享会撕裂。
            # centers 是本次推理新算出的 ndarray，所有权独占，无需 copy。
            self.frame_ready.emit(
                annotated_for_emit.copy(), outcome.violator_indices, outcome.centers,
            )
            # 目标详情（供检测页面板，1Hz 节流消费）
            self.details_ready.emit(outcome.details)

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

        # 收尾：关闭仍在录的报警片段（用户停止或 EOF），但不通知——半截片段不归档
        if self._clip_writer is not None:
            self._finalize_alarm_clip(notify=False)

        logger.info("VideoWorker 结束")

    # ---- 报警片段录像辅助 ----
    def _start_alarm_clip(self, frame: np.ndarray) -> None:
        """开始录一段报警片段：先写入环形缓冲（pre 秒历史），再准备录 post 秒后续。

        frame 仅用于取尺寸建 writer；历史帧来自 self._ring。
        连续报警（post 期内又触发）不延长——当前已在录时直接忽略（见调用处判空）。
        """
        import os
        from datetime import datetime
        try:
            os.makedirs(self._clips_dir, exist_ok=True)
        except OSError:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(self._clips_dir, f"alarm_{ts}.mp4")
        h, w = frame.shape[:2]
        src_fps = self._capture.source_fps if self._capture else 0.0
        fps = src_fps if src_fps > 0 else self._record_fps
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(path, fourcc, fps, (w, h))
        if not writer.isOpened():
            logger.warning("报警片段 VideoWriter 打开失败: %s", path)
            return
        # 先回溯写入环形缓冲的全部历史帧（触发前 pre 秒）
        for past in self._ring:
            writer.write(past)
        self._clip_writer = writer
        self._clip_path = path
        self._clip_remaining = max(1, int(self._clip_post * fps))
        logger.info("开始录报警片段: %s (pre=%d帧, post=%d帧)", path, len(self._ring), self._clip_remaining)

    def _finalize_alarm_clip(self, notify: bool) -> None:
        """关闭当前报警片段。notify=True（post 秒录满）则 emit 路径供归档；
        notify=False（被停止中断）则静默丢弃，不归档半截片段。"""
        if self._clip_writer is None:
            return
        self._clip_writer.release()
        path = self._clip_path
        self._clip_writer = None
        self._clip_path = None
        self._clip_remaining = 0
        if notify and path:
            logger.info("报警片段录制完成: %s", path)
            self.clip_recorded.emit(path)
        else:
            logger.info("报警片段被中断，丢弃: %s", path)

    # 不实现 __del__：解释器关闭阶段 C++ 对象可能已销毁，
    # __del__ 调用 self.wait() 会抛 RuntimeError（"wrapped C/C++ object has been deleted"）。
    # 正常停止由 MainWindow._stop_worker() 显式调用 stop()+wait() 处理。
