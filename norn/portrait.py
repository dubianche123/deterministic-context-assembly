"""A visit's visual keepsakes, separate from the frozen preference inference.

Every displayed card and label comes from a final retained selection. The
artifact describes those contents; it never promotes them to a learned trait.
The optional reading intent changes product prose, not evidence or fitting.
"""

from collections import Counter
from copy import deepcopy
import json

from .core import engine_module


PORTRAIT_VERSION = "norn.product-portrait.v1"
READING_INTENT_VERSION = "norn.reading-intent.visual-portrait.v1"
_MAX_CARDS = 6
_MAX_MOTIFS = 4
_GENERIC_MOTIFS = {"写实影像", "艺术绘画"}


def _selected_rows(profile, deck):
    if profile.get("deck") != deck.label:
        raise ValueError("Portrait belongs to another deck version")
    analysis = profile["analysis"]
    rows = analysis["attribution"]["selections"]
    if type(rows) is not list:
        raise ValueError("Portrait needs final retained selection evidence")
    result, seen = [], set()
    for row in sorted(rows, key=lambda row: (row["round"], row["event_id"], row["card_id"])):
        card_id = row["card_id"]
        if card_id not in deck.cards:
            raise ValueError("Portrait contains a card outside the sealed deck")
        if card_id not in seen:
            result.append(row)
            seen.add(card_id)
    if len(result) != analysis["total_selections"]:
        raise ValueError("Portrait selection count differs from the profile")
    return result


def _labels(card, language):
    if language == "en":
        # These are the source's English metadata, not invented personality
        # tags or a translation guessed from a latent feature coordinate.
        return list(dict.fromkeys(str(card[key]).replace("_", " ").strip()
                                  for key in ("group", "style") if card.get(key)))
    labels = list(dict.fromkeys(str(value) for value in card.get("motifs", []) if value))
    specific = [label for label in labels if label not in _GENERIC_MOTIFS]
    return specific or labels


def _parts(profile, deck, language):
    if language not in ("zh", "en"):
        raise ValueError("Portrait language must be zh or en")
    rows = _selected_rows(profile, deck)
    all_labels = [label for row in rows for label in _labels(deck.cards[row["card_id"]], language)]
    counts = Counter(all_labels)
    order = {label: index for index, label in enumerate(dict.fromkeys(all_labels))}
    motifs = sorted(counts, key=lambda label: (-counts[label], order[label]))[:_MAX_MOTIFS]
    observed = []
    for row in rows[:_MAX_CARDS]:
        card = deck.cards[row["card_id"]]
        labels = _labels(card, language)
        observed.append({"card_id": row["card_id"], "round": row["round"], "event_id": row["event_id"],
                         "scene": str(card.get("scene", ""))[:240], "motifs": labels[:_MAX_MOTIFS],
                         "description": (str(card.get("scene", ""))[:180] if language == "en" else
                                         "、".join(labels[:3]))})
    return rows, motifs, observed


def build_portrait(profile, deck, language="zh"):
    """Return a small deterministic artifact containing only observed contents."""
    rows, motifs, _ = _parts(profile, deck, language)
    english = language == "en"
    if rows:
        title = " · ".join(motifs[:2]) or ("Your visual keepsakes" if english else "这一回留下的画面")
        if english:
            title = title[:1].upper() + title[1:]
        subtitle = ("A small collection from this visit" if english else "这一回，你留下的画面")
    else:
        title = "A page still unwritten" if english else "还空着的一页"
        subtitle = ("No pictures kept this visit" if english else "这次还没有留下画面")
    return {"schema_version": PORTRAIT_VERSION, "title": title, "subtitle": subtitle,
            "card_ids": [row["card_id"] for row in rows[:_MAX_CARDS]], "motifs": motifs,
            "selected_count": len(rows), "round_count": profile["analysis"]["total_rounds"],
            "scope": "this_visit_observed_only", "language": language}


