<div align="right">
  <sub>
    <a href="README.md">English</a> | 
    <strong>中文</strong>
  </sub>
</div>

# The Norn Machine: Deterministic Context Assembly

## 一份关于脱离传统RAG依赖的低延迟AI交互架构的白皮书

---

**版本**：1.0  
**状态**：MVP Design Phase  
**作者**：Leo Wang  
**日期**：2026年4月

---

> *在这个以概率为核心的生成式人工智能浪潮里，本项目是一份关于如何坚定寻求确定性的架构提案。*

---

## 目录

1. [摘要](#1-摘要)
2. [设计哲学](#2-设计哲学)
   - 2.1 LLM不是大脑，是声带
   - 2.2 Stateless：不记对话，只记事实
   - 2.3 动态Few-Shot：比微调更优雅的风格控制
   - 2.4 为什么不是RAG
3. [系统架构](#3-系统架构)
   - 3.1 整体拓扑
   - 3.2 三步流水线
   - 3.3 分层Metadata设计
   - 3.4 分级Few-Shot路由
   - 3.5 安全护栏
4. [架构演进](#4-架构演进)
   - 4.1 算法扩展：智能限选器
   - 4.2 云架构扩展：SQS、CloudFront与全球部署
5. [跨领域迁移能力](#5-跨领域迁移能力)
   - 5.1 游戏NPC对话
   - 5.2 个人AI名片
   - 5.3 智能客服与教育辅助
6. [结论](#6-结论)
7. [附录：图片生成与元数据管理流水线](#7-附录图片生成与元数据管理流水线)
   - A.1 三层元数据结构
   - A.2 批量生成Prompt与图片
   - A.3 本地元数据管理脚本
   - A.4 图片生成API调用示例

---

## 1. 摘要

当前基于大语言模型（LLM）的生成式应用，普遍面临三个核心困境：**幻觉不可控**、**延迟不可预测**、**风格不一致**。主流解决方案往往试图通过更复杂的提示工程、更长的上下文窗口或更昂贵的微调来缓解这些问题，但并未从根本上改变“LLM同时承担决策与表达双重职责”的结构性缺陷。

The Norn Machine 提出了一条截然不同的路径：**让确定性规则做决策，让LLM纯粹做语言渲染。**

这一架构的核心洞见是将系统严格分为“快思考”与“慢思考”两层：

- **快思考层**：由经典代码构成的规则引擎，在毫秒级完成所有需要“正确性保证”的计算（原型判定、权重累加、状态转换）。
- **慢思考层**：由LLM在确定的剧本框架内，生成符合指定风格的自然语言文本。

LLM在这个系统中**不负责决定“发生了什么”，只负责“怎么说”**。所有它生成的文本，都是对一个已经由规则引擎确定的结构化结果的“朗读”与“表演”。

本白皮书将详述这一架构的设计哲学、技术实现，以及它如何迁移至游戏NPC对话、个人AI名片、智能客服等更广泛的业务场景。同时，附录部分提供了完整的图片生成与元数据管理流水线，用于支撑Demo的视觉素材生产。

---

## 2. 设计哲学

### 2.1 LLM不是大脑，是声带

当前大量AI产品的失败，根源在于让LLM承担了本应由确定性逻辑处理的任务。LLM的本质是一个概率性的文本生成器，它不擅长算术、不擅长逻辑、不擅长“永远给出相同正确的答案”。

The Norn Machine的架构把LLM的角色从一个“全能的智能体”降格为一个“听话的写作引擎”。这个降格是刻意且关键的：

- **决策层**：由FSM（有限状态机）或规则引擎维护，确保每一个判断都是可解释、可复现、可测试的。
- **表达层**：由LLM负责，将枯燥的结构化数据“翻译”成富有风格、温度与情感的自然语言。

这种分工，将不确定性关进了一个极小的笼子。

### 2.2 Stateless：不记对话，只记事实

传统LLM对话系统往往通过维护冗长的对话历史来模拟“记忆”。但这带来了两个致命问题：上下文窗口的线性膨胀导致延迟与成本飙升，以及LLM对长文本中“中间信息”关注度天然衰减导致的遗忘与幻觉。

The Norn Machine采用完全无状态的设计：

- **不记录任何对话原文**（“说了什么”）
- **只记录结构化的事件结果**（“发生了什么”）

每次对话开始时，系统根据当前状态快照重新组装Prompt。这意味着LLM每一次都是在“从零开始，读完剧本再上场演戏”。上一轮的嘴瓢不会污染下一轮，上下文长度始终恒定且极短。

对于一个面向公众的轻量Demo，这一哲学被推向了极致：**整个系统无数据库，无持久化存储，每一次请求都是一次独立的“无状态计算”**。

### 2.3 动态Few-Shot：比微调更优雅的风格控制

解决“AI生成文本千篇一律、缺乏角色辨识度”的问题，业界主流方案是微调（Fine-tuning）或LoRA。但这些方案存在明显缺陷：训练成本高、更新迭代慢、无法根据实时状态动态切换风格。

The Norn Machine采用**动态Few-Shot注入**：根据规则引擎判定的当前状态（如用户原型、数据密度、选择的语气），从预设的风格库中精确抽取对应的Few-Shot示例，拼入Prompt。

这种做法把“风格控制”从模型权重的静态训练，转化为了“基于状态的实时检索与组装”。它不需要任何GPU训练，可以在运行时灵活切换风格，且随着Few-Shot库的迭代而持续进化。

### 2.4 为什么不是RAG

RAG（检索增强生成）可能是人类迄今为止造出的最复杂的搜索引擎。它通过向量化、近似最近邻搜索（HNSW/FAISS）、语义排序等机制，从海量文档中检索与当前查询最相关的内容。

然而，RAG存在几个本质性的局限，使其不适合本项目所追求的“低延迟、强确定性”目标：

- **检索噪声**：语义相似不等于逻辑相关。RAG可能召回看似相关但实际包含剧透、误信息或不应被当前状态可见的内容。
- **延迟不可控**：向量检索、重排序、多轮召回等步骤，让端到端延迟变得难以预测且难以优化。
- **维护成本高**：需要管理向量数据库、嵌入模型、分片策略、索引重建等重型基础设施。
- **“杀鸡用牛刀”**：当知识库规模仅在上万条或更少，且应用场景本身有明确的规则边界时，BM25甚至直接拼接往往已足够。

因此，The Norn Machine选择了一条更极致的路径：**完全放弃RAG，转而通过FSM和分层Metadata实现O(1)的确定性上下文组装。**

---

## 3. 系统架构

### 3.1 整体拓扑

```
[用户浏览器]  ←→  [S3 静态网站托管]  (纯前端交互)
     |
     | (HTTPS)
     v
[API Gateway]  (身份校验、限流)
     |
     v
[AWS Lambda]  (快思考 + 慢思考)
     |
     | (AWS SDK)
     v
[Amazon Bedrock]  (Claude / Llama 等模型)
```

- **前端**：纯静态网页，托管于S3，交互逻辑完全在浏览器内完成。
- **API层**：API Gateway提供REST接口，通过简单的查询参数或Token进行身份校验。
- **计算层**：单个Lambda函数实现全部后端逻辑（快思考、模板装载、慢思考调用）。
- **模型层**：Amazon Bedrock提供托管的LLM推理服务，无需管理底层GPU。

### 3.2 三步流水线

**Step 1 — 快思考（< 5ms）**

代码层接收前端传来的用户选择记录，通过预设的JSON权重映射表，瞬时计算出用户倾向的原型标签与置信度。

- 纯规则逻辑，无网络调用，无LLM参与
- 输出：原型标签（如MBTI四维坐标）、核心关键词、置信度等级

**Step 2 — 模板装载（< 1ms）**

根据快思考的结果，从配置库中精确拉取对应风格的Few-Shot示例与Prompt模板。

- 本质是一次内存中的查表操作
- 数据稀疏（1轮）→ 调用“直觉流”模板
- 数据丰满（10轮）→ 调用“精准毒舌”模板
- 数据过量（>20轮）→ 先触发代码层摘要，再走“分析师总结”模板

**Step 3 — 慢思考（~1-2s）**

将前两步组装好的Prompt发送至Bedrock，由LLM生成最终的自然语言文本。

- LLM看到的是一份已高度结构化的“剧本”
- 其任务仅是：用指定的语气，把这份剧本朗读出来

**端到端延迟目标**：≤ 1.2秒（短输出模式下可逼近0.8秒）。

### 3.3 分层Metadata设计

每一张用于Demo的图片，均携带三层元数据，分别服务于不同的系统层级：

```json
{
  "image_id": "NORN_042",
  "layer1_coords": { "E_I": -7, "S_N": 5, "T_F": 3, "J_P": -2 },
  "layer2_keywords": ["废墟", "雨夜", "霓虹灯", "孤独", "沉思"],
  "layer3_poetic": [
    "他站在世界的残骸上，却仿佛在等待一场从未到来的雨。",
    "霓虹是城市的假笑，而他是这座城市唯一不愿入睡的人。",
    "孤独不是没有人陪伴，而是所有人都以为他不需要陪伴。"
  ]
}
```

- **Layer 1（快思考用）**：可量化的MBTI四维坐标（-10到10），直接用于加权计算，无需任何语义解析。
- **Layer 2（中间层）**：精炼过的风格关键词，用于快速匹配与统计。
- **Layer 3（慢思考用）**：经过人工审核的诗意描述，作为LLM发挥文学性的核心素材。

这种分层的设计本质上是将“上下文管理”从概率检索转为确定性拼装，且每一层都有明确的工程分工。

### 3.4 分级Few-Shot路由

不是一套Few-Shot吃遍所有场景，而是根据数据密度动态路由：

| 状态 | 触发条件 | Few-Shot风格 | 模板倾向 |
|:---|:---|:---|:---|
| `sparse_mode` | 用户选择 < 5轮 | 故弄玄虚的直觉流 | 模棱两可但充满宿命感 |
| `dense_mode` | 5-20轮 | 精准毒舌的分析师 | 数据驱动，一针见血 |
| `overflow_mode` | >20轮 | 先代码层摘要，再生成立论 | 统计概括 + 性格定论 |

LLM的System Prompt仅需根据当前模式，加载对应的Few-Shot示例与简短的角色指令。这种极轻量的切换逻辑，保证了系统在不同数据密度下都能产出风格适配的文本。

### 3.5 安全护栏

通过Amazon Bedrock Guardrails设置多层防护：

- **输入过滤**：拒绝包含暴力、色情、仇恨言论、医疗/心理健康咨询的请求
- **输出过滤**：过滤侮辱性词汇与敏感话题
- **兜底策略**：当Guardrails触发拦截时，返回预设的友好拒绝文案，而非裸奔的模型拒绝响应

---

## 4. 架构演进

### 4.1 算法扩展：智能限选器

**目标**：当用户选择图片数量超过阈值（如20张），不把全部Layer 3描述和Layer 2关键词全量塞给LLM，而是先用代码层进行统计摘要。

**方法**：
- 对Layer 2关键词做词频统计，选取Top-K高频词
- 对Layer 3诗意描述，通过规则（如包含高频情绪词的句子优先抽取）或轻量级TF-IDF算法，生成一份简短的文本摘要
- 摘要结果作为“用户偏好总结”字段注入Prompt，替代原始的全量数据

**价值**：
- 保证Prompt Token消耗不随用户选择规模线性增长
- 维持端到端延迟在O(1)级别
- 进一步实践“绝不向LLM多传一个不必要的Token”的架构信仰

**状态**：MVP阶段预留设计接口，未实际实现，但在白皮书中作为架构完整性的一部分描述。

### 4.2 云架构扩展：SQS、CloudFront与全球部署

**当前MVP架构**：单Lambda + S3静态托管，适合邀请制小范围Demo。

**高并发场景扩展**：
```
[API Gateway] → [SQS 队列] → [Lambda A: 快思考] → [SQS 队列] → [Lambda B: 慢思考]
```
- 快慢思考解耦为两个独立Lambda，通过SQS缓冲
- 当Bedrock调用成为瓶颈时，可独立扩容慢思考Lambda
- 快思考的极低延迟不受模型推理波动影响

**全球加速**：
- 引入CloudFront CDN，将S3上的静态资源（HTML/JS/CSS/图片）缓存至全球边缘节点
- 首屏加载与图片资源延迟大幅降低

**状态**：不实际实施。这部分作为“架构师视野”的证明，展示作者对云原生扩展模式的理解。Demo阶段无需为此付出额外成本。

---

## 5. 跨领域迁移能力

The Norn Machine的架构并非仅适用于一个性格分析Demo。它的核心模块——**确定性状态机 + 分层Metadata + 动态Few-Shot + LLM语言渲染**——本质上是一套**领域无关的“确定性上下文组装引擎”**。

### 5.1 游戏NPC对话
- **FSM**：维护剧情进度、NPC好感度、世界观解锁状态
- **动态Few-Shot**：根据NPC性格与当前情绪状态，注入对应风格示例
- **LLM**：仅负责将“这个NPC此刻要说的话”生成出来

### 5.2 个人AI名片
- **FSM**：根据访问者浏览的页面、点击的项目，判断其兴趣点
- **动态Few-Shot**：根据访问者选择的语气（专业/轻松/极客），注入对应风格
- **LLM**：以“我的语气”回答关于我经历、项目、技能的问题

### 5.3 智能客服与教育辅助
- **FSM**：维护SOP流程、学员学习路径、合规边界
- **动态Few-Shot**：根据用户等级/情绪，切换应答风格
- **LLM**：在确定的业务边界内，生成人性化的回复文本

---

## 6. 结论

The Norn Machine是对当前“LLM万能论”的一次反思与回应。

它证明了：**AI产品最大的价值，往往不在于让LLM做更多的事，而在于精心设计LLM不做的事。**

通过将确定性规则与概率性生成严格分层，我们可以在保持LLM强大表达力的同时，获得传统软件系统的可靠性、可测试性与可维护性。

**外部事物Stateless，是为了让真正重要的东西Stateful。** 系统越无状态，就越容易扩展、越不容易出错、越能经受住时间的考验。而真正需要被记住的东西——人的体验、选择背后的情绪——被结构化成Metadata，被精心管理在配置里，而非塞进一个临时内存。

这不仅是一套关于“AI算命”的技术方案，更是一份关于“如何让AI应用真正落地”的设计宣言。

---

## 7. 附录：图片生成与元数据管理流水线

本附录描述Demo所需的256张统一风格图片的批量生成流程，以及本地元数据管理方案。

### A.1 三层元数据结构

每张图片的完整元数据JSON示例：

```json
{
  "image_id": "NORN_042",
  "image_url": "https://your-s3-bucket/norn-images/NORN_042.png",
  "layer1_coords": {
    "E_I": -7,
    "S_N": 5,
    "T_F": 3,
    "J_P": -2
  },
  "layer2_keywords": ["废墟", "雨夜", "霓虹灯", "孤独", "沉思"],
  "layer3_poetic": [
    "他站在世界的残骸上，却仿佛在等待一场从未到来的雨。",
    "霓虹是城市的假笑，而他是这座城市唯一不愿入睡的人。",
    "孤独不是没有人陪伴，而是所有人都以为他不需要陪伴。"
  ],
  "gen_prompt_raw": "A solitary figure standing in a ruined cyberpunk city, rain, neon reflections on wet pavement, cinematic lighting, high contrast, melancholic atmosphere --ar 16:9",
  "style_anchor": "cyberpunk cinematic",
  "generated_by": "gpt_plus_account_1",
  "status": "reviewed"
}
```

### A.2 批量生成Prompt与图片

**Step 1：批量生成元数据骨架**

调用GPT-4 / Claude等大模型，生成256组满足以下条件的初始数据：
- 四维MBTI坐标（-10到10）的随机组合，覆盖16种人格的典型与非典型区间
- 每组附带3-5个风格关键词
- 每组附带1条用于生图的英文Prompt，统一包含风格锚点（如“cyberpunk, cinematic lighting, high contrast”）
- 每组附带3句中文诗意草稿

输出格式为CSV或JSON Lines，便于后续脚本解析。

**Step 2：人工审核与润色**

- 这是**注入个人品味的关键节点**
- 审核Layer 3诗意描述，确保文学质量与风格统一
- 审核关键词列表，确保无歧义或不当词汇
- 审核MBTI坐标分布，确保各维度覆盖平衡
- 将审核后的数据导入Airtable或Notion数据库进行管理

**Step 3：批量调用生图API**

使用脚本遍历审核后的数据，调用Midjourney API、DALL-E API或Stable Diffusion，生图并将返回的图片URL记录回数据库。

### A.3 本地元数据管理脚本

以下Python伪代码展示了如何使用脚本在本地管理元数据，并批量调用生图API。

```python
import json
import csv
import requests
import time
from pathlib import Path

# 1. 从CSV/JSON导入元数据
def load_metadata(filepath):
    """加载经过审核的图片元数据"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

# 2. 调用生图API（以DALL-E为例）
def generate_image(prompt, api_key, size="1792x1024"):
    """调用OpenAI DALL-E 3生成图片，返回图片URL"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "dall-e-3",
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": "hd"
    }
    response = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers=headers,
        json=payload
    )
    response.raise_for_status()
    return response.json()["data"][0]["url"]

# 3. 主流程：遍历元数据，生图并更新记录
def main():
    metadata = load_metadata("norn_metadata_reviewed.json")
    updated_metadata = []

    for item in metadata:
        if item.get("status") != "reviewed":
            continue  # 跳过未审核条目

        # 避免重复生成
        if item.get("image_url"):
            updated_metadata.append(item)
            continue

        try:
            print(f"Generating image for {item['image_id']}...")
            image_url = generate_image(
                item["gen_prompt_raw"],
                api_key="YOUR_OPENAI_API_KEY"
            )
            item["image_url"] = image_url
            item["status"] = "generated"
            print(f"  -> {image_url}")
        except Exception as e:
            print(f"  Failed: {e}")
            item["status"] = "generation_failed"

        updated_metadata.append(item)
        time.sleep(1)  # 遵守API速率限制

    # 保存更新后的元数据
    with open("norn_metadata_with_images.json", "w", encoding="utf-8") as f:
        json.dump(updated_metadata, f, ensure_ascii=False, indent=2)
    print("Done. Metadata saved.")

if __name__ == "__main__":
    main()
```

### A.4 图片生成API调用示例

**DALL-E 3 / GPT-4o Images**

```bash
curl https://api.openai.com/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "dall-e-3",
    "prompt": "A solitary figure standing in a ruined cyberpunk city, rain, neon reflections on wet pavement, cinematic lighting, high contrast, melancholic atmosphere",
    "n": 1,
    "size": "1792x1024",
    "quality": "hd"
  }'
```

**稳定扩散 (Stable Diffusion WebUI API)**

```bash
curl http://localhost:7860/sdapi/v1/txt2img \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A solitary figure standing in a ruined cyberpunk city, rain, neon reflections, cinematic lighting, high contrast",
    "negative_prompt": "blurry, low quality, distorted face",
    "steps": 30,
    "cfg_scale": 7,
    "width": 1024,
    "height": 576
  }'
```

**注意**：
- 各大生图服务均有每日调用次数限制。若使用多个账号（如2个GPT Plus + 1个Gemini Pro），可在脚本中实现轮询切换API Key。
- 生成的图片应压缩并上传至S3，前端通过CloudFront或S3直接URL加载。
- 元数据JSON文件最终随前端代码一同部署至S3，供Lambda读取。

---

*The Norn Machine. Deterministic Context Assembly. A Serverless, Stateless Prompt Engine.*
