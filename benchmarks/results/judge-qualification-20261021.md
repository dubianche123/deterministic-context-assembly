# MiMo / HY4 裁判资格实验与交接（2026-10-21）

**真实测试已完成；两者均未通过本轮“可独立自动排名”的预声明门槛。** MiMo 在增加 JSON 约束后 14/14 格式通过、三类控制全部通过，但两个自然 pair 的胜者随位置改变，且至少一份裁决的 winner 与分数、理由矛盾；适合辅助列问题，尚不能单独决定排名。HY4 提高到 24000 / 360 后 11/12 有效、已完成的三对自然排名稳定，但仍有一次 360 秒超时，不适合自动备用链。

用户选择的是**增加上限，保持模型默认推理**。三轮合计 50 次 judge HTTP 尝试、无自动重试/换模型、未重新生成解读，所有失败保留。结论、原始输入输出和 Claude 开工项都在本文；项目入口为 [AGENT_HANDOFF.md](../../the-norn-machine/AGENT_HANDOFF.md) 及 [CLAUDE.md](../../CLAUDE.md)。这里的门槛是小样本工程筛选，不是任何模型的通用质量判决。

## 1. 冻结方法与基线结果

复用 [prose-eval-v2.json](prose-eval-v2.json) 的 v4/v5 解读，未重新生成。四个自然 pair 覆盖 sparse / dense / overflow，外加明显伪造及过度断言对照、完全相同文本对照；每组各两个方向，每个模型 12 次请求。自然文本 A/B 对调后按原文本标签比较。相同文本的两次输入完全一样，只是独立重复，不构成有效的换序压力测试。

冻结参数：`temperature=0`、`max_tokens=6000`、超时 180 秒、并发上限 6、单次尝试、reasoning effort 省略、无 fallback。门槛要求每模型 12/12 有效且正常结束，两类对照各 2/2，至少三个自然 pair winner 一致、无 hard reversal。详见 [protocol.json](judge-qualification-v1-20261021/protocol.json)。

| 模型 | 严格 JSON 且 stop | 伪造/过度断言对照 | 相同文本对照 | 完整自然 pair | 换序 winner 一致 | hard reversal | 门槛 |
|---|---:|---:|---:|---:|---:|---:|---|
| MiMo v2.6 Pro | 11/12 | 2/2 | 2/2 | 3/4 | 3/3 完整 pair | 0/3 | 未通过 |
| HY4 preview | 2/12 | 1/2 | 1/2 | 0/4 | 无法评价 | 无完整 pair 可评价 | 未通过 |

MiMo 的三个完整自然 pair 均在两方向偏好 v5；剩余 pair 一次输出的 `reason` 有未转义引号，严格 JSON 失败。**未人工修复、未补算成功。** MiMo 的 winner 稳定不代表分数校准稳定：自然 pair 单维分数随换序最大漂移 2 分，两个 pair 的 v4 `overclaim` 标记发生变化。

HY4 的十个失败输出均为 `finish_reason=length`；它们主要消耗为 reasoning tokens，正文为空或只有 JSON 前缀。两个有效结果分别来自两类对照的一个方向。这证明当前接口、预算与输出要求的组合未完成资格任务，不能证明 HY4 的判断质量差，也不能从截断的思考内容推断质量。

| 模型 | 请求数 | 单请求延迟中位 / 最大（秒） | prompt tokens | completion tokens | 其中 reasoning tokens |
|---|---:|---:|---:|---:|---:|
| MiMo v2.6 Pro | 12 | 2.4235 / 2.946 | 20,724 | 1,094 | 0 |
| HY4 preview | 12 | 113.2705 / 173.432 | 20,082 | 71,033 | 70,819 |
| 合计 | 24 | — | 40,806 | 72,127 | 70,819 |

24/24 返回 HTTP 200，无 retry/fallback；HTTP 成功不能替代输出有效性。计数与 usage 来自 Workbuddy 返回记录；proxy 内部尝试未独立测量。货币成本未知（`cost_usd=null`），开发、修复和本地验证成本也未测量。

来源：[results.json](judge-qualification-v1-20261021/results.json)、[原始 samples](judge-qualification-v1-20261021/results.json.samples.jsonl)、[resources.json](judge-qualification-v1-20261021/resources.json)、[独立评审及主 Agent 回应](judge-qualification-v1-20261021/review.md)。

## 2. 解释边界与恢复记录

