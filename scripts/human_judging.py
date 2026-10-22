"""Human blind judging of saved v4/v5 readings, to calibrate the automatic judge.

Build a self-contained page from saved readings (no model or network calls):

    python3 scripts/human_judging.py

Open benchmarks/results/human-judging-v1.html in a browser, judge every pair,
press 导出 and import the downloaded file:

    python3 scripts/human_judging.py --import ~/Downloads/human-judging-answers.json

The page shows each pair as 甲 (A) and 乙 (B) only. Which side is v5 is a
seeded, balanced assignment recorded in the key file next to the page; do not
open the key before judging. The import unblinds the answers and compares them
with the saved MiMo verdicts.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prose_eval import (FIXTURES, PROMPT_V5, PROMPT_VERSION as PROMPT_V4, PROSE_SCORING_VERSION, SCORES,  # noqa: E402
                                _write_new_json, digest, facts_for, pair_outcome, valid_verdict,
                                verdict_issue)

RESULTS = ROOT / "benchmarks" / "results"
SESSIONS = RESULTS / "prose-eval-v2.json.sessions.json"
VERDICTS = RESULTS / "prose-eval-v3-mimo-consistency.json"
PAGE = RESULTS / "human-judging-v1.html"
KEY = RESULTS / "human-judging-v1.key.json"
OUTPUT = RESULTS / "human-judging-v1.json"
SEED = "norn.human-judging.v1"
SIDES = ("A", "B")  # A = 甲 (left), B = 乙 (right); the same letters the judge uses
LABELS = {"A": "甲", "B": "乙"}
DECIDED = ("v5", "v4", "tie")


def text_digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rank(seed, purpose, name):
    return hashlib.sha256(f"{seed}\0{purpose}\0{name}".encode()).hexdigest()


def _other(side):
    return "B" if side == "A" else "A"


def _relative(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def build_pairs(sessions):
    """Every (model, fixture) whose v4 and v5 readings are both ok, in a stable order."""
    index = {}
    for row in sessions:
        identity = (row["model"], row["prompt_version"], row["fixture"])
        if identity in index:
            raise ValueError(f"Duplicate saved reading {identity}")
        index[identity] = row
    pairs = []
    for model, fixture in sorted({(model, fixture) for model, _, fixture in index}):
        old, new = (index.get((model, version, fixture)) for version in (PROMPT_V4, PROMPT_V5))
        if old and new and old["turns"][0]["status"] == "ok" and new["turns"][0]["status"] == "ok":
            pairs.append({"pair": f"{model}/fixture-{fixture}", "model": model, "fixture": fixture,
                          "texts": {"v4": old["turns"][0]["text"], "v5": new["turns"][0]["text"]}})
    return pairs


def assign(pairs, seed):
    """Seeded and balanced: half the pairs show v5 as 甲, presented in an independent order.

    Sides and order use separate hashes, so neither can be guessed from the
    other; with an odd count the extra pair shows v5 as 甲.
    """
    by_side = sorted(pairs, key=lambda pair: _rank(seed, "side", pair["pair"]))
    v5_side = {pair["pair"]: "A" if rank < math.ceil(len(pairs) / 2) else "B"
               for rank, pair in enumerate(by_side)}
    shown = sorted(pairs, key=lambda pair: _rank(seed, "order", pair["pair"]))
    return [{"position": position + 1, "blind_id": "p" + _rank(seed, "id", pair["pair"])[:10],
             **pair, "v5_side": v5_side[pair["pair"]]} for position, pair in enumerate(shown)]


def blind_texts(entry):
    """Map A/B to the two readings of one assigned pair, always keyed A then B.

    Key order is visible in the page source, so it must not follow the assignment.
    """
    return {side: entry["texts"]["v5" if side == entry["v5_side"] else "v4"] for side in SIDES}


def page_data(assigned, facts, seed):
    """Everything the page shows; no model, prompt version or side mapping."""
    rows = []
    for entry in assigned:
        texts = blind_texts(entry)
        rows.append({"id": entry["blind_id"], "position": entry["position"], "facts": facts[entry["pair"]],
                     "texts": texts, "inputs": {"facts": text_digest(facts[entry["pair"]]),
                                                **{side: text_digest(texts[side]) for side in SIDES}}})
    return {"schema_version": "norn.human-judging.page.v1", "seed": seed,
            "page_digest": digest({"seed": seed, "pairs": rows}), "pairs": rows}


def judge_cache_keys(entry, facts):
    """The cache keys prose_eval gave MiMo's two verdicts for exactly these inputs."""
    texts = entry["texts"]
    return {v4_side: digest({"model": entry["model"], "fixture": entry["fixture"], "v4_position": v4_side,
                             "facts": facts, "first": first, "second": second})
            for v4_side, first, second in (("A", texts["v4"], texts["v5"]), ("B", texts["v5"], texts["v4"]))}


