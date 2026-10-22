# Judge qualification 后续变体独立结果评审

评审范围：HY4 24k/360 变体与 MiMo JSON guard/单元素对照变体各自冻结的 `protocol.json`、`results.json`、`results.json.samples.jsonl`、`run.py`，并以基线 `protocol.json`、`results.json`、`execution-notes.md`、`resources.json` 作资源与变更参照。评审先据后续原始工件形成判断，再读取基线 `review.md` 和尚未补完的作者汇总 `judge-qualification-20261021.md`；因此不是对作者稿盲评。未调用实验模型、未重新生成解读、未改评分或原始输出。

## 独立结论

- **两项后续变体仍均未通过预声明门槛。** HY4 为 11/12 有效、严格 JSON 且 `stop`，唯一失败格是 `natural-glm-5.3-flash-fixture-0` 反向：360.003 秒后 `network_error`，无 HTTP 状态、正文、返回模型标签或 usage。首格 gate 有效后其余 11 格按冻结规则执行，未执行格为 0；两类对照各 2/2，通过的三个完整自然 pair 均双序偏好 v5、无硬翻转，但 12/12 完成要求失败。MiMo 为 14/14 有效、严格 JSON 且 `stop`，三类对照各 2/2；四个自然 pair 仅 2 个双序一致，另外两个均为 v4/v5 硬翻转，故一致数 2 < 3、硬翻转 2 > 0。
- 重算与两个 `results.json` 的 `passes_predeclared_gate=false` 一致。全部有效行也确为 `finish_reason=stop`；没有把超时格、无效格或未执行格补算为成功。
- 请求与返回模型标签相符：11 个有响应的 HY4 行均为 `hy4-preview`，14 个 MiMo 行均为 `mimo-v2.6-pro`；无 retry/fallback，结束时 active=0。标签只证明所报告的路由结果，不是模型质量证明。

## I/O 可靠性与语义判断分开

HY4 提高上限后，基线 10/12 `length` 的 thinking-only 截断不再出现：11 个已返回请求都给出严格 JSON 并正常停止。这支持“大预算配置显著改善本批输出可用性”，但同时改了 `max_tokens` 与 timeout，且没有同一请求的隔离重复，不能把改善单独归因于 token 上限。唯一 360 秒失败没有服务端状态和用量，故也不能断言服务端未计算或未计费。资格门槛衡量的是端到端可用性；即使失败更像传输/超时，也不能豁免。

在成功输出内部，HY4 的语义信号比“未通过”更细：明显伪造/宿命式对照和完全相同文本对照均两序通过，三个完整自然 pair 均稳定偏好 v5，第四个只取得正向 v5。完整自然 pair 的单项分数换序漂移最高 1 分，overclaim 无漂移。不过 identical control 两次独立请求的 grounded 从 3 变 4，grounding control 的 bad clarity 从 2 变 5，说明绝对分数仍非稳定标尺。自然样本无人工 gold，不能把稳定偏好等同正确判断或 v5 真实更好。

MiMo 的格式与交付可靠性在本变体为 14/14，但自然判断暴露明显位置敏感：两个 GLM pair 的两次输出都选择 A，归一化后分别从 v4 翻到 v5；这不是 tie 边界变化，而是两次硬翻转。两个 DeepSeek pair 双序稳定偏好 v5，但自然分数仍有最高 2 分漂移；`natural-glm-5.3-flash-fixture-4` 的 v4 overclaim 也随顺序从 false 变 true。因而不能用格式修复或部分稳定 pair 宣称其已具备可靠排序能力。相较基线，MiMo 从 3 个完整自然 pair 均一致变为同一原始 4 对中的 2 次硬翻转，也表明单次、temperature=0 并未带来可复现的顺序不变性。

新增 matched control 两段除“手套/围巾”外相同；MiMo 两序都选择并给更高 grounded 分给 facts 中存在“手套”的文本，双方 overclaim 均 false，grounded 差分别为 3、2。它支持相对所给 facts 检出**这一处**单元素替换。它是一个人工编写 pair、一个元素类型、两个互为换序但并非独立 gold 来源，不能外推为普遍细微幻觉检出率，也不能证明自然文本的全部细节 grounded。winner 虽也两序正确，冻结 pass 条件主要是 grounded 差与 overclaim。

