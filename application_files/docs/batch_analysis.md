# Batch Analysis

`application_files/scripts/batch_analysis.py`

## Purpose

Analyse a previously-scraped website across a configurable set of topics.
The script reads stored page content through the **batcher skill**, runs
AI-powered extraction using adaptive batching and multi-topic prompts,
and outputs a consolidated markdown report.

Topics and extraction guidance are defined in **YAML analysis profiles**,
making the script reusable for marketing intelligence, technical audits,
competitive analysis, or any domain-specific extraction.

## Analysis profiles

Profiles live in `application_files/config/analysis_profiles/<name>.yaml`.

### Profile format

```yaml
name: Marketing & Competitive Intelligence
description: >
  Extract marketing-relevant intelligence from a company's website.

# Storage source
namespace: webscrape       # storage namespace containing the records
key_separator: "\x00"      # separator between sitename and page URL in keys

# AI settings
ai_profile: agent          # aiserver profile to use
context_pct: 0.80          # fraction of context window to fill with content
chars_per_token: 3.5       # approximate characters per token
ai_timeout: 660            # HTTP timeout (seconds) for AI calls

topics:
  - name: Headquarters
    guidance: >
      Look for the company's main office location, headquarters
      city/state/country, and any mention of corporate address.

  - name: Products
    guidance: >
      Extract product names, product lines, and brief descriptions
      of what each product does.  Include pricing tiers if mentioned.
```

**Fields:**

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | yes | — | Human-readable profile title (appears in report header) |
| `description` | no | — | Included in the AI prompt to frame the analysis context |
| `namespace` | no | `webscrape` | Storage namespace containing the scraped records |
| `key_separator` | no | `\x00` | Separator between sitename and page URL in storage keys |
| `ai_profile` | no | `agent` | aiserver profile (maps to a provider + model) |
| `context_pct` | no | `0.80` | Fraction of the model's context window to fill with content |
| `chars_per_token` | no | `3.5` | Approximate characters per token for batch size planning |
| `ai_timeout` | no | `660` | HTTP timeout in seconds for AI generation calls |
| `topics` | yes | — | List of topics, each with `name` and optional `guidance` |
| `topics[].name` | yes | — | Topic name (used as section heading in the report) |
| `topics[].guidance` | no | — | Extraction guidance included in the AI prompt — tells the model what specifically to look for, what format to use, and what to infer |

### Included profiles

| Profile | Topics | Focus |
|---|---|---|
| `marketing` | 16 | Corporate facts, infrastructure, customers, offerings, positioning |
| `technical` | 10 | Architecture, hardware stack, APIs, security, benchmarks, pricing |

### Creating a new profile

1. Create `application_files/config/analysis_profiles/<name>.yaml`
2. Define topics with guidance specific to your analysis
3. Run: `python3 batch_analysis.py https://example.com --profile <name>`

List available profiles:

```bash
python3 batch_analysis.py --list-profiles https://placeholder
```

### How guidance improves extraction

Without guidance, the AI interprets each topic name broadly. Guidance narrows
the extraction to specific signals:

```yaml
# Without guidance — the AI decides what "Products" means
- name: Products

# With guidance — the AI knows exactly what to extract
- name: Products
  guidance: >
    Extract product names, product lines, product categories, and brief
    descriptions of what each product does.  Include pricing tiers if
    mentioned.  Distinguish between current products and discontinued or
    legacy products.
```

Guidance is embedded directly into the extraction prompt alongside the topic
name, so the AI sees both the topic and its context in a single pass.

## Optimisation techniques

### 1. Multi-topic prompts

All topics are extracted in a **single structured prompt per batch**.
The AI returns numbered sections, one per topic, and the script parses them
back into individual findings.

Reduces AI calls per batch from N (number of topics) to 1.

### 2. Adaptive batching via context window probing

The script queries the aiserver's `/model-info` endpoint to discover the
**actual context window** of the configured model (resolved via live API
probe, YAML fallback, or env override). It then computes a character budget:

```
content_budget = context_window_tokens × chars_per_token × context_pct − prompt_overhead
```

Where `chars_per_token` and `context_pct` come from the profile YAML, and
`prompt_overhead` accounts for the extraction template, all topic names,
all guidance text, and the profile description.

Pages are bin-packed into batches that fill the budget. No content is truncated.

### Combined effect

For a 748-page site with the `marketing` profile (16 topics):

| Model | Context | Batches | Phase 1 | Phase 2 | Total calls |
|---|---|---|---|---|---|
| Claude Opus 4.6 | 1M tokens | 1 | 1 | 0 | 1 |
| GPT-4o | 128k tokens | ~6 | 6 | ~16 | ~22 |
| Mistral 7B (MLX) | 32k tokens | ~20 | 20 | ~16 | ~36 |

Compared to ~416 calls with the original single-topic, fixed-batch approach.

## Three-phase execution

**Phase 1 — Batch extraction.** For each adaptively-sized batch, sends a
single multi-topic prompt. The prompt includes the profile's description and
each topic's guidance. Responses are parsed into per-topic findings. Topics
with `NO_RELEVANT_INFO` are discarded.

**Phase 2 — Synthesis.** For each topic with findings from multiple batches,
a consolidation prompt merges per-batch extractions. Single-batch topics skip
synthesis. Empty topics are marked as such.

**Phase 3 — Report.** Assembled into markdown with profile name in the header,
one section per topic, and run metadata (pages, batches, AI calls, model).

## Output

Reports are written to `application_files/data/<profile_name>/<site>_<timestamp>.md`
by default. Use `--out` to override.

```
application_files/data/marketing/nebius_com_20260328_143022.md
application_files/data/technical/nebius_com_20260328_150112.md
```

## CLI reference

```
python3 batch_analysis.py <url> [options]
```

| Flag | Default | Description |
|---|---|---|
| `url` | — | Website to analyse (must be previously scraped) |
| `--profile` | `marketing` | Analysis profile name |
| `--out` | auto-generated | Override output path |
| `--namespace` | from profile | Override the profile's storage namespace |
| `--context-pct` | from profile | Override the profile's context window fill fraction |
| `--registry-url` | from env | Registry server URL |
| `--worker-url` | auto-discovered | Worker URL override |
| `--list-profiles` | — | List available profiles and exit |

## Context window resolution

The aiserver resolves context window size through a four-layer chain:

1. **Environment variable:** `AISERVER_CONTEXT_WINDOW_<MODEL>`
2. **Live probe:** Anthropic, Google, and Ollama APIs are queried at startup
3. **YAML table:** `config/aiserver.yaml` → `context_windows.<model>`
4. **YAML default:** `config/aiserver.yaml` → `default_context_window`

The `/model-info` response includes `context_window_source` (`env`, `probe`,
`yaml`, or `default`) for transparency.

## Services used

| Service | Purpose |
|---|---|
| Registry | Discover worker and aiserver URLs |
| Worker (batcher_skill) | Paginated access to stored page records |
| Storage | Underlying record store (called by batcher) |
| AI Server | `/model-info` for context window; `/generate` for extraction and synthesis |