def key_record(assigned, page, facts, verdicts, *, sessions_path, verdicts_path):
    keys = {row.get("cache_key") for row in verdicts}
    pairs = []
    for entry, shown in zip(assigned, page["pairs"]):
        expected = judge_cache_keys(entry, facts[entry["pair"]])
        matched = None if not keys or None in keys else all(value in keys for value in expected.values())
        pairs.append({"blind_id": entry["blind_id"], "position": entry["position"], "pair": entry["pair"],
                      "model": entry["model"], "fixture": entry["fixture"], "v5_side": entry["v5_side"],
                      "inputs": {**shown["inputs"], "v4": text_digest(entry["texts"]["v4"]),
                                 "v5": text_digest(entry["texts"]["v5"])},
                      "judge_inputs_match": matched})
    return {"schema_version": "norn.human-judging.key.v1", "seed": page["seed"],
            "page_digest": page["page_digest"],
            "assignment": "balanced: pairs sorted by sha256(seed, 'side', pair); the first ceil(n/2) show v5 as 甲 (A); "
                          "presentation order sorted by sha256(seed, 'order', pair)",
            "sessions": {"path": _relative(sessions_path), "sha256": file_digest(sessions_path)},
            "verdicts": {"path": _relative(verdicts_path), "sha256": file_digest(verdicts_path)},
            "pairs": pairs}


