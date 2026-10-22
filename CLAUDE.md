# 开工入口

## 当前产品方向 — 2026-10-22

用户确认 **PAM∃LA 牌库 + v6 即 Norn Machine 的下一版主站**，不保留“产品版／研究版”双入口。论文实验、原始数据、结果和冻结分析计划仍在本地 `../norn-research`，不进 GitHub；本仓库 README 只写研究问题，不写离线实验结果数字。没有真实旧用户，本次不做历史会话迁移。

**最新展示更新（第36节）：** Worker `e8cc1ffd-aa2e-4075-a8ff-12a49cc5856e` 已发布。公共文案统一“选图回顾”；X-Ray 的两个开局开关收在“开局设置”，进入后自动折叠，仍独立默认OFF、开局后锁定。双语README采用暗金封面、真实CI徽章、三张功能卡、各自语言的4张真实界面图和原生SVG架构图，无Mermaid。截图来自本地mock/no-collect，模型拟合与回放真实、文字明确标注模拟；不是模型效果或真人研究证据。冻结模型、事件及初始任务prompt字节未改。最低Python改为3.12+，CI覆盖3.12/3.13/3.14：旧3.10黄金哈希失败与3.12浮点求和改进有关，不能通过改黄金或数值契约掩盖。线上37项存档验收通过、零模型调用、测试记录已撤回；首个Python3.14网络TLS失败保留独立报告，不能算产品失败或已通过的检查。

主构建使用 `cloudflare/` 与既有 Worker `norn-machine`，沿用域名、DeepSeek 官方路线、存储 bindings。本地 `norn serve --mock` 默认 `pamela-v1`。**第34节首版已发布 `3b7045f9-c186-441f-aca5-9fb87e984040`：** 当前开放轮的真实选择／撤回和临时 attention 尽量保存，500ms 防抖、10s 周期、隐藏／退出 ≤60KiB keepalive，不造 `round_completed` 或 STOP。两个入口都有实选图的视觉小传与事先猜牌回顾。[线上存档验收](benchmarks/results/norn-active-round-live-20261022.json)28项通过、零模型调用、测试记录清理；另行真实验收成功调用Jev一次、DeepSeek一次，不能混进“零模型”的存档报告。

**当前版本已发布（`dbce9b2d-f3be-40cf-81fc-4357b2386909`）：** 用户明确逐轮反馈要和Jev一样藏在X-Ray，默认OFF、开局前独立勾选、开局后锁定。两入口默认都只结尾揭晓；私密不自动开启，Jev也不连带开启。研究同意与两个开关独立：同意的访问可显式开反馈，但要用独立experience附录记录开关与实际可见曝光，不能混进原live-v3无反馈样本。不要把缺失旧字段当作“确认无曝光”。冻结events/model/profile数学不改。手机反馈布局一并修复。Jev此前选择与候选数值特征送Typesafe，概率结果只在本页；反馈配置/曝光元数据单独留档。Jev独立额度仍为actor活动30分钟能力合计8次、全球100次/UTC日、并发2、15s deadline、零自动重试，不支持非空位置层。

**研究接口边界已做最小保护：** frozen live-v3 reader本身依旧忽略experience；审计确认原来同deployment的on会混入。本轮未改研究源码/锁，而由partial/full builder把新on记录统一路由成 `deployment='<真实host>.round-feedback.v1'`，`source_deployment`保存真实host；SQL事务拒绝on写回原deployment或source/cohort不匹配。只读旧 `eligible` 实证接受off/unspecified、以 `other_deployment` 排除on。保证只覆盖新builder和归档校验的新on，不替历史未知补判。deployment现在是cohort路由字段；off/缺省原shape不变，旧缺失=unspecified，不等于off。`classify_round_feedback` / `filter_explicit_feedback_off` 供以后批准的显式offonly分析及未知分层，不自动修改冻结计划。保留下来的反馈组原始数据未来需要自己的研究协议，不能用自选开关推断因果收益。

