# MDT：公开数据与证据实验方案

版本 2.2 · 2026-09-19 · 适用条件：仅使用公开研究数据和仿真，不新增真人采集。

本文是待执行的研究方案。数据集规格来自原作者论文、官方仓库及数据平台；本文未完成全量下载、文件级质量审计或算法实验，所有实验规模、参数和判据均为拟议设置，不是实验结果。本版重构证据顺序和发表路线，保留 v2.1 的数学、执行语义与统计纠正；不改变公开数据与仿真的工作范围。图内标注全部为英文。


# 1 选择结论与论文定位

建议用 **CASE + WESAD + 反应性模拟器** 建立首版实验，取得使用资格后加入 **DREAMT 2.2.0** 的睡眠域验证。**BCMI** 是最值得优先审计的音乐场景补充，**PMEmo** 用于可选弱监督，**MMASH** 用于无需申请的长时 RR 验证。不要要求一个数据集同时承担感知训练、音乐偏好、控制学习和睡眠疗效证明。

最小可运行方案只依赖 CASE 与自建模拟器；WESAD 增强跨设备可信度。推荐论文完整版应争取加入音乐域与睡眠域的独立外部验证。若相关数据最终不可用，论文应收窄为生理反馈控制方法与仿真研究，不把缺失的证据用合成结果填补。

建议工作题目：**Execution Contracts for Learning-Enabled Physiological-Feedback Music Regulation**。在仅有公开信号与仿真证据时，副标题明确为 Public-Data Validation and Reactive Simulation；实际数据尚未处理时，不把这个工作题目当成已完成验证的声明。层级组织是实现方式，H 对 B2 的 E_hier 仅为探索性比较；不因探索结果显著就事后改成题目和摘要的主贡献。

核心研究问题收敛为“可归因执行”：在相同模型、音乐约束和计算预算下，执行契约能否减少过期、冲突与故障建议对实际输出的影响，并维持可接受的控制性能和时延？层级组织的额外收益与学习收益分开检验。可归因执行表示日志和权限责任可追溯，不等于音乐对真人产生的因果效果已被识别。“睡前放松”界定任务与评价场景，不等于已证明促进睡眠。

| 证据类型 | 本研究可以检验 | 本研究不能据此宣称 |
| --- | --- | --- |
| 真实公开记录 | 感知误差、按人泛化、因果预处理、回放故障处理 | 新音乐策略对同一个人的反事实效果 |
| 反应性仿真 | 指定动力学假设下的调节、约束与执行一致性 | 临床人群中的疗效大小 |
| 睡眠域数据 | 夜间输入适用性、睡眠辅助识别、分布变化 | 音乐降低真实入睡潜伏期、提高睡眠效率 |

完整闭环必须是“执行音乐改变虚拟状态，虚拟状态产生新观测”。从文件读下一条与动作无关的生理记录只能构成回放。把不同数据集的同编号参与者拼成一个人，也不能补齐真实的跨模态干预数据。

## 1.1 前提 P0、M0 与当前证据状态

为避免与统计零假设混淆，本文把附件的 H0 研究前提记作 **P0**：自然运行中，可归因执行失效是否达到预先规定的不可忽略频率。为避免与本文件实验 E1 混淆，附件的层级探索问题 E1 记作 **E_hier**。实验 E1/E2/E3 的编号保持不变。

M0 是自然运行日志审计，不注入故障，不用仿真补齐自然事件。当前盘点 code、files、outputs 中相关工件，未发现合格自然运行会话；code/data 目录不存在。v21_contract_demo 明确是人工构造场景与 NullEngine 模拟 ACK，legacy_trace 是合成回归工件，测试汇总是软件证据。它们均不进入 M0 分母，也不建立 F1 故障库。

当前状态为 M0=NOT_ASSESSABLE、P0=NOT_ASSESSED、自然运行发生率和置信区间=未估计。这里不是“零失败”，不是“P0 不成立”，也不是“真实故障罕见”。工作区中找到的合格日志数为零只描述本次可用性盘点，不能推断系统历史部署情况。

| 决策门 | 预先规定的依据 | 可作出的结论 |
| --- | --- | --- |
| 来源与可判定性 | 自然执行 provenance、完整会话、精确时间、血缘与可核查执行事实 | 无合格记录或关键字段不足时 NOT_ASSESSABLE |
| 频率证据不足 | 已有可判定数据，但区间跨预设频率阈值或覆盖不足 | INCONCLUSIVE；不能把不显著写成无问题 |
| 前提获支持 | 至少一类事件达到预先冻结的频率证据标准 | 可把该类真实事件构成 F1 主证据 |
| 低频证据 | 各相关类别的适用上界均低于预设阈值，且缺失不能改变判断 | 转向低频事件防护，仍不证明零风险 |

日志中的缺 ACK、缺字段或不可核实时钟首先产生 UNKNOWN；不能填为“未发生”。若完整日志明确显示 ACK 缺失后系统错误地前移了执行状态，可判定“状态误判”事件，但仍不能编造真实音频已经执行的事实。M0 事件定义、检测规则、风险集、阈值、统计单位与审计版本必须在正式频率分析前冻结。

当前先完成 M0 的可用性盘点与审计规范，继续公开数据适配、F2 来源核查和 F3 机制实验。若找到公开的自然闭环执行日志，再按上述门槛审计；“公开生理记录”或“有音乐反馈”本身不能证明其日志合格。没有合格日志时，现实发生频率与 F1 收益保持未测，论文一暂收窄为机制与仿真研究，不宣称达到附件 M0–M3 全部前置条件。

## 1.2 三篇论文的证据分工

| 路线 | 当前问题与证据 | 边界与前置门 |
| --- | --- | --- |
| 论文一：系统机制 | 执行契约、独立强基线、分级故障、控制与时延代价；面向系统/生物医学工程/普适计算读者 | P0/M0 的现实前提未可判断；当前只允许限定仿真与软件机制结论 |
| 论文二：人体效果 | 未来比较个体化与确定性闭环对真实参与者的影响 | 独立伦理、输出链路校准和预注册；不属于本轮公开数据＋仿真任务 |
| 论文三：执行算子下的离线评估 | 独立学习问题、理论草稿与公开/合成基准；MDT 只是动机 | 文献重合检查、最小实验和非平凡理论三项立项门；不能用论文一工件代替新颖性 |

论文三不得把“原始动作概率不同于执行动作概率”这项观察单独包装为新算法。相关文献与候选问题按主模型 v2.2 研究稿单独审查；本数据方案不新增未经审计的数据集或方法引用。三条路线分别报告状态，人体路线或论文三未启动不阻塞当前允许的工程验证。

[英文公开数据与仿真验证图见排版 PDF](pdf/MDT-v2.2-data-evidence-protocol.pdf)

# 2 数据集选择：用途、获取与缺口

## 2.1 CASE：A2 的核心真实数据

CASE 包含 30 名参与者观看情绪视频时的同步生理记录，提供 ECG、EDA、BVP 等信号及连续 valence/arousal 自评；生理采样为 1000 Hz，自评为 20 Hz。原始信号、时间与刺激元数据适合重建因果窗口。它没有音乐控制动作，也没有睡眠干预结局。[1,2]

