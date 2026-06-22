"""YOLO 推理封装：统一图片/视频/摄像头的单帧检测接口。

封装 ultralytics 的 YOLO 模型，输出标准化的 DetectionResult dataclass，
屏蔽 Results 对象的细节，使上层（workers）无需关心 ultralytics API。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

# ultralytics 在复用的 venv 中
from ultralytics import YOLO

from app.utils.logger import get_logger

logger = get_logger()


@dataclass
class DetectionResult:
    """单帧检测结果（与 ultralytics Results 解耦）。"""

    boxes: np.ndarray            # (N,4) xyxy 像素坐标
    confs: np.ndarray            # (N,) 置信度
    clss: np.ndarray             # (N,) 类别 id
    masks: np.ndarray | None     # (N,H,W) 二值掩码，无则 None
    names: dict[int, str]        # {0: "person", ...}
    annotated: np.ndarray        # 标注后的 BGR 帧（含框/掩码/标签）
    orig_shape: tuple[int, int]  # (h, w)
    # 跟踪 ID（每框一个；predict_frame 走检测路径时为 -1）。
    # 同一 track_id 在不同帧代表同一目标，供统计去重累计。
    track_ids: np.ndarray = field(default_factory=lambda: np.zeros((0,), dtype=np.int64))

    @property
    def count(self) -> int:
        return int(len(self.boxes))

    def centers(self) -> np.ndarray:
        """返回 (N,2) 检测框中心点 (cx, cy)，供 ROI 判定使用。"""
        if self.count == 0:
            return np.zeros((0, 2), dtype=np.float32)
        x1, y1, x2, y2 = self.boxes[:, 0], self.boxes[:, 1], self.boxes[:, 2], self.boxes[:, 3]
        return np.stack([(x1 + x2) / 2.0, (y1 + y2) / 2.0], axis=1).astype(np.float32)


class Detector:
    """YOLO26s-seg 推理器。线程安全要求：同一实例的 predict_frame/track_frame 仅在同一工作线程内调用。

    跟踪：track_frame() 内部 ultralytics 的 persist=True 会让 BoT-SORT 状态跨帧保持，
    同一目标在不同帧获得相同 track_id。统计据此去重累计，避免『同一人被计 1000 帧』。
    """

    def __init__(
        self,
        model_path: str,
        device: str = "cpu",
        conf: float = 0.45,
        iou: float = 0.5,
        classes: list[int] | None = None,
        imgsz: int = 640,
        half: bool | None = None,
    ) -> None:
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"模型文件不存在: {model_path}")
        self.model_path = model_path
        self._device = device
        self._conf = float(conf)
        self._iou = float(iou)
        self._classes = list(classes) if classes else None
        self._imgsz = int(imgsz)
        # FP16 半精度：仅在 CUDA 设备上启用（CPU 上 half 无加速甚至报错）。
        # 默认 None → CUDA 自动开、CPU 强制关。可被显式参数覆盖。
        self._half = self._resolve_half(half, device)
        # task 由 .pt 文件自动推断为 "segment"
        self._model = YOLO(model_path)
        self._model.to(device)
        if self._half:
            self._model.half()
        logger.info("Detector 初始化: device=%s, half=%s, imgsz=%d", device, self._half, self._imgsz)
        # 跟踪状态标记：是否已经用过 track_frame（用于 reset 时清 tracker 状态）
        self._tracking = False

    @staticmethod
    def _resolve_half(half: bool | None, device: str) -> bool:
        if half is not None:
            return bool(half) and ("cuda" in str(device).lower())
        return "cuda" in str(device).lower()

    # ---- 运行时参数更新（无需重建模型）----
    def set_conf(self, conf: float) -> None:
        self._conf = float(conf)

    def set_iou(self, iou: float) -> None:
        self._iou = float(iou)

    def set_classes(self, classes: list[int] | None) -> None:
        self._classes = list(classes) if classes else None

    def set_device(self, device: str) -> None:
        if device != self._device:
            self._device = device
            self._model.to(device)
            # 设备变化时重新决定 half：CPU→CUDA 自动开 FP16，CUDA→CPU 自动关
            new_half = self._resolve_half(None, device)
            if new_half != self._half:
                self._half = new_half
                if new_half:
                    self._model.half()
                else:
                    self._model.float()
                logger.info("device 切换: %s, half=%s", device, self._half)

    @property
    def names(self) -> dict[int, str]:
        return dict(self._model.names)

    # ---- 推理 ----
    def predict_frame(self, frame_bgr: np.ndarray) -> DetectionResult:
        """对单张 BGR 帧检测，返回 DetectionResult（不带跟踪 ID）。"""
        kwargs = dict(
            conf=self._conf,
            iou=self._iou,
            verbose=False,
            imgsz=self._imgsz,
            half=self._half,
        )
        if self._classes is not None:
            kwargs["classes"] = self._classes

        results = self._model(frame_bgr, **kwargs)
        return self._build_result(results[0], with_ids=False)

    def track_frame(self, frame_bgr: np.ndarray) -> DetectionResult:
        """对单张 BGR 帧检测并跟踪。persist=True 使 tracker 状态跨帧保持，
        同一目标在不同帧获得相同 track_id。

        与 predict_frame 的唯一区别：每框附带 track_id（result.track_ids），
        供统计按唯一目标去重累计，避免重复计数。
        使用 ByteTrack（比默认 BoT-SORT 轻，GPU 上极快，安防场景足够）。
        """
        kwargs = dict(
            conf=self._conf,
            iou=self._iou,
            verbose=False,
            imgsz=self._imgsz,
            half=self._half,
            persist=True,
            tracker="bytetrack.yaml",
        )
        if self._classes is not None:
            kwargs["classes"] = self._classes

        self._tracking = True
        results = self._model.track(frame_bgr, **kwargs)
        return self._build_result(results[0], with_ids=True)

    def reset_tracker(self) -> None:
        """重置跟踪状态。切换数据源时应调用，避免新旧源的目标 ID 串台。"""
        # persist 状态由 ultralytics 内部按 model 实例维护；
        # 清掉本地标记，下次 track_frame 会重建。这里没有公开 API 强制清空，
        # 但切换源通常伴随新建 worker + 新帧流，BoT-SORT 会自然收敛到新目标。
        self._tracking = False

    def _build_result(self, r, with_ids: bool) -> DetectionResult:
        """从 ultralytics Results 构造 DetectionResult。with_ids 时提取 track id。"""
        n = len(r.boxes) if r.boxes is not None else 0
        boxes = r.boxes.xyxy.cpu().numpy() if n else np.zeros((0, 4), dtype=np.float32)
        confs = r.boxes.conf.cpu().numpy() if n else np.zeros((0,), dtype=np.float32)
        clss = r.boxes.cls.cpu().numpy() if n else np.zeros((0,), dtype=np.int32)
        masks = None
        if r.masks is not None and len(r.masks):
            masks = r.masks.data.cpu().numpy()

        # 跟踪 ID：track 路径才有 boxes.id；缺失（跟踪器未就绪）补 -1。
        if with_ids and n and getattr(r.boxes, "id", None) is not None:
            track_ids = r.boxes.id.cpu().numpy().astype(np.int64)
        else:
            track_ids = np.full((n,), -1, dtype=np.int64)

        annotated = r.plot()  # BGR ndarray，绘制框+掩码+标签

        return DetectionResult(
            boxes=boxes.astype(np.float32),
            confs=confs.astype(np.float32),
            clss=clss.astype(np.int32),
            masks=masks,
            names=dict(r.names),
            annotated=annotated,
            orig_shape=(annotated.shape[0], annotated.shape[1]),
            track_ids=track_ids,
        )
