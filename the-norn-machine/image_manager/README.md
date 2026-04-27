# Norn Plus Image Manager v2 - Curated Metadata Seeder

这一版修复旧版最大的问题：旧版 `seed` 会把风格、主体和后端描述都推向“赛博朋克男性单人图像”。v2 改成**策展式 metadata 生成**。

## 核心原则

- 不随机生成人格描述；
- 不默认男性；
- 不默认赛博朋克；
- 不默认必须有人；
- 16 种 MBTI 各有自己的视觉逻辑；
- 默认 256 张 = 16 型 × 16 个不同场景；
- 每条记录有唯一 `scene_id`，并有 `audit` 检查重复 prompt。

## 快速开始

```bash
python norn_plus_manager_v2.py init
python norn_plus_manager_v2.py seed-curated --count 256 --replace
python norn_plus_manager_v2.py audit
python norn_plus_manager_v2.py next --batch-size 4 --copy
```

然后去 ChatGPT Plus 网页端粘贴生成。下载图片后重命名为：

```text
NORN_0001.png
NORN_0002.png
NORN_0003.png
NORN_0004.png
```

丢进：

```text
output/images_inbox/
```

再执行：

```bash
python norn_plus_manager_v2.py ingest
python norn_plus_manager_v2.py export --format json --only-generated
```

## 只生成某一种人格的 metadata

例如只看 INTJ 的 16 张：

```bash
python norn_plus_manager_v2.py seed-curated --type INTJ --count 16 --replace
python norn_plus_manager_v2.py export --format json
```

## 新增字段

每条 metadata 会包含：

- `mbti_type`
- `role_group`
- `visual_rationale`
- `scene_id`
- `scene_cn`
- `scene_en`
- `human_presence`
- `subject_mode`
- `gender_mode`
- `people_count`
- `style_family`
- `palette`
- `composition`
- `motifs`
- `layer3_poetic`
- `gen_prompt_raw`

## 为什么这版不会再全是男人

`layer3_poetic` 已经改成场景叙事句，不再使用“他/她”作为默认主语。人物是否出现、出现几人、性别模式，都由 `visual_policy` 明确控制。

## 规则库在哪里

```text
config/curated_seed_rules_v2.json
```

这是你后续真正应该维护的文件。脚本只是执行器，审美和人格映射应该沉淀在这个 JSON 规则库里。
