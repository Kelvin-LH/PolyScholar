# 论文置信度评分量化打分文档

版本：1.0.0。名义满分：100。只评价论文陈述的文本内可信程度，不评价创新性、研究价值、录用可能性。

执行本文档的明示标准，禁止引入文档外标准。低置信度不等于论文无价值，表示引用其结论需更谨慎。以“陈述可信的概率”为评估目标，但本量表输出的是**未经概率校准的证据支持指数**：80 分不等于 80% 的陈述真实。没有已知真假的样本及校准检验，禁止将分数解释为概率。

**禁止联网、查数据库、访问代码/数据/作者主页、调用模型记忆中的领域排行榜。** 输入只含指定版本的完整论文及其作者附录、本文档和统一材料清单；论文里的链接作为文本记录检查，不访问。可检查论文内的图像、公式、表格和参考文献，但不能据此确认作者身份、机构、引文或制品在现实中真实存在。文字、图表完全一致也不证明数据未被编造。

## 1. 维度与权重表

| key | 名称 | 权重/满分 | 考察要点 | 无法判断时的固定处理 |
| --- | --- | ---: | --- | --- |
| evidence | 证据强度 | 40% / 40 | E1 对象/数据；E2 划分；E3 设置；E4 随机性/不确定性；E5 指标；E6 对照；E7 主张与证据；E8 复现操作描述 | 真正无法判定适用性或事实时，对应条目取 2.5/5；全维未知取 20/40，归“存疑”。明确未报告的必要细节按 RF01–08 扣分，不伪装成未知 |
| consistency | 内部一致性 | 25% / 25 | I1 样本计数；I2 结果数值；I3 派生运算；I4 图表/描述；I5 流程与陈述 | 没有可交叉核对的同对象表述时，对应条目取 2.5/5；全维未知取 12.5/25，“存疑”。有矛盾时按 RF09–11 处理 |
| traceability | 来源可溯源性 | 15% / 15 | T1 作者元数据；T2 论文内引用映射；T3 代码/数据/证明制品定位 | 无作者信息、无可检查引用、类型/制品适用性不明时，对应条目取 2.5/5；全维未知取 7.5/15，“存疑”。现实身份/链接真实性一律标“未外核”，不因此另扣分 |
| plausibility | 结果合理性 | 20% / 20 | P1 文内硬约束；P2 幅度与解释；P3 结论范围；P4 绝对化主张 | 文内未定义可适用的界限或没有可计算增幅时，P1/P2 各取 2.5/5，不用领域直觉补齐；全维未知取 10/20，“存疑” |

权重理由：证据与一致性合计 65%，使可信度主要由能否核对、是否自洽决定；来源占 15%，避免公开链接或机构名称主导评分；合理性占 20%，仅检查文内明确边界和主张范围，不惩罚陌生或颠覆性结果。名气、venue、是否同行评审、引用量、语言、文笔、篇幅、研究主题均不单独加减分。

### 1.1 固定材料、单位和未知规则

协调者冻结论文精确版本、主文及实际提供的作者附录、文件 SHA-256、本文档 SHA-256，并摘录主要结论清单与原文位置。主要结论取摘要、引言贡献列表、结论中的可检验研究主张，排除愿景、背景及他人工作的引述。不得向评委提供优劣判断。

证据模式固定为 `empirical`、`theoretical` 或 `mixed`。纯理论论文按第 3.2 节等价条件检查，不因未使用数据集、随机种子或没有手写证明代码失分；混合论文检查所有主要实验/理论主张，不用一部分的完整记录覆盖另一部分缺口。

**以下情况不能混淆：**

1. `assessable`：适用条件和事实能从全文判定。必要细节明确缺失也是可判定事实，按规定扣分。
2. `partially_assessable`：同一条目部分条件可判定、部分关键事实未知。基准2.5，仅扣已确认红旗，最多扣完2.5；不能用未知状态抹掉已确认缺口。
3. `unknown`：无可确认红旗，且文内不足以判定适用性、缺乏交叉核对机会，或合理性判断需要文外边界。取条目中性分2.5，不再追加未知扣分，也不授予“无问题所以满分”。若已有部分可核对事实但没有红旗，仍有关键未知，标partially_assessable，分数同为2.5。
4. 材料阻塞：主文缺页、必要表图无法读清、已声明提供但未取得的附录缺失。暂停并报告；不能生成正式评分。作者根本未提供制品/附录与读取失败不同。

身份、引用和链接的**现实真实性**始终为“未外核”；这是本流程边界，不是发现造假，也不强制将记录完整的 T1–T3 降到中性分。T 项只评分文本里的可追踪记录。不得写“作者/机构已验证”“代码可运行”“引文真实存在”。

## 2. 高考式分档锚点

### 2.1 唯一计分算法

固定 20 个条目：`E1–E8、I1–I5、T1–T3、P1–P4`，每条目上限 5。

