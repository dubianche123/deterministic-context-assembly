#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Norn Plus Image Manager v2 - Curated Metadata Seeder

核心变化：
- seed 不再随机生成，而是读取 config/curated_seed_rules_v2.json
- 16 种人格各有人工策展的视觉逻辑、主体策略、风格池、构图池和 16 个场景
- 默认 256 张 = 16 型 × 16 张，每条记录的 scene_id 唯一
- 增加 audit 命令检查重复 prompt、类型覆盖、主体覆盖和风格覆盖
- 仍然不调用 API，不模拟 ChatGPT 网页点击；只负责本地管理和 Prompt 组装
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Iterable, Optional

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
PROMPT_DIR = OUTPUT_DIR / "prompts"
INBOX_DIR = OUTPUT_DIR / "images_inbox"
FINAL_DIR = OUTPUT_DIR / "images_final"
EXPORT_DIR = OUTPUT_DIR / "exports"
DB_PATH = DATA_DIR / "norn_metadata.jsonl"
RULES_PATH = ROOT / "config" / "curated_seed_rules_v2.json"

STATUS_ORDER = ["draft", "reviewed", "queued", "copied_to_chatgpt", "generated", "rejected"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for p in [DATA_DIR, OUTPUT_DIR, PROMPT_DIR, INBOX_DIR, FINAL_DIR, EXPORT_DIR]:
        p.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path = DB_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"JSONL parse error at line {line_no}: {e}") from e
    return items


def write_jsonl(items: List[Dict[str, Any]], path: Path = DB_PATH) -> None:
    ensure_dirs()
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_rules() -> Dict[str, Any]:
    if not RULES_PATH.exists():
        raise FileNotFoundError(f"Missing rules file: {RULES_PATH}")
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def make_id(i: int) -> str:
    return f"NORN_{i:04d}"


def parse_scene(scene: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "scene_cn": scene["scene_cn"],
        "scene_en": scene["scene_en"],
        "motifs": scene["motifs"],
    }


def build_layer3_poetic(type_code: str, profile: Dict[str, Any], scene: Dict[str, Any],
                        style: str, subject: Dict[str, Any], composition: str) -> List[str]:
    # 故意不用“他/她”，避免把图像锁死成男性单人。
    # 这里是“场景叙事句 / 镜头提示句 / 人格张力句”。
    motifs = "、".join(scene["motifs"][:3])
    role = profile["role_group"]
    count = subject["people_count"]
    if count == 0:
        subject_cn = "画面不需要出现人物，重点应落在空间、物件与痕迹本身"
    elif count == 1:
        subject_cn = "可以出现一个人物，但人物应服务于场景气质，而不是占满画面"
    elif count == 2:
        subject_cn = "双人关系可以出现，但重点是互动张力与位置关系"
    else:
        subject_cn = "多人场面可以出现，但需要让群体结构与人格倾向相互对应"

    return [
        f"{scene['scene_cn']}不是单纯背景，而是 {type_code} 的人格张力被转译成可见空间后的结果。",
        f"镜头应优先呈现{motifs}等意象；{subject_cn}。",
        f"整体气质应贴近{role}组的{ '、'.join(profile['trait_keywords'][:3]) }，使用 {style} 与 {composition} 表达，而不是依赖固定男主角叙事。"
    ]


def build_prompt(item: Dict[str, Any]) -> str:
    subject = item["visual_policy"]
    motifs = ", ".join(item["motifs"])
    traits = ", ".join(item["trait_keywords"])
    return (
        f"{item['style_family']}, {item['palette']}, {item['composition']}. "
        f"Scene: {item['scene_en']}. "
        f"Subject rule: {subject['human_presence']} human presence, "
        f"{subject['subject_mode']}, gender mode {subject['gender_mode']}, "
        f"people count {subject['people_count']}. "
        f"Visual motifs: {motifs}. "
        f"Personality metaphor for {item['mbti_type']} ({item['role_group']}): {traits}; "
        f"{item['visual_rationale']} "
        f"Create one coherent finished illustration, strong visual storytelling, no readable text, no logo, no watermark."
    )


