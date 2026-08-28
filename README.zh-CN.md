# MDT 闭环控制系统

[English](README.md) · [详细使用说明](docs/USAGE.zh-CN.md) · [系统架构](docs/ARCHITECTURE.md) · [验证报告](docs/VALIDATION.md)

这是一个复合音乐数字疗法（MDT）闭环控制研究原型。系统把 EDA 和 RR 间期窗口转化为个体化唤醒度估计，将估计状态与治疗目标轨迹比较，再通过带约束的 PI 控制器和音乐语法层生成可播放的音乐参数。

> [!IMPORTANT]
> 本项目是研究软件，**不是医疗器械**，不提供医疗建议，也不能用于无人监督的患者治疗。当前验证全部基于合成信号，不构成临床有效性证据。

## 已实现功能

- 多速率输入：2 秒 EDA 快速通路和 15 秒 EDA/HRV 慢速通路。
- 信号输入校验、缺失插值、RR 生理范围过滤、EDA 分解及时域/频域 HRV 特征。
- 个体基线标准化、加权多模态融合、一维 Kalman 平滑及后验不确定性输出。
- FULL_LOOP 使用响应驱动的自适应 ISO 轨迹；开环 ISO 研究臂保留固定轨迹作为对照。
- 带不确定性降额、死区、积分泄漏、抗积分饱和和输出限幅的 PI 控制器。
- 音乐语法安全层：速度范围、变化率限制、可逆配器层级和明确的小节/乐句边界提交。
- 会话状态机、单调事件时钟、校准隔离、原子化 JSON 记录、剂量管理、ISI 结局、无效停治和安全升级接口。
- `FULL_LOOP`、`SHAM`、`DIRECT`、`ISO` 四种研究分臂。
- 确定性、隔离的合成测试及端到端合成演示。

## 闭环过程

```mermaid
flowchart LR
    P[参与者生理状态] --> S[EDA 与 RR 信号窗口]
    S --> L0[L0 校验、清洗与特征]
    L0 --> L1[L1 个体标准化、融合、Kalman]
    L1 --> L2[L2 治疗目标轨迹]
    L1 --> C[L3 带约束 PI 控制器]
    L2 --> C
    C --> G[L3.5 音乐语法]
    G --> E[音乐引擎]
    E --> P
    L0 --> R[L4 同步记录]
    L1 --> R
    G --> R
    R --> O[L5 剂量、结局、无效停治、安全]
    A[L6 研究分臂] --> L2
    A --> C
```

核心控制律：

```text
e(k) = target_arousal(k) - estimated_arousal(k)
q(k) = confidence(k) * uncertainty_scale(P(k))
I(k) = clamp(I(k-1) + q(k) * e(k) * dt)
u(k) = clamp(q(k) * (Kp * e(k) + Ki * I(k)))
```

`q(k)` 同时考虑特征覆盖/信号质量和 Kalman 后验方差。可靠度下降时控制连续降额；超过硬不确定性阈值或置信度不足时停止实时反馈、泄放积分并取消尚未播放的命令。FULL_LOOP 的 ISO 轨迹在患者跟踪良好时加快，在明显滞后时减慢，在状态不可靠时冻结。`MusicGrammar` 再把控制量映射为速度、配器层级和派生音乐参数，并等待音频引擎明确报告的小节或乐句边界后提交。

## 快速开始

要求 Python 3.10 或更高版本。

```bash
git clone <你的仓库地址>
cd mdt-closed-loop
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python demo.py
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

执行完整开发检查：

```bash
python -m pip install -e ".[dev]"
ruff check mdt_core tests demo.py
mypy --no-site-packages --ignore-missing-imports mdt_core tests demo.py
python -W error -m unittest discover -s tests -v
```

## 合成数据隔离

`demo.py` 和 `tests/synthetic.py` 使用固定随机种子生成数学信号，不读取、不包含、也不推断真实患者数据。测试和演示记录只写入 `tempfile.TemporaryDirectory`，结束后自动删除。合成结果不能解释为疗效或真实实时性能证据。

## 代码结构

| 位置 | 职责 |
|---|---|
| `mdt_core/config.py` | 可整定且经过校验的配置 |
| `mdt_core/l0_signal.py` | 信号质量、清洗、EDA 与 HRV 特征 |
| `mdt_core/l1_state.py` | 个体基线与唤醒度状态估计 |
| `mdt_core/l2_planner.py` | 治疗目标轨迹与剂量区间 |
| `mdt_core/l3_control.py` | PI 控制器与音乐语法约束 |
| `mdt_core/l4_l6.py` | 记录、结局、安全与研究分臂 |
| `mdt_core/engine.py` | 音乐引擎接口、离线引擎、SHAM 引擎 |
| `mdt_core/session.py` | 多速率编排与会话生命周期 |
| `tests/` | 隔离的确定性合成测试 |

## 详细文档

- [中文使用与集成说明](docs/USAGE.zh-CN.md)
- [英文使用与集成说明](docs/USAGE.md)
- [系统架构与核心算法](docs/ARCHITECTURE.md)
- [验证范围与复现方法](docs/VALIDATION.md)
- [贡献指南](CONTRIBUTING.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [安全政策](SECURITY.md)
- [GitHub 发布清单](docs/RELEASE_CHECKLIST.md)

## 当前边界

- `MubertEngine` 仍是适配骨架；仓库内实际可执行的是 `NullEngine` 和 `ShamEngine`。
- 尚未验证真实传感器、声卡、WebRTC 或供应商链路的端到端时延。
- 信号算法和控制参数尚未用临床数据完成对照验证与整定。
- 自适应轨迹阈值和不确定性阈值目前仅完成合成软件测试，尚未形成临床参数。
- JSON 记录未加密且包含用户标识，不能用于生产健康数据。
- 尚无硬实时调度、看门狗、断线重连、网络安全、风险管理和医疗器械软件验证材料。
- `SafetyMonitor.escalate_hook` 在任何监督性研究使用前都必须连接到真实、有人值守的人工复核流程。

因此，当前版本适合算法审查、离线仿真和系统集成原型，不适合临床部署。

## 许可证

项目尚未选择开源许可证。公开发布前必须添加 `LICENSE`：若希望包含明确专利授权，通常可考虑 Apache-2.0；若偏好简短宽松条款，可考虑 MIT。具体步骤见[发布清单](docs/RELEASE_CHECKLIST.md)。