- 完全可判定条目基准分`base=5`；部分可判定条目`base=2.5`并扣已确认红旗；未知条目`base=2.5、deduction=0、score=2.5`。
- 第 3 章每个扣分触发条件须引用证据。先去重，再执行条目内扣分：E/T/P 条目将不同条件的扣分相加，最多 5；I 条目只取该条目内最严重事件的扣分，最多 5。所有实际扣分不得超过条目基准分。
- `score=base−deduction`；维度分为条目分之和；`raw_total`为四维之和，最终`total=min(raw_total,ceiling)`，ceiling只按第2.2节计算。使用0.5分刻度，禁止印象调分、规定之外的封顶或重新分配权重。
- 同一个具体错误只扣一次红旗分。未知分不是红旗扣分，须单列为信息不足，不能称作造假迹象。

### 2.2 中心结论保护规则

资料齐全不能抵消中心主张的可核对矛盾。保留原始四维分，只限制总体可信度上限，不再对条目叠扣同一红旗：

| ceiling | 唯一触发条件 |
| ---: | --- |
| 19.5 | 满足第3.4节的疑似造假模式；低分表示系统性内部异常，不证明造假 |
| 39.5 | 未触发上一条，且至少一条RF15涉及主要结论：该结果违反论文明确硬界限，单位、对象、舍入已排除，需引用界限与结果两处 |
| 59.5 | 未触发以上两条，且至少一条I类红旗为L=5并涉及主要结论，即文本版本差异能改变比较顺序、阈值成立或适用范围 |
| 100 | 其余情况；缺代码、陌生作者、未知或幅度大本身均不触发上限 |

`integrity_guard`必须记录raw_total、ceiling、触发红旗ID及依据。四维分解释原始证据指数，最终total用于判档；**上限生效时四维和等于raw_total，不等于total**。不得隐去这一差别，不能给不存在的条目补扣分凑总数。

### 2.3 总分五档

| verdict | 分数区间 | 可观察锚点 |
| --- | ---: | --- |
| 高度可信 | 80–100 | 原始损失不超过20且未触发中心结论上限；典型为主要设置和口径可核对、数字在容差内一致、记录可定位、声明未超出文内边界。仍只代表文本内支持 |
| 较可信 | 60–79.5 | 总损失 20.5–40；例如证据强度 28、一致性 20、来源 10、合理性 15，合计 73，有明确的细节缺口/未知项但主要主张有可定位证据 |
| 存疑 | 40–59.5 | 总损失 40.5–60；全部条目未知的中性锚点为 50。多个主要设置未报告或多数交叉检查机会不足，不得写成“已发现造假” |
| 明显存疑 | 20–39.5 | 最终损失60.5–80；多个必要条件缺失，或中心结果违反文内硬界限而触发39.5上限 |
| 高度存疑 | 0–19.5 | 最终损失至少80.5；证据链大面积缺失/失败，或独立异常模式触发19.5上限。分数仍不确定行为意图 |

这些描述是典型证据画像，正式判档只按计算区间；不能因为觉得“像造假”跳档，也不能因为文笔流畅进档。

### 2.4 四维各五档

| 维度 | 高度可信 | 较可信 | 存疑 | 明显存疑 | 高度存疑 |
| --- | --- | --- | --- | --- | --- |
| 证据强度 /40 | **32–40**：必要设置/口径损失≤8；典型为来源、划分、配置、统计、主张映射可查，仅有限缺口 | **24–31.5**：损失8.5–16；有具体实验/证明，但配置、对照或不确定性有规定缺口 | **16–23.5**：损失16.5–24；多个必要细节缺失；全维未知为20 | **8–15.5**：损失24.5–32；大部分协议/定义条件未满足 | **0–7.5**：损失≥32.5；主要验证只剩声明或严重缺少必要定义 |
| 内部一致性 /25 | **20–25**：未知损失与矛盾扣分合计≤5；主要重复量与表图可交叉核对且在容差内 | **15–19.5**：损失5.5–10；部分检查未知或有已定位局部矛盾 | **10–14.5**：损失10.5–15；较多检查无机会或出现中心矛盾；全维未知12.5 | **5–9.5**：损失15.5–20；多个类别触发中心矛盾/不足 | **0–4.5**：损失≥20.5；大部分交叉检查类别失败 |
| 来源可溯源性 /15 | **12–15**：损失≤3；作者元数据、引文映射和制品记录可定位，不表示现实已认证 | **9–11.5**：损失3.5–6；记录存在但缺联系方式、引用标识或部分制品定位 | **6–8.5**：损失6.5–9；多个记录不充分；全维未知7.5 | **3–5.5**：损失9.5–12；引文/制品多项定位失败或出现明确映射矛盾 | **0–2.5**：损失≥12.5；来源记录大部分缺失或内部冲突 |
| 结果合理性 /20 | **16–20**：损失≤4；文内边界可检查、主要幅度有支持解释、结论未越界 | **12–15.5**：损失4.5–8；部分边界未知，或有一类未解释跃升/主张扩张 | **8–11.5**：损失8.5–12；关键边界不能文本内判断或多项主张无支持；全维未知10 | **4–7.5**：损失12.5–16；多个类别有文内界限/范围问题 | **0–3.5**：损失≥16.5；合理性多数类别失败，须列明具体违反的定义 |

**相邻档规则**：同时满足“扣分有触发条文和原文位置、未知规则统一、去重/加总/上限正确、最终分达到上一档下限”，才进入上一档；差0.5分不进档。维度按原始维度分判档，总档次按上限处理后的total判定。公开链接且数字一致不能单独决定总档次。