def curated_records(count: int, start_index: int = 1, only_type: Optional[str] = None) -> List[Dict[str, Any]]:
    rules = load_rules()
    type_order = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP",
    ]
    if only_type:
        only_type = only_type.upper()
        if only_type not in rules["types"]:
            raise ValueError(f"Unknown type: {only_type}")
        type_order = [only_type]

    records: List[Dict[str, Any]] = []
    # 不随机：按类型顺序 + 场景顺序确定生成。
    # 默认 256 时，每个类型 16 条，正好覆盖每个类型的 16 个场景。
    idx = start_index
    per_type_cursor = defaultdict(int)

    while len(records) < count:
        for type_code in type_order:
            if len(records) >= count:
                break

            profile = rules["types"][type_code]
            cursor = per_type_cursor[type_code]
            scene = parse_scene(profile["scenes"][cursor % len(profile["scenes"])])
            style = profile["style_cycle"][cursor % len(profile["style_cycle"])]
            subject = profile["subject_cycle"][cursor % len(profile["subject_cycle"])]
            palette = profile["palette_cycle"][cursor % len(profile["palette_cycle"])]
            composition = profile["composition_cycle"][cursor % len(profile["composition_cycle"])]

            item = {
                "image_id": make_id(idx),
                "schema_version": "2.0-curated",
                "image_url": "",
                "local_path": "",
                "mbti_type": type_code,
                "role_group": profile["role_group"],
                "layer1_coords": profile["layer1_coords"],
                "trait_keywords": profile["trait_keywords"],
                "visual_rationale": profile["visual_rationale"],
                "scene_id": scene["scene_id"],
                "scene_cn": scene["scene_cn"],
                "scene_en": scene["scene_en"],
                "motifs": scene["motifs"],
                "layer2_keywords": profile["trait_keywords"] + scene["motifs"][:4],
                "visual_policy": subject,
                "human_presence": subject["human_presence"],
                "subject_mode": subject["subject_mode"],
                "gender_mode": subject["gender_mode"],
                "people_count": subject["people_count"],
                "style_family": style,
                "palette": palette,
                "composition": composition,
                "camera_distance": ["wide", "medium_wide", "medium", "close_detail"][cursor % 4],
                "generated_by": "chatgpt_plus_manual",
                "status": "reviewed",
                "batch_id": "",
                "version": 2,
                "notes": "curated seed; no random prompt generation",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            item["layer3_poetic"] = build_layer3_poetic(type_code, profile, scene, style, subject, composition)
            item["gen_prompt_raw"] = build_prompt(item)
            item["negative_prompt"] = rules.get("global_negative_prompt", "")
            records.append(item)
            idx += 1
            per_type_cursor[type_code] += 1

    return records


def init_project(args: argparse.Namespace) -> None:
    ensure_dirs()
    if not DB_PATH.exists():
        write_jsonl([])
    print(f"[OK] Initialized at {ROOT}")
    print(f"[OK] Rules: {RULES_PATH}")
    print(f"[OK] DB: {DB_PATH}")
    print(f"[OK] Image inbox: {INBOX_DIR}")


def backup_db() -> Optional[Path]:
    if not DB_PATH.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DATA_DIR / f"norn_metadata.backup_{ts}.jsonl"
    shutil.copy2(DB_PATH, backup)
    return backup


def seed_curated(args: argparse.Namespace) -> None:
    ensure_dirs()
    existing = read_jsonl()
    if existing and not args.replace:
        print("[ERROR] Existing metadata DB is not empty.")
        print("Use --replace to replace it, or export/backup manually first.")
        return

    if args.replace:
        b = backup_db()
        if b:
            print(f"[OK] Backup created: {b}")

    items = curated_records(count=args.count, start_index=1, only_type=args.type)
    write_jsonl(items)
    print(f"[OK] Curated metadata generated: {len(items)} records")
    if args.type:
        print(f"[OK] Type: {args.type.upper()}")
    print("[OK] No random seed used. Records are generated from curated type-scene rules.")
    audit_namespace = argparse.Namespace(verbose=False)
    audit(audit_namespace)


def list_items(args: argparse.Namespace) -> None:
    items = read_jsonl()
    if args.status:
        items = [x for x in items if x.get("status") == args.status]
    if args.type:
        items = [x for x in items if x.get("mbti_type") == args.type.upper()]
    if args.limit:
        items = items[:args.limit]
    if not items:
        print("(empty)")
        return
    for x in items:
        print(
            f"{x.get('image_id')} | {x.get('status')} | {x.get('mbti_type')} | "
            f"{x.get('scene_id')} | {x.get('subject_mode')} | {x.get('style_family')} | {x.get('local_path','')}"
        )


def batch_prompt(batch: List[Dict[str, Any]]) -> str:
    lines = [
        "请为下面每个 image_id 分别生成一张独立图片。",
        "这是一组用于 The Norn Machine Demo 的人格视觉素材，不要把它们都画成同一个男性角色。",
        "",
        "硬性要求：",
        "1. 每个 image_id 是不同任务，必须严格遵守各自的 Subject rule。",
        "2. 可以没有人物；可以是女性、男性、中性人物、双人或多人；不要自动默认为男性。",
        "3. 画面里不要出现可读文字、编号、Logo、水印、对话框。",
        "4. 每张图可以有不同画风，但要保持成品感和视觉叙事质量。",
        "5. 回复里请用 image_id 标注每张图，方便我下载后重命名。",
        "",
        "图片任务：",
    ]
    for i, item in enumerate(batch, 1):
        lines.extend([
            f"\n[{i}] image_id: {item['image_id']}",
            f"MBTI: {item['mbti_type']} / role_group: {item['role_group']}",
            f"layer1_coords: {json.dumps(item['layer1_coords'], ensure_ascii=False)}",
            f"visual_rationale: {item['visual_rationale']}",
            f"scene: {item['scene_cn']} / {item['scene_en']}",
            f"subject_rule: human_presence={item['human_presence']}; subject_mode={item['subject_mode']}; gender_mode={item['gender_mode']}; people_count={item['people_count']}",
            f"style_family: {item['style_family']}; palette: {item['palette']}; composition: {item['composition']}",
            f"motifs: {', '.join(item['motifs'])}",
            "layer3_direction:",
            *[f"- {line}" for line in item["layer3_poetic"]],
            f"English image prompt: {item['gen_prompt_raw']}",
            f"Negative prompt: {item.get('negative_prompt','')}",
        ])
    return "\n".join(lines)


def copy_to_clipboard(text: str) -> bool:
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True
    except Exception:
        return False


def next_prompt(args: argparse.Namespace) -> None:
    ensure_dirs()
    items = read_jsonl()
    candidates = [
        x for x in items
        if x.get("status") in ("reviewed", "queued")
        and not x.get("local_path")
        and not x.get("image_url")
    ]
    candidates.sort(key=lambda x: x.get("image_id", ""))
    batch = candidates[:args.batch_size]
    if not batch:
        print("[OK] No pending reviewed/queued items.")
        return

    batch_id = datetime.now().strftime("BATCH_%Y%m%d_%H%M%S")
    prompt = batch_prompt(batch)
    prompt_file = PROMPT_DIR / f"{batch_id}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    batch_ids = {x["image_id"] for x in batch}
    for item in items:
        if item["image_id"] in batch_ids:
            item["status"] = "copied_to_chatgpt" if args.copy else "queued"
            item["batch_id"] = batch_id
            item["updated_at"] = now_iso()
    write_jsonl(items)

    copied = copy_to_clipboard(prompt) if args.copy else False
    print(f"[OK] Prompt file: {prompt_file}")
    print(f"[OK] Batch id: {batch_id}")
    print(f"[OK] Items: {', '.join(x['image_id'] for x in batch)}")
    if args.copy:
        print("[OK] Copied to clipboard." if copied else "[WARN] Clipboard copy failed. Open the prompt file manually.")
    if args.print:
        print("\n" + prompt)


def ingest(args: argparse.Namespace) -> None:
    ensure_dirs()
    items = read_jsonl()
    by_id = {x["image_id"]: x for x in items}
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}

    files = [p for p in INBOX_DIR.iterdir() if p.is_file() and p.suffix.lower() in image_exts]
    if not files:
        print(f"[WARN] No images found in {INBOX_DIR}")
        return

    imported, unknown = 0, []
    for p in files:
        image_id = p.stem.split("__")[0].upper()
        if image_id not in by_id:
            unknown.append(p.name)
            continue

        final_name = f"{image_id}{p.suffix.lower()}"
        dest = FINAL_DIR / final_name
        if dest.exists() and not args.overwrite:
            n = 2
            while (FINAL_DIR / f"{image_id}_v{n}{p.suffix.lower()}").exists():
                n += 1
            dest = FINAL_DIR / f"{image_id}_v{n}{p.suffix.lower()}"

        shutil.move(str(p), str(dest))
        by_id[image_id]["local_path"] = dest.relative_to(ROOT).as_posix()
        by_id[image_id]["status"] = "generated"
        by_id[image_id]["updated_at"] = now_iso()
        imported += 1

    write_jsonl(list(by_id.values()))
    print(f"[OK] Imported images: {imported}")
    if unknown:
        print("[WARN] Unknown filenames not imported:")
        for name in unknown:
            print(f"  - {name}")