选择理由是“标签与当前连续唤醒估计最接近”，而非样本量最大。A2 主任务定义为预测主观唤醒评分的代理量，并报告自评延迟、个体差异及误差。不能将预测值解释为客观的睡眠深度。

获取入口是官方 GitLab 与 Figshare。官方代码和说明已核查；本次 Figshare 正文读取受限，**具体数据许可证尚未完成确认**。首次下载前保存该版本的数据许可，不把论文的 CC BY 或代码许可自动套用到数据。视频本身的版权与生理记录也应分别处理。[2]

## 2.2 WESAD：质量与跨设备辅助集

WESAD 发布 15 人的压力/情感实验，胸部 ECG、EDA 等为 700 Hz；腕部 EDA 4 Hz、BVP 64 Hz、ACC 32 Hz。机构官网允许科学、非商业用途，并要求引用。其压力、基线等条件标签不等价于连续自评唤醒。[3]

本方案用它比较胸/腕测量差异、单模态缺失、运动相关输入与故障处理。压力分类可以作为单独的外部任务；不把 stress=1、baseline=0 混入 CASE 的回归标签。它不用于学习音乐响应系数。

## 2.3 DREAMT 2.2.0：优先争取的睡眠扩展

DREAMT 包含 100 名睡眠门诊参与者的腕部 E4 与 PSG 数据。EDA 原生 4 Hz、BVP 64 Hz、ACC 32 Hz、HR 1 Hz；提供 PSG 睡眠分期，100 Hz 数据版本还包含 PSG ECG 等信号。当前 2.2.0 修正过时间对齐问题，应固定版本。平台标为受限访问，需注册并签署 DUA，适用 PhysioNet Restricted Health Data License 1.5.0。[4]

使用范围是睡眠状态辅助感知与夜间输入测试。该人群与健康睡前放松人群不同，且没有音乐干预。E4 的 IBI 源于脉搏，不能自动当作 ECG RR。若使用 PSG ECG，应明确这是实验室配置；若采用腕部部署配置，就单独验证 PRV 路线。上采样不增加原始信号分辨率。[4]

## 2.4 BCMI：最相关的音乐资源，但需先审计

Daly 等公开了 affective brain-computer music interface 的校准、训练和在线测试数据，论文描述了 ECG/GSR 等记录及情绪评价。测试阶段确实发生过音乐反馈调节，因此不能笼统声称“公开音乐数据全部是开环”。但在线测试生成的音乐音频未包含在发布内容中；现有说明不足以确认可重建 MDT 所需的完整动作、执行时间和行为策略概率。[5]

三个入口分别为 ds002722（校准）、ds002724（训练）、ds002723（测试）。本次发现：论文涉及 19 名 BCMI 参与者；训练 README 出现 16/17 等不同人数，当前可见训练 participants.tsv 列 10 人；测试 README 写 8 人。这些可能涉及不同阶段/发布范围，不能选一个数字作为最终可用样本量。[5,6,7]

另有许可元数据 CC0 与 README CC BY 4.0 不一致。记录这一冲突，按具体发布版澄清后使用；不把某个声明推定为覆盖全部生理数据、音频与代码。先验证 channels、events、个体自评、音乐可关联性、完整会话和许可，再决定纳入。目标情绪事件码只是 intended affect，不能当作 achieved affect 真值。[6,7,8]

## 2.5 PMEmo 与 MMASH：各补一项能力

PMEmo 的论文规模为 794 个音乐片段、457 名招募参与者，提供 EDA 和情绪标注；EDA 50 Hz，动态标注 2 Hz，前 15 s 的动态标签被移除。官方示例中 EDA 有 subjectId/musicId，而动态标注是 musicId/frameTime 对应的群体均值；因此只能把这部分作为音乐层面的弱监督，不能称为同一人的逐时自评。无 ECG/RR，不能补齐双模态输入。音频再分发权需单独核查。[9,10]

MMASH 是 22 名健康年轻男性约 24 h 的日常活动资料，包含 RR、活动与睡眠统计，官方采用 ODbL 1.0；缺少 EDA、PSG 分期和控制音乐。适合长时 RR 管线与睡前附近自然变化的补充验证。它可在 DREAMT 尚不可用时启动长时测试，但不是其睡眠分期的等价替代。[11]

| 优先级 | 资源 | 首次实施决策 |
| --- | --- | --- |
| 核心 | CASE | 核心 A2 数据；下载前确认具体数据许可 |
| 核心 | 自建反应性模拟器 | 核心闭环实验；全部假设与代码可审计 |
| 增强 | WESAD | 加入跨设备与质量压力测试 |
| 增强 | DREAMT 2.2.0 | 取得协议访问后加入睡眠域独立实验 |
| 增强 | BCMI | 文件/许可审计通过后加入音乐域外部验证 |
| 可选 | PMEmo | 仅作 EDA 音乐弱监督和音乐情绪先验 |
| 可选 | MMASH | 先行长时 RR 验证；不承担 PSG 结论 |

## 2.6 为什么首版不以 DEAP、DEAM、AMIGOS 为核心

DEAP 的音乐视频与 liking 标签、DEAM 的音乐情绪资料、AMIGOS 的 ECG/EDA 情绪记录各有价值，但都会引入额外的标签、访问或任务适配工作。首版不需要用多个类似数据集堆积规模。若后续研究 A5，须先确认 liking 字段、稳定的评价者 ID、跨音乐覆盖和音频权利，不能用 valence 代替 preference。AMIGOS 等协议数据也不应被写作无需申请的“完全开源”。[12,13,14]

# 3 A1-A5 分工与学习证据

保留 C0 会话监督、C1 感知协调、C2 轨迹协调、C3 控制协调，以及 A1-A5 五个有限职责 Agent。这里的 Agent 是带明确输入输出和权限的小模块，不要求 LLM，也不要求五个模块都在线学习。协调器与守卫采用确定性实现。

| Agent | 输入与输出 | 数据来源及首版设置 |
| --- | --- | --- |
| A1 Quality | 原始/派生信号、时间与掩码 → 质量、有效性 | CASE/WESAD 回放；规则为主，注入故障有单独真值 |
| A2 State | 同一个人的过去 EDA/RR 窗口 → 状态分布 | CASE 监督训练；BCMI 外部验证视审计结果启用 |
| A3 Trajectory | 首个有效锚点与背景 → 有界轨迹参数 | 仅在训练模拟器学习；每会话至多一次有效决策尝试 |
| A4 Gain | 有效状态/误差/质量 → PI 参数候选 | 训练模拟器搜索教师，再训练小型回归器；不改硬约束 |
| A5 Preference | 控制兼容音乐候选 → 排序 | 主实验冻结规则排序，学习门控为零；不声称已验证个体偏好 |

A2 的基线与学习分支各有独立滤波状态，主方法使用 w∈{0,1} 的分支选择；整分支权限或其他门控未满足时回到有效基线。连续混合及启发式方差单列对照，分支选择后的经验覆盖仍需独立检验。A4 的固定与增益调度 PI 各有独立积分状态。A3 不通过会话中反复改目标制造更小误差。会话内权重冻结，发布新模型必须重做校准与回放。