## 3. 红旗清单（造假与不可信迹象的扣分细则）

### 3.1 核对方法与严重程度

对数字先统一指标、对象、划分、预测时域、单位和统计方式，再比较。不同设置的数值不同不算矛盾。若数值保留 d 位小数，允许舍入区间 `x±0.5×10^(−d)`；两表述区间相交视为一致。百分数与小数先换算；相对提升和百分点不得混用。图中只有近似读数时，记录坐标刻度与读数区间，区间重叠不扣；无法读清则暂停该材料的核对，不编造精确数。

样本数相加只适用于文内明确互斥且穷尽的集合；类别可能重叠、分母不明时不凭加和猜测矛盾。p 值真实性、原始数据分布、图像是否由别处复制，没有文本内核验材料就未知，不靠“看起来太整齐”扣分。

**一致性事件的固定扣分 L：**

- `L=1`：非主要结果的局部错误，且不改变样本/指标口径、主要比较顺序或结论成立性。
- `L=3`：涉及主要结论的数字/流程矛盾，但两种文本版本均不改变主要比较顺序、阈值通过情况或适用范围。
- `L=5`：改变主要比较顺序、阈值通过情况、适用范围；或违反文内明确硬约束；或存在互斥的关键实验流程。无法判断结论影响时按 `L=3`，不能猜测取5。

红旗 `severity` 按名义扣分固定映射：0=`info`；1=`minor`；1.5–2=`moderate`；2.5–3=`major`；3.5–5=`critical`。严重程度不等于造假概率。

### 3.2 完整条目和红旗扣分表

以下每个编号后a/b/c是独立触发条件。缺失项说明全文检查范围；矛盾项引证双方。完全可判定基准5，部分可判定基准2.5，按表扣分至基准分下限0。红旗另记录是否涉及主要结论，供第2.2节校验。

| 条目 / 红旗 | 文本识别方法与固定扣分 | 理论等价/未知边界 |
| --- | --- | --- |
| E1 / RF01 对象/数据不明 | a 未说明评测数据来源/名称扣2；b 未给测试样本数扣2；c 未定义采样/纳入对象扣1 | 理论对应：命题作用域、参数范围、适用实例，分别2/2/1；不是要求跑数据集 |
| E2 / RF02 划分与隔离不明 | a 未说明训练/验证/测试划分或评测对象选择扣3；b 未说明测试对象与训练/调参的隔离程序扣2 | 理论对应：a 假设与待证结论不区分；b 引理依赖/论证起点不明；不能把待证结论作为假设 |
| E3 / RF03 关键设置缺失 | a 核心方法/关键超参数没有明确值、范围或完整算法定义扣2；b 训练/推理预算与资源条件未报告扣2；c 主要结果对应的配置版本不可定位扣1 | 理论对应：核心构造定义、计算/适用约束、结果对应的假设版本；资源不涉及主张时 b 由“适用约束是否给出”判定 |
| E4 / RF04 随机性与统计缺失 | a 随机方法不报告种子或种子生成规则扣1；b 未报告运行/重复次数扣1；c 主要统计结果没有离散程度或区间扣2；d 未定义统计/采样单位扣1 | 确定性方法明确声明时 a 不扣；固定总体的确定性精确计数，明确无随机/抽样不确定性时 c 不扣。理论对应：分情况规则(1)、近似/数值计算次数或步骤(1)、误差界(2)、作用单位/参数(1)；明确精确演绎且不涉及近似的前三项不扣 |
| E5 / RF05 指标口径缺失 | a 主要指标未给公式或无歧义定义扣2；b 方向/单位未定义扣1；c 聚合方式、分母或权重未明确扣2 | 理论对应：待证量/性质定义、单位/方向、量词/参数范围，分别2/1/2 |
| E6 / RF06 对照不可比 | a 声称优于前作但无具体对照结果扣2；b 输入/预算/协议差异影响比较且无匹配对照扣2；c 对照版本/配置未说明扣1 | 未声称比较优越时 a/c 不扣；理论比较命题、共同假设和前作版本。单纯没有 SOTA 名称不扣 |
| E7 / RF07 中心主张缺证据 | a 主要经验/理论结论没有对应结果表、图或命题/证明扣3；b 不能将这些证据逐项对应到所有主要结论扣2 | 负面结果也可完整支持主张。只因效果很大或结果不显著不扣 |
| E8 / RF08 操作细节不足 | a 未提供评测脚本或足够执行的伪代码/步骤扣3；b 缺少执行步骤、依赖或输入构造所需信息扣2 | 理论对应可重建的推导步骤(3)、初始/边界条件(2)。纸面步骤完整可得满分，不强制公开代码 |
| I1 / RF09-count 计数矛盾 | 同一对象样本数在不同位置不符，或互斥穷尽计数加和不符，扣 L | 没有可比较的计数/参数范围记录时取2.5；不同筛选范围不得直接相加 |
| I2 / RF09-value 结果数值矛盾 | 同指标同设置的正文、表格、图中数值舍入区间不相交，扣 L | 没有重复结果数值可核对时取2.5；不能把不同指标/时域当同一数值 |
| I3 / RF09-calc 派生计算错误 | 依论文原数值重算均值、百分点、相对增幅、比例或明确代数推导，发现不符，扣 L | 不能仅凭摘要 p 值推算真实性；没有可独立重算的运算时取2.5 |
| I4 / RF10 图文不符 | 图例、轴、对象、caption或正文描述与图表内容明确冲突，扣 L | 理论可核对公式/算法编号与文字所指对象；没有对应结构时取2.5。排版/压缩导致无法读清属材料阻塞 |
| I5 / RF11 流程/陈述矛盾 | 同一设置被描述为互斥流程，或方法假设与主结论逻辑冲突，扣 L | 例如同一实验同时声称测试数据未见过及用于选型；需两处文本同指该实验。没有足够流程或重复陈述时取2.5 |
| T1 / RF12 作者记录不足/冲突 | a 作者到机构对应不明扣1.5；b 没有通讯渠道或作者标识扣1；c 同一作者/机构映射在文内明确自相矛盾扣5 | 完全无作者元数据取2.5，记录为unknown，不再叠扣a/b。现实身份无法验证额外扣0；名字陌生、机构未听过、个人无机构都不能推断造假。明确独立作者身份不要求机构 |
| T2 / RF13 引用映射异常 | a 支撑主要结论的文内引文找不到唯一参考条目扣2；b 对应记录不足以唯一定位扣1（完整作者/标题/年份或唯一标识均可）；c 同一引用标识被赋予互斥记录扣3；d 论文内提供的引文内容与其所支持主张明确冲突扣3 | 无需引文或全文无引文及参考条目时取2.5；有引文却缺对应记录不能标未知。现实存在无法外核扣0；仅凭标题陌生/跨领域不能判“凑数”。无文内引文内容支持不相关判断时不扣d |
| T3 / RF14 制品定位缺失 | a 无代码/制品的具体定位链接或标识扣3；b 无数据来源的具体标识、版本或获取说明扣2 | 手写理论证明改查证明/附录位置(3)、论文/附录版本(2)，不因无代码失分。链接不访问，不能保证可用；制品适用性不明取2.5 |
| P1 / RF15 文内硬界限失败 | 结果违反论文自己定义的范围、非负性、数量约束或已给出的定理上界，扣5 | 先排除单位/舍入/对象差异。文内没有适用边界时取2.5；不得从外部“常识”发明性能上限 |
| P2 / RF16 异常增幅无支持 | 达到下述筛查阈值，且没有对应机制说明、匹配控制或独立重复/区间证据中的任何一种，扣3 | 只有“大幅提升”没有可计算数值时取2.5；幅度大但有上述任一种具体支持，单因增幅扣0。筛查阈值不是造假判据 |
| P3 / RF17 结论越界 | a 从已限定对象/环境/预算推及未覆盖范围且无对应验证，扣3；b 把相关观察写成已确认因果且无区分替代解释的控制，扣3 | 主张限定在实际对象范围内不扣。与RF07同一“没有证据”事件只扣一次 |
| P4 / RF18 无支持的绝对化 | a 对中心结果声称“全部/完美/永不/所有场景”等，未限定可核对范围且无覆盖证明/结果，扣3；b 把单次经验结果写成无条件精确保证扣2 | 排除引述、愿景和形式定理中的有界量词。有限测试集零错误仅支持该测试集零错误。与RF11/RF17同一事件不重复扣 |

