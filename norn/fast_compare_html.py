"""Standalone, offline comparison viewer for the fast-model experiment.

The report is data, never markup: user-controlled strings are escaped in the
initial document and inserted with ``textContent`` by the viewer. The embedded
JSON also escapes HTML delimiters so a journal cannot close its script element.
"""

from html import escape
import json


_LANES = ("heuristic", "ml", "decision")


def _json_for_script(value):
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_comparison(report, *, language="zh"):
    """Return a complete HTML page, with no network or third-party dependencies.

    ``language`` selects the initial language (``zh`` or ``en``); the page's
    language button switches all explanatory labels without reloading. The
    original report remains available in a closed technical-details disclosure.
    Missing or failed models retain their own lane and never receive substitute
    scores or probability bars.
    """
    language = "en" if str(language).lower().startswith("en") else "zh"
    title = "三种直觉，同一组选择" if language == "zh" else "One history. Three fast thinkers."
    models = report.get("models", {})
    lanes = []
    for index, model_id in enumerate(_LANES, 1):
        model = models.get(model_id, {})
        local_labels = {
            "zh": {"heuristic": "固定规则", "ml": "个体学习"},
            "en": {"heuristic": "Fixed rules", "ml": "Personal ML"},
        }[language]
        label = escape(str(local_labels.get(model_id, model.get("label", "Decision model"))))
        model_status = str(model.get("status", "unavailable"))
        status = escape(model_status, quote=True)
        status_label = {
            "zh": {"ok": "已测量", "failed": "运行失败", "unavailable": "未启用"},
            "en": {"ok": "Measured", "failed": "Failed", "unavailable": "Unavailable"},
        }[language].get(model_status, "—")
        placeholder = ""
        if model_status == "unavailable":
            placeholder = (
                '<div class="empty-state"><strong>'
                + ("暂无预测结果" if language == "zh" else "No predictions available")
                + "</strong><p>"
                + ("本次运行未能使用此模型，没有填入替代分数或概率。" if language == "zh" else
                   "This model was not available for this run. No scores or probabilities have been substituted.")
                + "</p></div>"
            )
        variant = ("<div class=\"ml-variants hidden\" id=\"ml-variants\" role=\"group\">"
                   "<button type=\"button\" id=\"ml-original\"></button>"
                   "<button type=\"button\" id=\"ml-guarded\"></button></div>"
                   "<p class=\"policy-status hidden\" id=\"policy-status\"></p>"
                   if model_id == "ml" else "")
        lanes.append(
            f'<article class="lane lane-{model_id}" data-model="{model_id}" '
            f'data-status="{status}"><div class="lane-top"><span class="lane-number">'
            f'0{index}</span><span class="status">{status_label}</span></div><h2>{label}</h2>'
            f'<p class="model-description"></p>{variant}<div class="metrics"></div>'
            '<div class="prediction-heading"><h3></h3><span class="prediction-time"></span></div>'
            f'<div class="predictions">{placeholder}</div><div class="model-footer"></div></article>'
        )
    html = _DOCUMENT.replace("__LANGUAGE__", language).replace("__TITLE__", title)
    before_lanes, after_lanes = html.split("__LANES__", 1)
    before_data, after_data = after_lanes.split("__REPORT_DATA__", 1)
    # Join once so reserved template strings inside user data stay literal.
    return before_lanes + "".join(lanes) + before_data + _json_for_script(report) + after_data