A5 接口仍参与完整流程：先限制为控制兼容候选，再规则排序，再经音乐守卫和执行网关。因此“完整闭环”与“A5 尚未用真实偏好训练”不冲突。若论文将四个学习模块都作为贡献，则必须另补 A5 的真实标签实验；当前主结论应只涵盖已验证的模块。

睡眠辅助模型属于单独实验头/观察器，不直接把 W、N1、N2、N3、REM 编码成单调下降的 arousal。未经专门验证，REM 或低 EDA 都不能触发“已经入睡”的临床判断。

# 4 数据收集与整理流程

这里的“收集”是取得合法可用的公开版本、保存出处并生成可复现派生数据。当前不进行招募、设备采集或新的干预。

## 4.1 下载前到首次入库

第一步建立清单：数据 DOI/版本、官方入口、发布日期、访问条件、许可文本、模态、单位、原生采样率、标签含义和预期角色。分别记载 paper_reported_n、manifest_n、downloaded_n、usable_n；最后两项必须在实际取得数据后填写。

第二步先读 README、participants、channels、events 和小型标注文件。抽查至少两个参与者、不同会话以及一个缺失/异常样本。确认文件不是 Git LFS/git-annex 指针，确认时间轴、信号单位、刺激 ID 与个体标签可以关联，然后才安排大文件下载。

第三步保持 raw 只读，计算 SHA-256，建立版本快照。把生理记录、音频、标注、软件许可分开存档。协议数据存储在受控本地目录；公开仓库默认发布处理脚本、清单和可分享的汇总结果，不把访问受限原文件纳入 Git。

第四步保存数据排除流程：原始参与者 → 成功读取 → 时间可对齐 → 模态满足 → 标签有效 → 最终可用。报告每一步的数量与原因，不能只给清洗后窗口总数。

## 4.2 最小数据契约

| 表 | 必需字段 |
| --- | --- |
| dataset_manifest | dataset_id, version, source_url, license_status, access_date, raw_hash |
| session | dataset_id, subject_id, session_id, stimulus_id, domain, split |
| stream | modality, native_rate, unit, sensor_site, device_time, aligned_time, missing |
| feature_window | source_window_id, per_modality_start/end, available_at, update_kind, values, mask, quality, pipeline_version |
| label | type, value, scale, start, end, available_at, self_report_or_PSG |
| simulation | family, profile_id, seed, empirical_sources, assumed_parameters |
| decision/action | proposal_id, accepted_id, target_id, parent_ack_id, command_id, epoch, ttl_obs_s, ttl_proc_s, boundary_id, ACK, timestamps |

双 TTL 是两项独立约束：从每个必需来源窗口结束计观测年龄，从原建议产生计管线停留。重打包和重发均不刷新起点；误差方向、阶段、候选集、父 ACK 或权限改变另做适用性检查。相同 source_window_id 的复用不是新独立观测，标签与可用时间严格分离。

每行另有 data_origin：recorded、derived、injected 或 simulated。模型参数另有 evidence：estimated、literature 或 stress_test_assumption。这两项只描述信号与参数来源；另存 run_origin（natural_deployment、passive_public_recording、simulation、replay、test）、fault_injected、engine_kind、log_completeness、source_hash 与 provenance_review_status。由真实波形驱动的模拟执行仍是 simulation/replay，不能升级为自然执行日志。

故障另存 fault_provenance=F1/F2/F3/unreviewed、来源定位、触发参数出处和人工扩展范围；自然来源未知时使用 unreviewed，不自动归为 F1。执行算子身份另保存 operator_version、guard_config_hash、gate_policy_version、scheduler_version、candidate_set_id、epoch，以及原始动作、接纳权重、绝对目标、投影、提交与 ACK。schema 是拟议的数据契约；不能据此声称当前所有字段已自动采集。

标签至少分为 self_report_arousal、stimulus_mean_arousal、experimental_condition、liking、observer_rating、PSG_stage。它们可以关联到同一流程，但不能无说明地训练同一个输出标量。

# 5 预处理与防泄漏约定

所有在线特征只使用当前及过去的数据。可参考现有系统 EDA 过去 10 s、每 2 s 更新；RR/HRV 过去 60 s、每 15 s 更新。具体特征、滤波与质量阈值只在训练折及内部验证中选择，并将训练到部署的相同实现版本冻结。

## 5.1 不同采样率与模态

EDA 4 Hz 数据必须按其原生带宽重新设计预处理；不能只插值到 32 Hz 就宣称与原实现完全等效。高频信号降采样前做适当抗混叠，并把因果滤波产生的延迟记入时间预算。离线零相位滤波可以另作上限分析，但不能用于声称可在线复现的主结果。

ECG 经 R 峰质量筛查得到 RR；BVP 得到脉搏间隔与 PRV，两条结果分开报告。1 Hz 平均 HR 无法重建真实逐搏 RR。HRV 首版侧重经过窗长验证的时间域特征；不把极短窗 LF/HF 当稳定的睡眠或交感副交感指标。

## 5.2 窗口与标签可用时间

BCMI 的一些音乐片段短于当前 60 s HRV 窗口。跨试次拼接会混入不同刺激，不能产生纯净的 trial 标签。主外部分析可以用 EDA-only，双模态只纳入具备完整真实历史且定义了混合刺激语境的连续窗口，并明确结果所对应的任务。

CASE 的连续自评也有人类反应延迟；只在训练受试者上估计并冻结延迟规则。片段结束后的自评可作为片段级监督目标，但 label_available_at 在片段结束后；不能作为在线上下文，也不能复制成每秒的独立真值。

## 5.3 拆分层级

先按参与者拆分，再产生窗口；同一参与者、连续会话和重叠窗口不得跨训练/测试。另做刺激留出及参与者×刺激双重留出，检查模型是否只记住某段视频的时间进程。BCMI 三阶段沿用同一人的代码，必须跨数据集统一分组。

标准化只用训练数据或协议允许的测试前基线。不用整夜/整段测试记录的均值与方差模拟在线标准化。特征选择、插补、噪声模型拟合、区间校准与门控阈值都必须遵守同一拆分。实际有效人数是统计单位；窗口数仅描述计算量。

# 6 实验 E1：真实信号上的状态估计

目的：确定小型 A2 是否比固定生理映射更准确，并且不确定性是否足够支持门控。主数据 CASE，完成审计的 BCMI 为音乐域外部验证。WESAD 的压力识别另列表，不混入主回归分数。

## 6.1 划分与模型

CASE 采用 5 折参与者外层验证；若 30 人全部可用，每折 6 人最终测试，其余 24 人中固定留 6 人专用于区间/门控校准，18 人做内层参与者分组调参并拟合模型。校准后不把这 6 人回填重拟合，确保校准分数对应最终模型。任何测试人标签不参与调参。另报告未见刺激和双重留出的结果；可用人数变化时按固定规则重新分组。小样本和时间依赖下的覆盖率只作实测校准结果，不声称分布外覆盖保证。

比较：训练均值预测、当前确定性映射、岭回归加因果平滑、紧凑异方差发射加独立 Kalman、小型 MLP。所有方法使用相同传感模态和过去窗口。再做 EDA-only、RR-only、融合三组消融。模型尺寸、参数量和推理时延一并报告。

