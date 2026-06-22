# 测试素材 (Test Assets)

用于安防视觉系统的功能与性能测试。所有素材来自公开 GitHub 仓库的测试样本（ultralytics / darknet / opencv / 其他检测项目），可自由使用。

## 图片 (`images/`)

共 28 张，覆盖 COCO 80 类主要场景：

| 文件 | 场景 | 来源 |
|------|------|------|
| `bus.jpg` | 公交车 + 行人 | ultralytics |
| `zidane.jpg` | 双人(人物) | ultralytics |
| `dog.jpg` | 狗 + 自行车 + 卡车 | darknet |
| `eagle.jpg` | 鹰(鸟) | darknet |
| `giraffe.jpg` | 长颈鹿 | darknet |
| `horses.jpg` | 马群 | darknet |
| `kite.jpg` | 风筝 + 人 | darknet |
| `person.jpg` | 单人 | darknet |
| `scream.jpg` | 人物(蒙克呐喊) | darknet |
| `dog_bicycle_person.jpg` | 狗 + 自行车 + 人 | dharsandip |
| `person_car_horse.jpg` | 人 + 车 + 马 | dharsandip |
| `persons_car_dog.jpg` | 多人 + 车 + 狗 | dharsandip |
| `messi5.jpg` | 梅西(人物) | opencv |
| `baboon.jpg` | 狒狒(动物) | opencv |
| `fruits.jpg` | 水果 | opencv |
| `butterfly.jpg` | 蝴蝶 | opencv |
| `home.jpg` | 家居场景 | opencv |
| `pic1.png` ~ `pic6.png` | 通用场景 | opencv |
| `licenseplate_motion.jpg` | 车牌 | opencv |
| `stuff.jpg` | 杂物场景 | opencv |
| `smarties.png` | 糖果 | opencv |
| `bus35.jpg` | 公交车 | yxu1168 |
| `police_car.png` | 警车 | yxu1168 |

## 视频 (`videos/`)

共 7 个，覆盖监控/交通/行人等安防典型场景：

| 文件 | 分辨率 | 帧数 | 场景 | 来源 |
|------|--------|------|------|------|
| `vtest.avi` | 768×576 | 795 | **行人监控(经典)** | opencv |
| `vehicle_pedestrian.mp4` | 640×352 | 905 | **车辆 + 行人** | jmarti44 |
| `test_scene.mp4` | 1280×720 | 234 | **测试场景(高清)** | jmarti44 |
| `ambulance.avi` | 1280×720 | 328 | **救护车(交通)** | yxu1168 |
| `tree.avi` | 320×240 | 444 | 树(运动检测) | opencv |
| `megamind.avi` | 720×528 | 270 | 动画片段 | opencv |
| `megamind_bugy.avi` | 720×528 | 270 | 动画(损坏版,测容错) | opencv |

## 使用方法

在程序中通过"打开文件"选择这些素材：

- **图片检测**：选择 `images/` 下任意 `.jpg`/`.png`，单帧推理
- **视频检测**：选择 `videos/` 下任意 `.avi`/`.mp4`，连续推理 + 跟踪
- **摄像头**：在设置里选摄像头索引（如 0），实时推理

## 致谢

- [ultralytics](https://github.com/ultralytics/ultralytics)
- [pjreddie/darknet](https://github.com/pjreddie/darknet)
- [opencv/opencv](https://github.com/opencv/opencv)
- [dharsandip/Multiple-objects-detection-in-images-with-YOLOv3](https://github.com/dharsandip/Multiple-objects-detection-in-images-with-YOLOv3)
- [jmarti44/Vehicle-and-Human-Detection](https://github.com/jmarti44/Vehicle-and-Human-Detection)
- [yxu1168/YOLOV3-Detection-of-Truck-Vehicle-Types](https://github.com/yxu1168/YOLOV3-Detection-of-Truck-Vehicle-Types)
