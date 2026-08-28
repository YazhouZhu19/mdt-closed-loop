# 使用与集成说明

本文说明本地安装、合成演示、会话接入、研究分臂、输出记录和音乐引擎适配。

## 1. 环境准备

- Python 3.10 或更高版本
- NumPy 1.26 或更高版本
- SciPy 1.11 或更高版本

在仓库根目录建立隔离环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Windows PowerShell 使用：

```powershell
.venv\Scripts\Activate.ps1
```

## 2. 运行隔离演示

```bash
python demo.py
```

演示依次运行三次校准会话和一次闭环会话。EDA 与 RR 全部由固定随机种子生成，记录只写入临时目录并在程序结束后自动删除。

## 3. 创建核心对象

```python
from mdt_core import Arm, IndividualBaseline, ProgramState, Session
from mdt_core.engine import NullEngine

baseline = IndividualBaseline()
program = ProgramState(baseline_isi=18.0)

session = Session(
    user_id="经过假名化的参与者编号",
    baseline=baseline,
    program=program,
    engine=NullEngine(),
    arm=Arm.FULL_LOOP,
    out_dir="./data",
    is_calibration=True,
)
```

默认记录器会把 `user_id` 写入 JSON，因此必须使用假名化编号。没有额外加密和访问控制时，不得写入可识别身份的健康数据。

## 4. 输入窗口与时钟

`RawWindow.t` 是会话开始后的秒数，不是系统墙钟时间。所有传感与音频事件必须按时间单调处理。

```python
from mdt_core import RawWindow

window = RawWindow(
    t=0.0,
    eda=[...],                 # 均匀采样的 EDA，单位通常为 µS
    eda_fs=32.0,               # Hz
    rr_intervals=[...],        # RR 间期，毫秒
    contact_impedance=1.2e6,   # 可选的接触质量指标
    accel_rms=0.01,            # 可选的运动质量指标
)
```

集成层负责构造重叠窗口：

| 接口 | 建议窗口 | 最小更新间隔 | 作用 |
|---|---|---:|---|
| `fast_tick` | 约 10 秒 EDA | 2 秒 | EDA 快速状态更新 |
| `slow_tick` | 约 60 秒 EDA 与 RR | 15 秒 | 完整 EDA/HRV 状态更新 |
| `music_boundary` | 音频时钟时间 | 引擎事件 | 提交排队的音乐变化 |

传感器缓冲与窗口构造依赖具体设备 SDK，因此不包含在本仓库中。

## 5. 校准流程

校准必须显式设置 `is_calibration=True`。系统只累计前 120 秒的有效特征，校准不会播放音乐，也不会计入治疗剂量。

```python
calibration = Session(
    "participant-001",
    baseline,
    program,
    engine=NullEngine(),
    arm=Arm.FULL_LOOP,
    out_dir="./data",
    is_calibration=True,
)

for window in calibration_windows:
    calibration.slow_tick(window)

calibration.finish()
```

默认需要三次校准会话。只有全部 EDA/HRV 基线特征都具有有效均值和正标准差时，`baseline.is_ready` 才会变为 `True`。无有效信号的校准不会计数。

## 6. 治疗会话

```python
treatment = Session(
    "participant-001",
    baseline,
    program,
    engine=NullEngine(),
    arm=Arm.FULL_LOOP,
    out_dir="./data",
)

# 传感调度回调
state, params = treatment.fast_tick(eda_window)
state, params = treatment.slow_tick(eda_hrv_window)

# 音频引擎在真实小节/乐句边界触发
params = treatment.music_boundary(audio_time, phrase_boundary=False)

record_path = treatment.finish(
    post_survey={"calm": 4},
    isi_score=17.0,
)
```

不要用浮点取模猜测小节或乐句边界；必须由音频引擎自身的 transport 回调触发 `music_boundary`。

`tick` 只是兼容旧调用的 `slow_tick` 别名，新系统应使用明确的多速率接口。

## 7. 研究分臂

| 分臂 | 目标轨迹 | 实时生理反馈控制音乐 | 说明 |
|---|---|---|---|
| `FULL_LOOP` | ISO | 是 | 完整闭环 |
| `DIRECT` | 固定直接目标 | 是 | 直接引导闭环 |
| `ISO` | ISO | 不做连续反馈 | 开环计划轨迹 |
| `SHAM` | 预注册轨迹 | 否 | 只随音频边界推进 |

SHAM 会话必须提供非空、预注册的音乐轨迹：

```python
from mdt_core.types import MusicParams

trajectory = [
    MusicParams(tempo=68.0),
    MusicParams(tempo=66.0),
    MusicParams(tempo=64.0),
]

sham = Session(
    "participant-002",
    baseline,
    program,
    engine=NullEngine(),
    arm=Arm.SHAM,
    sham_trajectory=trajectory,
    out_dir="./data",
)
```

`ShamEngine` 创建时会复制轨迹；记录写入实际播放参数，而不是控制器建议参数。

## 8. 信号质量与降级

- 非法时间戳或采样率直接报错。
- 少量 EDA 缺失会被插值并标记为 `NOISY`。
- 大量 EDA 丢失会移除 EDA 特征；可用 HRV 仍可保留。
- 非生理范围或异位 RR 会被剔除。
- 接触阻抗异常会标记为 `LOST`。
- 运动、特征覆盖不足或样本剔除会降低置信度。
- `LOST` 或低置信度会退出实时反馈并撤销排队命令。

设备集成层应捕获输入异常、单独记录设备事件，不得用伪造数据重试。

## 9. 安全与异常退出

监督性研究前必须连接人工升级接口：

```python
from mdt_core.l4_l6 import SafetyMonitor

def enqueue_human_review(user_id: str, text: str) -> None:
    # 替换为经过认证、审计且有人值守的流程
    ...

monitor = SafetyMonitor(enqueue_human_review)
```

主观文本收到后应立即提交：

```python
hit = treatment.submit_subjective(instrument={"free_text": response})
```

安全关键词命中后会停止音乐、标记人工升级、保存已有数据，并且不计完成剂量。其他异常使用：

```python
treatment.abort("device_disconnected")
```

## 10. 会话记录

`SessionRecorder` 输出一个 JSON 文件，包含：

- `session_id`、`user_id` 和研究分臂；
- `physio`：特征、质量、状态、置信度和 Z 分数；
- `music`：实际参数、目标、估计、误差和控制原因；
- `subjective`：前后测问卷与量表事件。

写入过程先生成同目录 `.json.tmp`，完成后原子替换。它可以避免半写 JSON，但不提供加密、身份认证、保留策略或审计日志。

## 11. 接入真实音乐引擎

实现 `mdt_core.engine.MusicEngine` 的三个方法：

```python
class VendorEngine(MusicEngine):
    def start(self, session_id: str, params: MusicParams) -> None:
        ...

    def apply(self, params: MusicParams) -> None:
        ...

    def stop(self) -> None:
        ...
```

适配器还必须实现音频 transport 回调、供应商参数映射、确认与重连、端到端时延测量。当前 `MubertEngine` 仅有接口骨架，不是可用的 SDK 集成。

## 12. 配置调整

所有可调常量位于 `mdt_core/config.py` 的不可变 dataclass 中。不要修改 `DEFAULT`，应创建新配置：

```python
from mdt_core.config import Config, ControlConfig

cfg = Config(control=ControlConfig(kp=0.35, ki=0.03))
```

任何用于研究的参数变更都应版本化、预注册并独立验证。