先检验连续自评，再检验门控。状态评分的尺度按原始定义线性映射，不能按每个测试人的全段标签重标定。跨数据集时保留域标识；未校准外推与合法训练后的适配结果必须分别命名。

## 6.2 指标与成功判据

主要指标是受试者级 MAE；辅助指标包括 RMSE、适用时的 CCC、区间覆盖与区间宽度、质量分层误差、可用窗口比例。校准曲线同时报告误差和覆盖，避免通过极宽区间获得表面上的良好覆盖。

预先指定主要对比为“紧凑学习估计 vs 当前确定性映射”；先逐人计算差值，再做参与者聚类 bootstrap。报告置信区间与绝对误差变化，不只报告 p 值。门控通过标准由验证数据选定并冻结，不按测试结果临时更改阈值。

可接受的结果不一定是学习模型胜出：若不能在新受试者稳定改进，保留基线为默认、学习分支权重降低，并将负结果写为边界。不能先筛掉低响应或高误差的人，再宣称普适个体化。

# 7 实验 E2：睡眠域辅助验证

优先使用 DREAMT，在获得数据使用资格后执行。其作用是验证本系统在夜间生理分布中的适用程度；不为仿真音乐调节提供真人睡眠疗效证据。

## 7.1 两项分开的任务

任务 S1 是输入适用性：夜间有效 EDA/RR 或 PRV 窗口比例、质量拒绝率、特征漂移、不确定性变化和回退频率。现有 A2 可以作为冻结模型观察其夜间行为，但没有同义连续自评标签时，不能声称获得了 arousal 误差。

任务 S2 是单独训练的睡眠辅助观察器：以 ECG+EDA 或腕部 BVP+EDA 等明确配置预测 wake/sleep，五分类分期列为次要任务。PSG 标签只进入训练目标和离线评分；PSG EEG、EOG、EMG 不能作为声称“仅 EDA/RR 输入”的模型特征。两个传感配置的结果分别报告。

## 7.2 评价设置

按参与者 5 折外层验证，内层调参、类权重与校准。主指标 balanced accuracy 和 macro-F1，辅助报告各类召回、混淆矩阵、校准及有效时间覆盖。逐人汇总再统计，不能用一个长夜的大量 epoch 压倒其他人。

时间对齐按版本元数据校验；分期边界使用结束于当前时刻的特征。准备阶段与有效 PSG 记录分开，避免把准备时间混作自然清醒。主任务对模态、标签缺失设预先规则并报告排除量。

如仅能使用 MMASH，就执行 RR 长时输入与回放可用性分析，睡眠统计最多作探索性关联。报告中应明确未完成 PSG 分期验证，不把 actigraphy/日记统计与 PSG 混为同一种真值。

## 7.3 对闭环的接口限制

主控制实验不让睡眠标签决定动作。可以额外评估“辅助观察器提示可能进入睡眠时，系统是否降低不必要的音乐变化”的模拟策略，但这会新增策略，应独立命名和消融。其输出是研究级状态提示，不是已验证的入睡检测产品。

# 8 实验 E3：反应性虚拟用户与完整闭环

建议采用特征层模拟器：真实原始信号用于 E1/E2 与故障回放，仿真从 A1/A2 所需特征层进入。这样能测试控制反馈，同时明确没有合成并验证逼真的 ECG/EDA 波形。

## 8.1 状态与动作方程

令 a 为归一化的模拟唤醒状态，m 为经过守卫、边界提交与引擎 ACK 后实际生效的音乐参数；采用可解释的研究模型：

**Equation (1)**

```text
a_(k+1) = clip[a_(k) + Δt(μ_(i)(t)-a_(k))/τ_(i) + Δt β_(i)(t) g_(i)(m^(exec)_(k-d)) + w_(i,k), 0, 1]
z_(k) = h_(i)(a_(k), motion_(k), respiration_(k)) + ε_(i,k)
```

μ 表示自然变化，τ 表示恢复时间，β 与 g 表示响应方向和非线性，d 表示生理反应延迟，z 是控制器看到的观测。潜在 a 仅供评估器评分，不能暗中提供给控制器。

公开数据可以估计观测尺度、协方差和部分噪声形态；未能从干预记录识别的 β、τ、d 属于显式仿真假设。相关拟合不能升级为因果响应。仿真须公开这种参数来源差异，也不称为已验证的人体数字孪生。

v2.1 控制量 u 是生成音乐绝对目标的意图，不是直接作用于人体的生理输入。以父决策的已确认音乐 p_parent_ack 生成固定目标 p_target；例如 tempo_target=tempo_parent_ack+12u。执行网关再在真实边界投影，ACK 后才将实际参数送入响应方程。相同 command ID 重传不得重复生效；仍适用的同一固定目标在新边界可有新的部分执行命令，但不能重新加 12u。ACK 缺失时状态未知，冻结新目标和反算，保留拒绝/停止事件。

## 8.2 三个动力学族

动力学族使用 DYN-L/DYN-N/DYN-R；F1/F2/F3 专用于故障来源。两者为独立维度，不按响应函数的复杂程度推断故障是否真实。

DYN-L：线性或弱非线性、有恢复与固定延迟。用于训练与内部验证，系数不与测试配置重复。

DYN-N：饱和、迟滞、习惯化和时变敏感度。主要用于未见模型族测试；观测函数也不与 A2 的高斯发射完全相同。

DYN-R：零响应、反向响应、强自然恢复、基线漂移、极端延迟。用于报告失败边界。若系统没有识别反向响应的机制，就如实报告恶化与饱和，不假设它会自动修复。

所有响应范围先作为无量纲压力测试规格公布。延迟可设置 0/10/30/60 s，缺失比例可设置 0/10/30%，但这些数值不是已确认的人体响应范围。每个场景单独报告结果，避免通过任意混合权重制造总体优势。

## 8.3 会话与训练协议

拟议会话为 20 min：3 min 初始历史与锚定、12 min 缓慢下降、5 min 保持。设置 40 min 作为持续运行压力测试。时间仅是实验设计，不是助眠处方。

共同评分参考由模拟起点和固定任务规则产生。例如 a0 取 0.45-0.80，最终目标 max(0.20,a0-0.25)；这些是模拟归一化值，没有临床单位。A3 的任务子集先限定相同终点与总时长的有限形状参数，如前缓后快/近线性/前快后缓；这不同于允许所有五维参数自由变化的扩展实验。锚点 r_anchor=clip(a0_hat+delta_anchor,r_min,r_max)，r_goal=min(r_anchor,r_floor)；睡前子集冻结不主动抬高锚点的偏移规则。控制器可见参考由正常锚点生成，评估器另用统一参考评分，避免不同方法各自降低目标难度。

A3 在训练配置上探索有限动作，保存候选、概率、随机种子和门控后结果。A4 在训练配置上离线搜索满足预先定义的误差、振荡和约束判据的有界增益，拟合紧凑回归器；有限仿真不构成稳定域证明。模型发布前独立校准，测试会话内只做冻结推理。A5 主实验不训练个体偏好。A4 工件绑定 controller_variant、积分归一化尺度与泄漏定义，不能把旧 Ki 直接移到新积分量纲。学习权限仅升权限速、降权立即；该连续斜坡用于控制混合，不把 A2 的离散分支选择改成连续融合。HOLD 设有限 T_hold,max，独立计时超时转 STOPPING；重复无效消息不重置计时，恢复先回 BASELINE。

