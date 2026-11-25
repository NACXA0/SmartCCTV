# SmartCCTV（已裁定归档）

> **本仓库准备 Archive，不再作为智慧办公室监控实现。**  
> 理由：[docs/决策-归档自研SmartCCTV.md](../docs/决策-归档自研SmartCCTV.md)  
> 终态：[docs/架构-HAOS四App.md](../docs/架构-HAOS四App.md)  
> 主仓操作清单：[docs/归档-SmartCCTV准备清单.md](../docs/归档-SmartCCTV准备清单.md)

## 被什么替代

| 旧自研 | 新 |
|--------|-----|
| 拉流 / 录像 / 检测 / 人脸 UI | HAOS **Frigate Full Access** |
| 规则与「智能」 | **analytics-sidecar**（MQTT） |
| HMAC → 总控 HTTP | MQTT `frigate/#` / `business/#` |
| 自研面板 :8300 | Frigate Web + sidecar 看板 iframe |

勿再执行本仓 `docs/06` 实施方案，勿做「HMAC 兼容 sidecar」续命。

历史调研文档见 [`docs/00-INDEX.md`](docs/00-INDEX.md)（方案废弃，仅存档）。
