# 架构详解 Architecture

## 1. 分层架构

```
┌──────────────────────────────────────────────┐
│  ui 层 (PyQt5)    main_window / widgets       │  仅显示与交互
├──────────────────────────────────────────────┤
│  workers 层       video_worker / image_worker │  Qt 线程桥接
├──────────────────────────────────────────────┤
│  core 层          detector / roi / alarm /     │  纯逻辑，无 Qt
│                   statistics / config_manager  │  可独立单测
├──────────────────────────────────────────────┤
│  utils 层         image_convert / logger /     │  通用工具
│                   fps_counter                  │
└──────────────────────────────────────────────┘
```

**分层原则**：
- `core/` 不依赖 PyQt5，方便单元测试与复用
- `workers/` 用 QThread 把耗时推理放到子线程，通过信号通知 UI
- `ui/` 只负责渲染和接收用户输入，不做推理
- `utils/` 纯函数工具，无副作用

## 2. 核心数据流

```
摄像头/视频/图片
     │ cv2.VideoCapture / 文件
     ▼
[VideoWorker 子线程]
     │
     ├──► Detector.predict_frame(frame) ──► DetectionResult
     │                                         │
     ├──► RoiManager.violators(boxes, clss) ───┤  返回进入 ROI 的框索引
     │                                         │
     ├──► 画标注帧 + ROI 叠加 + 红框高亮       │
     │                                         │
     ├──► (有违反) AlarmEngine.trigger(event) ─┤
     │              ├── VisualChannel  (界面)
     │              ├── SoundChannel   (声音)
     │              ├── SnapshotChannel(截图/录像)
     │              └── LogChannel     (日志csv)
     │
     └──► StatsCollector.record(ts, counts, alarms)
     │
     ▼ Qt 信号
[主界面]
   frame_ready  ─► VideoCanvas 显示
   alarm_ready  ─► 状态灯红 + 弹窗 + 异常帧列表追加
   stats_ready  ─► StatsPanel 刷新图表
   fps_updated  ─► 状态栏
```

## 3. 模块职责

### core/detector.py
封装 YOLO 推理。统一接口 `predict_frame(frame_bgr) -> DetectionResult`。
内部调用 `model(frame, conf=, iou=, classes=, verbose=False)`。
返回 dataclass：boxes(N,4 xyxy)、confs(N,)、clss(N,)、masks(N,H,W)、names、annotated_bgr。
支持运行时更新 conf/iou/classes/device。

### core/roi.py
- `RoiRegion`：单个多边形 ROI，用 shapely.Polygon。`contains(cx,cy)` 中心点判定。
- `RoiManager`：管理多个 ROI。`violators(boxes_xyxy, clss)` 返回进入任意 ROI 的框索引列表 + 命中的 roi_id。
- 支持序列化为坐标点列表（用于保存配置）。

### core/alarm.py
- `AlarmEvent`：dataclass，含 timestamp/frame/clss/confs/roi_id/snapshot_path。
- `AlarmEngine`：四通道分发。`trigger(event)` 按开关分发到各 Channel。
- 冷却机制：cooldown 秒内同 (roi_id, cls) 合并，避免刷屏。
- 各 Channel 在独立 QThread 或线程池执行，不阻塞 UI。

### core/statistics.py
- `StatsCollector`：累积记录。`record(timestamp, clss_counts, alarms)`。
- 查询接口：`class_counts()` / `time_series(bin)` / `class_ratio()` / `alarm_trend(bin)`。
- 持久化到 data/logs/stats.csv（轻量）。

### core/config_manager.py
- `ConfigManager`：读写 config/settings.json，深合并默认配置。
- 提供 get/set 路径式访问（如 `cfg.get("detection.conf")`）。
- 单例，全局共享。保存时发出信号（通过回调，避免 core 依赖 Qt）。

### workers/video_worker.py
- `VideoWorker(QThread)`：摄像头/视频共用。
- 信号：`frame_ready(ndarray, list)`、`alarm_ready(object)`、`stats_ready(dict)`、`fps_updated(float)`、`error_occurred(str)`。
- run() 循环：读帧 → 推理 → ROI 判定 → 画图 → 发信号。
- 支持暂停/停止，资源释放（cap.release()）。

### workers/image_worker.py
- `ImageWorker(QThread)`：单图推理。一次性，发 `frame_ready` + `stats_ready` 后退出。

## 4. 线程模型

- **主线程**：Qt 事件循环，所有 UI 更新。
- **VideoWorker 线程**：推理循环。通过 `pyqtSignal` 跨线程传帧。
- **报警 Channel 线程**：截图写盘、录像编码在独立线程，避免拖慢推理。
- 图像数据用 numpy ndarray 经信号传递（Qt 不会深拷贝，但 ndarray 引用计数安全）。

## 5. 配置驱动

所有可调参数集中在 `config/settings.json`，由 `ConfigManager` 加载。
检测参数（conf/iou/classes/device）可在运行中通过 detector 的 setter 实时更新，
无需重启 worker。

## 6. 错误处理

- 模型加载失败 → 弹窗提示 + 状态栏红字，不崩溃。
- 摄像头打开失败 → 提示检查设备号，工具栏回退到停止态。
- 推理异常 → 捕获后发 `error_occurred` 信号，记录日志，跳过该帧继续。
- 磁盘写入失败（截图/日志）→ 日志记录，不中断主流程。

## 7. 可测试性

`core/` 无 Qt 依赖，可用 pytest 直接测试：
- Detector 用一张已知图片断言检测到 person
- RoiManager 用合成 boxes 断言 violators 正确
- StatsCollector 断言聚合数据
- ConfigManager 断言读写与默认合并
