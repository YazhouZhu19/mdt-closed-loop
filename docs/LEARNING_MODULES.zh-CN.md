# 学习型模块改造与使用

本改造对应《MDT 闭环控制系统 · 学习型模块改造方案》v0.1。默认配置保持原数值行为；学习模块只允许在 `FULL_LOOP` 治疗会话中显式启用。仓库未提供真实参与者训练数据或经过临床验证的策略权重。

## 实现范围

| 层 | 实现 | 保留的约束 |
|---|---|---|
| L0 | 原确定性特征与质量分类 | 学习型伪迹检测器为附件中的可选项，本次不实现 |
| L1 | 默认发射接口、弱标签高斯发射模型、原一维 Kalman | 基线、观测和后验区间校准、OOD/超时回退 |
| L2 | 参数化轨迹、上下文老虎机与群体/个体部分池化 | 起始锚定前至多一次决策，确定性轨迹生成 |
| L3 | 3-A 离线线性增益调度 | 独立基线 PI、积分/输出硬限、状态可靠度门控 |
| L3.5 | 离线偏好排序、独立映射与冻结守卫 | 速度/速率/规范层级、真实边界提交、待播取消 |
| L4 | 原三轨 JSON、完整策略审计、试验清单 | 原子文件写入，异常非有限值转为 JSON null |
| L5/L6 | 原停治、安全升级与分臂 | 中止覆盖学习输出；对照臂完全绕过模型 |

本次未实现 3-B MPC、3-C 端到端 RL、在线训练或在线深度探索。通用 `Policy` 接口为后续策略保留扩展点。

模块之间的信息流、仲裁公式及论文风格结构图见[修改后的模型描述](LEARNING_MODEL.zh-CN.md)。

## 配置与接入

`Config.learning` 默认为 `LearningConfig()`：`mode="disabled"`，四个开关均为 `False`。原 `Session(...)` 调用无须改变，不提供模型时不会自动下载或生成权重。

```python
Session(
    user_id, baseline, program, cfg=cfg,
    policies={"l2": trajectory_policy, "l3": gain_policy, "taste": taste_policy},
    emission_model=emission_model,
    policy_context={...},     # 预先定义的历史/作息摘要，禁止直接标识与自由文本
    policy_approval=callback, # suggest 模式：callback(module, decision) -> bool
)
```

建议先使用影子模式。下例中的模型必须已在会话外拟合或加载：

```python
from dataclasses import replace
from mdt_core.config import DEFAULT, LearningConfig

cfg = replace(DEFAULT, learning=LearningConfig(mode="shadow", enable_l2=True))
```

可执行模式必须固定试验期与内容哈希：

```python
cfg = replace(DEFAULT, learning=LearningConfig(
    mode="restricted", enable_l2=True,
    trial_id="preregistered-period-001",
    policy_versions=(("l2", trajectory_policy.version),),
))
```

L1 用 `model.model_hash`，其他内置模型用 `model.version`。模型在会话开始时复制，推理前后验证版本。共享研究输出目录的 `.trials/` 清单固定试验期的策略版本、模块开关和权限级别；相同试验期出现不同清单会拒绝启动。更新策略或权限需新试验期。跨机器或不同输出目录需要部署层统一注册表，本地清单不是分布式锁。

| mode | 权限 |
|---|---|
| disabled | 不加载、复制或调用模型 |
| shadow | 只记录建议，实际状态、轨迹、控制和播放保持基线 |
| suggest | 回调明确接受该次建议后才可能通过门控执行 |
| restricted | 通过校准和门控，幅度限制在基线邻域 |
| autonomous | 在门控允许范围采用建议，仍保留守卫和人工安全升级 |

没有自动晋级。建议模式回调必须快速返回，不能在控制线程等待界面；未设置、拒绝或异常都不采纳。外部界面可按 `context_hash` 匹配预先审阅的具体建议。

仲裁是纯函数，迟滞状态显式传入/返回。OOD、低状态可靠度、低置信度下的大分歧、异常/超时/哈希不匹配撤销学习权限。默认退出/进入/全权可靠度为 0.35/0.50/0.85，中间区间按可靠度与验证过的策略置信度凸组合。`restricted_delta=0.10` 对 L3 是控制量差，对 L2/偏好是按参数范围归一化的差。

每模型推理默认限时 50 ms；超时后整场隔离，丢弃迟到结果，不重复创建线程。Python 守护线程无法强制终止底层计算，这只是软实时隔离，不是硬实时或不可信代码沙箱。模型是受信任的本地数值代码，没有引擎句柄。

## 离线模型

拟合/校准都在 `Session` 外完成，得到不可变模型工件。JSON 保存/加载检查内容哈希，不使用 pickle。

### L2

`HierarchicalTrajectoryBandit.fit()` 接受 `TrajectoryObservation(context, action, reward)`。动作是预登记的有限组 `TrajectoryParams`；奖励必须预先定义并归一化到 [0,1]，不能将 ISI 原分数直接当作越大越好的奖励。

| 参数 | 学习边界 | 默认 |
|---|---|---|
| anchor_offset | [-0.10, 0.05] | 0 |
| match_seconds | [120, 600] | 300 |
| descent_seconds | [600, 1800] | 900 |
| floor | [0.10, 0.45] | 配置值 |
| speed_gain | [0.5, 1.5] | 1 |

模型采用群体上下文奖励回归与受群体先验约束的个体效应，新个体冷启动用群体后验。采样采用后验均值 softmax 类别分布，记录精确类别概率；没有将 Thompson 蒙特卡洛频率称为精确倾向性。`calibrate()` 以独立会话验证奖励预测误差覆盖，置信度为保守 Wilson 下界，不是临床疗效概率。

