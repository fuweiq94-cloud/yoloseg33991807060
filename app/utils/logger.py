"""日志配置：统一的 logging 设置，输出到文件 + 控制台。"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

_configured = False


def setup_logging(level: int = logging.INFO, logs_dir: str | None = None) -> logging.Logger:
    """配置全局日志。仅首次调用生效。"""
    global _configured
    if _configured:
        return logging.getLogger("svs")
    _configured = True

    logger = logging.getLogger("svs")
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # 文件
    if logs_dir is None:
        # 默认 <项目根>/data/logs
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        logs_dir = os.path.join(project_root, "data", "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
        log_file = os.path.join(logs_dir, f"app_{datetime.now().strftime('%Y%m%d')}.log")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass  # 磁盘不可写时仅用控制台

    return logger


def get_logger() -> logging.Logger:
    """获取应用 logger。未配置时自动配置。"""
    if not _configured:
        setup_logging()
    return logging.getLogger("svs")