def build_reading_intent(profile, deck, language="zh"):
    """Bounded source facts for a product reading, kept outside the sealed profile."""
    _, _, observed = _parts(profile, deck, language)
    return {"schema_version": READING_INTENT_VERSION, "kind": "visual_keepsake",
            "portrait": build_portrait(profile, deck, language), "observed": observed,
            "scope": "Describe contents actually kept this visit; do not infer a stable personal preference."}


def portrait_style(language):
    return ("Warm, specific English, 70–130 words in two short paragraphs. Describe two actual visual details "
            "when present, followed by one small comparison to explore next. The UI supplies a separate title; "
            "write prose without headings or lists. Keep observations limited to this visit."
            if language == "en" else
            "温暖、具体，中文120至220字，两个短段落。材料足够时写两个真实画面细节，"
            "结尾给一个下次可试的小对比。界面另有标题，正文不写标题或列表。观察只限本次。")


def _intent(context):
    block = context.get("block", {}) if type(context) is dict else {}
    intent = block.get("reading_intent")
    if (type(intent) is not dict or intent.get("schema_version") != READING_INTENT_VERSION
            or block.get("analysis_kind") != "learned_preference"
            or type(intent.get("observed")) is not list or len(intent["observed"]) > _MAX_CARDS
            or type(intent.get("portrait")) is not dict
            or intent["portrait"].get("schema_version") != PORTRAIT_VERSION):
        raise ValueError("Unsupported product reading intent")
    return intent


def build_analysis_prompt(context):
    """Keep the native wire format and canonical data; replace only the user task."""
    legacy = engine_module("session_context")
    prompt = legacy.build_analysis_prompt(context)
    _intent(context)
    if context["block"].get("language") == "en":
        task = ("[Visual portrait task v1]\nWrite a small visual keepsake of this visit, grounded in reading_intent.observed. "
                "Start with what is present in the pictures actually kept, using two concrete visual details when available; "
                "with one picture, use only its real contents. End with one small comparison the visitor could explore next, "
                "as an invitation rather than a prediction. If no pictures were kept, acknowledge the empty collection warmly "
                "without inventing a preference or a reason. Keep the scope to this visit. The title is supplied separately "
                "by the UI: write prose without a title, headings or lists. Do not open with statistical uncertainty or a "
                "comparison to reference raters, and do not paraphrase evidence_clarity as the purpose of the reading. "
                "The evidence gate still applies: contents alone do not establish a learned tendency or personality. "
                "Do not fill empty drawn_to or next_to_try fields with invented findings; a neutral visual comparison is "
                "allowed as exploration. Do not mention internal field names, metrics, models or algorithms. "
                "Follow the frozen style and answer questions about the limits honestly.")
    else:
        task = ("[视觉小传任务 v1]\n依据 reading_intent.observed 写一份这次选图的视觉小传。"
                "从真正留下的画面写起，材料足够时写两个具体画面细节；只有一张就只写它真实带有的内容。"
                "结尾给一个下次可试的小对比，作为邀请而非预测。没有留下画面时，温和承认这一页还空着，"
                "不编造偏好或原因。观察只限本次；标题由界面单独提供，正文不写标题、列表。"
                "不要以统计不确定性或与参考评分者的差异开场，也不要把转述 evidence_clarity 当成此次解读的卖点。"
                "证据门槛仍然适用：画面里有什么，不等于已经学到了稳定倾向或性格。"
                "drawn_to、next_to_try 为空时不得补造发现，可给中性的画面对比作为探索。"
                "不提内部字段名、指标、模型或算法。遵循冻结的风格与篇幅，被问到边界时如实解释。")
    prompt["messages"][-1] = {"role": "user", "content": task}
    return prompt


def build_dialogue_prompt(context, history, user_message, turn_count, max_turns):
    """Validate the real native prefix before reusing legacy turn validation.

    The temporary projection is never sent or stored. The returned request
    retains the actual product task and every native historical byte.
    """
    initial = build_analysis_prompt(context)["messages"]
    if type(history) is not list or history[:len(initial)] != initial:
        raise ValueError("History must preserve the original product reading request and answer")
    legacy = engine_module("session_context")
    legacy_initial = legacy.build_analysis_prompt(context)["messages"]
    projected = deepcopy(legacy_initial) + deepcopy(history[len(initial):])
    prompt = legacy.build_dialogue_prompt(context, projected, user_message, turn_count, max_turns)
    prompt["messages"] = deepcopy(history) + [prompt["messages"][-1]]
    return prompt


