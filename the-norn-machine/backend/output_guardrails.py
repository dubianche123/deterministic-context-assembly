"""Deterministic, sentence-level output checks shared by every provider.

These checks protect a small product contract; they are not a content safety
classifier. Normal metaphors are allowed. No extra model repair call is made.
"""

import re


FALLBACK_TEXT = ("这一次回声没有形成可以核对的回答。你的选择仍然保留着。"
                 "你可以重新尝试，或先把这次选择当作一个留给自己的小问题。")

_RULES = (
    ("internal_field", re.compile(r"(?i)(?<![a-z])(?:E[_/]I|S[_/]N|T[_/]F|J[_/]P)(?![a-z])|"
                                  r"\b(?:coords_avg|mbti_type|top_traits|top_motifs|rationale_snippets|"
                                  r"normalized_weight|hesitation_multiplier|profile_hash|confidence_ei|"
                                  r"confidence_sn|confidence_tf|confidence_jp|schema_version|algorithm_version|"
                                  r"metadata_version|context_hash|events_hash|canonical_block|total_jp_delta|"
                                  r"summation_delta|rounding_delta)\b", re.I)),
    ("internal_type", re.compile(r"\bMBTI\b|(?<![A-Za-z])(?:[EI][SN][TF][JP])(?![A-Za-z])", re.I)),
    ("internal_axis_values", re.compile(r"(?:四[轴维]|维度|置信度|坐标(?:值)?|均值|差值)\s*"
                                        r"(?:分别|依次|为|是|达到|等于|[:：=])[^。！？\n]{0,24}?[-+]?\d|"
                                        r"\b(?:axis|coordinate|dimension|confidence)\s*(?:values?|scores?)?\s*"
                                        r"(?:are|is|[:=])[^.!?\n]{0,24}?[-+]?\d", re.I)),
    ("cross_user_data", re.compile(r"(?:上[一个位]+|前[一个位]+)(?:玩家|测试者|用户|受试者|个人|人)?|"
                                    r"(?:其他|别的)(?:玩家|测试者|用户|受试者)|"
                                    r"(?:玩家|测试者|用户|受试者)(?:数据|样本|平均)|"
                                    r"(?:大多数人|其他人|别人)[^。！？\n]{0,12}(?:百分位|平均分|分数|数据)|"
                                    r"上一条数据|(?:previous|other)\s+(?:users?|players?|participants?)|"
                                    r"(?:超过|高于|低于)[^。！？\n]{0,8}\d+(?:\.\d+)?%[^。！？\n]{0,8}(?:用户|玩家|人)", re.I)),
)

_CROSS_USER_DENIAL = re.compile(r"(?:不会|不能|无法|没有|并未|从未|拒绝|不应|不该)[^。！？]{0,20}"
                                r"(?:比较|对比|读取|查看|访问|使用|掌握|获取|拥有)|"
                                r"(?:不必|不要|无需)[^。！？]{0,12}(?:与|和|拿|比)|"
                                r"\b(?:cannot|can't|do not|don't|will not|won't|have not|haven't|never)\b"
                                r"[^.!?\n]{0,80}\b(?:compare|access|read|use|see|view)\b", re.I)
_SENTENCES = re.compile(r".+?(?:[。！？!?；;]+|\.(?=\s|$)|\n+|$)", re.S)
_CLAUSES = re.compile(r"[，,]|但是|不过|然而|但|\b(?:but|however)\b|"
                      # Temporal "cannot/not yet access" is still one denial.
                      r"(?<!cannot )(?<!can't )(?<!not )(?<!never )(?<!n't )\byet\b", re.I)


def _reasons(sentence):
    reasons = []
    for code, pattern in _RULES:
        if pattern.search(sentence):
            if code == "cross_user_data":
                clauses = _CLAUSES.split(sentence)
                offending = [clause for clause in clauses if pattern.search(clause)
                             and not _CROSS_USER_DENIAL.search(clause)]
                if not offending:
                    continue
            reasons.append(code)
    return reasons


def output_reason_codes(text):
    """Return stable reason codes, including removable sentence violations."""
    if not isinstance(text, str) or not text.strip():
        return ["empty_output"]
    return sorted({code for sentence in _SENTENCES.findall(text) for code in _reasons(sentence)})


def validate_output(text):
    """Keep useful clean sentences; otherwise return one deterministic fallback.

    A sanitized result is usable and not blocked. An invalid result is blocked.
    Usage belongs to the original provider response and must remain untouched.
    """
    if not isinstance(text, str) or not text.strip():
        return {"text": FALLBACK_TEXT, "status": "invalid_output",
                "reason_codes": ["empty_output"], "blocked": True}
    sentences = _SENTENCES.findall(text.strip())
    reasons = sorted({code for sentence in sentences for code in _reasons(sentence)})
    if not reasons:
        return {"text": text.strip(), "status": "ok", "reason_codes": [], "blocked": False}
    clean = "".join(sentence for sentence in sentences if not _reasons(sentence)).strip()
    # Require meaningful surviving prose, not punctuation or a token fragment.
    meaningful = re.sub(r"[\W_]+", "", clean, flags=re.UNICODE)
    if len(meaningful) >= 16 and not output_reason_codes(clean):
        return {"text": clean, "status": "sanitized", "reason_codes": reasons, "blocked": False}
    return {"text": FALLBACK_TEXT, "status": "invalid_output",
            "reason_codes": reasons, "blocked": True}
