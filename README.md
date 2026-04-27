<div align="right">
  <sub>
    <strong>English</strong> |
    <a href="README_CN.md">中文</a>
  </sub>
</div>

# The Norn Machine

## A stateless prompt engine for deterministic context assembly

**Version**: 2.0 MVP  
**Author**: Leo Wang  
**Date**: April 2026

The Norn Machine is an AI personality-reading experiment built around a simple engineering principle:

**Deterministic code should make the judgment. The language model should render the judgment.**

The product looks like an intuitive card-selection ritual. Underneath, the frontend records only lightweight behavioral signals, the backend compresses them into a stable behavioral profile, and the model turns that profile into a readable result. The system does not store user sessions or raw choices in a database.

## Why This Exists

Many LLM applications let the model do everything at once: infer intent, remember context, choose facts, enforce safety, and write the answer. That is flexible, but it also creates inconsistent outputs, long prompts, higher latency, and more room for hallucination.

The Norn Machine uses a narrower model role. It sends the LLM a compact, structured profile:

- decision style
- relationship pattern
- stress response
- blind spot
- suggestion
- optional emotional and visual anchors

The model is still valuable, but it is treated as a renderer rather than an oracle.

## Architecture

```text
[Browser]
   |
   | HTTPS
   v
[CloudFront]
   |
   v
[S3 static frontend]

[Browser]
   |
   | POST /analyze, /dialogue
   v
[API Gateway]
   |
   v
[Lambda: Fast Thinker + Template Router]
   |
   v
[Amazon Bedrock: Slow Thinker + Guardrails]
```

CloudFront serves the frontend over HTTPS. API Gateway protects the Lambda endpoint with an API key and usage plan. Lambda performs deterministic feature extraction and prompt assembly. Bedrock handles language rendering and guardrail checks.

## Runtime Pipeline

### 1. Frontend Interaction

Players move through image-card rounds. Each round allows single selection, multiple selection, or no selection. The frontend tracks:

- selected card IDs and round numbers
- total rounds
- round durations
- deselection events

The app also supports a zero-selection result. If a player reveals fate without selecting any card, the frontend and backend both return a fixed mystical fallback instead of hanging on model inference.

### 2. Fast Thinker

The Fast Thinker is pure deterministic Python. It turns behavior into four-dimensional coordinates and summary signals.

Current signals:

- **Round decay**: later rounds carry more weight because later choices tend to reflect a sharper preference.
- **Hesitation bonus**: duration is a small nudge only, because image loading and rendering can pollute timing data.
- **Deselection signal**: canceling a selected card is treated as a stronger uncertainty signal than raw hesitation time.
- **Motif aggregation**: repeated traits, motifs, scenes, and rationales are weighted alongside the coordinate calculation.
- **Signature signal**: one optional word-level motif is selected from weighted motif frequency and cosine similarity against the final coordinate vector.

The signature signal is not a separate inference layer. It is integrated into the existing aggregation pass so the architecture stays small.

### 3. Template Router

The Template Router chooses a prompt mode based on information density:

- 1-5 rounds: short, intuitive reading; one follow-up dialogue turn.
- 6-20 rounds: sharper behavioral reading; two follow-up dialogue turns.
- More than 20 rounds: reflective long-form reading; three follow-up dialogue turns.

Every prompt receives the same behavioral skeleton. The style may change, but the analytical backbone stays stable.

### 4. Slow Thinker

The Slow Thinker calls Bedrock to render the final text. The model receives compressed facts and a small number of few-shot examples. It is instructed to translate concrete image anchors into broader emotional and behavioral patterns, rather than listing the objects the player selected.

### 5. Final Dialogue

After the result appears, the dialogue panel fades in. Input is capped at 100 characters, and the number of turns is bound to the number of rounds. When the limit is reached, the system closes the loop with an in-world response instead of continuing open-ended chat.

## Frontend Experience

The frontend is a static app. The reveal scene uses a canvas particle sequence:

1. particles gather while the result is being prepared;
2. the result text appears;
3. the particle cluster dissipates after the result is visible.

This timing matters because the animation should belong to the reading, not finish silently while the API is still loading.

Music is user-gesture bound: it starts when the player begins the test, which keeps browser autoplay behavior predictable.

## Security And Privacy

- No database stores player sessions.
- The backend receives only the current request payload.
- Dialogue is capped by turn count and character length.
- API Gateway provides API-key validation and request limiting.
- Bedrock Guardrails provide an additional output safety layer.
- The public frontend entry is CloudFront HTTPS; direct S3 website hosting is not the intended public route.

## Transferable Pattern

The useful pattern is not personality testing itself. It is:

**implicit behavior capture + deterministic state compression + model-rendered language**

This can transfer to:

- **Conversational commerce**: infer preference style from browsing behavior, then let the model explain deterministic product candidates.
- **Customer support**: keep workflow state in code, then let the model render bounded replies.
- **Game NPCs**: let game state and rule logic decide what is true, then let the model express it in character.
- **Personal AI profiles**: adapt presentation emphasis based on visitor behavior while keeping factual content fixed.

## Appendix: Asset Pipeline Concepts

The image pipeline is based on curated metadata rather than random prompt generation.

Each card has:

- a personality coordinate vector
- a role group
- trait keywords
- visual motifs
- a scene rationale
- human-presence policy
- style, palette, and composition fields

The image manager exists to keep those concepts consistent across the card set: it seeds curated metadata, audits duplicate prompts, tracks generated assets, and exports frontend/backend card data. The management scripts are part of the repository, but the important idea is the data contract: every image is both a visual asset and a structured semantic signal.

## Conclusion

The Norn Machine is a small product, but it tests a larger architectural stance: the more important the judgment is, the less casually it should be delegated to the model. Code should compress and constrain the world; the model should make that compressed world feel alive.