同一条目的同一子条件至多扣一次，跨多个实验/理论主张也不叠扣；不同子条件可相加。RF01–08、RF12–14、RF17–18的rule_id必须带子条件，如RF01.a；RF09使用RF09-count/value/calc，其余用RF10、RF11、RF15、RF16。每个触发标明对应主张，不用完整实验掩盖另一个主要实验缺口。

### 3.3 增幅筛查和去重

只用文内可比较的基线 b 和结果 r。高者优指标增幅为 `(r−b)/|b|`；低者优指标误差降低为 `(b−r)/|b|`。正分母、同单位、同对象、可比设置下，增幅/误差降低 **≥50%**，或文内声称的速度提升 **≥2倍**，启动 RF16 检查。b=0、分母/方向不明或对象不可比时不计算该比率；在 E5/E6 检查对应缺口。

这些阈值是统一审查触发器，不是领域正常值，更不是“不可能”的界线。有增幅但没有解释/控制/不确定性支持，只触发 RF16 的3分；不得改成凭直觉扣10分或认定造假。

“前所未有”作为程度词本身不扣；若落实为无条件胜过所有方案而无对应覆盖证据，按RF18扣3。“首次提出”需要外部查新，不能仅因本流程无法查新扣分。

**同一事件去重规则：**

1. 同一对象同一设置的冲突数值，在多个位置重复出现，组成一件事件，不按位置数重复扣。
2. 若修正同一处单个事实/数字/标签即可消除跨条目问题，列为同一duplicate_group，只保留名义扣分最大者；并列按E1…E8、I1…I5、T1…T3、P1…P4，再按flag_id字典序。其余实际扣0。补写整段多个独立信息不算单个修正。
3. 没有文本依据证明同一原因时，不猜测因果去重；分别描述不同事件。I条目仍只取最严重事件，不能靠多次罗列局部错误耗尽分数。
4. 同一条目同一rule_id的重复触发只保留名义扣分最高的一条，并列按flag_id选；其他保留但实际扣0。随后E/T/P的不同子条件相加，超过base时按名义扣分降序、flag_id排序分配至base。I只给所选最高事件分配至base。applied_deduction总和须等于条目的deduction。
5. 未知项不产生红旗扣分；“本文未进行外核”仅为流程说明，不得自动生成造假红旗。未知损失 `5−base` 与红旗实际扣分分开记录。