仅有开环记录或不完整的既有闭环日志时，不用 IPS/DR 推断新音乐策略的真人效果。对应方法需要适当的动作、行为策略与支持覆盖；反事实模拟还依赖结构假设。这也是本方案将回放与反应性仿真分开报告的原因。[15,16]

# 9 对照实验：分清契约、层级与学习贡献

| 对照臂 | 保留机制 | 直接比较与解释 |
| --- | --- | --- |
| B0：Deterministic | H 的同一执行契约/网关/硬守卫；A2/A3/A4 学习关闭，A5 固定规则 | H-B0：已启用学习模块总增益 |
| B1：Strong flat baseline | 独立常见实践实现，保留合理超时、时间戳检查、去重等已有能力，共享硬守卫/资源预算；不直接由 H 删除机制生成 | B2-B1：契约增量效果；解释须按故障来源分层 |
| B2：Flat with contract | 相同工件、完整契约、目标值网关、预算与硬限；扁平组织 | H-B2：E_hier，仅探索性层级增量 |
| H：Hierarchical | C0-C3/A1-A5 组织，完整契约；A5 固定规则 | 主方法 |
| Y（可选）：Yoked | 预先冻结独立供体模拟会话的已确认音乐序列 | H-Y：反馈作用，单列探索 |
| G（可选）：Gain scheduled | 确定性增益调度，相同状态输入与执行约束 | 与传统调度比较，单列强基线 |

B0/B1/B2/H 为四臂核心。B1 保留参数硬限及共同独立停止能力，具体采用哪些常见机制及与 B2 的差异逐项列入 manifest；不得含糊称为“无安全基线”，也不得刻意删除原本合理的保护来制造效果。B0 表示同契约下学习关闭的匹配对照；原始 legacy 确定性代码可额外回放，不能把其不同执行层造成的差异全归因于学习。安全机制消融只用于隔离软件仿真，不连接真实参与者或实际音频输出。FULL_LOOP/SHAM/DIRECT/ISO 是原协议模式，另存 experiment_variant，不能直接互相重命名。

组织比较固定工件、候选生成规则、初始条件、外生扰动、计算预算与硬限。回放等价性测试可固定完整输入流；反应性闭环只固定起点与外生扰动，必须允许动作造成不同后续状态/候选。H 和 B2 共享双 TTL、父 ACK、绝对目标、权重斜坡、分支选择及 PI 变体，才能隔离层级贡献。

Y 的供体库独立生成并冻结，不能取同一虚拟对象的最佳闭环轨迹。报告音乐时长、参数分布与变化次数匹配程度，避免把音乐暴露差异解释为层级收益。

## 9.1 模块与控制递推消融

A2/A3/A4 采用“分别加入单模块”与“完整方法逐个移除”，资源允许时做 8 种组合；A5 没有启用学习，关闭它不是学习消融。A2 默认分支选择与连续混合/启发式方差分别报告，不能混作一个结果。

PI 变体按单因素组织：旧式积分和输出双 q；只归一化时间且正确迁移 Ki/Imax；条件积分去除积分侧 q；按时间泄漏；ACK 反算；连续权重斜坡。避免一次修改五项再把全部改进归因于 q²。normalized 变体可靠且死区外时累积 εΔt/TI，0<q<q_on 冻结，q=0/死区/暂停时乘 exp(-Δt/τI)。ACK 反算只用于可归因到单支的速度通道，以命令历史的 qKi 为分母；近零、未知执行或重复 ACK 均禁用。

## 9.2 强 B1 的独立实现与公平审计

B1 先有书面规格，由未参与 F3 设计、未看故障注入结果的实现者完成，并在最终测试前冻结代码 hash、依赖、配置、预算与设计说明。独立实现不代表使用更弱模型、更少资源或更松硬限；当前首版 A5 固定规则，所有臂保持一致，不称四个已训练模型的比较。常见实践若已具备 ACK、幂等或有效时间检查，应保留并明确实际增量差异。

使用所有臂共用的外部审计观测器重建提交/执行事实；审计信息不馈入 B1 控制器。缺少本方案内部契约字段不能自动判为实际非法提交。共同违规定义应基于冻结时间、权限与输出条件，而不是“没有使用我们的契约”。外部工程师对基线代表性和比较公平性给出记录；自动化 agent 分工或代码 review 不替代独立外部评审。

当前状态：强 B1 独立实现、测试前冻结与外部公平评审均待完成；本文件仅固定要求，不宣称已取得独立性。若后续只能由同一作者实现，应如实降级为作者实现的比较基线，不能沿用“独立强基线”标签。

## 9.3 故障矩阵

覆盖 A3 并发与重启、陈旧 HRV 被新 EDA 包装、观测/处理 TTL 独立超时、未来时戳、乱序响应、A4 超时、A5 候选错配、版本不一致、重复/丢失 ACK、边界延迟、固定目标重复提交、STOP-submit 竞态、无输入时 HOLD 超时与资源阻塞。

响度测试在本轮验证数字力度/增益代理的绝对范围和变化率；尚未校准真实设备声压。越界候选被拒绝是正确拦截，计为 unsafe_attempts/rejections；只有实际提交违反冻结约束才计 invalid_commits，ACK 后实参越界另报。不能将成功拦截计作非法提交使更严格守卫看似更差。

## 9.4 F1/F2/F3 来源分层与登记

| 级别 | 来源与参数要求 | 当前状态与可允许结论 |
| --- | --- | --- |
| F1 经验性 | 合格 M0 自然日志中出现的失效；保存原时序、频率、组合和选择规则 | 当前不可用；不能用已有合成 demo 或公开被动波形补位 |
| F2 外部来源 | 具体设备/接口资料或同类系统原始报告；记录版本、页码/段落、条件和参数 | 逐条核查后才纳入；外部报告不能直接给出 MDT 的自然发生率 |
| F3 构造性 | 作者预设压力场景及扫描范围；包括未由经验材料支持的故障强度 | 现有单测/demo 属于此类；只作机制性质和条件性失败域验证 |

每条记录至少有 scenario_id、provenance_level、source_locator、source_version、source_hash、observed_or_assumed_parameters、source_case_id、selection_rule、design_freeze_id、review_status、tuning_or_test_split。某个故障类型有外部出处，不意味着作者任意选择的强度和频率也有出处；人工扩展的部分另列为 F3，不整项洗成 F2。

F1 原频率回放与人为富集失败片段分开。若为提高压力覆盖而富集，不再把富集库中的失败比例解释为自然发生率；保留抽样规则，不能在未说明权重时恢复自然总体结论。F1/F2/F3 不合并为一个平均通过率或收益数。当前没有 F1，F2 也尚未完成逐场景来源审计，因此不能报告现实条件下收益；已有 F3 通过只说明相应构造场景满足设计检查。

# 10 指标、样本量与统计计划

## 10.1 预先指定主要指标