# Answer rows mirror the judge's verdict JSON (better / grounded / resonance /
# clarity / overclaim / reason over sides A and B), so prose_eval's validity and
# consistency rules apply to them unchanged.
PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>解读盲评</title>
<style>
:root { color-scheme: light dark; --bg: #f6f4ef; --card: #fffdf8; --ink: #23201b; --muted: #6f685d;
  --line: #ddd5c6; --accent: #7a4b1f; --accent-ink: #fff; --warn: #9a3d12; --ok: #2f6b3a; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #17150f; --card: #211e17; --ink: #ece6da; --muted: #a99f8d; --line: #3a3427;
    --accent: #d39a5c; --accent-ink: #17150f; --warn: #f0a070; --ok: #8fc79a; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.7 -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; }
header { position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line); }
.bar { max-width: 1120px; margin: 0 auto; padding: 10px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.bar h1 { font-size: 18px; margin: 0 auto 0 0; }
.progress { color: var(--muted); font-variant-numeric: tabular-nums; }
button { font: inherit; border: 1px solid var(--line); background: var(--card); color: var(--ink);
  border-radius: 8px; padding: 6px 14px; cursor: pointer; }
button.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
main { max-width: 1120px; margin: 0 auto; padding: 16px; }
.intro, .pair { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 20px; margin-bottom: 20px; }
.intro p, .intro ul { margin: 6px 0; }
.pair h2 { font-size: 17px; margin: 0 0 8px; display: flex; gap: 10px; align-items: baseline; }
.state { font-size: 13px; color: var(--muted); font-weight: normal; }
.state.done { color: var(--ok); }
details { margin-bottom: 12px; }
summary { cursor: pointer; color: var(--muted); }
.facts { white-space: pre-wrap; font-size: 14px; line-height: 1.65; color: var(--muted); margin: 8px 0 0;
  padding: 10px 12px; border-left: 3px solid var(--line); max-height: 18em; overflow: auto; }
.sides { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 760px) { .sides { grid-template-columns: 1fr; } }
.side { border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; }
.side h3 { margin: 0 0 6px; font-size: 20px; color: var(--accent); }
.reading { white-space: pre-wrap; flex: 1; margin: 0 0 12px; }
fieldset { border: 0; padding: 0; margin: 6px 0; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
legend { float: left; width: 6.5em; padding: 0; color: var(--muted); font-size: 14px; }
label.chip { display: inline-flex; align-items: center; justify-content: center; min-width: 34px; padding: 2px 10px;
  border: 1px solid var(--line); border-radius: 999px; cursor: pointer; font-size: 14px; }
label.chip input { position: absolute; opacity: 0; pointer-events: none; }
label.chip:has(input:checked) { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
label.chip:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
.overall { margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 10px; }
.overall legend { width: auto; margin-right: 10px; color: var(--ink); font-size: 15px; }
textarea { width: 100%; min-height: 3.2em; font: inherit; font-size: 14px; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; margin-top: 6px; }
.hint { color: var(--warn); font-size: 14px; min-height: 1.4em; margin: 4px 0 0; }
.scale { color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<header><div class="bar">
  <h1>解读盲评</h1>
  <span class="progress" id="progress"></span>
  <button class="primary" id="export" type="button">导出 JSON</button>
  <button id="load" type="button">载入 JSON</button>
  <input type="file" id="load-file" accept=".json,application/json" hidden>
  <button id="clear" type="button">清空本页答案</button>
</div></header>
<main>
<section class="intro">
  <p>每组是同一次图片选择得到的两段解读，只标为<strong>甲</strong>和<strong>乙</strong>。「事实」列出生成解读时系统提供的全部材料，引用其中内容不算编造；事实里没有的具体画面、元素或经历才算编造。系统的视觉统计是技术描述，引用它不算编造，但会让文字不够清楚。</p>
  <ul>
    <li><strong>贴合事实</strong>（grounded）：具体、只引用事实且不编造。</li>
    <li><strong>共鸣</strong>（resonance）：读来有温度、像在说我。</li>
    <li><strong>清楚</strong>（clarity）：清楚自然、没有术语或数字指标。</li>
    <li><strong>说过头</strong>（overclaim）：是否把有限线索说成确定的性格或命运。</li>
  </ul>
  <p class="scale">评分 1 = 很差，3 = 一般，5 = 很好。答案自动保存在本浏览器；全部完成后点「导出 JSON」，再用 <code>--import</code> 导入。中途换浏览器可先导出，再「载入 JSON」继续。请不要在评完前打开 key 文件。</p>
  <p class="hint" id="storage" role="status"></p>
</section>
<div id="pairs"></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("data").textContent);
  var SIDES = ["A", "B"], LABEL = {A: "甲", B: "乙"};
  var SCORES = [["grounded", "贴合事实"], ["resonance", "共鸣"], ["clarity", "清楚"]];
  var STORE = "norn.human-judging:" + DATA.page_digest;
  var answers = {};
  var stored = true;
  try { answers = JSON.parse(localStorage.getItem(STORE) || "{}") || {}; } catch (error) { answers = {}; stored = false; }

  function blank() {
    var row = {better: null, overclaim: {A: null, B: null}, reason: "", updated_at: null};
    SCORES.forEach(function (score) { row[score[0]] = {A: null, B: null}; });
    return row;
  }
  function answer(id) {
    if (!answers[id] || typeof answers[id] !== "object") answers[id] = blank();
    return answers[id];
  }
  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(answers)); } catch (error) { stored = false; }
    document.getElementById("storage").textContent = stored ? "" :
      "这个浏览器不允许本页保存答案：刷新或关闭就会丢失。请随时导出 JSON，之后可用「载入 JSON」恢复。";
  }
  function complete(row) {
    if (!row || ["A", "B", "tie"].indexOf(row.better) < 0) return false;
    return SIDES.every(function (side) {
      return typeof row.overclaim[side] === "boolean" && SCORES.every(function (score) {
        return typeof row[score[0]][side] === "number";
      });
    });
  }
  // Same rule as prose_eval.verdict_issue; shown as a hint only, the answer stays as given.
  function issue(row) {
    if (!complete(row)) return null;
    var scores = {A: SCORES.map(function (s) { return row[s[0]].A; }), B: SCORES.map(function (s) { return row[s[0]].B; })};
    function dominates(high, low) {
      var same = scores[high].every(function (v, i) { return v === scores[low][i]; });
      return !same && scores[high].every(function (v, i) { return v >= scores[low][i]; });
    }
    if (row.better === "A" || row.better === "B") {
      var loser = row.better === "A" ? "B" : "A";
      var excused = row.overclaim[loser] && !row.overclaim[row.better];
      if (dominates(loser, row.better) && !excused) return "总体选了" + LABEL[row.better] + "，但三项评分都不高于" + LABEL[loser] + "。确认无误可保留。";
    } else {
      for (var i = 0; i < 2; i++) {
        var side = SIDES[i], other = SIDES[1 - i];
        var strict = scores[side].every(function (v, j) { return v > scores[other][j]; });
        if (strict && !(row.overclaim[side] && !row.overclaim[other])) return "选了差不多，但" + LABEL[side] + "三项评分都更高。确认无误可保留。";
      }
    }
    return null;
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function chips(name, legend, options, current, onChange) {
    var set = el("fieldset");
    set.appendChild(el("legend", {}, legend));
    options.forEach(function (option) {
      var label = el("label", {"class": "chip"});
      var input = el("input", {type: "radio", name: name, value: String(option[0])});
      input.checked = current === option[0];
      input.addEventListener("change", function () { onChange(option[0]); });
      label.appendChild(input);
      label.appendChild(document.createTextNode(option[1]));
      set.appendChild(label);
    });
    return set;
  }

  var refreshers = [];
  function refresh() {
    var done = DATA.pairs.filter(function (pair) { return complete(answers[pair.id]); }).length;
    document.getElementById("progress").textContent = "已完成 " + done + " / " + DATA.pairs.length;
    refreshers.forEach(function (fn) { fn(); });
  }
  function update(id, change) {
    var row = answer(id);
    change(row);
    row.updated_at = new Date().toISOString();
    save();
    refresh();
  }

  var root = document.getElementById("pairs");
  function render() {
    root.textContent = "";
    refreshers = [];
    DATA.pairs.forEach(function (pair) {
      var row = answer(pair.id);
      var card = el("section", {"class": "pair", id: pair.id});
      var title = el("h2", {}, "第 " + pair.position + " 组");
      var state = el("span", {"class": "state"});
      title.appendChild(state);
      card.appendChild(title);
      var details = el("details", {open: ""});
      details.appendChild(el("summary", {}, "事实"));
      details.appendChild(el("div", {"class": "facts"}, pair.facts));
      card.appendChild(details);
      var sides = el("div", {"class": "sides"});
      SIDES.forEach(function (side) {
        var box = el("div", {"class": "side"});
        box.appendChild(el("h3", {}, LABEL[side]));
        box.appendChild(el("div", {"class": "reading"}, pair.texts[side]));
        SCORES.forEach(function (score) {
          box.appendChild(chips(pair.id + "-" + score[0] + "-" + side, score[1], [[1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"]],
            row[score[0]][side], function (value) { update(pair.id, function (r) { r[score[0]][side] = value; }); }));
        });
        box.appendChild(chips(pair.id + "-overclaim-" + side, "说过头", [[true, "是"], [false, "否"]],
          row.overclaim[side], function (value) { update(pair.id, function (r) { r.overclaim[side] = value; }); }));
        sides.appendChild(box);
      });
      card.appendChild(sides);
      var overall = el("div", {"class": "overall"});
      overall.appendChild(chips(pair.id + "-better", "哪段更好？", [["A", "甲"], ["B", "乙"], ["tie", "差不多"]],
        row.better, function (value) { update(pair.id, function (r) { r.better = value; }); }));
      var hint = el("p", {"class": "hint", role: "status"});
      overall.appendChild(hint);
      var note = el("textarea", {placeholder: "备注（可选）：为什么这样选、哪里编造或说过头"});
      note.value = row.reason || "";
      note.addEventListener("input", function () { update(pair.id, function (r) { r.reason = note.value; }); });
      overall.appendChild(note);
      card.appendChild(overall);
      refreshers.push(function () {
        var current = answers[pair.id];
        var done = complete(current);
        state.textContent = done ? "已完成" : "未完成";
        state.className = "state" + (done ? " done" : "");
        hint.textContent = issue(current) || "";
      });
      root.appendChild(card);
    });
    refresh();
  }

  document.getElementById("export").addEventListener("click", function () {
    var rows = DATA.pairs.map(function (pair) {
      var row = answers[pair.id] || blank();
      var out = {pair: pair.id, position: pair.position, better: row.better};
      SCORES.forEach(function (score) { out[score[0]] = {A: row[score[0]].A, B: row[score[0]].B}; });
      out.overclaim = {A: row.overclaim.A, B: row.overclaim.B};
      out.reason = row.reason || "";
      out.inputs = pair.inputs;
      out.updated_at = row.updated_at;
      return out;
    });
    var payload = {schema_version: "norn.human-judging.answers.v1", seed: DATA.seed, page_digest: DATA.page_digest,
      sides: LABEL, exported_at: new Date().toISOString(),
      complete: rows.filter(complete).length, pairs: rows.length, rows: rows};
    var blob = new Blob([JSON.stringify(payload, null, 2) + "\\n"], {type: "application/json"});
    var link = el("a", {href: URL.createObjectURL(blob), download: "human-judging-answers.json"});
    document.body.appendChild(link);
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 0);
  });
  var picker = document.getElementById("load-file");
  document.getElementById("load").addEventListener("click", function () { picker.click(); });
  picker.addEventListener("change", function () {
    var file = picker.files[0];
    picker.value = "";
    if (!file) return;
    file.text().then(function (text) {
      var payload = JSON.parse(text);
      if (payload.page_digest !== DATA.page_digest) throw new Error("这个文件来自另一版页面，不能载入。");
      var known = {};
      DATA.pairs.forEach(function (pair) { known[pair.id] = true; });
      payload.rows.forEach(function (row) {
        if (!known[row.pair]) return;
        var copy = {better: row.better, overclaim: {A: row.overclaim.A, B: row.overclaim.B},
          reason: row.reason || "", updated_at: row.updated_at || null};
        SCORES.forEach(function (score) { copy[score[0]] = {A: row[score[0]].A, B: row[score[0]].B}; });
        answers[row.pair] = copy;
      });
      save();
      render();
    }).catch(function (error) { window.alert(error.message || "无法读取这个文件。"); });
  });
  document.getElementById("clear").addEventListener("click", function () {
    if (!window.confirm("清空本页全部答案？导出过的文件不受影响。")) return;
    answers = {};
    try { localStorage.removeItem(STORE); } catch (error) { /* storage blocked */ }
    render();
  });
  render();
  if (!stored) save();
})();
</script>
</body>
</html>
"""


def render_page(page):
    data = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
    # Inline JSON must not close the script element or open a comment.
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return PAGE_TEMPLATE.replace("__DATA__", data)


def _write_once(path, text):
    """Rebuilding is deterministic; never replace a page or key that differs."""
    path = Path(path)
    if path.exists():
        if path.read_text(encoding="utf-8") == text:
            return "unchanged"
        raise FileExistsError(f"{_relative(path)} exists with different content; choose a new path")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(text)
    return "written"


def journals():
    from norn.simulate import simulate_records
    return [simulate_records(1, visits=(1, 1), rounds=(low, high), seed=seed)[0]["journal"]
            for low, high, seed in FIXTURES]


def build(sessions_path=SESSIONS, verdicts_path=VERDICTS, *, seed=SEED, page_path=PAGE, key_path=KEY):
    sessions = json.loads(Path(sessions_path).read_text(encoding="utf-8"))
    verdicts = json.loads(Path(verdicts_path).read_text(encoding="utf-8"))["verdicts"]
    assigned = assign(build_pairs(sessions), seed)
    if not assigned:
        raise ValueError("No pair has two ok readings")
    rendered = [facts_for(journal, algorithm_version=PROSE_SCORING_VERSION) for journal in journals()]
    facts = {entry["pair"]: rendered[entry["fixture"]] for entry in assigned}
    page = page_data(assigned, facts, seed)
    key = key_record(assigned, page, facts, verdicts, sessions_path=sessions_path, verdicts_path=verdicts_path)
    states = {"page": _write_once(page_path, render_page(page)),
              "key": _write_once(key_path, json.dumps(key, ensure_ascii=False, indent=2) + "\n")}
    return page, key, states


def check_key(key, sessions):
    """The key must be the seeded assignment of these exact saved readings and
    the frozen scoring-v4 facts; returns assigned entries by blind id, each with the cache
    keys the judge's two verdicts must carry for exactly these inputs."""
    expected = {entry["blind_id"]: entry for entry in assign(build_pairs(sessions), key["seed"])}
    if {row["blind_id"] for row in key["pairs"]} != set(expected):
        raise ValueError("The key does not cover the pairs in the saved sessions")
    rendered = [facts_for(journal, algorithm_version=PROSE_SCORING_VERSION) for journal in journals()]
    for row in key["pairs"]:
        entry = expected[row["blind_id"]]
        shown = {side: text_digest(text) for side, text in blind_texts(entry).items()}
        if (row["pair"], row["v5_side"]) != (entry["pair"], entry["v5_side"]) or any(
                row["inputs"][version] != text_digest(entry["texts"][version]) for version in ("v4", "v5")) or any(
                row["inputs"][side] != shown[side] for side in SIDES):
            raise ValueError(f"Key entry {row['blind_id']} does not match the saved readings")
        facts = rendered[entry["fixture"]]
        if row["inputs"]["facts"] != text_digest(facts):
            raise ValueError(f"Key entry {row['blind_id']} was built from other facts; rebuild the page")
        entry["judge_cache_keys"] = judge_cache_keys(entry, facts)
    return expected


