# Norn Image Manager

The image manager maintains the curated visual dataset behind The Norn Machine.

Its purpose is not to invent personality logic at runtime. The personality-to-image mapping should live in structured metadata and rules, while the manager only helps seed, audit, ingest, and export that data consistently.

## Core Principles

- Do not randomly generate personality descriptions.
- Do not default to male subjects.
- Do not default to cyberpunk imagery.
- Do not require every card to contain a person.
- Give each of the 16 MBTI-like archetypes its own visual logic.
- Keep every card tied to a unique scene identity.
- Treat images as semantic signals, not decorative assets.

## Metadata Contract

Each card record describes both what the player sees and how the backend should interpret the choice.

Important fields include:

- `mbti_type`
- `role_group`
- `layer1_coords`
- `trait_keywords`
- `visual_rationale`
- `scene_id`
- `scene_en`
- `motifs`
- `human_presence`
- `subject_mode`
- `gender_mode`
- `people_count`
- `style_family`
- `palette`
- `composition`
- `gen_prompt_raw`

The backend eventually consumes a leaner card dataset, but the manager keeps the richer source metadata available for auditing and future regeneration.

## Visual Policy

The v2 dataset was created to avoid a previous failure mode: many cards drifting toward the same cyberpunk, male, single-character composition.

The current policy separates several concerns:

- personality coordinates define analytical meaning;
- motifs define concrete visual hooks;
- scene rationale explains why the image belongs to that archetype;
- human-presence policy decides whether the image should be empty, single-person, pair-based, or group-based;
- style, palette, and composition fields keep the visual system diverse.

## Data Flow

Conceptually, the asset flow is:

1. curated rules define archetype-to-visual logic;
2. metadata records are seeded from those rules;
3. generated images are matched back to metadata records;
4. audit checks catch duplication and drift;
5. exported datasets feed the frontend cards and backend analysis.

The management scripts live in this directory and are intentionally not reproduced here. The important contract is the metadata shape and the separation between visual generation, dataset auditing, and runtime personality analysis.

## What To Maintain

The most important file is the curated rule set under `config/`. That is where aesthetic direction and personality mapping should evolve.

The scripts are executors. The rule set is the product logic.