当前仿真机制主终点为“一个独立虚拟配置在某一个已声明故障来源层及规定种子/测试集合内是否至少发生一次非法提交”。F1、F2、F3 分层计算，不能跨来源合并。逐会话发生率和逐提交比例为辅助指标；存在同配置重复时，不把会话当独立 Bernoulli 单位。非法指实际提交的动作违反各臂共同预先冻结的时间、权限、边界或输出有效条件，必须由独立审计器判定。版本/epoch 或血缘可以作为该判定的证据，但仅缺少实现特定字段时应判 unknown，不直接算违规。A3 重复尝试等未产生实际提交的不变量违反另行统计，不能混入非法提交发生率。

控制性能是预定义关键权衡，采用相对共同参考的模拟真实状态 RMSE，并配套非劣界值；不与仿真机制主终点并列事后挑选。次要指标为 IAE、末 5 min 状态变化、振荡、反向变化、饱和比例和音乐参数总变差。不能只计算 A2 自己估计值对自己目标的误差。

**Equation (2)**

```text
RMSE = sqrt[(1/T) Σ_(k) Δt (a_(k) - r^(eval)_(k))²]
TV = Σ_(b) ||m^(exec)_(b) - m^(exec)_(b-1)||_(scaled,1)
```

可用性指标包括有效调节覆盖、hold 时长、目标推进完成率、P50/P95/P99 延迟、超时/拒绝原因、提交到 ACK 延迟及恢复时间。把性能与覆盖同时报告，防止通过全程 hold 得到“零错误”的无用系统。音乐参数总变差按各参数允许范围归一化，只对已确认边界 b 求和，不乘控制事件 Δt。延迟按实际依赖图关键路径 CP_G 计算，区分观测年龄、决策、队列、边界等待与 ACK，不能把并行模块时延机械相加。

## 10.2 仿真规模起点

训练 200 个虚拟参数配置，验证 50 个，测试 200 个独立配置；每个测试配置使用 5 个外生噪声种子。B0/B1/B2/H 四臂核心在单一场景中为 200×5×4=4,000 个模拟会话，10 个明确场景约 40,000 个会话。先用 20 个配置做工程试运行，再冻结场景与统计计划。

该数量是计算预算起点，不是统计功效保证，也不是 200 名真实参与者。按 Monte Carlo 标准误判断是否增加模拟重复；重复只降低所设仿真分布内的数值不确定性，不能弥补人体动力学假设错误。

主比较预先指定 B2-B1（执行契约）；H-B0 对应 H3 学习问题，H-B2 对应 E_hier 层级探索，Y 若纳入则 H-Y 反馈探索。H1 的现实收益主张需要 F1 证据；只有 F2/F3 时按来源收窄，不把仿真主终点更名为已确证现实收益。E_hier 不纳入确证假设族，不在显著后升级。各方法共享虚拟起点、参数与外生噪声，保留动作产生的后续状态差异。先在虚拟配置内聚合种子，再作配置级配对分析；同一配置的数千时间点不是独立样本。

## 10.3 Estimand 与非劣效计划

| 属性 | 冻结定义 |
| --- | --- |
| 目标总体 | 声明抽样分布内的虚拟配置；F1/F2/F3 分别定义，当前 F1 不可用 |
| 条件 | B2 对 B1；同一模型/预算/硬限，固定契约差异 |
| 变量 | 每配置的规定测试集合中是否出现非法提交 |
| 并发事件 | HOLD/超时/STOP/崩溃/未知 ACK 保留；未知执行单列并作最坏情形敏感性分析 |
| 汇总 | 配置级事件率差及区间；事件足够时另报率比；完整误差/coverage/时延作代价 |

停止后虚拟对象继续按统一停止后输入策略演化并评分至共同结束时间；不能删掉提前停止后的时间再算低误差。真实公开数据的 E1 estimand 单独定义为未见参与者平均 MAE 差，E2 为参与者平均睡眠识别性能，均不解释为干预效果。

H1 的契约代价以 D=RMSE_B2-RMSE_B1 定义，单侧 95% 区间上限低于预先冻结 ΔNI 才支持该场景下非劣；E_hier 的 RMSE_H-RMSE_B2 另外报告探索性效应与区间，不升级为层级确证。coverage 损失与 P99 代价也有预定方向和界值。界值须由独立试运行与任务可容许损失解释，不能在最终测试后选到刚好通过。没有合理界值时只报告区间，不称非劣。差异不显著不是非劣，未通过非劣也不自动证明劣效。

## 10.4 推断与报告

真实公开数据分析按真实参与者聚类 bootstrap，模拟实验按虚拟配置聚类 bootstrap，例如各 2,000 次。对预先指定的主要对比族采用 Holm 校正，探索性结果独立标记。报告效应量、区间、完整失败分布和预先定义的失败案例。

若零非法提交，应报告场景数、会话数、触发机会与聚类结构，写“在本测试范围未观察到”，不写“绝对安全”。全零事件不用退化为 [0,0] 的普通 bootstrap 区间。若配置独立随机抽样，以“一个配置的规定测试是否出现事件”为单位，可报告单侧 95% 精确上界：200 个独立配置均无事件时约为 1.49%。这是所设分布下配置级事件率的上界，不是每动作或真人风险。固定参数网格则只报告范围与计数，不套用该抽样上界。

零事件率比可能不可估计，此时优先报告率差、适用的精确界与覆盖，不能补造除零结果。独立 Bernoulli 零事件公式 p_upper=1-0.05^(1/n)，n_min=ceil[ln(0.05)/ln(1-p0)]；p0=1% 需 299 个独立单位，0.5% 需 598 个，本方案重复种子不计新增独立单位。

若 H-B2 接近，层级额外收益未获支持；若 B2-B1 有证据则结论转向执行契约。区间跨零表示不确定，不等于证伪；区间精确排除预定最小收益才否定该幅度收益，显著反向变化另列。所有界值与主要比较在最终数据前冻结。

## 10.5 M0 的会话分母与未知事件

每类自然事件保存 TRUE、FALSE、UNKNOWN、NOT_AT_RISK。过期执行需来源结束、决策产生、冻结 TTL、可靠时钟与实际执行证据；重复累积需原意图与逐次实际参数；STOP 竞态需 STOP 的线性化/取消规则和命令顺序；无 ACK 后误判需完整日志下软件前移状态的证据。合法的新边界继续逼近固定目标不算重复累积，成功拦截不算非法执行。

| 量 | 定义 |
| --- | --- |
| N_eligible | 纳入期内符合自然运行来源要求的会话 |
| N_at_risk | 该事件存在触发机会的会话；STOP 类另明确 STOP 请求风险集 |
| N_assessable | 风险集内该事件证据足够的会话 |
| N_event / N_unknown | 至少发生一次的可判定会话 / 证据不足会话 |
| 主发生比例 | N_event/N_assessable，并联报覆盖与未知原因 |
| 辅助量 | 全会话发生比例、提交/机会分母、暴露时长；不可互换 |

N_assessable=0 时率和置信区间均为 null。只对可判定会话估计的率不代表完整总体；若 N 为事件风险集总会话，E 为已确认事件会话、U 为未知会话，可另报缺失状态的识别范围 [E/N,(E+U)/N]。这不是置信区间。若未知状态仍可能改变阈值判断，不能宣称 P0 已被支持或低频已被证实。同一参与者多会话按参与者聚类；零事件精确上界仅用于满足独立抽样假设的正样本数。