def unblind(answer, entry):
    """A human answer as a prose_eval verdict row; None when the answer is incomplete."""
    if not valid_verdict(answer):
        return None
    v5_side = entry["v5_side"]
    v4_side = _other(v5_side)
    better = answer["better"]
    return {"model": entry["model"], "fixture": entry["fixture"], "v4_position": v4_side,
            "v5_score": 0.5 if better == "tie" else float(better == v5_side),
            **{name: {"v4": answer[name][v4_side], "v5": answer[name][v5_side]} for name in (*SCORES, "overclaim")},
            # A person's choice stands; a contradiction with their own scores is flagged, not dropped.
            "reason": answer["reason"], "issue": verdict_issue(answer), "raw_better": better, "judge": "human"}


def human_outcome(row):
    return None if row is None else {1.0: "v5", 0.0: "v4", 0.5: "tie"}[row["v5_score"]]


def judge_outcome(rows):
    return "missing" if not rows else pair_outcome(rows)


def agreement(human, judged):
    if human is None:
        return "human_incomplete"
    if judged in DECIDED:
        return "agree" if human == judged else "disagree"
    return {"order_disagreement": "judge_order_disagreement", "split": "judge_split",
            "missing": "judge_missing", "input_mismatch": "judge_input_mismatch"}.get(judged, "judge_abstained")


