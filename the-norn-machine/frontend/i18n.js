/* UI translations stay local. Language choice never creates a visitor identifier. */
(function () {
  'use strict';
  const STORAGE_KEY = 'norn.language.v1';
  const EN = {
    '每轮揭晓猜牌反馈': 'Reveal guessing feedback after each round',
    '本次已开启逐轮反馈；开局后不能更改。': 'Round feedback is on for this visit; locked after starting.',
    '逐轮反馈已关闭；结尾才揭晓猜牌回顾。': 'Round feedback is off. Reveal the guessing recap at the end.',
    '默认关闭 · 开启后每轮揭晓；若选择匿名织入，会记录开关与展示轮次。': 'Off by default · Reveals each round when enabled. Contributed visits record the setting and exposed rounds.',
    '结尾揭晓织机猜中了几轮。织入可匿名帮助研究，私密体验不留记录；逐轮反馈可在 X-Ray 开局前另选。': 'Reveal the score at the end. Weaving contributes anonymous choices; private play leaves no record. Round feedback is a separate pre-start option in X-Ray.',
    '私密体验 · 不留学习记录': 'Private experience · No research record',
    '织入后，结尾揭晓织机猜中了几轮，匿名选择帮助研究。私密体验可逐轮揭晓，不留研究记录。': 'Weave to reveal the score at the end and contribute anonymous choices. Private play reveals each round without a research record.',
    '可选付费模型 · 此前选图会发给 Typesafe，最多 8 轮；研究体验到结尾才揭晓。': 'Optional paid model · Earlier choices go to Typesafe, up to 8 rounds. Contributing visits reveal results only at the end.',
    '事先猜中 {0} / {1} 轮 · 打开 X-Ray 回看': 'Guessed {0} / {1} rounds in advance · Replay in X-Ray',
    '本次保留的画面': 'An image kept this visit',
    "开局加入 Jev 对手": "Add Jev as an opponent before starting",
    "正在检查 Jev……": "Checking Jev…",
    "哪些画面会让你停下来？": "Which images make you pause?",
    "选几轮，看看织机能否猜中你，再带走一份视觉小传。": "Choose a few rounds, see whether the loom can guess your choices, and take home a visual keepsake.",
    "画面由 AI 生成；这是一场视觉探索，不是人格测评。": "AI-generated images. A visual exploration, not a personality test.",
    "织入：安静选图，匿名帮助研究。试玩：当场揭晓猜测，不留研究记录。": "Weave: choose quietly and contribute anonymously. Play: reveal each guess, without a research record.",
    "猜牌试玩 · 不留学习记录": "Play the guessing game · No research record",
    "开始 Jev 试玩": "Start a Jev play session",
    "开始猜牌试玩": "Play the guessing game",
    "本次已开启 · 开局后不能更改；最多 8 轮，不自动重试。": "Enabled for this visit · Locked after starting; up to 8 rounds, no automatic retries.",
    "本次未开启 · 开局后不能更改。": "Off for this visit · Locked after starting.",
    "可选付费模型 · 开启后进入不留研究记录的试玩，最多 8 轮。": "Optional paid model · Enables play without a research record, for up to 8 rounds.",
    "Jev 暂不可用；本地学习模型和基线照常运行。": "Jev is unavailable. The local learning model and baseline still work.",
    "Jev 未能开启；继续本地两路试玩，不自动重试。": "Jev could not start. Continue with the two local lanes; no automatic retry.",
    "部分轮次 Jev 未返回结果；已有预测仍可回放，不自动重试。": "Jev did not return every round. Available predictions remain replayable; no automatic retries.",
    "织机正在悄悄猜。选好后翻到下一轮，看看有没有猜中。": "The loom is making a hidden guess. Turn the page after choosing to see how it did.",
    "这一轮留白。织机继续观察，不计输赢。": "A blank round. The loom keeps watching; no score this time.",
    "这一轮没有及时完成单张预测，不补算命中。": "No single-image guess was ready in time. This round earns no hit credit.",
    "✦ 猜中了！你首张保留的，正是织机事先押下的那张。": "✦ A hit! Your first kept image was the loom’s precommitted guess.",
    "这次你给了织机一个意外。下一轮再试。": "You surprised the loom this time. Let’s try another round.",
    "已猜中 {0} / {1} 轮": "Guessed {0} / {1} rounds",
    "本次视觉小传": "Visual keepsake from this visit",
    "Jev 已在开局前开启。打开、拖动和播放只读取已有预测，不新增调用。": "Jev was enabled before starting. Opening, scrubbing and playback only read existing predictions.",
    "Jev 默认关闭；可以在开局前手动开启，或事后导入结果。": "Jev is off by default. Enable it before starting, or import results later.",
    "三路曲线只比较都有预测的轮次；未及时返回的 Jev 结果仅供回放，不计现场猜中。": "All three curves use the same covered rounds. Late Jev results are replay-only and earn no live hit credit.",
    '它猜中了什么？': 'What did it predict?',
    '模型现在怎么看？': 'What does the model see now?',
    '尚未展示的画面': 'Images not shown yet',
    '这是当前模型对未展示画面的排序，不是已经验证的下一轮预测。': 'The current model ranks images not shown yet. These are not verified next-round predictions.',
    '如果选一张：用此前选择预测这一轮，再与你实际保留的图片对照。': 'If choosing one image: predict this round from earlier choices, then compare with what you kept.',
    '随着轮次，谁更接近你的选择？': 'Who gets closer to your choices?',
    '返回逐轮对照': 'Return to rolling predictions',
    '已返回逐轮对照 · 仅在本地切换。': 'Rolling comparison restored · Switched locally only.',
    '离线留出对照': 'Offline held-out comparison',
    '冻结前 {0} 轮': 'First {0} rounds frozen',
    '逐轮预测': 'Rolling predictions',
    '参考基线': 'Reference baseline',
    '学习模型': 'Learning model',
    'Jev · 本次未运行': 'Jev · Not run',
    'Jev · 结果不可用': 'Jev · Result unavailable',
    '首张保留指最终保留图片中最早被点选的一张，撤回的图片不算。': 'First kept means the earliest-selected image among those finally kept. Withdrawn images do not count.',
    '查看预测对照 JSON': 'View prediction comparison JSON',
    '三路都固定使用前 {0} 轮，后续选择不更新任何一条模型。': 'All three lanes use only the first {0} rounds. Later choices update none of the models.',
    '查看本轮对照 JSON': 'View this round’s comparison JSON',
    '前 {0} 轮只用于拟合。把时间轴拖到之后的轮次，查看留出对照。': 'The first {0} rounds are training only. Move the timeline past them to inspect held-out predictions.',
    '完成一轮后，这里会把当时的预测与你的选择放在一起。': 'Complete a round to compare its earlier prediction with your choices.',
    'Jev 只读取事后导入的结果；打开、拖动和播放都不会调用模型。': 'Jev uses imported offline results only. Opening, scrubbing and playback never call a model.',
    '第 {0} 轮 · 共 {1} 张': 'Round {0} · {1} images dealt',
    '曾见 {0} 张': '{0} ever seen',
    '就绪时 {0} 列': '{0} columns at ready',
    '首张保留': 'First kept',
    '{0}：{1}，并列第 {2} 名': '{0}: {1}, tied rank {2}',
    '这一轮没有保留图片，不计入“如果选一张”的对照。': 'No image was kept this round; it does not score in the card-conditional comparison.',
    '可比较图片不足两张，这轮不计入预测损失。': 'Fewer than two comparable images; this round does not score in prediction loss.',
    '证据尚未支持个人差异，本轮学习模型与参考基线相同。': 'Personal differences are not established yet. This round uses the reference baseline.',
    '本轮两路预测相同。': 'Both predictions match this round.',
    '首张保留的画面：基线第 {0} 名，学习模型第 {1} 名。': 'First kept image: baseline rank {0}, learning-model rank {1}.',
    '所有留出轮次都只用前 {0} 轮拟合。图片条件概率不包含“都不选”，也不表示人格准确率。': 'All held-out predictions use only the first {0} rounds. Card-conditional probabilities exclude “none” and do not measure personality accuracy.',
    '本轮模型只学到第 {0} 轮；在本轮曾见的相同图片中比较，不包含“都不选”。': 'This model learned through round {0} only. Both rank the same images ever seen this round; “none” is excluded.',
    '{0} 个可评分轮次 · 越低越好': '{0} scored rounds · Lower is better',
    '还没有可评分的图片选择。': 'No image choice can be scored yet.',
    '图片条件平均预测损失，越低越好；曲线只画到当前回放轮次。': 'Mean card-conditional prediction loss, lower is better. Lines stop at the selected replay round.',
    '完成选图后解锁对照，避免预测提示影响你的选择。': 'Finish choosing to unlock comparisons, so prediction hints do not influence your choices.',
    '导入离线三路对照': 'Import offline three-model comparison',
    '导入离线基线、学习模型与 Jev 对照': 'Import offline baseline, learning-model and Jev comparison',
    'Jev 默认不运行。这里读取事后算好的结果，不会调用模型。': 'Jev is off by default. This reads results computed afterwards and never calls a model.',
    '正在核验离线对照，读取不会调用模型……': 'Verifying the offline comparison; no model will be called…',
    '请选择冻结前缀的 PAMELA 离线对照报告。': 'Choose a frozen-prefix PAMELA comparison report.',
    '离线对照不属于当前记录。请先导入对应的选图记录。': 'This comparison belongs to another record. Import its selection record first.',
    '离线对照已核验 · 三路一起切换到同一冻结前缀，不混入逐轮模型。': 'Offline comparison verified. All three lanes now use one frozen prefix, without rolling-model results.',
    '离线对照与记录匹配 · 三路一起使用同一冻结前缀。': 'Offline comparison matches this record. All three lanes use the same frozen prefix.',
    '已校验记录、牌库、预测任务和报告完整性；这不认证报告中的远程模型调用。': 'Record, deck, prediction task and report integrity are checked. This does not authenticate a remote model call claimed by the report.',
    '画面星图': 'Image constellation',
    '牌库与轨迹版本不匹配，请刷新后重试。': 'The image deck and replay version do not match. Refresh and try again.',
    '位置按参考评分排序，保持不动；越亮，当前预测越靠前。白框是本轮展示，金框是本轮选中，金点是此前选过。': 'Positions stay fixed in reference-rating order. Brighter images rank higher now. White outlines mark this round; gold outlines mark your picks; gold dots mark earlier picks.',
    '织机了解到了哪一层？': 'How far has the loom learned?',
    '只有选择积累的证据达到门槛，才启用更个人化的一层；未通过不代表你没有偏好。': 'A more personal layer is used only when evidence crosses its threshold. Falling back does not mean you have no taste.',
    '此刻会推荐的画面': 'Images it would recommend now',
    '排序使用当前生效的一层；参考口味来自这批画的参考评分者。': 'Ranked by the currently active layer. The reference comes from the raters of this image collection.',
    '参考口味': 'Reference taste',
    '口味敏感度': 'Taste sensitivity',
    '个人偏好': 'Personal preference',
    '从参考评分者开始': 'Start with reference raters',
    '你多大程度跟随参考口味': 'How much you follow reference taste',
    '哪些画面元素更吸引你': 'Which image elements draw you in',
    '当前使用': 'Active now',
    '敏感度证据': 'Sensitivity evidence',
    '个人差异证据': 'Personal-difference evidence',
    '1× 达到证据门槛；这不是准确率或概率。': '1× meets the evidence threshold. This is not accuracy or probability.',
    '{0}：门槛的 {1} 倍': '{0}: {1} times the threshold',
    '第 {0} 轮 · {1}排序，越亮的画面预测越靠前。': 'Round {0} · Ranked by {1}. Brighter images rank higher.',
    '还没有个人证据，从参考评分者的画面排序开始。': 'No personal evidence yet. Start with the reference raters’ image ranking.',
    '这一轮启用了{0}。': 'This round activated {0}.',
    '当前使用{0}。': 'Currently using {0}.',
    '每张画面的分数 = 参考口味贡献 + 8 项内容贡献。这里展示生效参数，未通过门槛的候选不进入分数。': 'Image score = reference contribution + eight content contributions. These are active parameters; candidates below the evidence threshold do not contribute.',
    '拖动与播放只切换本地状态，不请求后端，也不调用模型。': 'Scrubbing and playback switch local states only. They make no server requests or model calls.',
    '画面分数是怎样组成的？': 'What makes up each image score?',
    '合计 {0}': 'Total {0}',
    '内容 {0}': 'Content {0}',
    '织机暂时还没有接上后端入口。': 'The loom is not connected to a service yet.',
    '请使用 norn serve --mock 启动本地服务，再打开终端显示的地址。': 'Run norn serve --mock, then open the address shown in your terminal.',
    '你的选择仍在本页保留，服务恢复后可以重试。': 'Your choices are still here. Try again when the service is available.',
    '在线服务暂时无法连接，请稍后重试。': 'The service is temporarily unavailable. Please try again.',
    '织 入 命 运': 'Weave my thread',
    '进 入 命 运': 'Enter the loom',
    '这台浏览器已留下 {0} 根线。': 'This browser has woven {0} threads.',
    '这台浏览器留有一个匿名编号。': 'This browser remembers an anonymous identifier.',
    '画面来自 {0}。': 'Images from {0}.',
    '已全部撤回，这台浏览器也不再记得你。': 'All your records have been withdrawn and this browser’s identifier cleared.',
    '服务暂时不可用': 'service unavailable',
    '撤回没有完成：{0}，可以再试一次。': 'Withdrawal could not be confirmed: {0}. You can try again.',
    '正在准备画面……': 'Preparing the images…',
    '请先运行 norn serve --mock，再打开终端中的本地地址。': 'Run norn serve --mock, then open the local address in your terminal.',
    '画面清单暂时无法读取。请确认本地服务正在运行，再重试。': 'The image list could not load. Check that the local service is running, then retry.',
    '在线演示': 'Hosted demo',
    '本地演示': 'Local demo',
    '{0} · 模拟文案 · 无模型调用': '{0} · Template prose · No model calls',
    'DeepSeek V4.1 Flash · 实时解读': 'DeepSeek V4.1 Flash · Live reading',
    '文案由所配置的模型生成': 'Reading generated by the configured model',
    '这次留下的线已经足够，可以揭示命运了。': 'You have reached the round limit. Your reading is ready to reveal.',
    '新画面': 'New images',
    '揭示之前，再看几组没见过的画面：同样选出触动你的，也可以直接揭示。': 'Before the reveal, a few sets of images you have not seen: pick the ones that move you, or reveal now.',
    '这是最后一组新画面，选好后就揭示命运。': 'This is the last set of new images. Reveal when you are ready.',
    '画面未能读取 · 点击重试': 'Image unavailable · Tap to retry',
    '解读内容为空，请重试。': 'The reading was empty. Please retry.',
    '织机暂时未能回应': 'The loom could not respond',
    '{0} 你的选择已保留，可以重试。': '{0} Your choices are saved on this page. You can retry.',
    '请确认本地服务正在运行，再点击重试。你的选择已保留。': 'Check that the local service is running, then retry. Your choices are still here.',
    '后端入口尚未配置。': 'The service endpoint is not configured.',
    '请使用本地服务打开页面，或检查 runtime-config.js 中的入口。': 'Open this page through the local service, or check the endpoint in runtime-config.js.',
    '画像 {0}': 'Profile {0}',
    '这次的记录没能确认是否保存；如果不想留下，可以撤回。': 'We could not confirm whether this record was saved. You can still withdraw it.',
    '✦ 你的第 {0} 根线已匿名织入织锦': '✦ Your thread #{0} has been woven in anonymously',
    '✦ 你的线已匿名织入织锦': '✦ Your thread has been woven in anonymously',
    '这次没能保存记录，体验不受影响。': 'This record could not be saved. Your experience is unaffected.',
    '这次没有留下学习记录。': 'This visit leaves no learning record.',
    '正在撤回……': 'Withdrawing…',
    '已撤回，这次的记录已经删除。': 'Withdrawn. This learning record has been deleted.',
    '正在准备本次记录……': 'Preparing your export…',
    '本次会话已过期或服务已重启，请重新编织后再导出。': 'This session has expired or the service restarted. Start a new reading to export.',
    '本次记录暂时无法导出，请稍后重试。': 'The record could not be exported. Please try again.',
    '本次记录暂时无法读取，请稍后重试。': 'The record could not be read. Please try again.',
    '已导出事件、版本化画像和贡献账本。记录包含本次选择，请自行保管。': 'Events, the versioned profile and contributions exported. The file contains your choices; keep it somewhere safe.',
    '已导出事件和版本化画像。记录包含本次选择，请自行保管。': 'Events and the versioned profile exported. The file contains your choices; keep it somewhere safe.',
    '暂时无法连接本地服务，请恢复后重试导出。': 'The local service is unavailable. Retry the export when it is running.',
    '{0} 分（满分 5）': '{0} out of 5',
    '最像我的一张：{0}': 'The image most like me: {0}',
    '正在记下……': 'Saving…',
    '本次会话已过期，评分未能保存。': 'This session has expired. Your rating could not be saved.',
    '评分暂时无法保存。': 'Your rating could not be saved.',
    '已记下，也织进了你留下的那根线；可以改选后再次记下。': 'Saved with your anonymous thread. You can change your choices and save again.',
    '已记下，会随“导出本次记录”一起保存；可以改选后再次记下。': 'Saved for this session and included in your export. You can update it.',
    '暂时无法连接服务。': 'The service is temporarily unavailable.',
    '{0} 这不影响判词、追问或导出。': '{0} Your reading, questions and export are unaffected.',
    '内向 I': 'Introverted I', '外向 E': 'Extraverted E',
    '实感 S': 'Sensing S', '直觉 N': 'Intuitive N',
    '情感 F': 'Feeling F', '思考 T': 'Thinking T',
    '感知 P': 'Perceiving P', '判断 J': 'Judging J',
    '正在读取 Python 计算……': 'Loading the calculated states…',
    '请通过 norn serve 打开本页。': 'Open this page through norn serve.',
    '本次冻结记录 · {0} 轮，全部状态已在本页，拖动不再请求。': 'Frozen reading · {0} rounds. All states are here; scrubbing makes no requests.',
    '实时 · 已封口 {0} 轮；当前一轮完成后加入轨迹。': 'Live · {0} completed rounds. The current round joins the replay when it ends.',
    '实时 · 已封口 {0} 轮。': 'Live · {0} completed rounds.',
    '请确认本地服务正在运行。': 'Check that the local service is running.',
    '透视暂时不可用：{0} 选图与追问仍可继续；可点“刷新”重试。': 'X-Ray is unavailable: {0} You can keep choosing and asking questions, or refresh X-Ray.',
    '正在核验导入记录并计算每一轮……': 'Verifying the imported record and calculating each round…',
    '记录超过 8 MiB 上限。': 'The record exceeds the 8 MiB limit.',
    '服务未返回轨迹。': 'The service returned no replay states.',
    '导入记录已验证 · 仅回放，不恢复判词或追问会话。': 'Record verified · Replay only; the reading and conversation are not resumed.',
    '导入的事件记录 · 未附画像，仅回放轨迹，不恢复判词或追问会话。': 'Imported events without a profile · Replay only; the reading and conversation are not resumed.',
    '无法读取记录。': 'The record could not be read.',
    '导入未通过：{0} 原体验保持不变。': 'Import failed: {0} Your current experience is unchanged.',
    '展开后显示。': 'Expand to view.',
    '未知': 'Unknown', '你': 'You',
    '导入的记录 · 已核验': 'Imported record · Verified',
    '导入的事件记录': 'Imported events',
    '本次体验 · 已冻结': 'This reading · Frozen',
    '本次体验 · 实时': 'This visit · Live',
    '{0} / {1} 轮': '{0} / {1} rounds',
    '暂停': 'Pause', '从第 {0} 轮播放': 'Play from round {0}',
    '第 {0} 轮后的偏好织锦：最亮的画面最可能吸引你。': 'Preference tapestry after round {0}. Brighter images have higher predicted appeal.',
    '当前最可能的画面原型 {0}，{1}%。': 'Current leading image archetype: {0}, {1}%.',
    '证据不足，暂不区分图片': 'Not enough evidence to distinguish images yet.',
    '{0} 与 {1}：估计 {2}，标准差 {3}': '{0} / {1}: estimate {2}, standard deviation {3}',
    '还没开始': 'Before the first choice',
    '没有任何选择时，四个方向同样可能；织锦整体半亮。': 'Before any choices, all directions are equally plausible and the tapestry is evenly lit.',
    '第 {0} 轮': 'Round {0}',
    ' · 未进入视野，不计入证据': ' · Not seen; excluded from evidence',
    ' · 用时 {0}s': ' · {0}s',
    '选了 {0} / {1} 张': 'Selected {0} of {1}',
    '{0} 张都没选（同样是证据）': 'Selected none of {0} (also evidence)',
    '重心几乎不动': 'Almost no change', '重心移向 ': 'Moved toward ',
    ' · 已选': ' · Selected',
    '当前排名 {0} / {1}': 'Current rank {0} / {1}',
    '尚无选择，排名未定': 'No choices yet; no ranking',
    '缓存状态未知': 'Cache status unknown',
    '模拟运行，无模型用量': 'Template run; no model usage',
    '缓存读取未知': 'Cache reads unknown',
    '，部分请求未知': '; some requests unknown',
    '缓存读取 {0} Token（实测{1}）': 'Observed cache reads: {0} tokens{1}',
    '参数 12 个偏好权重 + 1 个停止偏置（第 {0} 轮后 {1}）· 先验精度 {2}': '12 preference weights + 1 stop bias (after round {0}: {1}) · Prior precision {2}',
    '后验 {0}': 'Posterior {0}',
    '生效参数 {0}；候选标准差 {1}。': 'Effective parameters {0}; candidate standard deviations {1}.',
    '生效权重 {0}；候选标准差 {1}': 'Effective weight {0}; candidate standard deviation {1}',
    '候选标准差 {0}': 'Candidate standard deviation {0}',
    '这份轨迹没有逐维贡献数据。': 'This replay has no per-feature contribution data.',
    '画面': 'Image', '像素': 'Pixels', '合计': 'Total', '猜测': 'Prediction', '本轮选中': 'Picked this round',
    '权重 {0} ± {1}': 'Weight {0} ± {1}',
    '模型 {0} · 贝叶斯序贯选择：4 个画面原型轴 + 8 个像素主成分 + “停止选择”，拉普拉斯后验。': 'Model {0} · Bayesian sequential choice: 4 image-archetype axes, 8 pixel components and a stop option; Laplace posterior.',
    '模型 {0} · 收缩序贯选择；证据不足时使用等概率图片基线。': 'Model {0} · Regularized sequential choice; insufficient evidence uses an equal-image baseline.',
    '生效贡献用于打分；不确定性来自未门控候选，回退不代表确定没有偏好。': 'Effective contributions determine scores; uncertainty belongs to the ungated candidate. Falling back does not establish an absence of taste.',
    '计算 Python 一次算完全部 {0} 个状态；拖动与播放只在本地切换，不请求后端，也不调用模型。数值约定 {1}（轨迹是显示投影，不入哈希）。': 'Python computes all {0} states once. Scrubbing and playback stay in this browser with no backend or model requests. Numeric contract: {1}. The replay is a display projection, excluded from the hash.',
    '版本 画像 {0} · 评分 {1} · 偏好模型 {2} · 画像哈希 {3}': 'Profile {0} · Scoring {1} · Preference model {2} · Profile hash {3}',
    '版本 尚未冻结画像 · 轨迹 {0} · 事件哈希 {1}': 'No frozen profile yet · Replay {0} · Event hash {1}',
    '体积 轨迹 JSON 约 {0} · 事件 {1} 条': 'Replay JSON: about {0} · {1} events',
    ' · 原始记录 {0} · 模型上下文 {1} · 输入 Token 估算 {2}（估算不等于计费）': ' · Raw record {0} · Model context {1} · Estimated input tokens {2} (not billing data)',
    '模型调用 {0} 次 · {1}': '{0} model calls · {1}',
    '模型调用 0 次 · 尚无文案请求；轨迹计算不调用模型。': '0 model calls · No prose requested; replay calculation uses no model.',
    '逐维贡献：为什么这些画面排在前面': 'Contributions: why these images rank higher',
    '效用 u = Σ 权重 × 特征值，12 项为 4 个画面原型轴与 8 个像素主成分（像素列为 8 项之和，悬停看明细）。': 'Utility u = Σ weight × feature. The 12 terms are 4 archetype axes and 8 pixel components. Pixels combines the 8 components; hover for details. ',
    '每行是本轮前 6 个猜测或本轮选中的画面；± 为后验标准差。数值由 Python 计算，页面只展示。': 'Rows show the top 6 predictions and this round’s picks. ± is the posterior standard deviation. Python calculates the values; this page displays them.',
    '每行展示本轮生效贡献；候选标准差见悬停，不能当作回退分数的后验范围。': 'Rows show this round’s effective contributions. Hover for candidate standard deviations; they are not a posterior range around fallback scores.',
    '8 个像素主成分是什么': 'About the 8 pixel components',
    '{0} · 解释 {1}% · {2}': '{0} · {1}% variance explained · {2}',
    '查看本轮状态 JSON': 'View this round’s state JSON',
    '查看模型调用与用量 JSON': 'View model calls and usage JSON',
    '查看版本与来源 JSON': 'View versions and provenance JSON',
    '命运已经织成': 'Your thread has taken shape',
    '命运正在编织……': 'Weaving your reading…',
    '织机愿意回应{0}追问。': 'The loom can answer {0} of questions.',
    '输入不能超过 100 字。': 'Please keep your question within 100 characters.',
    '输入不能超过 {0} 字。': 'Please keep your question within {0} characters.',
    '命运正在回声中……': 'Listening for an echo…',
    '命运暂时没有更多话要说。': 'The loom has nothing more to add for now.',
    '{0}已满，织机不再继续回声。': 'The {0} of questions are complete. The loom rests for now.',
    '还剩 {0} 轮。': '{0} questions remaining.',
    '追问入口或会话不可用。请确认服务配置，必要时重新编织。': 'The question service or session is unavailable. Check the service, or start a new reading.',
    '暂时无法连接本地服务。问题已保留，恢复服务后可再次追问。': 'The local service is unavailable. Your question is preserved; try again when it is running.',
    '命运一时失语。': 'The loom is quiet for a moment.',
    '一轮': 'one round', '两轮': 'two rounds', '三轮': 'three rounds', '{0}轮': '{0} rounds',
    '与织机进行对话，窥探命运之绳。': 'Ask the loom a question about your reading.',
    '与织机低声交谈，听命运回响两次。': 'You have two questions to explore your reading.',
    '与织机继续对话，直到命运开始松动。': 'You have three questions to explore your reading.',
    'The Norn Machine — 命运织机': 'The Norn Machine — Follow your intuition',
    '本地演示 · 模拟文案 · 无模型调用': 'Local demo · Template prose · No model calls',
    '工程透视': 'Inside the loom',
    '刷新': 'Refresh', '关闭 ×': 'Close ×', '本次体验': 'This visit',
    '返回本次体验': 'Back to this visit',
    '展开后读取本次计算。': 'Open to explore this visit’s calculations.',
    '0 / 0 轮': '0 / 0 rounds',
    '命运织锦': 'The preference tapestry',
    '256 张画面 · 越亮越可能吸引你 · 白框为本轮展示，金框为本轮选中，金点为此前选过。圆点与光圈是 S/N × T/F 两轴的当前估计与不确定范围。': '256 images · Brighter means higher predicted appeal. White frames were shown this round, gold frames were picked, and gold dots mark earlier picks. The point and halo show the current S/N × T/F estimate and its uncertainty.',
    '偏好重心': 'Where your choices lean',
    '圆点是当前估计，金色带是 ±1 个标准差；证据越多，带越窄。只描述本次选择偏向哪类画面原型，不是人格测评。': 'The point is the current estimate; the gold band is ±1 standard deviation. More evidence usually narrows the band. These are image preferences in this visit, not a personality assessment.',
    '这一轮': 'This round', '织机此刻的猜测': 'What the loom predicts',
    '它猜你会被这些画面吸引：': 'Images you may be drawn to:',
    '工程细节': 'Technical details', '导入已保存的记录': 'Import a saved record',
    '命运织机': 'Follow your intuition',
    '这是一场关于选择与直觉的互动体验。': 'An interactive journey through choice and intuition.',
    '画面均由 AI 演化生成；解读用于自我探索，不是心理测评。': 'The images were generated with AI. Readings invite reflection; they are not psychological assessments.',
    '✦ 规则': '✦ How to play',
    '跟随直觉，在每一轮中选择你受吸引的画面。': 'In each round, choose the images that draw your eye.',
    '你可以': 'Choose ', '单选': 'one', '多选': 'several', '，甚至': ' or even ', '不选': 'none',
    '没有固定的轮数，觉得足够时，随时可以停下。': 'There is no fixed number of rounds. Reveal your reading whenever you feel ready.',
    '你的选择，可以成为织机的一根线：': 'Your choices can become a thread in the loom:',
    '匿名留下，让它慢慢学会读人。': 'leave an anonymous record to help it learn.',
    '只看看，不留学习记录': 'Just explore · No learning record',
    '留下的是什么？': 'What would be saved?',
    '织入后，会匿名保存每轮看到和选中的画面、它们在屏幕上的位置、点选与鼠标停留的时间、判词，以及你愿意给的评分；没揭示就离开，也会保存已经走过的轮次。这些用来改进织机，也用来研究它怎样从少量选择中学习。为了比较两种出牌方式，每次会随机采用其中一种。研究结果只以汇总形式公开，不含任何能认出你的信息。': 'Once you weave in, the images shown and chosen, where they sat on screen, the timing of your clicks and mouse hovers, your reading and any rating you choose to give are saved anonymously; if you leave before the reveal, the rounds you played are still saved. This is used to improve the loom and to study how it learns from a few choices. To compare two ways of dealing the cards, each visit uses one of them at random. Research results are published only in aggregate, with nothing that could identify you.',
    '不登录，不存 IP、设备信息或追问内容。这台浏览器会记住一个随机编号，把你几次的线连在一起，让织机看见同一个人的变化；它认不出你是谁。揭示后可以撤回这一次，也可以在这里全部撤回。撤回的记录不会被迟到的请求重新保存；全部撤回后，原匿名编号也会停用。': 'No login. Learning records exclude IP addresses, device details and question text. This browser remembers a random identifier that links repeat visits without knowing who you are. After the reading, withdraw this record, or withdraw all browser-linked records here. Delayed requests cannot restore withdrawn records. Withdrawing all records also retires the old anonymous identifier.',
    '全部撤回': 'Withdraw all', '重试加载画面': 'Retry loading images',
    '请启用 JavaScript，并通过本地服务打开页面。': 'Please enable JavaScript and open this page through the local service.',
    '已选': 'Selected ', '张': ' images',
    '下一轮 →': 'Next round →', '✦ 揭示命运': '✦ Reveal my reading',
    '保留选择，重试解读': 'Retry with my choices',
    '最后对话': 'A final conversation', '追问': 'Ask',
    '共经历': 'Explored ', '轮命运': ' rounds', '选择了': 'Selected ', '张画面': ' images',
    '这段判词像你吗？': 'Does this reading feel like you?', '可跳过': 'Optional',
    '1 · 完全不像': '1 · Not at all', '5 · 非常像': '5 · Very much',
    '哪一张最像你？': 'Which image feels most like you?', '可不选': 'Optional',
    '记下': 'Save', '跳过': 'Skip', '撤回': 'Withdraw',
    '导出本次记录': 'Export this visit', '重新编织': 'Weave again',
    '（丢失现在的数据）': '(starts a new visit)',
    '一场关于选择与直觉的互动体验。本地演示无需 AWS、API Key 或联网。': 'An interactive experience of choice and intuition. The local demo needs no AWS account, API key or internet connection.',
    '跟随直觉，选择吸引你的画面。命运正在编织。': 'Follow your intuition. Choose the images that draw you in, and watch your thread take shape.',
    '调整音量': 'Adjust volume', '关闭工程透视': 'Close X-Ray',
    '从第 0 轮播放': 'Play from round 0', '回放到第几轮': 'Replay round',
    '256 张画面的偏好织锦，正在读取': 'Preference tapestry of 256 images, loading',
    '导入记录 JSON 并验证回放': 'Import a JSON record and verify its replay',
    '向织机追问一句': 'Ask the loom about your reading',
    '画面视觉成分：绿色通道低强度占比较低、明亮度极低区间占比较低': 'Pixel component: less low-intensity green and fewer very dark pixels',
    '画面视觉成分：画面第2行第3列分区的饱和度较低、画面第3行第3列分区的饱和度较低': 'Pixel component: lower saturation in grid cells (row 2, column 3) and (row 3, column 3)',
    '画面视觉成分：绿色通道较低强度占比较高、红色通道较低强度占比较高': 'Pixel component: more lower-intensity green and red',
    '画面视觉成分：明暗变化朝向约0°至22.5°的边缘占比较低、明暗变化朝向约157.5°至180°的边缘占比较低': 'Pixel component: fewer edges oriented at 0–22.5° and 157.5–180°',
    '画面视觉成分：明暗变化朝向约135°至157.5°的边缘占比较低、明暗变化朝向约112.5°至135°的边缘占比较低': 'Pixel component: fewer edges oriented at 135–157.5° and 112.5–135°',
    '画面视觉成分：饱和度偏低区间占比较高、蓝色通道较高强度占比较高': 'Pixel component: more lower-saturation pixels and higher-intensity blue',
    '画面视觉成分：画面第2行第3列分区的明亮度较高、画面第2行第4列分区的明亮度较高': 'Pixel component: higher brightness in grid cells (row 2, column 3) and (row 2, column 4)',
    '画面视觉成分：明暗变化朝向约45°至67.5°的边缘占比较高、明暗变化朝向约22.5°至45°的边缘占比较高': 'Pixel component: more edges oriented at 45–67.5° and 22.5–45°',
    '界面语言已切换；现有判词和追问保持原语言，下一次编织使用新语言。': 'Interface language changed. This reading and its questions keep their original language; your next reading uses the new language.',
    '切换到英文': 'Switch to English', '切换到中文': 'Switch to Chinese'
  };
  const reverse = new Map(Object.entries(EN).map(([zh, en]) => [en, zh]));
  const rendered = new Map();
  const nodeSources = new WeakMap();
  let language = detectLanguage();

  function detectLanguage() {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === 'zh' || saved === 'en') return saved;
    } catch (_) { /* Language still works without persistent storage. */ }
    const languages = window.navigator.languages && window.navigator.languages.length
      ? window.navigator.languages : [window.navigator.language || 'en'];
    const supported = languages.find(value => /^(zh|en)(-|$)/i.test(value));
    return /^zh(-|$)/i.test(supported || '') ? 'zh' : 'en';
  }

  function sourceFor(value) {
    return reverse.get(value) || value;
  }

  function translate(value, target = language) {
    if (typeof value !== 'string') return value;
    const remembered = rendered.get(value);
    if (remembered) return remembered[target];
    const source = sourceFor(value);
    return target === 'en' ? (EN[source] || source) : source;
  }

  function t(input, ...values) {
    const key = typeof input === 'string' ? sourceFor(input)
      : input.reduce((text, part, index) => text + part + (index < values.length ? `{${index}}` : ''), '');
    const fill = (pattern, target) => pattern.replace(/\{(\d+)\}/g, (_, index) => String(translate(values[Number(index)], target)));
    const pair = { zh: fill(key, 'zh'), en: fill(EN[key] || key, 'en') };
    // Remember concrete dynamic labels so an immediate language switch also
    // updates already-rendered controls, without touching authored prose.
    if (rendered.size > 4096) rendered.clear();
    rendered.set(pair.zh, pair);
    rendered.set(pair.en, pair);
    return pair[language];
  }

  function apply(root = document) {
    const ignored = 'script,style,noscript,#reading-text,.dialogue-bubble.user,.dialogue-bubble.assistant';
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      if (!node.parentElement || node.parentElement.closest(ignored)) return;
      const value = node.nodeValue;
      const remembered = nodeSources.get(node);
      const source = remembered && remembered.last === value ? remembered.source : translate(value, 'zh');
      const core = source.trim();
      if (!core) return;
      const whole = translate(source);
      const translated = whole !== source ? whole : source.replace(core, translate(core));
      if (translated !== value) node.nodeValue = translated;
      nodeSources.set(node, { source, last: translated });
    });
    root.querySelectorAll('[aria-label],[placeholder],[title],meta[content]').forEach(element => {
      for (const attribute of ['aria-label', 'placeholder', 'title', 'content']) {
        if (element.hasAttribute(attribute)) {
          const value = element.getAttribute(attribute);
          element.setAttribute(attribute, translate(value));
        }
      }
    });
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    document.documentElement.dataset.language = language;
    root.querySelectorAll('[data-i18n-html="choice-rule"]').forEach(element => {
      element.innerHTML = language === 'en'
        ? 'Choose <strong>one</strong>, <strong>several</strong>, or <strong>none</strong>.'
        : '你可以 <strong>单选</strong>、<strong>多选</strong>，甚至 <strong>不选</strong>。';
    });
    const button = document.getElementById('language-toggle');
    if (button) {
      button.textContent = language === 'zh' ? 'English' : '中文';
      button.setAttribute('aria-label', language === 'zh' ? '切换到英文' : 'Switch to Chinese');
    }
  }

  function setLanguage(value) {
    if (value !== 'zh' && value !== 'en') return;
    language = value;
    try { window.localStorage.setItem(STORAGE_KEY, value); } catch (_) { /* page-only preference */ }
    apply();
  }

  function bind(button, onChange) {
    apply();
    button.addEventListener('click', () => {
      setLanguage(language === 'zh' ? 'en' : 'zh');
      onChange();
      apply();
    });
  }

  window.NornI18n = { t, translate, apply, bind, setLanguage, get language() { return language; } };
})();