会话在首个可锚定状态上尝试一次策略决策；可靠度不足时保持默认轨迹，不在会话中重新探索。参数经有界混合再由确定性规划器构造。旧配置超出学习动作族时保留旧行为，拒绝与学习动作混合。

独立数学示例（不启动引擎）：

```bash
python -m docs.LEARNING_BANDIT_EXAMPLE
python -m docs.LEARNING_BANDIT_EXAMPLE --output /tmp/mdt-synthetic-policy.json
```

### L1：保结构路线

`LearnedGaussianEmission.fit(training, calibration, evaluation, baseline=..., version=...)` 用 `EmissionExample` 的特征和主观弱标签拟合 `p(features | arousal)`。标签须明确量表转换和时间对齐；现行 logistic 输出不充当真实标签。

训练/校准/评估按会话分离，禁止共享会话或重复窗口膨胀样本。工件同时验证观测区间和时序 Kalman 后验：ECE < 0.05，90% 区间覆盖在 [0.85,0.95]。ECE 定义为多个中央区间的平均覆盖偏差，区别于分类 ECE。工件还固定基线哈希、滤波配置和已验证的过程时间尺度范围。

未验证的噪声、部分特征、缺失或采样历史会使学习估计整场回退，避免恢复未经校准的滤波状态。因此，只验证完整慢速窗口的工件不能声称支持混合快慢通路。基线估计器始终独立更新；混合状态采用较大方差并加上均值分歧项，防止虚假确定性。该保守规则不是新参与者或实际环境的覆盖率保证，仍需完整运行流程的目标数据验证。

### L3：只学增益

`GainPolicy.fit()` 输入独立选定的 `GainExample` 增益标签与上下文，拟合线性回归，输出冻结边界内的 Kp/Ki/死区/泄漏系数。校准和评估各需至少 30 个独立会话，验证“增益预测误差在指定容限内”的概率，不代表稳定性或疗效证据。

基线 PI 和增益 PI 的积分状态独立；模型不能修改积分/输出限幅及后验可靠度阈值。内置增益边界是工程原型约束，尚未经过真实生理系统整定。

### L3.5

`TastePolicy.fit(contexts, params, feedback)` 从已观察候选的喜欢/跳过或预定义收听反馈拟合偏好排序；`calibrate()` 需要独立反馈，未通过不执行。现有引擎只有参数/stem 接口，没有曲库服务，因此没有新增曲目播放服务。

`l35_guard.project(params, GuardLimits(...))` 接受任意候选：有限极端值裁剪，NaN/Inf/非数值保持原值，层级规范化，在固定边界快照上幂等。提交由真实小节/乐句事件驱动，学习音色回退到规则音色也等待边界。

## 审计与离策略评估

JSON 保留 `physio/music/subjective`，新增 `policy_manifest`（试验期、权限、版本、L1 校准）和 `policy_decisions`（完整上下文及哈希、动作、logprob、置信度、OOD、耗时/异常、仲裁、混合及选择结果）。音乐行新增 `policy_version/action_logprob/context_hash/ood_flag/arbiter_decision/lambda_mix` 和全模块 `policy_versions`，不沿用过期执行元数据。

**action_logprob 是原始建议的概率，不是混合/投影后实际播放参数的概率。** 完整行为策略包含模型、仲裁、守卫和权限；IPS/DR 必须建模执行链及验证动作支持集，不能直接使用建议概率。影子建议没有施加，不能与结局拼接后当作真实干预。当前提供审计基础，没有宣称已完成有效的 IPS/DR 疗效估计。

上下文包括基线、质量比例、首个状态、可靠度、上一结局、累计会话数和可选历史/作息摘要。个体效应使用 `subject_id`，训练/部署须使用同一假名规则；当前内置假名随试验期变化。

## 数据管理设计

原本地 JSON 仍含 user_id 与健康相关数据，未新增生产级加密或权限系统。模型上下文 SHA-256 假名不是匿名化或访问控制。真实数据训练前必须在部署层落实：

1. 受控导出采用密钥化 HMAC 假名，标识表独立保存，移除自由文本和未批准字段。
2. 日志、标签和工件加密，密钥放入部署密钥服务；传输采用认证加密，不向仓库提交真实数据/密钥。
3. 研究角色最小权限和导出审计，训练只读批准数据集，运行只读发布模型。
4. 按协议设置留存/删除期限、撤回和训练血缘，发布记录保留数据集版本与独立评估。
5. 负责人审阅试验期清单，多设备共享可信注册表；更新开启新试验期。

以上是部署设计要求，不代表仓库已实现相关基础设施。

## 验证

```bash
python -W error -m unittest discover -s tests -v
ruff check mdt_core tests demo.py docs/LEARNING_BANDIT_EXAMPLE.py
mypy --no-site-packages --ignore-missing-imports mdt_core tests demo.py
python demo.py
```

`tests/fixtures/legacy_trace.json` 是修改前代码的四臂数学回放，断言状态/音乐/引擎输出一致。新增测试覆盖仲裁回退/迟滞、实际超时隔离、守卫随机极端输入/幂等、观测和后验覆盖、对照臂不加载模型、掉线取消、安全中止与审计。

结果仅说明软件用例通过，不说明学习版本优于 PI。后续需真实设备时延、独立参与者校准、响应方向反转/基线漂移/偏好不匹配的闭环仿真，以及相对 ISO/SHAM 的预注册评估。
