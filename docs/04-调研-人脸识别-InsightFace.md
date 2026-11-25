# 04 · 调研：人脸识别（InsightFace 体系）

> 调研时间：2026-11-24。来源：deepinsight/insightface 官方仓库文档（主 README、python-package README 2.1、model_zoo.md、runtime.md）+ PyPI 包信息（原文摘录见 [08-原始资料存档.md](08-原始资料存档.md)）。
> 结论先行：模型用 **buffalo_l**（SCRFD-10G 检测 + ArcFace R50 识别）；**不装 insightface pip 包**（PyPI 停更），改用 **onnxruntime 直接加载 ONNX 模型 + 自写约 300 行胶水**。

---

## 1. InsightFace 项目近况（重要时间线）

| 日期 | 事件 |
|------|------|
| 2023-04-02 | **PyPI 最后一次发布：insightface 0.7.3，仅 sdist（源码包），无任何 wheel**；`requires_dist` 为空 |
| 2024-05-04 | InspireFace（C/C++ SDK）发布 |
| 2025-11-24 | **许可更新**：开源识别模型（如 buffalo_l）商用需联系 `recognition-oss-pack@insightface.ai` 授权；inswapper 系列另行授权 |
| 2026-05-23 | InsightFace **1.0**：桌面 GUI（Evaluation Studio）；**默认 Python 安装不再需要 C++ 编译器**（face3d 扩展默认不编译） |
| 2026-07-27 | InsightFace **Server**：自托管 Web UI + REST（AWS Rekognition 替代定位） |
| 2026-09-09 | InsightFace **2.0**：RGB 活体检测插件、PrivateFrame 视频人脸模糊、`raccoon_s/l` 模型包、CoreML/CUDA/CPU 自动 Provider 选择 |
| 2026-10-03 | InsightFace **2.1**：PersonAnalysis 更新（人体检测+ReID+人脸联合识别，`cheetah_s/l` 模型包） |

> ⚠️ **关键发现**：2.x 系列只存在于 GitHub（`pip install -e ./python-package`），**没有发到 PyPI**。`uv pip install insightface` 得到的仍是 2023 年的 0.7.3 sdist——在新版 Python 上走 Cython 编译路线大概率失败/不可靠。

## 2. 模型包选型：buffalo_l（用户已确认个人自用，无商用顾虑）

### Legacy 模型包（model_zoo.md 原表）

| 名称 | 检测模型 | 识别模型 | 对齐 | 属性 | 体积 | 自动下载 |
|------|----------|----------|------|------|------|----------|
| antelopev2 | SCRFD-10GF | ResNet100@Glint360K | 2d106+3d68 | 性别&年龄 | 407MB | 否 |
| **buffalo_l**（默认） | SCRFD-10GF | ResNet50@WebFace600K | 2d106+3d68 | 性别&年龄 | 326MB | **是** |
| buffalo_m | SCRFD-2.5GF | ResNet50@WebFace600K | 同上 | 同上 | 313MB | 否 |
| buffalo_s | SCRFD-500MF | MBF@WebFace600K | 同上 | 同上 | 159MB | 否 |
| buffalo_sc | SCRFD-500MF | MBF@WebFace600K | - | - | 16MB | 否 |

### 精度（model_zoo.md 原表）

| 名称 | MR-ALL | LFW | CFP-FP | AgeDB-30 | IJB-C(E4) |
|------|--------|-----|--------|----------|-----------|
| buffalo_l | 91.25 | **99.83** | 99.33 | 98.23 | 97.25 |
| buffalo_s | 71.87 | 99.70 | 98.00 | 96.58 | 95.02 |

- buffalo_m 精度同 buffalo_l；buffalo_sc 精度同 buffalo_s。
- 新系列：`raccoon_s/l`（FaceAnalysis/PrivateFrame 用）、`cheetah_s/l`（PersonAnalysis 用，含人体检测+ReID；cheetah_s 的人脸部分=buffalo_s、cheetah_l=buffalo_l）。
- 自动下载源：GitHub `model-zoo` release（insightface>=0.3.3 首次初始化时下载；离线可手动解压到 `~/.insightface/models/<包名>/`，需保留 manifest 与 MODEL.LICENSE）。
- 本项目落地：`scripts/download_models.py` 离线拉 zip 解压到 `models/buffalo_l/`，用到的 ONNX 文件为 `det_10g.onnx`（SCRFD）与 `w600k_r50.onnx`（ArcFace R50）。

## 3. API 与相似度匹配要点（python-package README 摘录要点）

```python
from insightface.app import FaceAnalysis
app = FaceAnalysis(providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
app.prepare(ctx_id=0, det_size=(640, 640))
faces = app.get(img)          # 每个含 bbox/kps/det_score/embedding/normed_embedding
```

- 相似度：余弦相似度（配 `normed_embedding` 点积）；社区通行匹配阈值 ~0.4-0.5（自建人脸库建议实测校准）。
- 2.x Provider 自动选择顺序：CoreML → CUDA → CPU；PersonAnalysis 默认 CPU。代码 MIT，**模型非商业研究用途**。
- **Liveness 插件（2.0+）**：`FaceAnalysis(addons=["liveness"])`，`face.liveness.status/is_live/live_score`，默认阈值 0.8，`normal` 模式不通过则跳过识别，`observe` 模式只记录——日后加防照片冒充时参考其思路或模型。
- **PersonAnalysis（2.1）**：`register()/get()/match()/update()`，人脸不清时用人体 ReID 续身份——与"门口机抓拍注册→室内持续识别"的诉求天然契合，二期可借鉴。

## 4. 推荐落地路线：绕开 insightface 包，直接 ONNX

**为什么**：PyPI 版停更仅 sdist（Cython 编译，py3.14 高危）；GitHub 2.x 源码安装可行但把整个 GUI/Server 依赖树拖进来，且 py3.14 兼容未验证。本项目只需要两个 ONNX 文件 + 固定的一小段前后处理。

**怎么做**（约 300 行，均有成熟社区参考实现可移植）：

1. `face/scrfd.py`：图像 letterbox 到 640×640 → onnxruntime 推理 `det_10g.onnx`（5 个 stride 输出）→ anchor 解码 + 置信度过滤 + NMS → 还原 bbox/kps/score。
2. `face/arcface.py`：按 5 个关键点做相似变换对齐到 112×112（标准 ArcFace 对齐模板）→ `w600k_r50.onnx` → 512 维 embedding，L2 归一化。
3. `face/pipeline.py`：门口摄像头帧 → SCRFD → ArcFace → 与 FaceDB 余弦匹配（阈值+连续 N 帧确认）→ 事件/开门。
4. onnxruntime 会话设置 `intra_op_num_threads` 控制线程；CPU 下 SCRFD(640) ~30ms、R50 embedding ~10ms/脸（估算）。

**备选路线**（记录备查）：`insightface @ git+https://github.com/deepinsight/insightface` 源码装 2.1（其 base 包按官方说明无需编译器）；或商业许可立场变化时换 OpenCV Zoo 的 **SFace+YuNet**（Apache-2.0，模型许可干净、精度略低）。

## 5. 许可备忘

- InsightFace **代码**：MIT（学术/商用皆可）。
- InsightFace **预训练模型**（含自动/手动下载）：**仅非商业研究用途**；商用联系官方。本项目用户已确认"个人/家庭内网自用"（见 [01-需求与决策记录.md](01-需求与决策记录.md) 决策 #3）。
- YOLO26/Ultralytics：AGPL-3.0（自用不分发无碍）。
- 若未来转为商业用途：人脸模型需换 SFace 等宽松许可方案或购买授权——届时再评估。