### 3.4 无意失误与疑似造假的区分

每条红旗记录一种解释标签；这些标签不另加减分：

- **资料缺失**：协议、作者元数据、引用或制品定位不足；只能说明证据链缺口，不支持动机推断。
- **无支持的主张扩张**：增幅无支持、超范围或绝对化；可能是过度解读，不能单独判造假。
- **可能无意失误**：已确认的局部矛盾属于 L=1，且有一处明确修正即可消除，不改变主要结论。表述为“可能”，不能断言原因。
- **需核验的内部异常**：其他已确认矛盾，包括单个中心数字/流程错误；既不能洗成笔误，也不能直接定为造假。
- **疑似造假（未证实）**：仅当至少 **3个独立矛盾事件**、跨至少 **2个I类核对条目**，其中至少 **2件为L=5且涉及主要结论**，并且不存在一处明确修正可消除整个模式时，才允许使用。必须同时列出构成模式的事件与双方原文；说明系统性错误、版本混杂仍是可能解释，需要后续独立外核。

上述模式不计缺失资料、未知、夸张词和增幅本身。无论得分多低，都禁止写“已证明造假”“作者不真实”“引用是伪造的”。可在论文文本中识别不可信迹象，不能由此确认行为意图。

## 4. 三子代理盲评流程（参照高考阅卷）

### 4.1 首轮

1. 冻结材料、版本、哈希、主要结论清单及证据模式；建立A/B/C三个独立新会话/子代理。三者读取同一全文和本文档。不能传入含同伴评分或协调者判断的父会话历史。
2. 隔离结果文件读取与通信，三者互不可见。若不支持子代理，使用三个上下文隔离的新会话；不得同一会话连演三位评委。
3. 三者使用同一指令：

> 只读取指定论文全文、作者附录、共同清单和confidence-scoring.md，禁止联网或用模型记忆核实身份、引文、领域边界。核对20条目，列明未知状态、红旗、名义与实际扣分。每维提供原文章节/表图/原句证据，矛盾引用双方。按第6章agent_report输出完整JSON；读取失败先报告，不给正式分。不要读取其他评委结果。

4. 原句逐字摘录；表格注明行列；缺失项注明已检查章节/附录范围。缺失/未知不得伪造原句，`quote=null`。每个条目至少有一个证据记录，已确认矛盾必须覆盖双方。
5. 协调者先校验结构、引用定位、去重及加总；格式/算术失败仅退回该代理自身错误，不提供同伴判断。三份有效结果齐全后汇总。

### 4.2 中位数与维度代表值

最终总分取三个有效total的**中位数**；极差=max−min。最终维度、红旗、integrity_guard和verdict沿用中位数代理；并列固定A→B→C。维度和等于该代理raw_total，再经同一上限得到total；不拼接各维度自己的中位数，不改用均值或协调者主观分。

按A/B/C保留三份完整报告。`original_output`逐字保存首轮JSON，复核时另存`recheck_output`，禁止覆盖首轮。展示最终报告必须同时给出三份首轮评分原文；复核发生时再给三份复核原文，不能只给最终总分或摘要。代表评分不等于所有红旗已获共识。

### 4.3 极差>15时复核及分歧表

首轮极差**>15**必须触发一次独立复核，等于15不自动触发。生成分歧表，列出所有首轮条目分不同的条目；若ceiling不同，另列integrity_guard行，A/B/C值为各自上限。给出条文、证据位置和不同点。此表用于最终交付，不向评委泄露他人分数。

向三者发送相同复核指令：

> 请复核条目{编号}及材料位置{位置}，重新检查适用性、舍入/单位、红旗触发、同事件去重和加总。不得猜测同伴分数，不以缩小差异为目标，仅据confidence-scoring.md纠正或维持判断。重新输出完整agent_report，并说明维持/变更依据。

三份有效复核齐全后重新取中位数，设置`rechecked=true`；`spread`用复核后分，`initial_spread`保留首轮极差。复核后仍>15，设置`unresolved_disagreement=true`并保留分歧，不强迫共识。复核未完成则流程未完成，不报正式最终分。新增材料/改版本须重开三个盲评首轮。

分歧点对照表呈现格式：

| 条目 | A首轮分 | B首轮分 | C首轮分 | 条文编号 | A/B/C原文定位 | 分歧说明 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| 实际条目编号 | 实际分 | 实际分 | 实际分 | RF及子条件 | 三者引用位置 | 如未知与明确缺失混淆/舍入区间/事件去重不同 |

## 5. 跨模型一致性纪律