最初全套回归747 tests / 1,305 subtests、19 skips，随后专项18 tests / 22 subtests；后续前端/experience修改须专项验证，不能相加或把旧全套数字冒充最终源码复跑。此前本地真实浏览器验证了研究中途无反馈、2轮后小传／猜牌回顾与追问；普通本地服务没有operator archive export，指向它的存档脚本失败不算通过。最终部署版本与线上新增开关／曝光／手机验收已完成，详见[交接第34节最终验收](the-norn-machine/AGENT_HANDOFF.md#34-final-publication-and-verification)。GitHub个人profile双语Norn行已发布；完整验收证据保留各次运行范围。

**上一已发布版本（第33节）：** `6ba425e2-a5cd-4da4-a492-537079da6496` 上线同意 v4 中途存档，位置层仍休眠，Jev 当时只有离线功能。完整回归712 tests / 1,265 subtests / 19 skips，后续专项9 tests / 26 subtests；线上存档22项、X-Ray23项、中英实页与素材验收通过，测试数据已撤回，零外部模型调用。它保留原发布范围，不能当作第34节新增功能的验收。此前DeepSeek付费验收仅作为第30节的历史证据。

**2026-10-22 Claude 接手（交接第 32 节）：** CodeX 停在第 31 节，用户让 Claude 接手产品仓库，并同意两项改动。当时已实现并测试；用户随后授权CodeX审查后部署，审查修复与正式发布见第33节。① 群体位置层（休眠）：引擎、浏览器、X-Ray 轨迹支持牌库文档里的 `context`（各张数的位置偏移、停止斜率），它属于牌库版本；当前牌库没有，v6 与画像哈希逐位不变；启用须由 `../norn-research` 冻结的 live v3 分析判定，之后生成新牌库版本再部署。② 同意 v4：同意的访客中途离开也保存已走过的轮次。浏览器每开一轮发一份快照到 `/api/record/partial`，存为 `norn.partial.v1`，同一会话只留最新一份，揭示后由完整记录取代（保留访问编号），全部撤回时一并删除。上线后运行 `scripts/verify_partial_visit.py` 验收（零模型调用，测试访客的数据会自动撤回）。`norn/evaluate.py` 被旧研究锁绑定，不要改。

研究仓库的 live v3 冻结分析计划（引擎加入可选位置层时取代了展示 v2，二者都还没有数据）锁定 `research_model.py`、`research_deck.py`、`display_policy.py`、`choice_model.py`、`numeric.py`、`norn/events.py` 和基础牌库版本；启用位置层后的新牌库版本在研究仓库登记。本轮解释层放在冻结文件之外，不改变其数学和画像哈希。v6 使用 native-libm，CPython／Workers-WASM 的拟合值和哈希可能不同；严格回放要求兼容数值运行时，不能沿用旧模型 Decimal34 验证宣称跨运行时精确。改动绑定文件仍需要新协议版本。

以下是历史完成节点，所述默认模型／部署状态以各自日期为准，当前方向以上述说明为准：

最新完成状态见[交接第 21 节](the-norn-machine/AGENT_HANDOFF.md)和[收缩／回退验证](docs/choice-policy.md)：本地默认评分／画像 v5 已实现，840 份原诊断日志哈希全部一致；训练内门控消除无信号过拟合，也牺牲部分弱信号的早期收益，原版ML与失败结果均保留。v1–v4精确回放、真实Pyodide和隔离wheel验证通过，零模型调用，尚未部署（线上仍v4）。历史prose/judge复核已显式pin评分v4，不能重算为新默认来污染缓存。勿改样例/阈值或把模拟说成真人效果。

先读 [项目交接文档第 20 节](the-norn-machine/AGENT_HANDOFF.md)及[样例诊断](docs/fast-thinker-diagnosis.md)：用户暂缓充值，已离线查清对照报告中 ML 落败的样例转移、评分目标及小样本过拟合问题。原始分数保留，数值检查通过，七组固定模拟及每人指标已保存；不能用这些模拟宣称真人效果。没有调整线上模型或部署，也不必重跑实验恢复上下文。

第 19 节及[快思考层对照协议](docs/fast-thinker-comparison.md)说明实验性 `norn compare`，默认仍离线。用户随后明确要求真实测试：Jev1.13.0官方单batch16题成功，NLL1.9221；规则1.7162、收缩策略1.6516、原始ML2.3147，uniform1.3863仍最好。这是原夹具，不能宣称真人/人群收益。整批8.608秒，21856input/1725outputtokens，1调用0重试0prose；成本/余额未知。详见交接第23节及`benchmarks/results/jev-live-fixture-20261021/`，勿重跑恢复上下文。Key已私密读取一次，仅进子进程环境，未进argv/日志/报告。`scripts/jev_keychain.py status`仍只查metadata，显式`compare`才读取并固定官方模型、单batch≤16题。远端必须显式选择provider，不能从ChatGPT订阅推断Decisions权限；本轮未部署。

第 18 节仍是未改变的线上试点发布：2026-10-21 构建已部署并验收；[真实流程报告](benchmarks/results/cloudflare-pilot-live-20261021.json)的 12 次 HTTP、20 项检查全部通过，完整运行使用 2 次模型调用。该次发布含排障累计 5 次模型调用、0 次裁判调用，详见[资源台账](benchmarks/results/cloudflare-pilot-resources-20261021.json)。此前安装包验证属于试点运行时快照，早于新增对照 CLI。自动选择夹具不是真人试点，真人预测结论仍待采集。第 13–17 节、[兼容审查](docs/handoff-compatibility-20261021.md)和[裁判实测报告](benchmarks/results/judge-qualification-20261021.md)保留各阶段证据；`norn.probe` 继续作为唯一新图选择协议。

用户要求后续第三模型使用 MiMo V2.6 Pro 或 HY4，不再默认调用 Kimi。裁判资格测试的冻结输入、原始响应和结论保存在 `benchmarks/results/judge-qualification-v1-20261021/`；以交接文档中最新完成状态为准。

裁判资格实测已结束：MiMo 新配置14/14格式和全部控制通过，但两对自然排名随位置翻转，并有 winner／评分／理由矛盾；HY4 提高到24000/360后11/12有效，仍一次超时。两者都未过独立自动排名门槛。第 13 节记录的裁决一致性检查、双序冲突弃权、事实输入补齐、移除自动 HY4 fallback 和主 CLI 预算／JSON 配置已完成，不再作为待办重复实施。MiMo 仍只用于辅助评审，不能以这些格式测试宣称自然排名可靠。

复用已完成的生成与裁判记录，不覆盖历史实验，也不要因恢复会话自动重跑付费请求。读取实际模型、输出预算和失败原因后再决定后续实验；HTTP 成功不等于裁判质量合格。实验模型通过本地 Workbuddy 显式调用，线上仍使用官方 DeepSeek，二者配置分开。

最终验收：`dbce9b2d-f3be-40cf-81fc-4357b2386909` 已部署；`benchmarks/results/norn-partial-visit-live-v4-20261022.json` 37/37，零模型调用，合成访客已撤回。真实 Jev 1 次和 DeepSeek 1 次为首版独立浏览器实测，不在零模型报告内。个人主页双语 Norn 行已在 GitHub 发布。整套回归最后一轮 760 通过/19 skipped/1339 subtests，一项旧 preparation 字段集合兼容失败已修复（无 experience 不添加空 key）；修复后直接相关 81 tests/66 subtests 全通过，新增合同复验 22/44 与之重叠，不相加。10个强制锁哈希未变。

2026-10-22 GitHub发布快照见交接第35节：用户已授权整体提交并推送main；最终完整复跑762 tests / 1339 subtests全部通过，19 skips，包含旧prepare返回字段兼容修复。uv隔离wheel及仓库外安装/doctor/replay/新模块和前端资源检查通过。公开内容仅产品与工程夹具；论文原始数据仍在独立研究仓库。v1盲评key是公开历史夹具，不能用于新正式盲评。
