# 独立结果评审

评审范围：冻结的 `protocol.json`、`results.json`、`results.json.samples.jsonl`、`execution-notes.md`、`run.py`。本评审未调用模型、未生成解读，也未读取后续的 HY4 大预算或 MiMo 引用约束实验。

## 结论

- 两个 judge 都**未通过**预声明资格门槛。MiMo v2.6 Pro 是接近通过但仍失败：12 个请求中 11 个为严格 JSON；两类对照各 2/2 通过；四个自然 pair 只有三个完整，这三个的 winner 在换序后都一致为 v5，且无 hard reversal。第四个 pair 的换序输出因 `reason` 内未转义引号而不是合法 JSON，不能把其语义上可辨认的选择补算为成功。HY4 仅 2/12 输出有效严格 JSON，另外 10 个均以 `finish_reason=length` 结束；自然 pair 0/4 完整，无法评价其自然判断或顺序一致性。
- 当前 gate 计算与冻结行数据相符，两个 `passes_predeclared_gate` 均为 false；未执行格为零，不能把失败格补零或补成功。`summarize` 实现按有效性计数而未另验 `finish_reason == stop`，与门槛名称 `valid_stopped_outputs_per_model` 有潜在偏差，但本批所有有效行恰好都是 `stop`，不改变本轮结论。
- MiMo 的控制正确性是实证支持：极端伪造/宿命式文本在两个顺序都输，完全相同文本在两个独立请求都判 tie、同分且无 overclaim。HY4 只在每类控制的 order 1 各产生一个有效且正确结果；另一个顺序均截断，所以不支持“两序通过”。
- MiMo 的顺序证据只支持粗粒度 winner 稳定，不支持分数校准稳定。三个完整自然 pair 中，各维分数绝对漂移最高为 2 分，且两个 pair 对 v4 的 overclaim 标记随换序改变。identical control 的两次请求完全一致，但它们输入相同，并不是有意义的 A/B 换序压力测试。

## Grounding 与解释边界

自然判断只能相对协议提供的 `facts` 评价。按此标准，MiMo 指出 v4 中未提供的视觉统计、模型验证和技术术语，方向基本符合 rubric；但其多条理由把 v5 概括成“只引用真实画面”，证据不足。例如：

- `natural-glm-5.3-flash-fixture-0` 的 v5 为未选中的“黑板天象馆”补了“黑板和粉笔轨道”；facts 只给了推荐画面名称。
- `natural-deepseek-v4.1-flash-fixture-3` 的 v5 为“派遣车库、机场登机口整理”补了“车辆、灯带、登机口、隔离带”；facts 同样只给名称。
- fixture 4 的 v5 为“厨房协作、开放图书馆活动”补了锅、灶台、交换书等细节，facts 未列这些推荐画面的元素；GLM 版本还把若干物件扩写成“提前铺好/插好/备好”和“为别人照亮”等动作或寓意。

因此，MiMo 的三组自然 winner 一致可以支持“在这组提示和候选中稳定偏好 v5”，不能支持“已可靠识别所有细微编造”或“v5 全部 grounded”。控制把明显虚构与严重人格/命运断言绑在同一个 bad 文本里，也无法隔离细微 hallucination、overclaim 与文风偏好。四个自然 pair 仅来自三个独立重建 journal；fixture 4 被两个 generator 复用，来源不独立、生成器分布不平衡，且没有人工 gold。模型间一致更不能当作真值；实际上 HY4 没有有效自然结果可供一致性比较。

HY4 的 10 个截断行中，completion 几乎全部消耗为 reasoning token 且没有可解析正文（仅一行留下 12 个字符的 JSON 前缀）。这只证明当前接口/预算/输出组合未能完成格式任务，不证明其 judge 判断好坏，也不能从 thinking-only 截断反推质量。MiMo 的一次格式失败同样是资格失败，但不是其语义判定的人工确认。

## 执行与成本

记录支持 24/24 计划请求均已派发、HTTP 200、无 retry/fallback，响应模型标签符合请求；总计 40,806 prompt tokens、72,127 completion tokens，其中 70,819 reasoning tokens。分模型为 HY4 20,082 prompt / 71,033 completion（70,819 reasoning），MiMo 20,724 prompt / 1,094 completion。恢复后的 `transport_this_invocation.calls=0` 和 `wall_seconds=0.009` 只描述离线重建，不能代表原实验零调用或零耗时。检查点故障说明能解释为何最终报告是 resume 生成，保存的逐请求状态、文本、延迟和 usage 足以重建上述计数；原始总体 wall time 与 max-active 未保存。

货币总成本未知，`cost_usd` 为 null；不能把缺失价格、缓存计费、CPU、开发/修复与离线验证成本记为零。本轮没有生成解读，已知付费资源只是 24 次 judge 尝试；后续实验不属于本基线账。

## 研究与产品含义

这批结果足以保留 MiMo 作为待进一步验证的 judge 候选，并明确淘汰当前 6000-token 配置下的 HY4 资格运行。它不足以将任一模型设为产品默认或可靠评审器，也不足以据此宣布 v5 的内容质量、真实性或产品收益更高。

可选的下一项优先实验：冻结 judge prompt、模型、temperature、单次尝试和双顺序，只新增一小组人工标注的“单一缺失具体细节”控制；good/bad 仅差一个 facts 中不存在的元素，文风、长度和 overclaim 保持相同。要求每个候选对所有控制两序都输出严格 JSON、选择 grounded 文本并正确标记该差异。任一无效输出或任一顺序漏判即停止为该候选追加自然排名费用，不以扩大样本挽救资格。

## 主 Agent 回应

采纳：两者都未通过这轮门槛；MiMo 的坏 JSON 不作人工修复后补算，HY4 的截断不当作品质判断。自然 pair 没有人工真值，推荐场景细节和原始视觉统计在事实输入中的缺失必须单列，不能把裁判对“编造”的标签直接当成生成模型真实犯错的证据；分数和 overclaim 的换序漂移也保留。

后续单独冻结两个变体：MiMo 增加 JSON 引号约束，保留原十二格，并加入一组独立设计、只替换一个画面元素的双序控制；HY4 按用户选择提高输出上限至 24000、超时至 360 秒，先测原先截断的一格，有效才继续。后续变体不覆盖本轮失败，也不以单次修复成功代替完整资格门槛。基线汇总恢复的保留字段冲突已修正，零模型检查确认重建不重发请求。