def judge_rows_for(entry, judge_rows):
    """The judge's verdicts on exactly this pair's facts and readings.

    Rows are matched by cache key, not by model and fixture: a verdicts file
    from another run can name the same pair while judging other inputs.
    Returns (rows, match) with match None when the judge has no row for the pair.
    """
    expected = entry["judge_cache_keys"]
    named = [row for row in judge_rows if row["model"] == entry["model"] and row["fixture"] == entry["fixture"]]
    rows = [row for row in named if row.get("cache_key") == expected.get(row.get("v4_position"))]
    if len({row["v4_position"] for row in rows}) != len(rows):
        raise ValueError(f"Repeated judge verdicts for {entry['pair']}")
    if not named:
        return [], None
    return (sorted(rows, key=lambda row: row["v4_position"]), True) if len(rows) == len(named) else ([], False)


def judge_overclaim(rows, version):
    """The judge's flag when both orders say the same, else 'inconsistent' or None."""
    flags = [row["overclaim"][version] for row in rows if isinstance(row.get("overclaim"), dict)]
    if len(flags) != 2:
        return None
    return flags[0] if flags[0] == flags[1] else "inconsistent"


def compare(entries, human_rows, judge_rows):
    """Per-pair comparison of one person's unblinded answers with the judge's verdict rows."""
    pairs = []
    for entry in entries:
        human = human_rows.get(entry["blind_id"])
        rows, matched = judge_rows_for(entry, judge_rows)
        outcome = human_outcome(human)
        judged = "input_mismatch" if matched is False else judge_outcome(rows)
        pairs.append({
            "position": entry["position"], "blind_id": entry["blind_id"], "pair": entry["pair"],
            "model": entry["model"], "fixture": entry["fixture"], "v5_side": entry["v5_side"],
            "judge_inputs_match": matched,
            "human": outcome, "human_issue": human and human["issue"], "judge": judged,
            "judge_orders": [{"v4_position": row["v4_position"], "raw_better": row.get("raw_better"),
                              "v5_score": row.get("v5_score"), "issue": row.get("issue")} for row in rows],
            "agreement": agreement(outcome, judged),
            # Each judge call on its own, so position bias inside a disagreement stays visible.
            "orders_agreeing": None if human is None else sum(
                row.get("v5_score") == human["v5_score"] for row in rows if row.get("v5_score") is not None),
            "orders_counted": sum(row.get("v5_score") is not None for row in rows),
            "overclaim": {version: {"human": None if human is None else human["overclaim"][version],
                                    "judge": judge_overclaim(rows, version)} for version in ("v4", "v5")},
        })
    return pairs


