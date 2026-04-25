<div align="right">
  <sub>
    <strong>English</strong> | 
    <a href="README_CN.md">中文</a>
  </sub>
</div>

# The Norn Machine: Deterministic Context Assembly — A Serverless, Stateless Prompt Engine

## Building a Cloud-Native Prompt Engine for Ultra-Short Context Windows, Without RAG.


---

**Version**: 1.0  
**Status**: MVP Design Phase  
**Author**: Leo Wang  
**Date**: April 2026

---

> *In an era of generative AI built upon probabilities, this project is an architectural proposal on how to resolutely pursue determinism.*

---

## Table of Contents

1. [Abstract](#1-abstract)
2. [Design Philosophy](#2-design-philosophy)
   - 2.1 The LLM Is Not the Brain — It Is the Vocal Cords
   - 2.2 Stateless: Don't Remember Conversations, Remember Facts
   - 2.3 Dynamic Few-Shot: Elegance Over Fine-Tuning for Style Control
   - 2.4 Why Not RAG
3. [System Architecture](#3-system-architecture)
   - 3.1 Overall Topology
   - 3.2 The Three-Step Pipeline
   - 3.3 Layered Metadata Design
   - 3.4 Tiered Few-Shot Routing
   - 3.5 Safety Guardrails
4. [Architecture Evolution](#4-architecture-evolution)
   - 4.1 Algorithm Extension: Smart Selector
   - 4.2 Cloud Architecture Extension: SQS, CloudFront & Global Deployment
5. [Cross-Domain Transferability](#5-cross-domain-transferability)
   - 5.1 Game NPC Dialogue
   - 5.2 Personal AI Business Card
   - 5.3 Intelligent Customer Service & Educational Assistance
6. [Conclusion](#6-conclusion)
7. [Appendix: Image Generation & Metadata Management Pipeline](#7-appendix-image-generation--metadata-management-pipeline)
   - A.1 Three-Layer Metadata Structure
   - A.2 Batch Prompt & Image Generation
   - A.3 Local Metadata Management Script
   - A.4 Image Generation API Call Examples

---

## 1. Abstract

Current generative applications built on large language models (LLMs) face three core dilemmas: **uncontrollable hallucinations**, **unpredictable latency**, and **inconsistent style**. Mainstream solutions often attempt to mitigate these issues through more complex prompt engineering, longer context windows, or more expensive fine-tuning — yet none fundamentally address the structural flaw of having "the LLM shoulder the dual responsibilities of decision-making and expression simultaneously."

The Norn Machine proposes a radically different path: **let deterministic rules handle the decisions, and let the LLM serve purely as a language renderer.**

The core insight of this architecture is the strict separation of the system into two layers: "Fast Thinking" and "Slow Thinking":

- **Fast Thinking Layer**: A rules engine built from classical code, completing all computations that require a "correctness guarantee" in milliseconds (archetype determination, weight accumulation, state transitions).
- **Slow Thinking Layer**: The LLM, operating within a deterministic script framework, generating natural language text that conforms to a specified style.

In this system, the LLM **is not responsible for deciding "what happened" — only for "how to say it."** Every piece of text it generates is a "narration" and "performance" of a structured result that has already been determined by the rules engine.

This white paper details the design philosophy and technical implementation of this architecture, as well as how it can be transferred to broader business scenarios such as game NPC dialogue, personal AI business cards, and intelligent customer service. The appendix also provides a complete image generation and metadata management pipeline to support the production of visual assets for the demo.

---

## 2. Design Philosophy

### 2.1 The LLM Is Not the Brain — It Is the Vocal Cords

A significant number of current AI product failures stem from assigning tasks to LLMs that should be handled by deterministic logic. An LLM is, at its core, a probabilistic text generator. It is not good at arithmetic, not good at logic, and not good at "always giving the same correct answer."

The Norn Machine's architecture demotes the LLM's role from an "omniscient intelligence" to an "obedient writing engine." This demotion is deliberate and crucial:

- **Decision Layer**: Maintained by an FSM (Finite State Machine) or rules engine, ensuring every judgment is explainable, reproducible, and testable.
- **Expression Layer**: Handled by the LLM, "translating" dry structured data into natural language rich with style, warmth, and emotion.

This division of labor locks uncertainty into the smallest possible cage.

### 2.2 Stateless: Don't Remember Conversations, Remember Facts

Traditional LLM dialogue systems often simulate "memory" by maintaining lengthy conversation histories. But this introduces two fatal problems: linearly ballooning context windows that drive up latency and cost, and the LLM's natural attention decay for "middle information" in long texts, leading to forgetting and hallucinations.

The Norn Machine adopts a completely stateless design:

- **Records no original conversation text** ("what was said")
- **Records only structured event outcomes** ("what happened")

At the start of each conversation, the system reassembles the Prompt from scratch based on a snapshot of the current state. This means the LLM every single time "starts from zero, reads the script, and then takes the stage to perform." A slip of the tongue in the previous round does not contaminate the next. The context length remains constant and extremely short.

For a lightweight public-facing demo, this philosophy is pushed to its extreme: **the entire system has no database, no persistent storage — every request is an independent "stateless computation."**

### 2.3 Dynamic Few-Shot: Elegance Over Fine-Tuning for Style Control

The industry mainstream for solving "AI-generated text is monotonous and lacks character distinctiveness" is fine-tuning or LoRA. But these approaches have obvious drawbacks: high training costs, slow iteration cycles, and the inability to dynamically switch styles based on real-time state.

The Norn Machine employs **Dynamic Few-Shot Injection**: based on the current state determined by the rules engine (e.g., user archetype, data density, selected tone), precisely extract the corresponding Few-Shot examples from a preset style library and splice them into the Prompt.

This approach transforms "style control" from static training of model weights into a "state-based real-time retrieval and assembly." It requires zero GPU training, can flexibly switch styles at runtime, and continuously evolves as the Few-Shot library is iterated upon.

### 2.4 Why Not RAG

RAG (Retrieval-Augmented Generation) may be the most complex search engine humanity has ever built. It uses vectorization, approximate nearest neighbor search (HNSW/FAISS), semantic ranking, and other mechanisms to retrieve the most relevant content from massive document collections.

However, RAG has several fundamental limitations that make it unsuitable for the "low-latency, strong-determinism" goals of this project:

- **Retrieval Noise**: Semantic similarity does not equal logical relevance. RAG may recall content that seems related but actually contains spoilers, misinformation, or information that should not be visible given the current state.
- **Uncontrollable Latency**: Steps like vector retrieval, re-ranking, and multi-round recall make end-to-end latency difficult to predict and optimize.
- **High Maintenance Cost**: Requires managing heavy infrastructure such as vector databases, embedding models, sharding strategies, and index rebuilding.
- **"Using a Sledgehammer to Crack a Nut"**: When the knowledge base is only in the tens of thousands of entries or fewer, and the application scenario has clear rule boundaries, BM25 or even direct concatenation is often sufficient.

Thus, The Norn Machine chooses a more extreme path: **abandon RAG entirely, and instead achieve O(1) deterministic context assembly through an FSM and layered Metadata.**

---

## 3. System Architecture

### 3.1 Overall Topology

```
[User Browser]  ←→  [S3 Static Website Hosting]  (Pure frontend interaction)
     |
     | (HTTPS)
     v
[API Gateway]  (Authentication, rate limiting)
     |
     v
[AWS Lambda]  (Fast Thinking + Slow Thinking)
     |
     | (AWS SDK)
     v
[Amazon Bedrock]  (Claude / Llama and other models)
```

- **Frontend**: Pure static web pages, hosted on S3. All interaction logic is completed entirely within the browser.
- **API Layer**: API Gateway provides REST interfaces, with authentication handled via simple query parameters or tokens.
- **Compute Layer**: A single Lambda function implements all backend logic (Fast Thinking, template loading, Slow Thinking invocation).
- **Model Layer**: Amazon Bedrock provides managed LLM inference services, eliminating the need to manage underlying GPUs.

### 3.2 The Three-Step Pipeline

**Step 1 — Fast Thinking (< 5ms)**

The code layer receives the user's selection history sent from the frontend and instantaneously calculates the archetype label and confidence level of the user's tendencies through a preset JSON weight mapping table.

- Pure rule logic, no network calls, no LLM involvement
- Output: Archetype label (e.g., MBTI four-dimensional coordinates), core keywords, confidence level

**Step 2 — Template Loading (< 1ms)**

Based on the results of Fast Thinking, precisely fetch the corresponding style's Few-Shot examples and Prompt template from the configuration library.

- Essentially an in-memory table lookup operation
- Sparse data (1 round) → invokes the "Intuitionist" template
- Dense data (10 rounds) → invokes the "Precision Roast" template
- Overflow data (>20 rounds) → triggers a code-layer summary first, then proceeds with the "Analyst Summary" template

**Step 3 — Slow Thinking (~1-2s)**

Send the Prompt assembled in the first two steps to Bedrock, where the LLM generates the final natural language text.

- The LLM sees a highly structured "script"
- Its sole task is: narrate this script in the specified tone

**End-to-end latency target**: ≤ 1.2 seconds (approaching 0.8 seconds in short-output mode).

### 3.3 Layered Metadata Design

Every image used in the demo carries three layers of metadata, each serving a different system layer:

```json
{
  "image_id": "NORN_042",
  "layer1_coords": { "E_I": -7, "S_N": 5, "T_F": 3, "J_P": -2 },
  "layer2_keywords": ["ruins", "rainy night", "neon lights", "solitude", "contemplation"],
  "layer3_poetic": [
    "He stands upon the wreckage of the world, as if waiting for a rain that never came.",
    "Neon is the city's fake smile, and he is the only one in this city who refuses to fall asleep.",
    "Loneliness is not the absence of company, but that everyone believes he needs none."
  ]
}
```

- **Layer 1 (for Fast Thinking)**: Quantifiable MBTI four-dimensional coordinates (-10 to 10), directly used for weighted calculations, requiring no semantic parsing whatsoever.
- **Layer 2 (Intermediate Layer)**: Refined style keywords for rapid matching and statistics.
- **Layer 3 (for Slow Thinking)**: Human-reviewed poetic descriptions, serving as the core material for the LLM to exercise its literary capability.

This layered design essentially transforms "context management" from probabilistic retrieval to deterministic assembly, with each layer having a clear engineering responsibility.

### 3.4 Tiered Few-Shot Routing

Rather than one set of Few-Shot examples serving all scenarios, routing is performed dynamically based on data density:

| State | Trigger Condition | Few-Shot Style | Template Tendency |
|:---|:---|:---|:---|
| `sparse_mode` | User selections < 5 rounds | Mystical Intuitionist | Ambiguous yet fate-laden |
| `dense_mode` | 5-20 rounds | Precision Roast Analyst | Data-driven, sharp and incisive |
| `overflow_mode` | >20 rounds | Code-layer summary first, then generate conclusion | Statistical overview + character verdict |

The LLM's System Prompt only needs to load the corresponding Few-Shot examples and a short role instruction based on the current mode. This extremely lightweight switching logic ensures the system produces style-appropriate text across varying data densities.

### 3.5 Safety Guardrails

Multiple layers of protection are established through Amazon Bedrock Guardrails:

- **Input Filtering**: Reject requests containing violence, pornography, hate speech, or medical/mental health counseling
- **Output Filtering**: Filter insulting language and sensitive topics
- **Fallback Strategy**: When Guardrails trigger an interception, return a preset friendly rejection message rather than an exposed model refusal response

---

## 4. Architecture Evolution

### 4.1 Algorithm Extension: Smart Selector

**Objective**: When the number of user-selected images exceeds a threshold (e.g., 20), instead of stuffing all Layer 3 descriptions and Layer 2 keywords into the LLM, first perform a statistical summary at the code layer.

**Method**:
- Perform word frequency statistics on Layer 2 keywords, selecting the Top-K high-frequency terms
- For Layer 3 poetic descriptions, generate a concise text summary through rules (e.g., prioritize sentences containing high-frequency emotional terms) or lightweight TF-IDF algorithms
- Inject the summary result into the Prompt as a "User Preference Summary" field, replacing the raw full dataset

**Value**:
- Ensures Prompt token consumption does not grow linearly with the scale of user selections
- Maintains end-to-end latency at O(1) level
- Further practices the architectural conviction of "never pass a single unnecessary token to the LLM"

**Status**: Design interface reserved during the MVP phase; not actually implemented, but described in this white paper as part of architectural completeness.

### 4.2 Cloud Architecture Extension: SQS, CloudFront & Global Deployment

**Current MVP Architecture**: Single Lambda + S3 static hosting, suitable for invite-only, small-scale demos.

**High-Concurrency Scenario Expansion**:
```
[API Gateway] → [SQS Queue] → [Lambda A: Fast Thinking] → [SQS Queue] → [Lambda B: Slow Thinking]
```
- Fast Thinking and Slow Thinking are decoupled into two independent Lambdas, buffered by SQS
- When Bedrock invocations become the bottleneck, Slow Thinking Lambdas can be independently scaled
- The extremely low latency of Fast Thinking remains unaffected by model inference fluctuations

**Global Acceleration**:
- Introduce CloudFront CDN to cache S3 static assets (HTML/JS/CSS/images) at global edge nodes
- First-screen load time and image resource latency are significantly reduced

**Status**: Not actually implemented. This section serves as proof of "architect's vision," demonstrating the author's understanding of cloud-native scaling patterns. No additional cost is needed for this during the demo phase.

---

## 5. Cross-Domain Transferability

The architecture of The Norn Machine is not limited to a single personality analysis demo. Its core modules — **deterministic state machine + layered Metadata + dynamic Few-Shot + LLM language rendering** — essentially constitute a **domain-agnostic "Deterministic Context Assembly Engine."**

### 5.1 Game NPC Dialogue
- **FSM**: Maintains plot progress, NPC affinity, and world-building unlock states
- **Dynamic Few-Shot**: Injects corresponding style examples based on NPC personality and current emotional state
- **LLM**: Solely responsible for generating "what this NPC needs to say at this moment"

### 5.2 Personal AI Business Card
- **FSM**: Determines visitor interest points based on pages browsed and items clicked
- **Dynamic Few-Shot**: Injects corresponding style based on the tone chosen by the visitor (professional/casual/geeky)
- **LLM**: Answers questions about personal experiences, projects, and skills in "my voice"

### 5.3 Intelligent Customer Service & Educational Assistance
- **FSM**: Maintains SOP workflows, student learning paths, and compliance boundaries
- **Dynamic Few-Shot**: Switches response style based on user level/emotion
- **LLM**: Generates humanized response text within defined business boundaries

---

## 6. Conclusion

The Norn Machine is a reflection on — and a response to — the current "LLM omnipotence" trend.

It demonstrates that: **the greatest value of an AI product often lies not in having the LLM do more, but in carefully designing what the LLM does not do.**

By strictly separating deterministic rules from probabilistic generation, we can retain the powerful expressiveness of the LLM while gaining the reliability, testability, and maintainability of traditional software systems.

**External things are Stateless so that the things that truly matter can be Stateful.** The more stateless the system, the easier it scales, the less prone it is to errors, and the more it can withstand the test of time. And the things that truly need to be remembered — human experiences, the emotions behind choices — are structured into Metadata, meticulously managed in configuration files, rather than crammed into temporary memory.

This is not merely a technical proposal for "AI fortune-telling." It is a design manifesto on "how to make AI applications truly land in the real world."

---

## 7. Appendix: Image Generation & Metadata Management Pipeline

This appendix describes the batch generation workflow for the 256 uniformly styled images required by the demo, as well as the local metadata management solution.

### A.1 Three-Layer Metadata Structure

Complete metadata JSON example for each image:

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
  "layer2_keywords": ["ruins", "rainy night", "neon lights", "solitude", "contemplation"],
  "layer3_poetic": [
    "He stands upon the wreckage of the world, as if waiting for a rain that never came.",
    "Neon is the city's fake smile, and he is the only one in this city who refuses to fall asleep.",
    "Loneliness is not the absence of company, but that everyone believes he needs none."
  ],
  "gen_prompt_raw": "A solitary figure standing in a ruined cyberpunk city, rain, neon reflections on wet pavement, cinematic lighting, high contrast, melancholic atmosphere --ar 16:9",
  "style_anchor": "cyberpunk cinematic",
  "generated_by": "gpt_plus_account_1",
  "status": "reviewed"
}
```

### A.2 Batch Prompt & Image Generation

**Step 1: Batch Generate Metadata Skeletons**

Invoke large models such as GPT-4 / Claude to generate 256 sets of initial data meeting the following conditions:
- Random combinations of four-dimensional MBTI coordinates (-10 to 10), covering typical and atypical ranges of all 16 personality types
- Each set accompanied by 3-5 style keywords
- Each set accompanied by 1 English prompt for image generation, uniformly incorporating the style anchor (e.g., "cyberpunk, cinematic lighting, high contrast")
- Each set accompanied by 3 Chinese poetic drafts

Output format: CSV or JSON Lines, for easy parsing by subsequent scripts.

**Step 2: Human Review & Polish**

- This is **the critical juncture for injecting personal taste**
- Review Layer 3 poetic descriptions to ensure literary quality and stylistic consistency
- Review keyword lists to ensure no ambiguity or inappropriate terms
- Review MBTI coordinate distribution to ensure balanced coverage across dimensions
- Import reviewed data into Airtable or Notion database for management

**Step 3: Batch Invoke Image Generation API**

Use a script to iterate through the reviewed data, invoke Midjourney API, DALL-E API, or Stable Diffusion to generate images, and record the returned image URLs back into the database.

### A.3 Local Metadata Management Script

The following Python pseudocode demonstrates how to use a script to manage metadata locally and batch-call image generation APIs.

```python
import json
import csv
import requests
import time
from pathlib import Path

# 1. Import metadata from CSV/JSON
def load_metadata(filepath):
    """Load reviewed image metadata"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

# 2. Call image generation API (DALL-E example)
def generate_image(prompt, api_key, size="1792x1024"):
    """Call OpenAI DALL-E 3 to generate an image, return image URL"""
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

# 3. Main workflow: iterate metadata, generate images, and update records
def main():
    metadata = load_metadata("norn_metadata_reviewed.json")
    updated_metadata = []

    for item in metadata:
        if item.get("status") != "reviewed":
            continue  # Skip unreviewed entries

        # Avoid duplicate generation
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
        time.sleep(1)  # Comply with API rate limits

    # Save updated metadata
    with open("norn_metadata_with_images.json", "w", encoding="utf-8") as f:
        json.dump(updated_metadata, f, ensure_ascii=False, indent=2)
    print("Done. Metadata saved.")

if __name__ == "__main__":
    main()
```

### A.4 Image Generation API Call Examples

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

**Stable Diffusion (Stable Diffusion WebUI API)**

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

**Notes**:
- All major image generation services have daily call limits. If using multiple accounts (e.g., 2 GPT Plus + 1 Gemini Pro), implement round-robin API key switching in your script.
- Generated images should be compressed and uploaded to S3. The frontend loads them via CloudFront or direct S3 URLs.
- The metadata JSON file is ultimately deployed alongside the frontend code to S3 for Lambda to read.

---

*The Norn Machine. Deterministic Context Assembly. A Serverless, Stateless Prompt Engine.*
```

---

## README_CN.md（中文版 — 在原内容顶部添加语言切换）

你只需在你现有的中文 `README.md` 文件**最顶部**插入下面这段即可（建议将文件名改为 `README_CN.md`，原位置留给英文版 `README.md`）：

```markdown
<div align="right">
  <sub>
    <a href="README.md">English</a> | 
    <strong>中文</strong>
  </sub>
</div>