1. 所有计分回到本文条文及论文证据。禁止凭名气、直觉、语气、排版、主题、venue或模型熟悉度加减分；不联网补全领域上下限。
2. 外部身份/引文/链接未核验与文本记录缺失严格分开；统一记录为“未外核”，不要把所有预印本或所有作者都扣一遍“真实性未知”分。
3. 未知按第1–2章固定2.5/5；不能换成0或5。明确必要内容未报告须按指定缺失条文处理，不用中性分掩盖协议缺口。已读材料无法支持动机判断时，不贴疑似造假标签。
4. 不把异常幅度当不可能，不把无代码当无真实性，不把标题看似不相关当引文造假。不能核验的事实不能说已证伪或已证实。
5. 支持时建议`temperature=0、top_p=1`及固定seed；不支持时用最低随机性/兼容默认，记录实际配置，未知配置填null。固定相同输入与足够上下文；零温度不能替代逐项检查。
6. 输出前逐项执行以下自检表，不把自检合格当作论文可信的额外得分：

| 自检项 | 必须满足的条件 |
| --- | --- |
| 材料 | 版本/哈希一致，全文与可读表图/附录齐全，无联网材料 |
| 适用性 | 模式固定，理论等价条件正确，20条目齐全 |
| 未知 | 每个unknown=2.5且无红旗扣分；明确缺失未被误标unknown |
| 数字 | 对象、设置、单位、舍入区间和分母核对，无误算相对增幅 |
| 红旗 | 每条有规定触发、名义扣分、证据；矛盾有双方；无意/疑似标签满足限定规则 |
| 去重 | 同事件仅一次，I取最高事件，其余条目封顶及实际分配正确 |
| 分数 | 条目base−扣分=score，四维和=raw_total，total=min(raw_total,ceiling)，档次按最终分 |
| 汇总 | 中位数、>15复核、平分顺序、原文保留及分歧表符合规则 |
| 输出 | Schema合法，且第6.2节跨字段语义校验通过 |

## 6. 机器可读输出格式（JSON Schema）

### 6.1 Schema

使用JSON Schema Draft 2020-12。根校验汇总报告；单代理使用`$defs.agent_report`。单代理只输出JSON对象。维度顺序固定evidence、consistency、traceability、plausibility；条目顺序固定E、I、T、P各组内编号递增。

