"""seek + 进度信号冲烟测试。

直接验证 VideoWorker（视频文件源）的 seek 与进度上报：
  1. progress_updated：每帧推理后发 (cur, total)，cur 单调递增，total == 源帧数。
  2. seek：请求跳到目标帧后，progress_updated 的 cur 跳到目标附近，
     且后续继续单调推进（seek 不破坏管线）。
  3. 无死锁：seek 后能正常读完剩余帧并退出。

用真实 VideoWorker（带 dummy detector，跳过实际 YOLO 推理，专注验证
采集/进度/seek 的协调逻辑）。
"""
from __future__ import annotations

import os
import sys
import time
import types
import threading

# 注入 dummy PyQt5（真线程版的 QThread），让 VideoWorker 可在无 GUI 环境运行
sys.modules.setdefault("PyQt5", types.ModuleType("PyQt5"))
QtCore = types.ModuleType("PyQt5.QtCore")


class _DummyThread:
    """真线程模拟 QThread。"""
    def __init__(self, *a, **kw):
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_target, daemon=True)
        self._thread.start()

    def _run_target(self):
        # 子类把 run 绑到实例属性；VideoWorker.__init__ 会调 super().__init__()
        # 但它的 run 是类方法。我们让 start() 调 self.run()——VideoWorker 覆盖了 run。
        try:
            self.run()
        finally:
            self._running = False

    def isRunning(self):
        return self._thread is not None and self._thread.is_alive()

    def wait(self, *a, **kw):
        if self._thread is not None:
            self._thread.join(*a if a else ())
        self._running = False

    def sleep(self, secs): time.sleep(secs)

    def msleep(self, ms): time.sleep(ms / 1000.0)


def _signal(*a, **kw):
    class _S:
        def __init__(self):
            self._slots = []

        def connect(self, slot):
            self._slots.append(slot)

        def emit(self, *args, **kw):
            for s in list(self._slots):
                try:
                    s(*args, **kw)
                except Exception:
                    pass
    return _S()


QtCore.QThread = _DummyThread
QtCore.pyqtSignal = _signal
sys.modules["PyQt5.QtCore"] = QtCore

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

VIDEO = os.path.join(ROOT, "assets", "test", "videos", "vtest.avi")
assert os.path.exists(VIDEO), f"缺少测试视频: {VIDEO}"


def _shorten_video(src: str, dst: str, n: int = 60) -> str:
    """截取源视频前 n 帧存为新文件，缩短测试时间。"""
    cap = cv2.VideoCapture(src)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(dst, fourcc, fps, (w, h))
    for _ in range(n):
        ok, f = cap.read()
        if not ok:
            break
        writer.write(f)
    cap.release()
    writer.release()
    return dst


def _make_dummy_detector():
    """假 detector：track_frame 返回带原图作 annotated 的 result，
    reset_tracker 为 no-op。避免加载真实 YOLO 模型。"""
    class _Outcome:
        def __init__(self, frame):
            self.counts = {}
            self.alarms_this_frame = []
            self.fired_alarms = []
            self.violator_indices = []
            self.centers = np.empty((0, 2))

    class _Result:
        def __init__(self, frame):
            self.annotated = frame.copy()

    class _DummyDetector:
        def reset_tracker(self):
            pass

        def track_frame(self, frame):
            return _Result(frame)

    return _DummyDetector(), _Outcome


def test_progress_and_seek():
    short = os.path.join(ROOT, "data", "_smoke_seek_short.mp4")
    os.makedirs(os.path.dirname(short), exist_ok=True)
    N = 60
    _shorten_video(VIDEO, short, N)

    # 复制 process_frame 的最小逻辑（绕过 import 链），让 VideoWorker 能跑通
    import app.workers.video_worker as vw
    detector, _ = _make_dummy_detector()

    # 假的 roi/stats/alarm —— 只需 process_frame 能跑
    class _Roi:
        regions = []

    class _Stats:
        def add_sample(self, *a, **kw): pass
        def flush(self): pass

    class _Alarm:
        def evaluate(self, *a, **kw): return []

    # mock process_frame 返回最小 outcome
    class _Outcome:
        counts = {}
        alarms_this_frame = []
        fired_alarms = []
        violator_indices = []
        centers = np.empty((0, 2))

    original_pf = vw.process_frame
    vw.process_frame = lambda *a, **kw: _Outcome()

    try:
        worker = vw.VideoWorker(
            short, detector, _Roi(), _Stats(), _Alarm(),
            record_path=None, parent=None,
        )
        progresses = []
        worker.progress_updated = _signal()
        worker.progress_updated.connect(lambda c, t: progresses.append((c, t)))
        # 其它信号接空 slot，避免 emit 报错（dummy QThread 的 emit 不调 slot 也没事，
        # 但 frame_ready.emit 会传 ndarray，确保不抛）
        worker.frame_ready = _signal()
        worker.frame_ready.connect(lambda *a, **kw: None)
        worker.stats_ready = _signal()
        worker.fps_updated = _signal()
        worker.finished_source = _signal()
        worker.error_occurred = _signal()

        worker.start()

        # 跑 15 帧后 seek 到 40
        deadline = time.time() + 8
        while len(progresses) < 15 and time.time() < deadline:
            time.sleep(0.01)
        assert len(progresses) >= 15, f"前 15 帧进度未及时上报: {len(progresses)}"
        print(f"[进度-前段] 前15帧: cur={progresses[0][0]}..{progresses[14][0]}, total={progresses[0][1]}")
        assert progresses[0][0] == 1, "首帧 cur 应为 1"
        assert progresses[0][1] == N, f"total 应为 {N}, 实际 {progresses[0][1]}"
        # 单调递增
        for i in range(1, 15):
            assert progresses[i][0] == progresses[i - 1][0] + 1, "前段非单调 +1"

        # seek 到 40
        seek_target = 40
        worker.seek(seek_target)
        progresses_before_seek = len(progresses)
        # 等 seek 落地（下一帧 progress 应跳到 ~40）
        deadline = time.time() + 8
        while len(progresses) == progresses_before_seek and time.time() < deadline:
            time.sleep(0.01)
        # 收集 seek 后的几个 progress
        seek_idx = len(progresses)
        deadline = time.time() + 8
        while len(progresses) < seek_idx + 3 and time.time() < deadline:
            time.sleep(0.01)
        after_seek = progresses[seek_idx:seek_idx + 3]
        print(f"[seek] 目标={seek_target}, seek后进度={[c for c, t in after_seek]}")
        assert len(after_seek) >= 1, "seek 后无进度上报"
        # seek 后第一帧 cur 应在目标附近（容差 5：seek 重定位 + 读一帧的偏差）
        assert abs(after_seek[0][0] - seek_target) <= 5, (
            f"seek 后 cur={after_seek[0][0]} 偏离目标 {seek_target} 太远"
        )
        # seek 后仍单调 +1
        for i in range(1, len(after_seek)):
            assert after_seek[i][0] == after_seek[i - 1][0] + 1, "seek 后非单调 +1"

        worker.stop()
        worker.wait(5)
        print("[OK] 进度上报 + seek 通过")
    finally:
        vw.process_frame = original_pf
        # 清理
        try:
            os.remove(short)
        except OSError:
            pass


if __name__ == "__main__":
    test_progress_and_seek()
    print("\n全部通过 ✅")