def summarize(pairs, human_rows):
    answered = [pair for pair in pairs if pair["human"] is not None]
    decided = [pair for pair in pairs if pair["agreement"] in ("agree", "disagree")]
    agree = sum(pair["agreement"] == "agree" for pair in decided)
    flags = [flag for pair in answered for flag in pair["overclaim"].values()]
    compared = [flag for flag in flags if isinstance(flag["judge"], bool)]
    rows = [row for row in human_rows.values() if row is not None]
    return {
        "unit": "independent session pair; one human order, two judge orders",
        "pairs": len(pairs), "human_answered": len(answered),
        "human_outcomes": {name: sum(pair["human"] == name for pair in pairs) for name in DECIDED},
        "human_v5_win_rate": (sum({"v5": 1.0, "v4": 0.0, "tie": 0.5}[pair["human"]] for pair in answered) / len(answered)
                              if answered else None),
        "human_self_contradictions": sum(row["issue"] is not None for row in rows),
        "human_mean_scores": {name: {version: statistics.fmean(row[name][version] for row in rows)
                                     for version in ("v4", "v5")} for name in SCORES} if rows else {},
        "human_overclaim_rate": {version: statistics.fmean(float(row["overclaim"][version]) for row in rows)
                                 for version in ("v4", "v5")} if rows else {},
        "judge_outcomes": {name: sum(pair["judge"] == name for pair in pairs)
                           for name in (*DECIDED, "order_disagreement", "split", "abstain", "missing",
                                        "input_mismatch")},
        "agreement": {
            "both_decided": len(decided), "agree": agree, "disagree": len(decided) - agree,
            "rate": agree / len(decided) if decided else None,
            "judge_order_disagreement": sum(pair["agreement"] == "judge_order_disagreement" for pair in pairs),
            "judge_split": sum(pair["agreement"] == "judge_split" for pair in pairs),
            "judge_abstained": sum(pair["agreement"] == "judge_abstained" for pair in pairs),
            "judge_missing": sum(pair["agreement"] == "judge_missing" for pair in pairs),
            "judge_input_mismatch": sum(pair["agreement"] == "judge_input_mismatch" for pair in pairs),
            "human_incomplete": sum(pair["agreement"] == "human_incomplete" for pair in pairs),
        },
        "order_agreement": {"judge_orders": sum(pair["orders_counted"] for pair in answered),
                            "agree_with_human": sum(pair["orders_agreeing"] for pair in answered)},
        "overclaim_agreement": {
            "compared": len(compared), "agree": sum(flag["human"] == flag["judge"] for flag in compared),
            "rate": sum(flag["human"] == flag["judge"] for flag in compared) / len(compared) if compared else None,
            "judge_inconsistent": sum(flag["judge"] == "inconsistent" for flag in flags),
            "judge_missing": sum(flag["judge"] is None for flag in flags),
            "table": {f"human_{'yes' if human else 'no'}_judge_{'yes' if judged else 'no'}":
                      sum(flag["human"] is human and flag["judge"] is judged for flag in compared)
                      for human in (True, False) for judged in (True, False)},
        },
    }