- 自然 pair 没有人工 gold，四组只有三个独立重建 journal；fixture 4 被两个 generator 复用，生成器分布不平衡。场景及轮次核对不等于精确还原每轮历史计数，也不构成人群测量。
- `facts` 缺少原始视觉统计，也仅列推荐画面名称，未列其元素。例如 v5 为“黑板天象馆”补“粉笔轨道”，为“厨房协作 / 开放图书馆活动”补锅、灶台或交换书等细节。MiMo 偏好 v5 不能证明这些细节全部 grounded；v4 中真实来源的统计在本次 facts 缺失，也不能直接视作生成模型事实编造。
- 原伪造对照把明显不存在的画面、人格诊断和宿命断言放在同一个 bad 文本中，无法隔离细微 grounding、overclaim 与文风效应。模型一致也不等于正确；HY4 本轮没有有效自然结果可供模型间一致性比较。
- 基线汇总按有效性计数，未另验 `finish_reason == stop`；本批有效行恰好全是 stop，不改变结论。后续 MiMo runner 已显式同时检查这两项；HY4 变体使用的旧实现中，有效行也全部是 stop。

初次运行在保存逐请求检查点后遇到本地汇总元数据冲突：`RunState.put` 保留 `kind`，runner 却用于 case 类型。修复后改用 `case_kind`，从 24 条已完成检查点 `--resume` 重建报告，**重建调用模型/网络零次**。基线仍有原始 24 次 judge 尝试；`transport_this_invocation=0` 和 `wall_seconds=0.009` 只描述离线重建，不能解释为原实验零耗时或零成本。原总体 wall time 与 max-active 未保存；逐请求文本、延迟、usage 仍保留。见 [execution-notes.md](judge-qualification-v1-20261021/execution-notes.md)。

## 3. HY4：预算恢复了裁决输出，超时仍未过关

冻结方案：[protocol.json](judge-qualification-hy4-budget-v1-20261021/protocol.json)。保持原十二格、rubric、输入、单次尝试和无 fallback，仅把输出上限改为 24,000、超时改为 360 秒。先单独运行原先截断的 GLM fixture 0 正向一格；若不是有效且 stop，停止其余十一格；成功才继续。

首格通过后按计划执行全部十二格，无未执行格。**11/12 严格 JSON + stop，0 次 token 截断，1 次网络/等待响应超时（360.003 秒）**；失败请求的上游完成情况和 usage 未观测到，未重发。两类控制各 2/2；自然 pair 3/4 完整，三个完整 pair 均两序偏 v5、无 hard reversal；缺失的一对不能补算稳定。三对的最大分项漂移为 1，overclaim 标记未变。

单请求延迟中位 184.0125 秒，最大 360.003 秒；完整批次 wall time 644.241 秒，客户端峰值并发 6。第一条原先截断的案例用了 12,796 completion tokens、180.733 秒：原 6000 / 180 配置确实不足。提高 token ceiling 和超时同时发生，不能只归因其中一个参数。预算恢复不等于判断可靠性通过，预声明 gate 仍为 false。

11 条已观测 usage 合计 prompt 19,201、completion 107,595（reasoning 106,486）；超时一条未知，不能补零。响应模型标签为 `hy4-preview`。本轮没有新的单元素细微控制，因此不能把 MiMo 的控制成绩移植给 HY4。

来源：[结果](judge-qualification-hy4-budget-v1-20261021/results.json)、[原始 samples](judge-qualification-hy4-budget-v1-20261021/results.json.samples.jsonl)。结论是可手动取得第二意见，但目前不推荐自动 fallback；也不是依据 token 截断断言模型语义能力差。

## 4. MiMo：格式与控制通过，排名和字段一致性未过关

冻结方案：[protocol.json](judge-qualification-mimo-json-v1-20261021/protocol.json)。保留原十二格，追加明确 JSON 引号转义要求：引用优先使用中文「」。启动前按用户选择，把上限统一提高到 24000、超时提高到 360；rubric、原事实与文本未变。因为多个设置同时改变，不能把格式改善单独归因于引号指令。新增一个独立冻结的 matched pair，两段各 92 字且其余字符相同，仅把“自行车链条维修”的“手套”替换为 facts 中不存在的“围巾”，两者均保持试探语气。

本变体共十四格；新增对照两序都要求 `grounded(good) > grounded(bad)`、双方 `overclaim=false`，winner 作为次要诊断。它只检验一个手写元素替换，不是广泛细微幻觉检出率；gold 相对所给元素记录，不声称检查过原画。

