<div align="right">
  <sub>
    <strong>English</strong> | 
    <a href="README_CN.md">中文</a>
  </sub>
</div>

# The Norn Machine: Deterministic Context Assembly — A Serverless, Stateless Prompt Engine

## Building a Cloud-Native Prompt Engine for Ultra-Short Context Windows, Without RAG.

---

**Version**: 2.0  
**Status**: MVP Design Phase  
**Author**: Leo Wang  
**Date**: April 2026

---

> *In an era of generative AI built upon probabilities, this project is an architectural proposal on how to resolutely pursue determinism.*

---

## Table of Contents

1. [Abstract](#1-abstract)
2. [Design Philosophy: Three Core Discussions](#2-design-philosophy-three-core-discussions)
   - 2.1 Reject the Blackbox: Be Cautious of LLM Logical Deduction
   - 2.2 Context Control: State Compression to Mitigate Memory Bloat
   - 2.3 Dynamic Rendering: Real-time Generation from Behavioral Skeletons
3. [System Architecture](#3-system-architecture)
   - 3.1 Global Acceleration & Cloud Defense Topology
   - 3.2 The Minimalist Three-Step Pipeline
   - 3.3 Prompt Injection & Dynamic Reordering
4. [Security & Boundaries](#4-security--boundaries)
   - 4.1 Boundary Control for the Dialogue Loop
   - 4.2 Guardrails Strategy
5. [Cross-Domain Transferability](#5-cross-domain-transferability)
   - 5.1 Conversational Commerce
   - 5.2 Dynamic Game NPC Interaction
   - 5.3 Personal AI Business Card
6. [Performance & A/B Testing (To Be Added)](#6-performance--ab-testing-to-be-added)
7. [Conclusion](#7-conclusion)

---

## 1. Abstract

Current generative applications built on Large Language Models (LLMs) frequently encounter challenges such as uncontrollable hallucinations and latency spikes caused by long context windows. Mainstream solutions often try to mitigate these issues through Retrieval-Augmented Generation (RAG) or complex prompt engineering, but they do not fundamentally resolve the structural problem of "having the LLM handle both decision-making and expression simultaneously."

The Norn Machine proposes an alternative approach: **Let deterministic rules handle the decision logic, and let the LLM focus on linguistic rendering.**

We removed the originally planned large volume of manually pre-written texts and introduced a weighting algorithm that combines "selection round decay," "hesitation duration," and "cancellation frequency." Paired with a basic cloud defense architecture using CloudFront and API Gateway, the final generation phase only passes a condensed "behavioral profile skeleton" to the LLM for real-time rendering. The system employs a Stateless design, storing no user data, attempting to achieve lower response latency while ensuring privacy.

---

## 2. Design Philosophy: Three Core Discussions

This architecture attempts to respond to several common engineering misuses in current AI implementations.

### 2.1 Reject the Blackbox: Be Cautious of LLM Logical Deduction

**Common Misuse**: Feeding user behaviors or massive amounts of context directly into an LLM, asking the model to deduce the user's psychological traits or next actions. This easily leads to the same input producing inconsistent outputs at different times, increasing the risk of hallucinations.

**Architectural Approach**: Large models are essentially probabilistic text generators, not precise calculation engines. In The Norn Machine, we handle logic with deterministic algorithms inside the "Fast Thinker" engine. By introducing **"Selection Round Decay" (later choices carry higher weight)**, **"Hesitation Tracking"**, and **"Cancellation Judgment"**, we converge these implicit behaviors into specific feature coordinates at the code layer. The large model in this system serves only as a "renderer," responsible for converting structured data into natural language.

### 2.2 Context Control: State Compression to Mitigate Memory Bloat

**Common Misuse**: Relying heavily on extremely long context windows to maintain "personas" or dialogue continuity by stuffing extensive chat histories into the Prompt. This not only increases API costs and response latency but also risks the LLM forgetting intermediate information (Lost in the Middle).

**Architectural Approach**: We attempt **minimalist state compression and a Stateless design**. The system does not retain the user's lengthy "original chat text"; it only dynamically updates the user's "behavioral tendency coordinates" at the code layer. Each request is independent for the backend, only requiring a highly condensed, few-hundred-word skeleton to be passed to the LLM. This architecture effectively reduces the Payload size of a single request, optimizes response times, and achieves zero data footprint—relying on no database to store user sessions.

### 2.3 Dynamic Rendering: Real-time Generation from Behavioral Skeletons

**Common Misuse**: Traditional testing products often rely on pre-written, fixed copy for condition matching, which requires immense manual writing and maintenance efforts, and the outputs can feel repetitive across different users.

**Architectural Approach**: Replacing static copy libraries with **Dynamic Rendering**. The backend Finite State Machine (FSM) passes an exact but dry "behavioral profile skeleton" (e.g., decision preferences, blind spots, stress responses) to the model. The LLM combines this with dynamically routed Few-Shot examples (like intuitive or analytical styles) to supplement details at runtime.

Crucially, **the amount of input information directly dictates the output resolution and dialogue depth**. If the user provides minimal information (few selection rounds), the system loads a shorter, fuzzier, more intuition-based template, and strictly limits the number of permitted follow-up questions. Conversely, rich input information unlocks detailed, precise templates and deeper conversational turns. Even if the final personality type is identical, variations in hesitation time or information density will shift the skeleton's weights, causing the LLM to render different thematic focuses.

---

## 3. System Architecture

### 3.1 Global Acceleration & Cloud Defense Topology

The architecture utilizes a Serverless cloud-native form:

```text
[User Browser] 
      │ (HTTPS)
      ▼
[AWS CloudFront CDN]  (Content delivery, basic Anti-DDoS)
      │
      ▼
[AWS API Gateway]     (API Key validation, Request limiting)
      │
      ▼
[AWS Lambda]          (Fast Thinking algorithm + Prompt Reordering)
      │
      ▼
[Amazon Bedrock]      (Claude Inference & Guardrails)
```

- **CloudFront Delivery**: Static frontend assets are delivered via edge nodes, optimizing latency for large images and providing basic DDoS protection.
- **API Limiting**: API Gateway is configured with Usage Plans and API Keys to control invocation frequency, preventing malicious concurrency from exhausting model quotas.

### 3.2 The Minimalist Three-Step Pipeline

The backend Lambda handles core orchestration in three steps:

**Step 1: Fast Thinker Engine**
Processes `[Card ID, Round, Hesitation Duration, Cancellation Count]` passed from the frontend. Using time decay and weighting calculations, it quickly derives the user's feature coordinates.

**Step 1.5: Signature Signal Algorithm (NEW)**
After computing the 4D personality coordinates, the system runs a **Signature Signal** extraction: it cross-references each concrete visual motif's frequency (weighted by the same round-decay and hesitation scheme) with its **cosine similarity** to the player's final personality vector. The single motif that scores highest on both frequency and alignment is extracted as the player's "signature signal" — a specific, concrete item (e.g., "lighthouse," "rain window," "light strip") that the LLM is instructed to mention by name during rendering. This creates a moment of precise recognition ("How did it know?") that elevates the reading from generic to personal, and serves as an early prototype for product-specific recommendation in conversational commerce scenarios.

**Step 2: Template Router & Dynamic Reordering**
The system performs **Dynamic Reordering** based on the calculated feature strengths. The implicit traits with the highest weight are prioritized at the top of the Prompt, utilizing the LLM's attention advantage on preceding information to enhance the expression priority of key traits.

**Step 3: Slow Thinker Rendering (via Bedrock)**
Injects the reordered Prompt into Bedrock. The LLM translates the structured deduction results into coherent feedback text.

---

## 4. Security & Boundaries

For public-facing AI applications, preventing Prompt Injection and the output of inappropriate content are crucial considerations.

### 4.1 Boundary Control for the Dialogue Loop
In the subsequent dialogue interactions, the architecture imposes strict constraints:
- **Dynamic Turn Limits Based on Information Density**: The depth of follow-up questions is strictly bound to the amount of information gathered (selection rounds). It does not offer unlimited open chatting, strictly controlling the session lifecycle.
- **Character Count Blocks**: Restricts the number of characters a user can input on both the frontend and backend, reducing the risk of long-text jailbreak attacks.

### 4.2 Guardrails Strategy
Utilizes Amazon Bedrock Guardrails for added security filtering:
- Rejects jailbreak instructions unrelated to the personality reading.
- Filters inappropriate language and sensitive topics.
- When an interception is triggered, the system smoothly falls back to a preset safety message (e.g., "The threads of fate are tangled too tightly; the loom temporarily cannot decipher this"), ensuring a consistent experience.

---

## 5. Cross-Domain Transferability

The paradigm of **"Implicit Behavior Tracking + State Compression + Dynamic Linguistic Rendering"** utilized by The Norn Machine holds potential for migrating to other business scenarios:

### 5.1 Conversational Commerce

Traditional e-commerce filtering relies on static tags, while current AI shopping assistants, if heavily dependent on RAG, are susceptible to retrieval quality issues and might recommend out-of-stock items.
This architecture offers an alternative approach:
- **Behavior Tracking**: The frontend records the user's **hesitation duration**, **zoom actions on details**, or **repeated cancellations** while browsing product images. The backend quantifies these implicit data points into a consumer preference skeleton (e.g., cares more about visual design than specs, or is extremely sensitive to a certain price range).
- **Deterministic Inventory Whitelist**: The code layer cross-references with the actual inventory database, ensuring only in-stock product IDs are passed.
- **Dynamic Sales Pitch Rendering**: The LLM receives the "consumer skeleton" and "deterministic in-stock products." It is no longer responsible for retrieving products; instead, it is **specifically responsible for using the rhetoric that best fits the user's preferences (such as emotional narrative or hard-core data analysis) to introduce the determined product**, thereby potentially increasing conversion rates and avoiding product hallucinations.

### 5.2 Intelligent Customer Service & Assistant Systems

- **State Management**: The backend logic maintains Standard Operating Procedures (SOPs), the user's current business node, and compliance boundaries.
- **Behavior Compression**: The user's time spent reading documentation, hesitation on certain options, or repeated questioning are extracted as state parameters.
- **Dialogue Rendering**: The LLM generates answers solely based on the "current business skeleton" and limited facts. It flexibly handles edge cases with an anthropomorphic tone while maximally avoiding the hallucinations and babbling risks brought by unrestricted Agent designs.

### 5.3 Personal AI Business Card

- **Interest Inference**: Based on the time a visitor spends on different sections of a digital resume, infers whether they are more focused on technical depth or business experience.
- **Factual Rendering**: The LLM dynamically introduces deterministic factual resume details, adjusting the emphasis and tone to be most agreeable to the visitor.

---

## 6. Performance & A/B Testing (To Be Added)

*【This section is reserved for future benchmark data placeholders】*

### 6.1 End-to-End Latency Breakdown
- **Network Connection & CDN Overhead**: [To Be Tested]
- **API GW + Lambda Internal Overhead**: [To Be Tested]
- **Bedrock Inference & Response Overhead**: [To Be Tested]

### 6.2 Architectural Comparative Analysis
- **VS Traditional End-to-End Prompting Architecture**: [Comparison on consistency and hallucination rates]
- **VS Long-Context/RAG Architecture**: [Comparison on Token consumption costs and response latency]

---

## 7. Conclusion

The architectural iteration of The Norn Machine attempts to illustrate that: **in the engineering practice of AI products, setting clear boundaries for large models is equally important.**

We reduced the privacy burden through a stateless design, assisted intention capturing by quantifying behavioral time (hesitation, cancellation, decay); protected system availability with CDN and rate limiting; and restricted output risks with dynamic reordering and Guardrails. Ultimately, we left the flexibility of linguistic rendering to the LLM.

This is not only the underlying architecture of a personality test but also an engineering practice in seeking a balance between performance, security, privacy, and business determinism.