def import_answers(answers_path, sessions_path=SESSIONS, verdicts_path=VERDICTS, *, key_path=KEY):
    answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    if answers.get("schema_version") != "norn.human-judging.answers.v1":
        raise ValueError("Not a human-judging answers file")
    if (answers.get("seed"), answers.get("page_digest")) != (key["seed"], key["page_digest"]):
        raise ValueError("The answers come from a different page than this key")
    assigned = check_key(key, json.loads(Path(sessions_path).read_text(encoding="utf-8")))
    verdicts_report = json.loads(Path(verdicts_path).read_text(encoding="utf-8"))
    entries = {row["blind_id"]: {**row, "judge_cache_keys": assigned[row["blind_id"]]["judge_cache_keys"]}
               for row in key["pairs"]}
    human_rows = {}
    for answer in answers.get("rows", []):
        entry = entries.get(answer.get("pair"))
        if entry is None or answer["pair"] in human_rows:
            raise ValueError(f"Unknown or repeated pair {answer.get('pair')!r}")
        if answer.get("inputs") != {name: entry["inputs"][name] for name in ("facts", *SIDES)}:
            raise ValueError(f"Pair {answer['pair']} was judged on different facts or readings")
        human_rows[answer["pair"]] = unblind(answer, entry)
    ordered = sorted(entries.values(), key=lambda entry: entry["position"])
    pairs = compare(ordered, human_rows, verdicts_report["verdicts"])
    verdicts = [{**row, "position": entry["position"], "blind_id": entry["blind_id"]}
                for entry in ordered for row in [human_rows.get(entry["blind_id"])] if row is not None]
    return {"schema_version": "norn.human-judging.v1",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": "synthetic sessions from norn.simulate; saved readings, no model calls; one human labeller",
            "judge": verdicts_report.get("judge"),
            "inputs": {"answers_sha256": file_digest(answers_path), "exported_at": answers.get("exported_at"),
                       "seed": key["seed"], "page_digest": key["page_digest"],
                       "key": {"path": _relative(key_path), "sha256": file_digest(key_path)},
                       "sessions": {"path": _relative(sessions_path), "sha256": file_digest(sessions_path)},
                       "verdicts": {"path": _relative(verdicts_path), "sha256": file_digest(verdicts_path),
                                    "same_file_as_key": file_digest(verdicts_path) == key["verdicts"]["sha256"]}},
            "rules": {"human": "one display order; the choice stands even when it contradicts the person's scores "
                               "(flagged as issue)",
                      "judge": "prose_eval.pair_outcome over both display orders; abstentions and "
                               "order disagreements are counted apart from agreement; a verdict counts only "
                               "when its cache key matches this page's facts and readings, otherwise the "
                               "pair is input_mismatch",
                      "overclaim": "per pair and version; the judge's flag counts only when both orders agree"},
            "summary": summarize(pairs, human_rows), "pairs": pairs, "verdicts": verdicts}


