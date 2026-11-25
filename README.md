# SmartCCTV（已裁定归档）

> **本仓库准备归档，不再作为智慧办公室监控实现。**  
> 决策说明：[docs/决策-归档自研SmartCCTV.md](../docs/决策-归档自研SmartCCTV.md)

## 为什么去掉

自研目标（多路 RTSP、检测、区域、人脸、录像回放、告警面板）已被 **[Frigate](https://github.com/blakeblackshear/frigate)** 完整覆盖。店内部署改为 **HAOS + Frigate 官方插件**；门禁、云端业务、告警编排与自定义面板留在 **SmartCenter**（HA `lobn_office`），不再维护第二套监控发行版。

## 历史角色（归档前）

内网智能监控原型：≤10 路 RTSP、YOLO 检测、人脸库、行为规则、TOTP 面板；开门只上报 SmartCenter（`center_only`），本地不执行继电器。

文档组（调研与旧实施方案，**方案已废弃**）见 [`docs/00-INDEX.md`](docs/00-INDEX.md)。

## 请勿

- 不要把本仓库当生产监控继续迭代  
- 不要再执行旧 `docs/06-实施方案` 或「Frigate + 自研 sidecar」折中作为长期形态  

新店监控：在 HAOS 安装 Frigate 插件并维护其 `config.yml`；总控逻辑只进 SmartCenter。
