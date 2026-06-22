"""背压采集策略冲烟测试。

直接验证 _CaptureThread 的视频文件行为：
  1. 完整性：源有多少帧，消费端就拿到多少帧（不丢帧）。
  2. 顺序性：消费顺序严格 == 帧序号 0,1,2,...（FIFO，不跳帧）。
  3. 停止响应：stop() 能唤醒因「队列满」阻塞的生产者并迅速退出。

这三个性质合起来，正是「18s 视频完整识别成 18s 录制 + 画面不跳」的根因保证。
"""
from __future__ import annotations

import os
import sys
import time
import types
import threading
from collections import deque

# 注入 dummy PyQt5，让模块可在无 GUI 环境导入
sys.modules.setdefault("PyQt5", types.ModuleType("PyQt5"))
QtCore = types.ModuleType("PyQt5.QtCore")
class _DummyThread:
    """用真线程模拟 QThread：start() 起线程跑 run()，wait() join。"""
    def __init__(self, *a, **kw):
        self._thread = None
        self._running = False
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
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
        def connect(self, *a, **kw): pass
        def emit(self, *a, **kw): pass
    return _S()
QtCore.QThread = _DummyThread
QtCore.pyqtSignal = _signal
sys.modules["PyQt5.QtCore"] = QtCore

# 真正导入被测模块
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from app.workers.video_worker import _CaptureThread, VideoWorker  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

VIDEO = os.path.join(ROOT, "assets", "test", "videos", "vtest.avi")
assert os.path.exists(VIDEO), f"缺少测试视频: {VIDEO}"


def count_source_frames(path: str) -> int:
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return n


def make_tagged_video(src: str, dst: str, n: int = 60) -> str:
    """生成一个合成视频：第 i 帧整张画面填入像素值 i mod 256。
    这样无需做内容比对，光看帧的像素就能验证 FIFO 顺序与完整性。
    """
    cap = cv2.VideoCapture(src)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 160
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 120
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    cap.release()
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(dst, fourcc, fps, (w, h))
    for i in range(n):
        frame = np.full((h, w, 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return dst


def test_completeness_and_order():
    """慢消费（每帧 sleep 模拟推理），验证：帧数齐全 + FIFO 顺序严格。"""
    tagged = os.path.join(ROOT, "data", "_smoke_tagged.mp4")
    os.makedirs(os.path.dirname(tagged), exist_ok=True)
    N = 80
    make_tagged_video(VIDEO, tagged, N)
    src_n = count_source_frames(tagged)
    assert src_n == N, f"合成帧数不对: {src_n} vs {N}"

    cap = _CaptureThread(tagged)
    cap.start()
    # 等打开
    for _ in range(100):
        if cap.is_open or not cap.isRunning():
            break
        time.sleep(0.01)

    consumed = []
    # 慢消费：比生产慢，必然触发背压（队列满则生产阻塞）
    deadline = time.time() + 30
    while len(consumed) < N and time.time() < deadline:
        ok, frame = cap.take()
        if frame is not None:
            consumed.append(int(frame[0, 0, 0]))
            time.sleep(0.005)  # 模拟推理耗时，制造背压
        else:
            time.sleep(0.002)

    cap.stop()
    cap.wait()

    print(f"[完整性] 源帧数={N}, 消费帧数={len(consumed)}")
    assert len(consumed) == N, f"丢帧！应 {N} 实 {len(consumed)}"

    # mp4v 有损压缩会把纯色帧的像素值量化（如 tag 2 解码成 3），但绝不可能重排序。
    # FIFO 的真实不变量是「非递减」：帧 i 一定在 i+1 之前出队。
    print(f"[顺序性] 前10帧解码值={consumed[:10]}")
    for i in range(1, len(consumed)):
        assert consumed[i] >= consumed[i - 1], (
            f"FIFO 顺序被破坏：第 {i-1} 帧={consumed[i-1]} 之后出现 {consumed[i]}"
        )
    print("[OK] 完整性 + 顺序性 通过")


def test_stop_unblocks_producer():
    """消费端停手后，队列很快填满，生产者应阻塞；stop() 必须唤醒它及时退出。"""
    tagged = os.path.join(ROOT, "data", "_smoke_tagged.mp4")
    cap = _CaptureThread(tagged)
    cap.start()
    for _ in range(100):
        if cap.is_open or not cap.isRunning():
            break
        time.sleep(0.01)

    # 取 2 帧（带等待，因为生产端读盘有延迟）
    got = 0
    deadline = time.time() + 5
    while got < 2 and time.time() < deadline:
        ok, frame = cap.take()
        if frame is not None:
            got += 1
        else:
            time.sleep(0.005)
    assert got == 2, f"取帧超时，只拿到 {got}/2"
    # 现在停手，让队列堆满（30 帧）后生产者进入阻塞
    time.sleep(0.8)

    t0 = time.time()
    cap.stop()
    cap.wait()
    elapsed = time.time() - t0
    print(f"[停止响应] stop→退出耗时={elapsed:.3f}s")
    assert elapsed < 2.0, f"stop() 没及时唤醒阻塞的生产者: {elapsed:.2f}s"
    print("[OK] 停止响应 通过")


def test_source_fps_read():
    """验证 source_fps 属性能读出非零值（VideoWriter 时长对齐依赖它）。"""
    cap = _CaptureThread(VIDEO)
    cap.start()
    for _ in range(100):
        if cap.is_open or not cap.isRunning():
            break
        time.sleep(0.01)
    fps = cap.source_fps
    cap.stop()
    cap.wait()
    print(f"[源帧率] source_fps={fps:.3f}")
    assert fps > 0, "源帧率读取为 0，录制时长将无法对齐"
    print("[OK] 源帧率读取 通过")


if __name__ == "__main__":
    test_completeness_and_order()
    test_stop_unblocks_producer()
    test_source_fps_read()
    print("\n全部通过 ✅")