def table(report):
    lines = [f"{'#':>2}  {'pair':34} {'human':6} {'judge v4 1st/2nd':17} {'judge pair':19} agreement"]
    for pair in report["pairs"]:
        orders = "/".join({1.0: "v5", 0.0: "v4", 0.5: "tie", None: "-"}[row["v5_score"]] for row in pair["judge_orders"])
        lines.append(f"{pair['position']:>2}  {pair['pair']:34} {pair['human'] or '-':6} {orders or '-':17} "
                     f"{pair['judge']:19} {pair['agreement']}")
    summary = report["summary"]
    agreement = summary["agreement"]
    overclaim = summary["overclaim_agreement"]
    rate = "n/a" if agreement["rate"] is None else f"{agreement['rate']:.0%}"
    lines += ["",
              f"human answered {summary['human_answered']}/{summary['pairs']}: "
              + ", ".join(f"{name} {count}" for name, count in summary["human_outcomes"].items()),
              f"pair agreement {agreement['agree']}/{agreement['both_decided']} ({rate}); "
              f"judge order disagreement {agreement['judge_order_disagreement']}, split {agreement['judge_split']}, "
              f"abstained {agreement['judge_abstained']}, missing {agreement['judge_missing']}, "
              f"other inputs {agreement['judge_input_mismatch']}",
              f"single judge orders agreeing with the human {summary['order_agreement']['agree_with_human']}/"
              f"{summary['order_agreement']['judge_orders']}",
              f"overclaim agreement {overclaim['agree']}/{overclaim['compared']}; "
              f"judge inconsistent across orders {overclaim['judge_inconsistent']}"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--import", dest="answers", type=Path, help="Exported answers JSON to unblind and compare")
    parser.add_argument("--sessions", type=Path, default=SESSIONS, help="Saved readings (prose_eval sessions.json)")
    parser.add_argument("--verdicts", type=Path, default=VERDICTS, help="prose_eval report with the judge's verdicts")
    parser.add_argument("--seed", default=SEED, help="Side-assignment seed (build only)")
    parser.add_argument("--page", type=Path, default=PAGE)
    parser.add_argument("--key", type=Path, default=KEY)
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Import report; never overwritten")
    args = parser.parse_args(argv)
    try:
        if args.answers is None:
            page, key, states = build(args.sessions, args.verdicts, seed=args.seed, page_path=args.page, key_path=args.key)
            mismatched = [row["pair"] for row in key["pairs"] if row["judge_inputs_match"] is False]
            print(json.dumps({"pairs": len(page["pairs"]),
                              "page": {"path": _relative(args.page), "state": states["page"]},
                              "key": {"path": _relative(args.key), "state": states["key"]},
                              "page_digest": page["page_digest"],
                              "judge_inputs_mismatch": mismatched}, ensure_ascii=False, indent=2))
            return 0
        if args.output.exists():
            raise FileExistsError(f"{_relative(args.output)} already exists")
        report = import_answers(args.answers, args.sessions, args.verdicts, key_path=args.key)
        _write_new_json(args.output.resolve(), report)
    except FileExistsError as exc:
        parser.error(f"{exc}. Earlier pages and reports are never replaced; pass --page/--key/--output")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.error(f"{type(exc).__name__}: {exc}")
    print(table(report))
    print(f"\nwrote {_relative(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
