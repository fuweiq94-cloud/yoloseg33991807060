"""图像转换工具：numpy ndarray <-> QImage <-> QPixmap。

视频帧为 BGR（cv2/ultralytics 约定），Qt 显示需转 RGB。
所有函数确保连续内存以避免 QImage 引用错乱。
"""
from __future__ import annotations

import numpy as np
from PyQt5.QtGui import QImage, QPixmap


def ndarray_bgr_to_qimage(frame_bgr: np.ndarray) -> QImage:
    """BGR ndarray -> QImage（RGB888）。"""
    if frame_bgr is None or frame_bgr.size == 0:
        return QImage()
    rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])  # BGR -> RGB
    h, w, ch = rgb.shape
    bytes_per_line = ch * w
    return QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()


def ndarray_bgr_to_qpixmap(frame_bgr: np.ndarray) -> QPixmap:
    """BGR ndarray -> QPixmap。"""
    return QPixmap.fromImage(ndarray_bgr_to_qimage(frame_bgr))


def qimage_to_ndarray_bgr(qimg: QImage) -> np.ndarray:
    """QImage -> BGR ndarray（用于把 Qt 图像喂回推理）。"""
    if qimg.isNull():
        return np.zeros((0, 0, 3), dtype=np.uint8)
    ptr = qimg.bits()
    ptr.setsize(qimg.byteCount())
    arr = np.array(ptr, dtype=np.uint8).reshape(qimg.height(), qimg.width(), -1)
    # QImage Format_RGB888 是 RGB，转 BGR
    if qimg.format() == QImage.Format_RGB888:
        arr = arr[:, :, ::-1]
    return np.ascontiguousarray(arr[:, :, :3])