## 设计、事实口径与成本

HY4 的原 12 格与基线输入/rubric 相同；MiMo 保留原 12 格并追加 2 格，但同时加入 JSON 引号 guard、把 6000/180 改为 24000/360，并增加新控制。因此 MiMo 变体只能评价整套预期配置，不能作“引号 guard 导致格式成功”的单因素因果结论。四个自然 pair 仍只有三个独立重建 journal，fixture 4 跨 generator 复用，生成器与模式不平衡；无人工 gold，且 facts 不含原始视觉统计、推荐画面的元素细节，也未精确恢复历史逐轮计数。模型对 facts 的相对判定不能追溯证明生成文本在原始世界中真假。

三轮合计 50 次已派发 judge 尝试：基线 24、HY4 后续 12、MiMo 后续 14；49 次已知 HTTP 200，HY4 超时 1 次状态未知。已返回 usage 合计为 82,619 prompt、181,074 completion、263,693 total，其中 177,305 reasoning tokens；这是**已知返回用量**，不含超时格可能发生但未返回的用量，不能当完整总量。三轮 `cost_usd` 均为 null；价格、缓存计费、代理内部尝试、超时格计费、CPU、研发和独立评审成本均未知，不得填零。本轮无新 reading generation，只有 judging 尝试。

## 支持范围、异议与下一步

结果支持：继续把 HY4 24k/360 视为有语义信号但端到端完成率尚未过门槛的研究候选；确认 MiMo 的 JSON/单元素对照能力在本批可用，同时把自然排序的位置偏差列为实质失败。结果不支持：采用任一模型为默认裁判、据此给 v4/v5 排名、宣称 MiMo 已稳定或 HY4 已合格、把 HTTP/JSON 成功当判断正确、把已知 token 当完整成本。

与作者汇总的实质差异是其后续两节仍写“待补/正在运行”；最终稿必须改为上述两个失败结论，并保留 HY4 的 I/O 失败与完成输出语义信号之间的区分，以及 MiMo 两个 A-position 硬翻转。基线独立评审建议的单元素控制已完成且通过，但它没有解决自然排序稳定性。

**一项优先可选实验（不构成自动授权）：**只针对 MiMo 的位置偏差做小型预注册复测：冻结当前 prompt、24000/360、temperature=0、无 retry/fallback 与原四个自然 pair；为每个 pair 预先生成固定的、语义等价但不以 `A/B` 命名的展示模板，并各做两次位置互换，另加入一组人工 gold 的近似等质偏好 pair，以区分首位偏置和内容判定。主要终点只看归一化 winner 的位置不变性，分数作诊断。**停止条件：**任一 gold control 失败、任一无效输出，或任一自然 pair 再出现 v4/v5 硬翻转，即停止为 MiMo 增加自然排名费用并维持不合格；只有全部有效、gold 全过、四个自然 pair 零硬翻转，才值得另行授权更大且有人类 gold 的验证，仍不自动改产品默认。

## 主 Agent 回应与补充

采纳两项均未通过门槛的结论，以及格式、判断稳定性和端到端完成率的区分。作者汇总现已补齐，不再保留“待补/正在运行”状态；所有失败和原始检查点仍保留。总账补充标明49条已知usage与1条未知，而非把263693 tokens冒称完整成本。

主Agent逐行复核还发现：MiMo的GLM fixture0正序 `better=A`，但三项评分均A=3/B=4、overclaim A=true/B=false，理由也赞B批A。这是可直接定位到原始输出的字段矛盾，不能仅解释为内容近似或真正tie，也不能自动把winner修成B后补算成功。交接把输出一致性检查、双序冲突弃权和报告覆盖率列为Claude的优先零模型工作；剔除冲突后的子集胜率也不可推广为所有case胜率。

可选位置复测保留为建议，本轮不再追加付费请求。MiMo保留为辅助发现问题的候选，HY4高预算可作显式第二意见，但都不自动提升为可靠排名器；不改变线上prompt或官方DeepSeek路由。