自然 M0 与模拟研究分开输出；两者都不能因日志损坏、崩溃、未知 ACK 或早停而仅保留成功会话。事件检测器测试通过只验证检测逻辑，不产生自然事件发生率。

# 11 论文应展示的图表与执行顺序

论文一优先安排六组图：英文系统总览；观测到执行与 STOP 时序；M0 会话发生率及可判定覆盖；B1/B2/H 在 F1/F2/F3 的分层结果；控制误差/coverage/P99 的代价；模型与执行成本。没有 M0/F1 数据时只展示证据状态和缺口，不绘制零高度失败率柱形。真人配对结局留给论文二。感知误差与校准、模拟轨迹及完整消融移入相应补充材料；所有结果图等待真实计算，示意图必须显式标示。

结果主表预设来源层、统计单位、样本/会话/机会数、主效应与区间、控制误差、coverage、非法提交、未知执行、P99 与模型成本；无数据填“未测量”，不可填 0。补充材料保存数据样本流、E1/E2 结果、版本、超参数、DYN 模型公式、全部场景、排除量及负结果。

论文一篇幅以读者问题和 H1 为中心：Introduction 12%、Related Work 8%、Formulation 10%、Method 25%、Evaluation 35%、Discussion 10%，仅为写作预算而非会场页数规定。感知/PI 推导、全部字段、完整故障矩阵与模型细节放补充材料；不能把本方案文档直接当论文正文。

| 阶段 | 具体产物 | 继续条件 |
| --- | --- | --- |
| 0 M0 可用性与注册 | 来源盘点、事件定义、风险集、精确时钟与缺失规则 | 当前无合格日志；保留 NOT_ASSESSABLE，允许继续限定仿真工作 |
| 0B 基线与来源冻结 | B1 独立规格/实现/外评，F1/F2/F3 注册表 | 无独立评审或来源证据则保留未完成状态并收窄比较 |
| 1 数据审计 | 许可快照、文件/字段清单、可用样本流程 | CASE 读取与标签关联可复现 |
| 2 感知基线 | 因果特征流水线、固定拆分、E1 报告 | 无参与者/时间泄漏，基线可复现 |
| 3 模拟器 | 参数来源表、三个模型族、动作/ACK 闭环 | 动作确实影响下一状态，测试真值不泄露 |
| 4 A3/A4 训练 | 工件、校准、版本冻结、训练日志 | 只用训练模拟配置，回退行为通过审计 |
| 5 主实验 | 配对 B0/B1/B2/H、消融、故障压力结果 | 同模型同预算，独立评价器一致 |
| 6 外部验证 | BCMI 音乐域、DREAMT 睡眠域结果 | 各自访问/字段审计通过，单独成表 |
| 7 论文复现 | 配置、种子、运行命令、汇总数据与图表脚本 | 一条入口可再生成已报告的结果 |

先记录阶段 0 的证据状态并冻结协议，再推进 1→2→3→4；阶段 0B 的公平比较要求约束最终阶段 5，最后到 7。M0 真实证据缺失不阻塞机制实现，但阻止以 F1/现实频率为基础的主张。外部数据获取、F2 审计及论文三立项检索可以并行；任何一条未完成都在题目范围、摘要和结论中明确。

最终应公开：代码版本、环境锁定文件、拆分清单、允许公开的数据派生规范、模拟器、参数来源、实验配置、评价器和汇总结果。原始数据是否可再发布按各自许可决定，不能因为代码开源就连带公开受限生理与音频文件。

# 12 论文主张、局限与完成判据

主贡献固定为有限权限协调下的可归因执行契约。真实公开信号感知验证、反应性模拟器、同模型对照与独立审计是支撑证据，学习控制是应用载体，不并列包装成五项独立创新。Agent 数量多、图形复杂或一条下降曲线本身不构成充分创新。

建议英文结论模板：Public physiological recordings evaluate subject-level observation performance. A separate reactive simulation evaluates regulation and execution reliability under explicit response assumptions. Sleep recordings provide auxiliary domain validation. Clinical sleep benefit is not established by this study.

必须披露四项局限：音乐生理响应尚未得到本系统的真人因果辨识；视频情绪自评向静卧睡前状态的迁移有限；真实数据参与者与临床睡眠数据人群代表性有限；A5 学习型个体偏好尚未纳入主证据。

不能写：显著缩短真实入睡潜伏期、改善真实睡眠效率、优于某临床治疗、已经验证人体闭环稳定性、五个学习 Agent 均获得真实数据证据。即使仿真差异很大，也不改变这些边界。

采用三篇论文路线：论文一以执行契约为系统问题，当前能推进公开数据与仿真机制验证，现实自然频率未测；论文二未来在独立伦理、输出声压校准与预注册后评价真人效果；论文三独立研究执行算子下 OPE，需新颖性、最小实验和理论决策门。后两者都不被写成本轮已完成。公开数据仍遵守各自许可，分享处理脚本和允许公开的汇总/合成工件。

| 实际证据状态 | 允许结论 |
| --- | --- |
| M0 无合格日志 | 自然发生频率未可判断；不能写“罕见”或“零失败” |
| 合格独立样本零事件 | 本范围未观察到事件，报告适用上界和缺失；不称零风险 |
| 仅 F3 通过 | 机制在所构造场景满足检查，不声称现实收益大小 |
| F2 有差异 | 在所引用外部条件的复现场景有差异，MDT 自然频率仍未知 |
| 强 B1 已表现良好 | 如实报告契约增量有限或不确定，不修改基线制造收益 |
| H-B2 有差异 | E_hier 探索性发现，保留区间与资源代价，不升级主题目 |
| B2-B1 或 H-B0 区间跨零 | 尚未获得预设支持；不能自动当作已证伪或已等效 |

本方案当前完成的是既有数据选型的保留、M0 工作区可用性盘点与 v2.2 证据设计重构；本轮未重新下载数据或完成全量质量审计。软件已提供 opt-in FULL_LOOP 离线仿真 profile，具体实现边界与通过测试见《MDT_v2.1_修订与验收清单》；B0/B1/B2/H 大型实验尚未执行，不能从本设计稿推断结果已经完成。下一阶段完成标准是：取得适用版本并审计、实现协议适配与模拟器、跑完冻结实验、记录全部结果，再据此撰写 Results。文中的样本量与指标表不能被误当作已经完成的实验。

# R 官方数据与方法来源