def export(args: argparse.Namespace) -> None:
    ensure_dirs()
    items = read_jsonl()
    if args.only_generated:
        items = [x for x in items if x.get("status") == "generated"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.format == "json":
        out = EXPORT_DIR / f"norn_metadata_export_{ts}.json"
        out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.format == "jsonl":
        out = EXPORT_DIR / f"norn_metadata_export_{ts}.jsonl"
        write_jsonl(items, out)
    elif args.format == "csv":
        out = EXPORT_DIR / f"norn_metadata_export_{ts}.csv"
        fieldnames = [
            "image_id","schema_version","status","local_path","mbti_type","role_group",
            "scene_id","scene_cn","human_presence","subject_mode","gender_mode","people_count",
            "style_family","palette","composition","motifs","layer1_coords","layer2_keywords",
            "layer3_poetic","gen_prompt_raw","negative_prompt","visual_rationale"
        ]
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for item in items:
                row = {k: item.get(k, "") for k in fieldnames}
                for k in ["motifs","layer1_coords","layer2_keywords","layer3_poetic"]:
                    row[k] = json.dumps(row[k], ensure_ascii=False)
                w.writerow(row)
    else:
        raise ValueError(args.format)

    print(f"[OK] Exported {len(items)} records -> {out}")


def audit(args: argparse.Namespace) -> None:
    items = read_jsonl()
    if not items:
        print("[WARN] DB is empty.")
        return

    def show_counter(title: str, c: Counter) -> None:
        print(f"\n{title}")
        for k, v in sorted(c.items(), key=lambda kv: str(kv[0])):
            print(f"  {k}: {v}")

    prompt_counts = Counter(x.get("gen_prompt_raw","") for x in items)
    dup_prompts = [p for p, n in prompt_counts.items() if n > 1 and p]
    scene_counts = Counter(x.get("scene_id") for x in items)
    dup_scene_ids = [s for s, n in scene_counts.items() if n > 1 and s]

    print("[AUDIT]")
    print(f"  total_records: {len(items)}")
    print(f"  duplicate_gen_prompt_raw: {len(dup_prompts)}")
    print(f"  duplicate_scene_id: {len(dup_scene_ids)}")
    print(f"  generated: {sum(1 for x in items if x.get('status') == 'generated')}")
    print(f"  pending_reviewed_or_queued: {sum(1 for x in items if x.get('status') in ('reviewed','queued','copied_to_chatgpt'))}")

    show_counter("  by_mbti_type", Counter(x.get("mbti_type") for x in items))
    show_counter("  by_human_presence", Counter(x.get("human_presence") for x in items))
    show_counter("  by_subject_mode", Counter(x.get("subject_mode") for x in items))
    show_counter("  by_style_family", Counter(x.get("style_family") for x in items))

    if args.verbose and dup_prompts:
        print("\n[DUPLICATE PROMPTS]")
        for p in dup_prompts[:20]:
            print(p[:300] + "...")


def set_status(args: argparse.Namespace) -> None:
    items = read_jsonl()
    found = False
    for item in items:
        if item.get("image_id") == args.image_id.upper():
            item["status"] = args.status
            if args.notes:
                item["notes"] = args.notes
            item["updated_at"] = now_iso()
            found = True
            break
    if not found:
        raise ValueError(f"image_id not found: {args.image_id}")
    write_jsonl(items)
    print(f"[OK] {args.image_id.upper()} -> {args.status}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Norn Plus Image Manager v2 - curated seed")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.set_defaults(func=init_project)

    s = sub.add_parser("seed-curated", help="Replace/generate curated metadata from 16-type scene rules.")
    s.add_argument("--count", type=int, default=256)
    s.add_argument("--type", default=None, help="Optional: generate only one MBTI type, e.g. INTJ")
    s.add_argument("--replace", action="store_true", help="Replace existing DB with backup.")
    s.set_defaults(func=seed_curated)

    # Backward-compatible alias: seed now means curated seed.
    s = sub.add_parser("seed", help="Alias of seed-curated. This version is not random.")
    s.add_argument("--count", type=int, default=256)
    s.add_argument("--type", default=None)
    s.add_argument("--replace", action="store_true")
    s.set_defaults(func=seed_curated)

    s = sub.add_parser("list")
    s.add_argument("--status", choices=STATUS_ORDER)
    s.add_argument("--type")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=list_items)

    s = sub.add_parser("next")
    s.add_argument("--batch-size", type=int, default=4)
    s.add_argument("--copy", action="store_true")
    s.add_argument("--print", action="store_true")
    s.set_defaults(func=next_prompt)

    s = sub.add_parser("ingest")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(func=ingest)

    s = sub.add_parser("export")
    s.add_argument("--format", choices=["json","jsonl","csv"], default="json")
    s.add_argument("--only-generated", action="store_true")
    s.set_defaults(func=export)

    s = sub.add_parser("audit")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=audit)

    s = sub.add_parser("status")
    s.add_argument("image_id")
    s.add_argument("status", choices=STATUS_ORDER)
    s.add_argument("--notes", default="")
    s.set_defaults(func=set_status)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