def intent_from_prompt(prompt):
    """Recognize only the new product intent; other mock rendering stays intact."""
    system = prompt.get("system", "")
    if type(system) is not str or "<SessionProfileBlock>\n" not in system:
        return None
    try:
        block = json.loads(system.split("<SessionProfileBlock>\n", 1)[1].split("\n</SessionProfileBlock>", 1)[0])
    except (ValueError, IndexError):
        return None
    intent = block.get("reading_intent") if type(block) is dict else None
    return intent if type(intent) is dict and intent.get("schema_version") == READING_INTENT_VERSION else None


def mock_text(intent, *, task, user_message, turn_count, max_turns, analysis):
    """Offline product prose from observed metadata; never claim inferred traits."""
    portrait, observed = intent["portrait"], intent["observed"]
    english = portrait["language"] == "en"
    details = [row["description"] for row in observed[:2] if row.get("description")]
    if not observed:
        return ("This page has no pictures yet. There is no choice to interpret this time. "
                "On another visit, start with a picture that makes you pause, then see what you want to place beside it."
                if english else "这一页还没有留下画面，这次暂时没有具体的选择可供解读。下次可以从一张让你愿意停一停的图开始，看看你会想把什么画面放在它旁边。")
    pictures = ("; and ".join(details) if english else "；以及".join(details))
    if task == "analysis":
        return (f"In this visit's collection: {pictures}. These are details from what you kept, "
                "a small visual record you can return to.\n\nOn another visit, try placing similar scenes with different light "
                "or colour side by side. Notice what you want to keep together, or what you would replace. "
                "That comparison can open a new page without turning this one into a definition of you."
                if english else
                f"这一回留下的画面里，有{pictures}。这些具体的色彩、场景和表现方式，组成了可以回看的这一页。"
                "它记录的是这次你愿意留下什么，每一张也可以和旁边的画面重新搭配。\n\n"
                "下次可以把相近的场景、不同的明暗或色彩放在一起，看看你想保留原来的组合，还是换掉其中一张。"
                "这一页是一个继续看图的起点，不必把它当作对自己的定论。")
    lowered = user_message.lower()
    if any(word in lowered for word in ("撤回", "改选", "犹豫", "undo", "deselect", "hesitat")):
        count = analysis.get("choice_revision", {}).get("total", 0)
        text = (f"This visit recorded {count} deselections. A change of selection records an action, "
                "not its psychological cause. The collection above contains only what was finally kept."
                if english else f"这次记录里有{count}次撤回。改选记录的是当时的操作，不能据此确定心理原因；上面这一页只收进最终留下的画面。")
    elif any(word in lowered for word in ("为什么", "依据", "证据", "准确", "性格", "why", "evidence", "accur", "personality")):
        text = (f"A checkable starting point is a picture you actually kept: {details[0]}. "
                "The visual portrait describes its contents within this visit. It does not by itself establish "
                "a stable preference, a personality type, or a reliable prediction of your next choice."
                if english else f"可以核对的起点是你真正留下的画面，例如{details[0]}。视觉小传只描述这次画面里的内容；"
                "这些内容本身不能证明稳定偏好、人格类型，也不能保证猜中下一次选择。")
    else:
        text = (f"Start with what you kept here: {details[0]}. Compare a similar scene in different light or colours, "
                "and notice whether you keep it beside the original. That is an exploration you can try, rather than "
                "a prediction that this visit has established."
                if english else f"可以从这次留下的{details[0]}开始，把相近场景里不同的明暗或色彩放在一起，"
                "看看你是否仍想把它和原来的画面留在同一页。这是一次可试的探索，不是这份记录已经确认的预测。")
    if turn_count >= max_turns:
        text += (" The next page can wait for another visit." if english else "下一页，留给下一次看图。")
    return text