_DOCUMENT = r'''<!doctype html>
<html lang="__LANGUAGE__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Norn · __TITLE__</title>
<style>
:root{--ink:#202c32;--muted:#67747a;--paper:#f5f5ef;--surface:#fff;--line:#dce0da;--violet:#7a65b2;--teal:#2b887c;--amber:#bd7940;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5}button,input,summary{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #487b89;outline-offset:4px}button:disabled{opacity:.35;cursor:default}main{max-width:1320px;margin:auto;padding:32px 40px 36px}header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:34px}.brand{font-size:12px;letter-spacing:.16em;font-weight:750}.brand b{font-size:18px;letter-spacing:.2em;margin-right:13px}.language{border:1px solid var(--line);border-radius:20px;background:transparent;padding:7px 13px;white-space:nowrap}.eyebrow{color:var(--teal);font-size:11px;font-weight:750;letter-spacing:.13em;margin:0 0 10px}h1{font-size:clamp(30px,3.5vw,46px);line-height:1.15;letter-spacing:-.045em;margin:0 0 14px;font-weight:650}h2,h3,p{margin:0}.intro{max-width:780px;color:var(--muted);font-size:15px;line-height:1.7}.provenance{display:flex;align-items:flex-start;gap:13px;border:1px solid #dcd7c5;border-radius:13px;background:#eeebdf;margin-top:23px;padding:14px 17px}.provenance.fixture{background:#fff4dc;border-color:#e7d3a5}.provenance-mark{display:grid;place-items:center;flex:0 0 24px;width:24px;height:24px;border-radius:50%;background:#fff7e6;color:#93681f;font-weight:750}.provenance strong{font-size:13px}.provenance p{color:#726955;font-size:12px;margin-top:3px;overflow-wrap:anywhere}.context{display:flex;flex-wrap:wrap;gap:9px;margin:18px 0 28px}.context span{border:1px solid var(--line);border-radius:6px;padding:5px 9px;color:var(--muted);font-size:11px}.context strong{font-weight:650;color:var(--ink);margin-left:5px}.step-control{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:18px}.step-header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.step-kicker{font-size:11px;color:var(--muted);margin-bottom:5px}.step-title{font-size:18px;font-weight:650;line-height:1.4}.observed{display:flex;flex-wrap:wrap;gap:6px;align-items:center;color:var(--muted);font-size:12px;margin-top:7px}.target-chip{border-radius:5px;padding:3px 7px;background:#e9f2ec;color:#2b6450;max-width:100%;overflow-wrap:anywhere;font-weight:650}.step-buttons{display:flex;gap:7px}.step-buttons button{width:35px;height:35px;border-radius:50%;border:1px solid var(--line);background:transparent;color:var(--ink);font-size:20px;line-height:1}.slider-row{display:flex;align-items:center;gap:14px;margin-top:17px}.slider-row input{width:100%;min-width:0;accent-color:var(--teal);cursor:pointer}.slider-counter{font-size:11px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}.lanes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.lane{--accent:var(--violet);--tint:#efebf7;min-width:0;background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:13px;padding:20px 20px 16px;display:flex;flex-direction:column}.lane-ml{--accent:var(--teal);--tint:#e7f2ef}.lane-decision{--accent:var(--amber);--tint:#f8eee3}.lane-top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}.lane-number{font-size:11px;color:var(--accent);font-weight:750;letter-spacing:.08em}.status{font-size:10px;border-radius:20px;background:var(--tint);color:var(--accent);padding:3px 8px;line-height:1.4}.lane[data-status="unavailable"] .status,.lane[data-status="failed"] .status{background:#f1efea;color:#8c7460}.lane h2{font-size:23px;line-height:1.25;letter-spacing:-.03em;overflow-wrap:anywhere}.model-description{color:var(--muted);font-size:11px;line-height:1.6;margin-top:6px;min-height:36px}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:14px 10px;margin:18px 0 23px;padding:15px 0;border-top:1px solid #edf0eb;border-bottom:1px solid #edf0eb}.metric{min-width:0}.metric dt{font-size:10px;color:var(--muted);line-height:1.5}.metric dd{font-size:22px;line-height:1.2;font-weight:650;letter-spacing:-.04em;margin:4px 0 0;font-variant-numeric:tabular-nums}.metric dd small{font-size:11px;letter-spacing:0;font-weight:400;color:var(--muted);margin-left:3px}.prediction-heading{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:13px}.prediction-heading h3{font-size:11px;font-weight:650}.prediction-time{font-size:10px;color:var(--muted);white-space:nowrap}.predictions{flex:1;min-height:160px}.option{margin-bottom:12px}.option-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:11px;line-height:1.45;margin-bottom:4px}.option-name{min-width:0;overflow-wrap:anywhere;color:#56636a}.option.actual .option-name{color:#245d48;font-weight:650}.choice-mark{font-size:10px;display:inline;margin-left:5px;color:#2b7658}.option-value{font-variant-numeric:tabular-nums;white-space:nowrap;color:#536168;font-size:10px}.bar-track{height:7px;background:#f0f1ed;border-radius:5px;overflow:hidden}.bar-fill{height:100%;background:var(--accent);opacity:.72;border-radius:5px;transition:width .18s ease}.option.actual .bar-track{box-shadow:0 0 0 1px #6d9b82}.option.actual .bar-fill{opacity:1}.option.stop .option-name{font-style:italic}.model-footer{color:var(--muted);font-size:10px;line-height:1.6;margin-top:14px;padding-top:11px;border-top:1px solid #edf0eb;overflow-wrap:anywhere}.empty-state{padding:22px 0 18px}.empty-icon{font-size:21px;color:#a39a8d;margin-bottom:9px}.empty-state strong{font-size:13px;font-weight:600}.empty-state p{font-size:11px;line-height:1.8;color:var(--muted);margin-top:7px;overflow-wrap:anywhere}.chart-panel{background:#fff;border:1px solid var(--line);border-radius:14px;margin-top:22px;padding:23px 24px 18px}.section-top{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.section-top h2{font-size:17px;letter-spacing:-.02em}.section-subtitle{font-size:11px;color:var(--muted);line-height:1.7;margin-top:5px}.legend{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:12px;font-size:10px;color:var(--muted);padding-top:4px}.legend span{display:flex;align-items:center;gap:5px}.legend i{width:13px;height:3px;border-radius:3px;display:inline-block}.chart-wrap{margin-top:10px;min-width:0}svg{display:block;width:100%;height:auto;overflow:visible}svg text{font-family:var(--sans);font-size:11px;fill:#7a858a}.chart-note{color:var(--muted);font-size:10px;line-height:1.7;margin:8px 0 0}.limits{margin-top:20px;padding:0 2px}.limits h2{font-size:12px;margin-bottom:8px}.limits ul{margin:0;padding-left:17px;font-size:11px;color:var(--muted);line-height:1.8;overflow-wrap:anywhere}.technical{margin-top:20px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:11px}.technical summary{cursor:pointer;width:fit-content;max-width:100%}.technical pre{font-size:10px;background:#eaece7;padding:12px;border-radius:8px;max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;margin-bottom:0}.footer{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;margin-top:22px;color:#88928f;font-size:10px;letter-spacing:.02em}.no-script{padding:15px;margin:15px 0;border:1px solid #dfbd80;background:#fff3dd;border-radius:8px}.hidden{display:none!important}
@media(min-width:1050px){.chart-wrap{max-width:1100px;margin-left:auto;margin-right:auto}}
@media(max-width:850px){main{padding:25px 23px}.lanes{gap:10px}.lane{padding:17px 13px 14px}.lane h2{font-size:20px}.metric dd{font-size:20px}.prediction-heading{align-items:flex-start;flex-direction:column;gap:2px}.section-top{flex-wrap:wrap}.legend{justify-content:flex-start}.brand{font-size:10px}.brand b{font-size:17px}}
@media(max-width:640px){main{padding:20px 16px 26px}header{margin-bottom:28px;gap:10px}.brand span{display:none}.brand b{margin-right:0}.language{font-size:12px;padding:6px 11px}.intro{font-size:13px}h1{font-size:33px;max-width:95%}.provenance{padding:12px;gap:9px}.context{margin-bottom:20px;gap:6px}.context span{font-size:10px}.step-control{padding:15px}.step-header{gap:9px}.step-title{font-size:16px}.step-buttons{gap:5px}.step-buttons button{width:30px;height:30px;font-size:18px}.lanes{grid-template-columns:minmax(0,1fr);gap:14px}.lane{padding:18px}.lane h2{font-size:24px}.model-description{min-height:0}.metrics{grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:16px 0 20px;padding:13px 0}.metric dd{font-size:21px}.metric dt{font-size:9px}.metric dd small{font-size:9px;margin-left:1px}.prediction-heading{flex-direction:row;align-items:baseline}.predictions{min-height:0}.chart-panel{padding:18px 13px 15px}.chart-wrap{margin-top:14px}.section-top h2{font-size:16px}.legend{gap:10px}.footer{margin-top:18px;font-size:9px}.empty-state{padding-top:8px}}
@media(prefers-reduced-motion:reduce){.bar-fill{transition:none}}
.option-name{display:flex;align-items:center;gap:8px}.option-thumbnail{width:34px;height:34px;flex:0 0 34px;object-fit:cover;border-radius:5px;background:#f0f1ed}.card-copy{min-width:0}.card-scene{display:block;font-size:10px;font-weight:400;color:var(--muted);margin-top:1px}
@media(max-width:640px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:13px 10px}.metric dt{font-size:10px}.metric dd{font-size:23px}}
.methodology{margin-top:12px;font-size:11px;color:var(--muted)}.methodology summary{cursor:pointer;width:fit-content;max-width:100%}.methodology ul{margin-top:8px}
.ml-variants{display:flex;gap:3px;margin-top:10px;border:1px solid var(--line);border-radius:7px;padding:3px;background:#f6f8f4}.ml-variants button{flex:1;min-width:0;border:0;border-radius:4px;padding:5px 7px;font-size:11px;background:transparent;color:var(--muted)}.ml-variants button[aria-pressed="true"]{background:#e2efe9;color:#245e53;font-weight:650}.policy-status{font-size:10px;color:var(--muted);line-height:1.6;margin-top:7px}
</style>
</head>
<body>
<main>
<header><div class="brand"><b>NORN</b><span>MODEL COMPARISON / 01</span></div><button type="button" class="language" id="language"></button></header>
<section aria-labelledby="page-title"><p class="eyebrow" id="eyebrow"></p><h1 id="page-title">__TITLE__</h1><p class="intro" id="intro"></p>
<div class="provenance" id="provenance"><span class="provenance-mark" aria-hidden="true">!</span><div><strong id="provenance-title"></strong><p id="provenance-note"></p></div></div>
<div class="context" id="context"></div></section>
<noscript><p class="no-script">请启用 JavaScript 查看交互式预测对比。 / Enable JavaScript to explore this comparison.</p></noscript>
<section class="step-control" aria-labelledby="step-title"><div class="step-header"><div><p class="step-kicker" id="step-kicker"></p><h2 class="step-title" id="step-title"></h2><p class="observed"><span id="observed-label"></span><span class="target-chip" id="target-choice"></span></p></div><div class="step-buttons"><button type="button" id="previous">&#8249;</button><button type="button" id="next">&#8250;</button></div></div><div class="slider-row"><input type="range" id="step" min="0" max="0" value="0"><output class="slider-counter" id="step-counter" for="step"></output></div></section>
<section class="lanes" aria-label="Model comparison">__LANES__</section>
<section class="chart-panel" aria-labelledby="chart-title"><div class="section-top"><div><h2 id="chart-title"></h2><p class="section-subtitle" id="chart-subtitle"></p></div><div class="legend" id="legend"></div></div><div class="chart-wrap" id="chart-wrap"></div><p class="chart-note" id="chart-note"></p></section>
<section class="limits" id="limits-section"><h2 id="limits-title"></h2><ul id="reading-notes"></ul><details class="methodology" id="limits-details"><summary id="limits-details-title"></summary><ul id="limits"></ul></details></section>
<details class="technical"><summary id="technical-title"></summary><pre id="technical-json"></pre></details>
<footer class="footer"><span>NORN / FAST COMPARISON</span><span id="footer-note"></span></footer>
</main>
<script type="application/json" id="comparison-data">__REPORT_DATA__</script>
<script>
"use strict";
(() => {
  const report = JSON.parse(document.getElementById("comparison-data").textContent);
  const modelIds = ["heuristic", "ml", "decision"];
  const colors = {heuristic:"#7a65b2",ml:"#2b887c",decision:"#bd7940",stop_only:"#8b928e"};
  const models = report.models || {};
  const decisions = report.decisions || [];
  const $ = id => document.getElementById(id);
  let language = document.documentElement.lang;
  let selected = 0;
  let mlVariant = models.ml_policy ? "ml_policy" : "ml";
  const activeId = id => id === "ml" ? mlVariant : id;
  const words = {
    en: {
      eyebrow:"HELD-OUT CHOICE EXPERIMENT", title:"One history. Three fast thinkers.",
      intro:"Each held-out round is scored once: predict the first card ultimately kept, or stopping if no card was kept. Move through the rounds to compare each model’s probabilities with the observed choice.",
      demoTitle:"Synthetic fixture · demonstration only", demoNote:"These choices are synthetic. This run checks the comparison pipeline; it does not establish real-world accuracy or a winning model.",
      realTitle:"Your journal · chronological holdout", realNote:"Models see the same training prefix. The later choices are held out for this comparison; this single journal does not establish general accuracy.",
      training:"Training rounds", steps:"Held-out decisions", prefix:"Prefix hash", stepKicker:"SAME HISTORY · SAME OPTIONS", step:"Decision", round:"round", observed:"Observed choice", stop:"Stop selecting", previous:"Previous decision", next:"Next decision", slider:"Choose a held-out decision", noDecisions:"No held-out decisions", noTarget:"No observed choice", ready:"Measured", unavailable:"Unavailable", failed:"Failed", heuristic:"Fixed rules using the training history.", ml:"A learned choice model fitted on the same history.", decision:"Pretrained decision model via the configured service.", nll:"Mean NLL ↓", accuracy:"Top-1 match", coverage:"Coverage", elapsed:"Run time", prediction:"First-kept choice / stop probability", actual:"observed", probability:"probability", noModel:"No predictions available", unavailableNote:"This model was not available for this run. No scores or probabilities have been substituted.", failedNote:"This model failed during the run. Its available measurements remain in the report.", failedStep:"Prediction failed for this decision", failedStepNote:"This decision is unscored. Move to another decision to inspect a successful prediction.", missingStep:"No prediction for this decision", noPredictionNote:"This decision is unscored for this model.", prep:"Preparation", calls:"Provider calls", cardsNll:"Cards-only NLL", brier:"Brier", chartTitle:"Accumulated surprise", chartSubtitle:"Cumulative negative log-likelihood across held-out decisions. Lower is better; the vertical guide marks the selected decision.", chartX:"Decision", chartY:"Cumulative NLL", chartNote:"Each line uses only successful predictions with a target probability. Failed or missing decisions create gaps; coverage matters when comparing lines. Observed probabilities are floored at 10⁻¹² for this chart.", noChart:"No successful predictions to chart.", limits:"How to read this run", technical:"Technical details · report JSON", footer:"Local report · no network requests", perPrediction:"prediction", noMetric:"Not measured", oneDecision:"Only one held-out decision", reason:"Reason", unknown:"Unknown"
    },
    zh: {
      eyebrow:"留出选择实验", title:"三种直觉，同一组选择",
      intro:"每个留出轮次只验证一次：预测首张最终保留的卡片，或未保留卡片时的停止选择。拖动时间轴，查看模型概率与真实选择。",
      demoTitle:"合成样例 · 仅用于演示", demoNote:"这里的选择是合成数据。本次运行用于验证对比流程，不能据此判断真实准确率或模型优劣。",
      realTitle:"你的选择记录 · 按时间留出验证", realNote:"所有模型使用相同的训练前缀，后续选择仅用于验证。单份记录的结果不能代表普遍准确率。",
      training:"训练轮数", steps:"留出决策", prefix:"前缀哈希", stepKicker:"相同历史 · 相同选项", step:"第", round:"轮", observed:"真实选择", stop:"停止选择", previous:"上一步", next:"下一步", slider:"选择留出决策步骤", noDecisions:"没有留出决策", noTarget:"没有真实选择", ready:"已测量", unavailable:"未启用", failed:"运行失败", heuristic:"依据训练历史，使用固定规则预测。", ml:"使用相同历史拟合的选择模型。", decision:"预训练决策模型，经配置的服务返回概率。", nll:"平均 NLL ↓", accuracy:"首选命中率", coverage:"覆盖率", elapsed:"预测总耗时", prediction:"首张保留卡片／停止概率", actual:"真实选择", probability:"概率", noModel:"暂无预测结果", unavailableNote:"本次运行未能使用此模型，没有填入替代分数或概率。", failedNote:"此模型运行失败。报告保留已经产生的有效测量。", failedStep:"这一步预测失败", failedStepNote:"这一步未计分。可切换步骤查看其他有效预测。", missingStep:"这一步没有预测", noPredictionNote:"此模型在这一步未计分。", prep:"准备耗时", calls:"服务调用", cardsNll:"仅卡片 NLL", brier:"Brier", chartTitle:"累计预测损失", chartSubtitle:"逐步累加留出决策的负对数似然，越低越好。竖线标出当前步骤。", chartX:"决策步骤", chartY:"累计 NLL", chartNote:"仅对包含真实选择概率的成功预测计分；失败或缺失步骤处曲线断开。比较曲线时也应考虑覆盖率。本图将真实选择概率的下限设为 10⁻¹²。", noChart:"暂无可绘制的有效预测。", limits:"如何理解本次结果", technical:"技术详情 · 报告 JSON", footer:"本地报告 · 无网络请求", perPrediction:"本步耗时", noMetric:"未测量", oneDecision:"仅有一个留出决策", reason:"原因", unknown:"未知"
    }
  };
  Object.assign(words.en, {
    variantLabel:"Personal ML version", original:"Original", guarded:"Shrinkage + fallback",
    mlPolicy:"A shrinkage model enabled only by evidence from earlier training choices.",
    baseline:"Stop-only reference", baselineGain:"Gain vs reference", baselineNll:"Reference NLL",
    fallbackNote:"Content evidence is insufficient: cards have equal utility; the learned stop habit remains.",
    activeNote:"Training-prefix content evidence passed the fixed gate; shrunken image preferences are enabled.",
    originalNote:"Original model retained as an unchanged reference."
  });
  Object.assign(words.zh, {
    variantLabel:"个体学习版本", original:"原版", guarded:"收缩与回退",
    mlPolicy:"仅依据较早训练选择的证据，决定是否启用收缩后的图片偏好。",
    baseline:"停止习惯基线", baselineGain:"相对基线增益", baselineNll:"基线 NLL",
    fallbackNote:"图片偏好证据不足：卡片效用等同，保留学到的停止习惯。",
    activeNote:"训练前缀的图片证据通过固定门槛，已启用收缩后的偏好。",
    originalNote:"原版模型作为未改动的参照保留。"
  });
  const t = key => words[language][key];
  function text(id, value) { $(id).textContent = value; }
  function node(tag, className, content) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (content !== undefined) el.textContent = content;
    return el;
  }
  function number(value, digits = 3) {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
  }
  function percent(value) {
    return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
  }
  function duration(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value.toFixed(1)} ms`;
  }
  function cardLabel(id) { return id === "__STOP__" ? t("stop") : String(id); }
  function label(id) {
    if (id === "heuristic") return language === "zh" ? "固定规则" : "Fixed rules";
    if (id === "ml") {
      const base = language === "zh" ? "个体学习" : "Personal ML";
      return models.ml_policy ? `${base} · ${t(mlVariant === "ml_policy" ? "guarded" : "original")}` : base;
    }
    if (id === "stop_only") return t("baseline");
    return String((models[id] || {}).label || "Decision model");
  }
  function status(id) { return (models[activeId(id)] || {}).status || "unavailable"; }
  function validPrediction(id, decision) {
    const prediction = (decision.predictions || {})[activeId(id)];
    return prediction && prediction.status === "ok" && prediction.probabilities && typeof prediction.probabilities === "object" ? prediction : null;
  }
  function empty(container, title, message) {
    const box = node("div", "empty-state");
    box.append(node("div", "empty-icon", "○"), node("strong", "", title), node("p", "", message));
    container.append(box);
  }
  function renderContext() {
    const context = $("context"); context.replaceChildren();
    const training = report.training || {};
    for (const [key, value] of [["training",training.rounds || 0],["steps",decisions.length],["prefix",String(training.prefix_hash || "—").slice(0,12)]]) {
      const item = node("span", "", t(key)); item.append(node("strong", "", value)); context.append(item);
    }
  }
  function renderMetrics(lane, id) {
    const model = models[activeId(id)] || {};
    const summary = model.summary || {};
    const measured = status(id) !== "unavailable" && Number(summary.decision_count) > 0;
    const metrics = lane.querySelector(".metrics"); metrics.replaceChildren();
    const values = [
      ["nll", measured ? number(summary.all_nll) : "—"],
      ["accuracy", measured ? percent(summary.top1_accuracy) : "—"],
      ["coverage", status(id) === "unavailable" ? "—" : percent(summary.coverage)],
      ["elapsed", status(id) === "unavailable" ? "—" : duration(summary.elapsed_ms)]
    ];
    for (const [key, value] of values) {
      const metric = node("dl", "metric"); metric.style.margin = "0";
      metric.append(node("dt", "", t(key)), node("dd", "", value)); metrics.append(metric);
    }
    const footer = lane.querySelector(".model-footer");
    if (status(id) === "unavailable") {
      footer.textContent = t("noMetric");
    } else {
      const parts = [`${t("prep")} ${duration(model.prepare_ms)}`, `${t("calls")} ${summary.provider_calls ?? "—"}`];
      if (measured && summary.cards_nll != null) parts.push(`${t("cardsNll")} ${number(summary.cards_nll)}`);
      if (measured && summary.brier != null) parts.push(`${t("brier")} ${number(summary.brier)}`);
      const reference = ((models.stop_only || {}).summary || {}).all_nll;
      if (measured && typeof reference === "number") {
        parts.push(`${t("baselineNll")} ${number(reference)}`);
        if (typeof summary.gain_over_stop_only_nats === "number")
          parts.push(`${t("baselineGain")} ${number(summary.gain_over_stop_only_nats)} nats`);
      }
      footer.textContent = parts.join(" · ");
    }
  }
  function renderVariant() {
    const available = !!models.ml_policy;
    $("ml-variants").classList.toggle("hidden", !available);
    $("policy-status").classList.toggle("hidden", !available);
    $("ml-variants").setAttribute("aria-label", t("variantLabel"));
    text("ml-original", t("original")); text("ml-guarded", t("guarded"));
    $("ml-original").setAttribute("aria-pressed", String(mlVariant === "ml"));
    $("ml-guarded").setAttribute("aria-pressed", String(mlVariant === "ml_policy"));
    const guard = ((models.ml_policy || {}).metadata || {}).guard || {};
    text("policy-status", t(mlVariant === "ml" ? "originalNote" :
      guard.status === "active" ? "activeNote" : "fallbackNote"));
  }
  function renderLane(id, decision) {
    const lane = document.querySelector(`[data-model="${id}"]`);
    const currentStatus = status(id);
    lane.querySelector("h2").textContent = label(id);
    lane.dataset.status = currentStatus;
    lane.querySelector(".status").textContent = t(currentStatus === "ok" ? "ready" : currentStatus === "failed" ? "failed" : "unavailable");
    lane.querySelector(".model-description").textContent = t(id === "ml" && mlVariant === "ml_policy" ? "mlPolicy" : id);
    lane.querySelector(".prediction-heading h3").textContent = t("prediction");
    const time = lane.querySelector(".prediction-time"); time.textContent = "";
    renderMetrics(lane, id);
    const container = lane.querySelector(".predictions"); container.replaceChildren();
    if (currentStatus === "unavailable") {
      empty(container, t("noModel"), t("unavailableNote"));
      const metadata = (models[activeId(id)] || {}).metadata || {};
      const reason = metadata.reason || metadata.error;
      if (reason) container.querySelector(".empty-state").append(node("p", "", `${t("reason")}: ${String(reason).slice(0,300)}`));
      return;
    }
    if (!decision) { empty(container, t("noDecisions"), t("noPredictionNote")); return; }
    const prediction = validPrediction(id, decision);
    if (!prediction) {
      const step = (decision.predictions || {})[activeId(id)];
      empty(container, t(step && step.status === "failed" ? "failedStep" : "missingStep"), t(step && step.status === "failed" ? "failedStepNote" : "noPredictionNote"));
      return;
    }
    time.textContent = `${duration(prediction.elapsed_ms)} / ${t("perPrediction")}`;
    const options = [...new Set([...(decision.candidate_ids || []), "__STOP__"])];
    for (const optionId of options) {
      const probability = prediction.probabilities[optionId];
      const usable = typeof probability === "number" && Number.isFinite(probability) && probability >= 0 && probability <= 1;
      const actual = optionId === decision.target;
      const row = node("div", `option${actual ? " actual" : ""}${optionId === "__STOP__" ? " stop" : ""}`);
      const head = node("div", "option-head");
      const name = node("span", "option-name");
      const card = (report.cards || {})[optionId] || {};
      // Only embedded raster images are allowed: report input cannot initiate a request.
      if (typeof card.thumbnail === "string" && /^data:image\/(?:webp|png|jpeg|gif);base64,[A-Za-z0-9+/=]+$/.test(card.thumbnail)) {
        const image = node("img", "option-thumbnail"); image.src = card.thumbnail; image.alt = ""; name.append(image);
      }
      const copy = node("span", "card-copy", cardLabel(optionId));
      if (actual) copy.append(node("span", "choice-mark", `✓ ${t("actual")}`));
      if (typeof card.scene === "string" && card.scene) copy.append(node("span", "card-scene", card.scene));
      name.append(copy);
      head.append(name, node("span", "option-value", usable ? percent(probability) : "—"));
      const track = node("div", "bar-track");
      const fill = node("div", "bar-fill"); fill.style.width = `${usable ? probability * 100 : 0}%`;
      track.append(fill); row.append(head, track);
      row.setAttribute("aria-label", `${cardLabel(optionId)}: ${usable ? percent(probability) : t("noMetric")}${actual ? `, ${t("actual")}` : ""}`);
      container.append(row);
    }
  }
  function renderStep() {
    renderVariant();
    const decision = decisions[selected];
    text("step-kicker", t("stepKicker"));
    text("step-title", decision ? (language === "zh" ? `第 ${selected + 1} 步 · 第 ${decision.round} 轮` : `Decision ${selected + 1} · round ${decision.round}`) : t("noDecisions"));
    text("observed-label", t("observed")); text("target-choice", decision ? cardLabel(decision.target) : t("noTarget"));
    text("step-counter", `${decisions.length ? selected + 1 : 0} / ${decisions.length}`);
    $("step").max = String(Math.max(0, decisions.length - 1)); $("step").value = String(selected);
    $("step").disabled = decisions.length < 2; $("step").setAttribute("aria-label", t("slider"));
    $("step").setAttribute("aria-valuetext", $("step-title").textContent);
    $("previous").disabled = selected === 0; $("next").disabled = selected >= decisions.length - 1;
    $("previous").setAttribute("aria-label", t("previous")); $("next").setAttribute("aria-label", t("next"));
    for (const id of modelIds) renderLane(id, decision);
    renderChart();
  }
  function svgNode(tag, attrs = {}, content) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    if (content !== undefined) el.textContent = content;
    return el;
  }
  function renderChart() {
    const wrap = $("chart-wrap"); wrap.replaceChildren();
    const legend = $("legend"); legend.replaceChildren();
    const series = [];
    for (const id of [...modelIds, "stop_only"]) {
      if (status(id) === "unavailable") continue;
      let total = 0, count = 0;
      const points = decisions.map((decision, i) => {
        const prediction = validPrediction(id, decision);
        const probability = prediction && prediction.probabilities[decision.target];
        if (typeof probability !== "number" || !Number.isFinite(probability) || probability < 0 || probability > 1) return null;
        total -= Math.log(Math.max(1e-12, probability)); count++;
        return {i, value:total};
      });
      if (count) series.push({id, points});
    }
    if (!series.length) { empty(wrap, t("noChart"), t("noPredictionNote")); return; }
    for (const line of series) {
      const item = node("span"); const swatch = node("i"); swatch.style.backgroundColor = colors[line.id];
      item.append(swatch, node("span", "", label(line.id))); legend.append(item);
    }
    const width = Math.max(240, Math.min(1040, wrap.clientWidth || 1040));
    const height = width < 500 ? 225 : 245, left = 38, right = 16, top = 16, bottom = 36;
    const chartWidth = width - left - right, chartHeight = height - top - bottom;
    const maxValue = Math.max(1, ...series.flatMap(line => line.points.filter(Boolean).map(p => p.value)));
    const ceiling = Math.ceil(maxValue / 4) * 4;
    const x = i => left + (decisions.length > 1 ? i / (decisions.length - 1) : .5) * chartWidth;
    const y = value => top + (1 - value / ceiling) * chartHeight;
    const svg = svgNode("svg", {viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":t("chartTitle")});
    svg.append(svgNode("title", {}, t("chartSubtitle")));
    for (let tick = 0; tick <= 4; tick++) {
      const value = ceiling * tick / 4, py = y(value);
      svg.append(svgNode("line", {x1:left,y1:py,x2:width-right,y2:py,stroke:"#e9ece6","stroke-width":1}));
      svg.append(svgNode("text", {x:left-10,y:py+4,"text-anchor":"end"}, number(value, value % 1 ? 1 : 0)));
    }
    const ticks = [...new Set([0, Math.floor((decisions.length-1)/4), Math.floor((decisions.length-1)/2), Math.floor(3*(decisions.length-1)/4), decisions.length-1])];
    for (const tick of ticks) svg.append(svgNode("text", {x:x(tick),y:height-15,"text-anchor":"middle"}, String(tick+1)));
    const guideX = x(selected);
    svg.append(svgNode("line", {x1:guideX,y1:top,x2:guideX,y2:height-bottom,stroke:"#8f9b98","stroke-width":1,"stroke-dasharray":"4 5"}));
    for (const line of series) {
      let path = "", connected = false;
      for (const point of line.points) {
        if (!point) { connected = false; continue; }
        path += `${connected ? "L" : "M"}${x(point.i).toFixed(2)},${y(point.value).toFixed(2)} `;
        connected = true;
      }
      svg.append(svgNode("path", {d:path.trim(),fill:"none",stroke:colors[line.id],"stroke-width":2.4,"stroke-dasharray":line.id === "stop_only" ? "5 5" : "none","stroke-linecap":"round","stroke-linejoin":"round","data-series":activeId(line.id)}));
      // A single successful point still needs a visible mark.
      const count = line.points.filter(Boolean).length;
      for (let i = 0; i < line.points.length; i++) {
        const point = line.points[i];
        const isolated = !line.points[i-1] && !line.points[i+1];
        if (!point || (i !== selected && count > 1 && !isolated)) continue;
        const dot = svgNode("circle", {cx:x(point.i),cy:y(point.value),r:i === selected ? 4.5 : 3,fill:colors[line.id],stroke:"white","stroke-width":2});
        dot.append(svgNode("title", {}, `${label(line.id)} · ${point.i+1}: ${number(point.value)}`)); svg.append(dot);
      }
    }
    wrap.append(svg);
  }
  function render() {
    document.documentElement.lang = language;
    document.title = `Norn · ${t("title")}`;
    text("language", language === "zh" ? "English" : "中文");
    $("language").setAttribute("aria-label", language === "zh" ? "Switch to English" : "切换为中文");
    text("eyebrow", t("eyebrow")); text("page-title", t("title")); text("intro", t("intro"));
    const fixture = (report.provenance || {}).kind !== "user_journal";
    $("provenance").classList.toggle("fixture", fixture);
    text("provenance-title", t(fixture ? "demoTitle" : "realTitle"));
    const note = (report.provenance || {}).note;
    text("provenance-note", `${t(fixture ? "demoNote" : "realNote")}${!fixture && note ? ` ${String(note)}` : ""}`);
    renderContext();
    document.querySelector(".lanes").setAttribute("aria-label", language === "zh" ? "模型对比" : "Model comparison");
    text("chart-title", t("chartTitle")); text("chart-subtitle", t("chartSubtitle")); text("chart-note", t("chartNote"));
    text("limits-title", t("limits"));
    const readingNotes = $("reading-notes"); readingNotes.replaceChildren();
    const notes = language === "zh" ? ["损失越低，预测越贴近留出选择。", "解读质量仍需人工评价。"] : ["Lower loss means better prediction of held-out choices.", "Reading quality still needs human review."];
    for (const note of notes) readingNotes.append(node("li", "", note));
    text("limits-details-title", language === "zh" ? "更多评估说明" : "More evaluation notes");
    const limits = $("limits"); limits.replaceChildren();
    for (const limit of report.limits || []) limits.append(node("li", "", String(limit)));
    $("limits-details").classList.toggle("hidden", !(report.limits || []).length);
    text("technical-title", t("technical")); text("technical-json", JSON.stringify(report, null, 2));
    text("footer-note", t("footer"));
    renderStep();
  }
  $("language").addEventListener("click", () => {language = language === "zh" ? "en" : "zh"; render();});
  $("ml-original").addEventListener("click", () => {mlVariant = "ml"; renderStep();});
  $("ml-guarded").addEventListener("click", () => {if (models.ml_policy) mlVariant = "ml_policy"; renderStep();});
  $("step").addEventListener("input", event => {selected = Number(event.target.value); renderStep();});
  $("previous").addEventListener("click", () => {selected = Math.max(0, selected-1); renderStep();});
  $("next").addEventListener("click", () => {selected = Math.min(decisions.length-1, selected+1); renderStep();});
  let resizeFrame = 0;
  window.addEventListener("resize", () => {cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(renderChart);});
  render();
})();
</script>
</body>
</html>
'''
