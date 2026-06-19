"""帧率统计：滑动窗口平滑 FPS。"""
from __future__ import annotations

import time
from collections import deque


class FpsCounter:
    """基于最近 N 帧时间戳的滑动窗口 FPS。"""

    def __init__(self, window: int = 30) -> None:
        self._window = max(1, window)
        self._timestamps: deque[float] = deque(maxlen=self._window)

    def tick(self, ts: float | None = None) -> None:
        """记录一帧。ts 为 None 则用当前时间。"""
        self._timestamps.append(ts if ts is not None else time.time())

    def fps(self) -> float:
        """返回当前 FPS，样本不足返回 0。"""
        if len(self._timestamps) < 2:
            return 0.0
        span = self._timestamps[-1] - self._timestamps[0]
        if span <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / span

    def reset(self) -> None:
        self._timestamps.clear()