1. [Sharma et al. (2019). A dataset of continuous affect annotations and physiological signals for emotion analysis. Scientific Data.](https://pmc.ncbi.nlm.nih.gov/articles/PMC6785543/) — CASE 原始数据描述；用于信号、自评与实验范式核查。

2. [CASE official repository and data deposit. Accessed 2026-09-19.](https://gitlab.com/karan-shr/case_dataset) — 官方 README 与处理脚本；数据入口：https://figshare.com/articles/dataset/CASE_Dataset-full/8869157 。本次数据许可正文尚未确认。

3. [University of Siegen. WESAD dataset: download and usage conditions.](https://ubi29.informatik.uni-siegen.de/usi/data_wesad.html) — 以机构科学、非商业使用条件为依据；不采用第三方页面的许可证替代。

4. [Wang et al. (2026). DREAMT, version 2.2.0. PhysioNet. DOI: 10.13026/3f7y-2d80.](https://physionet.org/content/dreamt/2.2.0/) — 版本、模态、对齐修正、研究人群及当前访问条件。

5. [Daly et al. (2020). Neural and physiological data from participants listening to affective music. Scientific Data 7, 177.](https://www.nature.com/articles/s41597-020-0507-6) — BCMI 与相关实验原始论文；在线测试合成音乐的缺口。

6. [OpenNeuro ds002724. Training sessions: README, participant manifest and dataset description. DOI: 10.18112/openneuro.ds002724.v1.0.1.](https://github.com/OpenNeuroDatasets/ds002724) — 当前可见清单10人，README人数与论文范围不同；CC0/CC BY声明冲突，需按固定发布版审计。

7. [OpenNeuro ds002723. Testing session. DOI: 10.18112/openneuro.ds002723.v1.1.0.](https://github.com/OpenNeuroDatasets/ds002723) — 测试 README 写8名参与者；说明在线调节范式。

8. [OpenNeuro ds002724. Example event dictionary.](https://github.com/OpenNeuroDatasets/ds002724/blob/master/sub-08/ses-3/eeg/sub-08_ses-3_task-run1_events.json) — 事件类型描述 targeted affect；不能代替参与者真实自评。

9. [Zhang et al. PMEmo official repository and dataset description.](https://github.com/HuiZhangDB/PMEmo) — 音乐片段、EDA、动态标签和文件组织。

10. [PMEmo. Official dynamic music-emotion recognition example.](https://github.com/HuiZhangDB/PMEmo/blob/master/dynamic_MER.ipynb) — musicId/frameTime 与群体均值标签的连接方式；个体动态标签不能据此假定存在。

11. [Rossi et al. (2020). Multilevel Monitoring of Activity and Sleep in Healthy people (MMASH), version 1.0.0. DOI: 10.13026/cerq-fc86.](https://physionet.org/content/mmash/1.0.0/) — 公开RR、日常活动及睡眠统计；ODbL 1.0。

12. [DEAP. Official end-user license agreement.](https://www.eecs.qmul.ac.uk/mmv/datasets/deap/doc/eula.pdf) — 学术研究用途与访问协议；下载入口当前可达性需复核。

13. [Aljanaki, Yang and Soleymani (2017). Developing a benchmark for emotional analysis of music. PLOS ONE 12(3), e0173392.](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0173392) — DEAM 的音乐情绪与其他标注；用于后续音乐候选库选型。

14. [AMIGOS. Official dataset and access instructions.](https://eecs.qmul.ac.uk/mmv/datasets/amigos/) — 协议访问的外部情绪生理数据备选。

15. [Dudik, Langford and Li (2011). Doubly Robust Policy Evaluation and Learning. ICML.](https://icml.cc/2011/papers/554_icmlpaper.pdf) — 离线策略评价依赖相应动作与行为策略日志；不据此弥补本任务缺失的动作支持。

16. [Oberst and Sontag (2019). Counterfactual Off-Policy Evaluation with Gumbel-Max Structural Causal Models. ICML.](https://proceedings.mlr.press/v97/oberst19a.html) — 反事实模拟依赖结构假设；本文因此独立报告仿真假设与真实数据证据。

[1] [Sharma et al. (2019). A dataset of continuous affect annotations and physiological signals for emotion analysis. Scientific Data.](https://pmc.ncbi.nlm.nih.gov/articles/PMC6785543/)

CASE 原始数据描述；用于信号、自评与实验范式核查。

[2] [CASE official repository and data deposit. Accessed 2026-09-19.](https://gitlab.com/karan-shr/case_dataset)

官方 README 与处理脚本；数据入口：https://figshare.com/articles/dataset/CASE_Dataset-full/8869157 。本次数据许可正文尚未确认。

[3] [University of Siegen. WESAD dataset: download and usage conditions.](https://ubi29.informatik.uni-siegen.de/usi/data_wesad.html)

以机构科学、非商业使用条件为依据；不采用第三方页面的许可证替代。

[4] [Wang et al. (2026). DREAMT, version 2.2.0. PhysioNet. DOI: 10.13026/3f7y-2d80.](https://physionet.org/content/dreamt/2.2.0/)

版本、模态、对齐修正、研究人群及当前访问条件。

[5] [Daly et al. (2020). Neural and physiological data from participants listening to affective music. Scientific Data 7, 177.](https://www.nature.com/articles/s41597-020-0507-6)

BCMI 与相关实验原始论文；在线测试合成音乐的缺口。

[6] [OpenNeuro ds002724. Training sessions: README, participant manifest and dataset description. DOI: 10.18112/openneuro.ds002724.v1.0.1.](https://github.com/OpenNeuroDatasets/ds002724)

当前可见清单10人，README人数与论文范围不同；CC0/CC BY声明冲突，需按固定发布版审计。

[7] [OpenNeuro ds002723. Testing session. DOI: 10.18112/openneuro.ds002723.v1.1.0.](https://github.com/OpenNeuroDatasets/ds002723)

测试 README 写8名参与者；说明在线调节范式。

[8] [OpenNeuro ds002724. Example event dictionary.](https://github.com/OpenNeuroDatasets/ds002724/blob/master/sub-08/ses-3/eeg/sub-08_ses-3_task-run1_events.json)

事件类型描述 targeted affect；不能代替参与者真实自评。

[9] [Zhang et al. PMEmo official repository and dataset description.](https://github.com/HuiZhangDB/PMEmo)

音乐片段、EDA、动态标签和文件组织。

[10] [PMEmo. Official dynamic music-emotion recognition example.](https://github.com/HuiZhangDB/PMEmo/blob/master/dynamic_MER.ipynb)

musicId/frameTime 与群体均值标签的连接方式；个体动态标签不能据此假定存在。

[11] [Rossi et al. (2020). Multilevel Monitoring of Activity and Sleep in Healthy people (MMASH), version 1.0.0. DOI: 10.13026/cerq-fc86.](https://physionet.org/content/mmash/1.0.0/)

公开RR、日常活动及睡眠统计；ODbL 1.0。

[12] [DEAP. Official end-user license agreement.](https://www.eecs.qmul.ac.uk/mmv/datasets/deap/doc/eula.pdf)

学术研究用途与访问协议；下载入口当前可达性需复核。

[13] [Aljanaki, Yang and Soleymani (2017). Developing a benchmark for emotional analysis of music. PLOS ONE 12(3), e0173392.](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0173392)

DEAM 的音乐情绪与其他标注；用于后续音乐候选库选型。

[14] [AMIGOS. Official dataset and access instructions.](https://eecs.qmul.ac.uk/mmv/datasets/amigos/)

协议访问的外部情绪生理数据备选。

[15] [Dudik, Langford and Li (2011). Doubly Robust Policy Evaluation and Learning. ICML.](https://icml.cc/2011/papers/554_icmlpaper.pdf)

离线策略评价依赖相应动作与行为策略日志；不据此弥补本任务缺失的动作支持。

[16] [Oberst and Sontag (2019). Counterfactual Off-Policy Evaluation with Gumbel-Max Structural Causal Models. ICML.](https://proceedings.mlr.press/v97/oberst19a.html)

反事实模拟依赖结构假设；本文因此独立报告仿真假设与真实数据证据。