`rationale`写适用性、通过检查、缺口/条文和原文位置；base与deduction区分未知损失和红旗扣分。integrity_guard公开上限处理。red_flags保留实际扣0的已去重事件，不将纯未知或未外核列为造假红旗。所有分数为0.5的整数倍。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PaperConfidenceReport-v1.0.0",
  "type": "object", "additionalProperties": false,
  "required": ["rubric_version", "manifest", "total", "dimensions", "red_flags", "verdict", "integrity_guard", "sub_scores", "aggregation", "disagreement_table"],
  "properties": {
    "rubric_version": {"const": "1.0.0"},
    "manifest": {"$ref": "#/$defs/manifest"},
    "total": {"$ref": "#/$defs/total"},
    "dimensions": {"$ref": "#/$defs/dimensions"},
    "red_flags": {"type": "array", "items": {"$ref": "#/$defs/red_flag"}},
    "verdict": {"$ref": "#/$defs/verdict"},
    "integrity_guard": {"$ref": "#/$defs/integrity_guard"},
    "sub_scores": {
      "type": "array", "minItems": 3, "maxItems": 3,
      "prefixItems": [
        {"allOf": [{"$ref": "#/$defs/sub_score"}, {"properties": {"agent_id": {"const": "A"}}}]},
        {"allOf": [{"$ref": "#/$defs/sub_score"}, {"properties": {"agent_id": {"const": "B"}}}]},
        {"allOf": [{"$ref": "#/$defs/sub_score"}, {"properties": {"agent_id": {"const": "C"}}}]}
      ], "items": false
    },
    "aggregation": {
      "type": "object", "additionalProperties": false,
      "required": ["method", "spread", "initial_spread", "rechecked", "rounds", "selected_agent_id", "unresolved_disagreement", "recheck_reason"],
      "properties": {
        "method": {"const": "median"},
        "spread": {"$ref": "#/$defs/total"},
        "initial_spread": {"$ref": "#/$defs/total"},
        "rechecked": {"type": "boolean"},
        "rounds": {"type": "integer", "minimum": 0, "maximum": 1},
        "selected_agent_id": {"enum": ["A", "B", "C"]},
        "unresolved_disagreement": {"type": "boolean"},
        "recheck_reason": {"type": ["string", "null"]}
      }
    },
    "disagreement_table": {
      "type": "array", "items": {
        "type": "object", "additionalProperties": false,
        "required": ["item_id", "score_A", "score_B", "score_C", "rule_ids", "evidence_A", "evidence_B", "evidence_C", "reason"],
        "properties": {
          "item_id": {"anyOf": [{"$ref": "#/$defs/item_id"}, {"const": "integrity_guard"}]},
          "score_A": {"$ref": "#/$defs/total"},
          "score_B": {"$ref": "#/$defs/total"},
          "score_C": {"$ref": "#/$defs/total"},
          "rule_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
          "evidence_A": {"$ref": "#/$defs/evidences"},
          "evidence_B": {"$ref": "#/$defs/evidences"},
          "evidence_C": {"$ref": "#/$defs/evidences"},
          "reason": {"type": "string", "minLength": 1}
        }
      }
    }
  },
  "$defs": {
    "total": {"type": "number", "minimum": 0, "maximum": 100, "multipleOf": 0.5},
    "item_score": {"type": "number", "minimum": 0, "maximum": 5, "multipleOf": 0.5},
    "item_id": {"type": "string", "pattern": "^(E[1-8]|I[1-5]|T[1-3]|P[1-4])$"},
    "verdict": {"enum": ["高度可信", "较可信", "存疑", "明显存疑", "高度存疑"]},
    "integrity_guard": {
      "type": "object", "additionalProperties": false,
      "required": ["raw_total", "ceiling", "trigger_flag_ids", "rationale"],
      "properties": {
        "raw_total": {"$ref": "#/$defs/total"},
        "ceiling": {"enum": [100, 59.5, 39.5, 19.5]},
        "trigger_flag_ids": {"type": "array", "uniqueItems": true, "items": {"type": "string", "minLength": 1}},
        "rationale": {"type": "string", "minLength": 1}
      }
    },
    "manifest": {
      "type": "object", "additionalProperties": false,
      "required": ["paper_ref", "rubric_sha256", "evidence_mode", "inputs", "main_claims", "external_verification"],
      "properties": {
        "paper_ref": {"type": "string", "minLength": 1},
        "rubric_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "evidence_mode": {"enum": ["empirical", "theoretical", "mixed"]},
        "external_verification": {"const": "not_performed"},
        "inputs": {"type": "array", "minItems": 1, "items": {
          "type": "object", "additionalProperties": false,
          "required": ["source_id", "label", "sha256"],
          "properties": {
            "source_id": {"type": "string", "minLength": 1},
            "label": {"type": "string", "minLength": 1},
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}
          }
        }},
        "main_claims": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
      }
    },
    "evidence": {
      "type": "object", "additionalProperties": false,
      "required": ["source_id", "locator", "quote", "status"],
      "properties": {
        "source_id": {"type": "string", "minLength": 1},
        "locator": {"type": "string", "minLength": 1},
        "quote": {"type": ["string", "null"]},
        "status": {"enum": ["observed", "not_reported", "unknown"]}
      },
      "allOf": [{
        "if": {"properties": {"status": {"const": "observed"}}},
        "then": {"properties": {"quote": {"type": "string", "minLength": 1}}},
        "else": {"properties": {"quote": {"type": "null"}}}
      }]
    },
    "evidences": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/evidence"}},
    "audit_item": {
      "type": "object", "additionalProperties": false,
      "required": ["id", "status", "base", "deduction", "score", "rationale", "evidence"],
      "properties": {
        "id": {"$ref": "#/$defs/item_id"},
        "status": {"enum": ["assessable", "partially_assessable", "unknown"]},
        "base": {"enum": [2.5, 5]},
        "deduction": {"$ref": "#/$defs/item_score"},
        "score": {"$ref": "#/$defs/item_score"},
        "rationale": {"type": "string", "minLength": 1},
        "evidence": {"$ref": "#/$defs/evidences"}
      },
      "allOf": [{
        "if": {"properties": {"status": {"const": "unknown"}}},
        "then": {"properties": {"base": {"const": 2.5}, "deduction": {"const": 0}, "score": {"const": 2.5}}},
        "else": {
          "if": {"properties": {"status": {"const": "partially_assessable"}}},
          "then": {"properties": {"base": {"const": 2.5}, "deduction": {"maximum": 2.5}, "score": {"maximum": 2.5}}},
          "else": {"properties": {"base": {"const": 5}}}
        }
      }]
    },
    "dimension": {
      "type": "object", "additionalProperties": false,
      "required": ["key", "name", "max", "score", "rationale", "audit_items"],
      "properties": {
        "key": {"type": "string"}, "name": {"type": "string"}, "max": {"type": "integer"},
        "score": {"$ref": "#/$defs/total"},
        "rationale": {"type": "string", "minLength": 1},
        "audit_items": {"type": "array", "items": {"$ref": "#/$defs/audit_item"}}
      }
    },
    "dimensions": {
      "type": "array", "minItems": 4, "maxItems": 4,
      "prefixItems": [
        {"allOf": [{"$ref": "#/$defs/dimension"}, {"properties": {"key": {"const": "evidence"}, "name": {"const": "证据强度"}, "max": {"const": 40}, "score": {"maximum": 40}, "audit_items": {"minItems": 8, "maxItems": 8}}}]},
        {"allOf": [{"$ref": "#/$defs/dimension"}, {"properties": {"key": {"const": "consistency"}, "name": {"const": "内部一致性"}, "max": {"const": 25}, "score": {"maximum": 25}, "audit_items": {"minItems": 5, "maxItems": 5}}}]},
        {"allOf": [{"$ref": "#/$defs/dimension"}, {"properties": {"key": {"const": "traceability"}, "name": {"const": "来源可溯源性"}, "max": {"const": 15}, "score": {"maximum": 15}, "audit_items": {"minItems": 3, "maxItems": 3}}}]},
        {"allOf": [{"$ref": "#/$defs/dimension"}, {"properties": {"key": {"const": "plausibility"}, "name": {"const": "结果合理性"}, "max": {"const": 20}, "score": {"maximum": 20}, "audit_items": {"minItems": 4, "maxItems": 4}}}]}
      ], "items": false
    },
    "red_flag": {
      "type": "object", "additionalProperties": false,
      "required": ["flag_id", "rule_id", "flag", "evidence", "severity", "affected_item", "claimed_deduction", "applied_deduction", "duplicate_group", "affects_main_claim", "interpretation"],
      "properties": {
        "flag_id": {"type": "string", "minLength": 1},
        "rule_id": {"type": "string", "pattern": "^RF(0[1-9]|1[0-8])([.-][a-z]+)?$"},
        "flag": {"type": "string", "minLength": 1},
        "evidence": {"$ref": "#/$defs/evidences"},
        "severity": {"enum": ["info", "minor", "moderate", "major", "critical"]},
        "affected_item": {"$ref": "#/$defs/item_id"},
        "claimed_deduction": {"$ref": "#/$defs/item_score"},
        "applied_deduction": {"$ref": "#/$defs/item_score"},
        "duplicate_group": {"type": "string", "minLength": 1},
        "affects_main_claim": {"type": "boolean"},
        "interpretation": {"enum": ["资料缺失", "无支持的主张扩张", "可能无意失误", "需核验的内部异常", "疑似造假（未证实）"]}
      }
    },
    "report_fields": {
      "type": "object",
      "required": ["agent_id", "model", "settings", "total", "dimensions", "red_flags", "verdict", "integrity_guard"],
      "properties": {
        "agent_id": {"enum": ["A", "B", "C"]},
        "model": {"type": "string", "minLength": 1},
        "settings": {
          "type": "object", "additionalProperties": false,
          "required": ["temperature", "top_p", "seed"],
          "properties": {
            "temperature": {"type": ["number", "null"], "minimum": 0},
            "top_p": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "seed": {"type": ["integer", "null"]}
          }
        },
        "total": {"$ref": "#/$defs/total"},
        "dimensions": {"$ref": "#/$defs/dimensions"},
        "red_flags": {"type": "array", "items": {"$ref": "#/$defs/red_flag"}},
        "verdict": {"$ref": "#/$defs/verdict"},
        "integrity_guard": {"$ref": "#/$defs/integrity_guard"}
      }
    },
    "agent_report": {"allOf": [{"$ref": "#/$defs/report_fields"}], "unevaluatedProperties": false},
    "sub_score": {
      "allOf": [
        {"$ref": "#/$defs/report_fields"},
        {"type": "object", "required": ["original_output", "recheck_output"], "properties": {
          "original_output": {"type": "string", "minLength": 1},
          "recheck_output": {"type": ["string", "null"], "minLength": 1}
        }}
      ], "unevaluatedProperties": false
    }
  }
}
```

`paper_ref`必须含精确版本；`inputs`只登记实际读取的主文/作者附录，不能列联网前作。`quote`为短原句或表格数值，不用概述冒充原句。外部未核验统一由`manifest.external_verification=not_performed`表示。

### 6.2 程序必须另做的语义校验

标准Schema不执行跨字段运算、原文真实性或字符串内JSON验证。导入程序另外执行：

1. 条目ID按四维恰为E1–E8、I1–I5、T1–T3、P1–P4，无遗漏/重复/跨维；每条score=base−deduction，每维等于条目和，四维和=integrity_guard.raw_total，total=min(raw_total,ceiling)。
2. 所有证据source_id存在于manifest.inputs，定位与摘录可在论文核对；中心结论清单相同，条件、未知原因及理论等价解释符合本文。
3. flag_id唯一，rule_id与affected_item匹配；名义扣分等于触发条件，severity符合映射；claimed/applied非负且applied≤claimed。按去重、同子条件取一次、I取最大、其余封顶重算，实际扣分之和等于对应deduction；unknown无实际扣分。疑似造假标签符合第3.4节。
4. 检查affects_main_claim有原文依据；按第2.2节及3.4节重算ceiling，触发ID必须存在且满足对应规则，不得遗漏或主观增加上限。verdict按最终total计算：≥80高度可信、≥60较可信、≥40存疑、≥20明显存疑，其余高度存疑。
5. original_output和非空recheck_output能解析并通过agent_report与本节校验，代理ID一致；原文逐字保留。sub_scores当前字段等于无复核时首轮报告、有复核时复核报告。
6. initial_spread由首轮total计算；spread由当前有效total计算；总分取中位数，代表代理按A→B→C；根维度、红旗、verdict、integrity_guard与该代理一致。
7. initial_spread>15时rechecked=true、rounds=1、三份recheck_output非空；否则rechecked=false、rounds=0、三份recheck_output=null。unresolved_disagreement当且仅当复核后spread>15。
8. 触发复核时disagreement_table覆盖全部首轮条目分差异，条目行分数≤5；首轮上限不同还须有integrity_guard行，分数为对应ceiling。分数/证据取首轮原文；未触发为[]。复核要求一致，不向评委透露他人分数。
9. 任何校验失败退回修正，不静默补零、改成平均数、漏报原文或通过格式来掩盖评分分歧。

完成后交付：最终总分/四维分/verdict、完整汇总JSON、三份首轮原文、必要的三份复核原文与分歧点对照表。须注明“文本内置信度；未进行外部核验；分数不是已校准概率”。