**14/14 严格 JSON + stop，三类控制各 2/2，通过格式与这几项事实辨别检查。** “手套→围巾”两序都正确指出不存在的元素，grounded 好/坏分别为 5/2 与 5/3，双方 overclaim 均 false。它只证明这一组元素替换被识别。

自然 pair 仅 **2/4 换序 winner 一致，另 2/4 hard reversal**。两个 DeepSeek pair 均为 v5/v5；两个 GLM pair 都是正序 v4、逆序 v5，即都选显示位置 A。不能据此把对冲结果平均成真正的 tie，也不能把位置依赖当成内容排名。

更具体的字段一致性问题出现在 GLM fixture 0 正序：`better=A`，但 grounded/resonance/clarity 都是 A=3、B=4，overclaim A=true、B=false，理由也在赞 B、批 A。合法 JSON 并没有保证语义内部一致。GLM fixture 4 的 grounded 对 v5 随换序漂移 2 分，v4 的 overclaim 标记也改变。故预声明 gate 仍为 false，不人工改 winner 补算成功。

单请求延迟中位 2.3535 秒、最大 2.764 秒，14 格 wall time 6.418 秒，客户端峰值并发 6；prompt 22,612、completion 1,352，代理报告 reasoning=0。响应模型标签为 `mimo-v2.6-pro`。较高上限没有强制本批输出填满，但不能据此推断所有任务的开销或供应商价格。

来源：[结果](judge-qualification-mimo-json-v1-20261021/results.json)、[原始 samples](judge-qualification-mimo-json-v1-20261021/results.json.samples.jsonl)。可以保留为快速辅助评审；不能作为无人复核的唯一排名器。

## 5. Claude 接续操作

本轮只验证裁判并交接，尚未把实验变体自动写进通用 evaluator 的全部默认或判定规则。当前主脚本里的 MiMo→HY4 仍是之前按偏好选的链，**不是本轮验证通过的可靠链**；Claude 开工先处理以下问题，再跑大批新排名：

1. 给裁判输出加语义一致性检查：明显被另一侧全部分数支配、overclaim 也更差，却宣称胜出的 verdict 应标为矛盾并弃权，不能只验 JSON。不要自动翻转 `better` 字段或修坏 JSON 来制造成功。
2. A/B 两序分开保存并归一。hard reversal 必须记为 `order_disagreement`、从主胜率分母移除并报告覆盖率，不能把两次对立胜出平均成“真实 tie”。MiMo 可辅助找问题，排名冲突需复核；HY4 不再自动接管每个失败请求。
3. 按用户选择使用足够的输出预算，而非强行降低 HY4 推理。HY4 显式复核至少采用这次验证的 24000 / 360 配置，并有单次请求和总调用边界。JSON 引号约束有格式证据，但不是排名可靠性证明；不要把本轮实验成功局部推广为通用承诺。
4. 补齐裁判的事实输入：推荐画面对应的真实 motifs、原始统计的出处与允许引用范围要明确。缺失的上下文不能被裁判直接标成原生成模型“编造”。补完后先做零模型回归，保持一处权威实现。
5. 复用本次检查点与原始输出，不因 Claude 恢复会话而重跑这 50 次请求，不自动回到 Kimi。接续原来的项目改进时保留历史结果；本实验不决定线上 prompt 或产品效果。

三轮资源账：[judge-qualification-resources-20261021.json](judge-qualification-resources-20261021.json)。合计 50 次 HTTP 尝试、49 次 HTTP 200、1 次未观察到 HTTP 状态的超时、0 重试；49 条报告 usage 合计 263,693 tokens（reasoning 177,305），另一条未知。没有重新生成判词，货币成本及开发/评审成本未知。原始基线和后续失败都未被覆盖。

## 6. 独立复核

[基线独立评审](judge-qualification-v1-20261021/review.md) 和 [后续独立评审及主 Agent 回应](judge-qualification-followup-review-20261021.md) 分别依据各轮冻结工件形成结论。后续评审独立重算确认两项 gate 都为 false，认可 HY4 的预算恢复与完成率失败需分开、MiMo 的控制成绩不抵消自然排序硬翻转。主 Agent 补充了 winner/评分/理由矛盾的具体样本。独立模型复核不是人工gold或外部统计证明；其可选复测建议没有自动执行。
