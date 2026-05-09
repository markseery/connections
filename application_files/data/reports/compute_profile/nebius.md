# How compute products and services are messaged: https://nebius.com

*Extract concrete, verifiable facts from a company's website: compute products and services, H100, B200, GB200, B300, GB300, NVL72, Vera Rubin*

*Based on 748 stored pages analysed in 2 batch(es) with 9 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: compute_profile).*

---

## Compute Products And Services

# Nebius Compute Products and Services — Consolidated Summary

## GPU Products

### NVIDIA GB300 NVL72 (Blackwell Ultra)
- Liquid-cooled, rack-scale systems with 72 Blackwell Ultra GPUs, 270 GB memory per GPU
- NVIDIA NVLink 5 interconnects; NVIDIA Quantum-X800 InfiniBand (800 Gb/s end-to-end)
- Purpose-built for maximum throughput and TCO on the most sophisticated AI workloads
- Key claims: 1,000 tokens/sec, 50x boost in AI Factory output performance, 30x improvement for real-time video generation
- Higher FP4 performance; average 12.6% reduction in training time vs HGX B200
- Europe's first operational deployment (Finland data center)
- HGX B300 variant also noted: "Built for the age of AI reasoning to enable the next wave of accelerated computing for every data center"
- **MLPerf Training v5.1**: 1st-place results (e.g., Llama-2-70B LoRA: 8.48 min on 8× B300 GPUs)

### NVIDIA GB200 NVL72
- Liquid-cooled, rack-scale platform: 72 Blackwell GPUs + 36 Grace CPUs connected via NVLink 5th gen (130 TB/s GPU-to-GPU)
- Up to 17 TB LPDDR5X memory; 28.8 Tbit/s InfiniBand
- Claimed 25x lower cost and energy consumption vs HGX H100
- Generally available in Europe; deployed in Finland and Kansas City
- Expected 180 GBps per rack for read operations
- Pre-orders accepted from December 2024

### NVIDIA HGX B200
- Air-cooled, Blackwell architecture; 208 billion transistors
- 1× or 8× B200 GPU (180 GB SXM); 16× or 128× vCPU (Intel Emerald Rapids); 224 or 1,792 GB DDR5; 3.2 Tbit/s InfiniBand
- **Pricing**: **$5.50/GPU-hour** (self-service) · **$3.00/GPU-hour** (commitment)
- Available now as self-service
- **MLPerf Training v5.1**: Llama-2-70B LoRA — 9.55 min (8 GPU), 5.82 min (16 GPU), 3.10 min (32 GPU); ~3.1× scaling 8→32 GPUs
- **MLPerf Inference v5.1**: 3×–4.3× performance over HGX H200

### NVIDIA HGX H200
- 1× or 8× H200 GPU (141 GB SXM); 16× or 128× vCPU (Intel Sapphire Rapids); 200 or 1,600 GB DDR5; 3.2 Tbit/s InfiniBand
- **Pricing**: **$3.50/GPU-hour** (self-service) · **$2.30/GPU-hour** (commitment)
- NVIDIA Exemplar Cloud Status (>97% performance benchmark for training workloads)
- Available now in Paris, Finland, Kansas City, and Iceland

### NVIDIA HGX H100
- 1× or 8× H100 GPU (80 GB SXM); 16× or 128× vCPU (Intel Sapphire Rapids); 200 or 1,600 GB DDR5; 3.2 Tbit/s InfiniBand
- 8 GPUs connected via NVLink 4th gen (900 GB/s)
- **Pricing**: **$2.95/GPU-hour** (self-service) · **$2.00/GPU-hour** (commitment) · **Explorer Tier: $1.50/GPU-hour** (first 1,000 hours/month)
- JupyterLab configuration starts at $2.95/hour
- Up to 16 H100 GPUs available immediately via self-service

### NVIDIA L40S
- 48 GB PCIe GPU; cost-effective for fine-tuning, experiments, and lightweight GenAI inference
- Intel config (8–40 vCPU, 32–160 GB): from **$1.55/hour**
- AMD EPYC config (16–192 vCPU, 96–1,152 GB): from **$1.82/hour**

### NVIDIA RTX PRO 6000 Blackwell Server Edition
- 96 GB GDDR7; FP4 precision support; MIG technology (up to 4 isolated 24 GB instances)
- Claimed up to 6× performance of L40S and >2× price-performance of HGX H100 for LLM inference
- Target workloads: AI inference, industrial robotics, physical AI simulations, visual computing, drug discovery

## CPU-Only Instances
- **AMD EPYC Genoa**: 4–64 vCPU, 16–256 GB RAM, from **$0.10/hour**
- **Intel Ice Lake**: 2–80 vCPU, 8–320 GB RAM, from **$0.05/hour**

## Networking
- **NVIDIA Quantum-2 InfiniBand**: up to 3.2 Tbit/s per host, 400 Gbit/s per GPU NIC
- **NVIDIA Quantum-X800 InfiniBand**: 800 Gb/s end-to-end (used with GB300 NVL72 and GB200 NVL72)
- Rail-optimized fat-tree topology in scalable units of 32 servers

## Storage
| Tier | Price | Key Specs |
|---|---|---|
| Shared Filesystem | $0.08/GiB/month | Up to 100 GBps & 1M IOPS aggregated reads (up to 1 TB/s read throughput) |
| WEKA Filesystem | $0.10/GiB/month | — |
| Object Storage – Standard | $0.0147/GiB/month | — |
| Object Storage – Enhanced | $0.11/GiB/month | Up to 2 GB/s write throughput per GPU |

## Commitment & Reserve Pricing (H100 examples)
| Term | Price/GPU-hour |
|---|---|
| 3-year reserve | $2.12 |
| 1-year reserve | $2.94 |
| 6-month reserve | $3.20 |
| 3-month reserve | $3.34 |

Up to **35% savings** on multi-month commitments across GPU types.

## Managed Services & Platform
- **Nebius AI Cloud**: Full-stack platform; bare-metal-level performance (no GPU/network virtualization)
- **Managed Kubernetes** (free); **Managed Soperator/Slurm** (free software, consumption-based)
- **Managed MLflow** (GA, from $0.14/h for 2 vCPU / 8 GB)
- **Managed PostgreSQL** (GA, from $0.14/h for 2 vCPU / 8 GB)
- **Apache Spark**, **JupyterLab**, **Container Registry**
- **Serverless AI**: DevPods, Jobs, and Endpoints
- **TractoAI**: Data processing and distributed training platform
- **Tavily**: Agentic search (acquired)

## Token Factory (formerly AI Studio)
- **60+ open-source models** (DeepSeek R1, Llama, Qwen, Mistral, GPT-OSS, etc.)
- **Fast and Base inference tiers**; dedicated endpoints with **99.9% SLA** and autoscaling
- **Fine-tuning**: LoRA and full FT for 30+ models
- **Batch API**: 50% of base model price
- **Image generation**: from $0.0013/image (Flux Schnell)
- Pricing examples: Qwen2.5-72B at $0.38/M output tokens (Fast); Llama-3.1-8B-Instruct at $0.02/$0.06 per M tokens (Base)
- Rate limits: up to 400K TPM default; 100M+ for enterprise

## Key Performance & Reliability Claims
- **MLPerf Training v5.0**: 124.5 min for Llama 3.1 405B on 1,024 H200 GPUs (128 nodes); near-linear scaling (1.97× with 2× GPUs)
- **MLPerf Training v5.1**: 7 first-place results out of 9 submissions
- **MLPerf Inference v5.1**: Leading results on GB200 NVL72 and HGX B200; 6.7% and 14.2% gains for Llama 3.1 405B on GB200 NVL72 vs previous best
- **167,000 GPU-hours MTBF** on 3,000-GPU cluster; **~12-minute average MTTR** for multi-host training node failures
- **~90% GPU utilization** reported by customers (e.g., Prime Intellect)
- **ISEG2 supercomputer**: Ranked as high as **#13 on Top500** (also cited as #16/#19 at different points), the most powerful commercially available system in Europe

## Data Center Locations & Scale
| Location | Details |
|---|---|
| **Finland** (owned) | Up to 75 MW, expanding; ISEG2 supercomputer; first GB300 NVL72 deployment |
| **New Jersey, USA** | Up to 300 MW (DataOne colocation) |
| **Kansas City / Missouri, USA** | Up to 40 MW (Patmos colocation); GB200 NVL72 deployed |
| **Independence, Missouri, USA** | Up to 1.2 GW approved |
| **Paris, France** | Equinix PA10 |
| **Iceland** | 10 MW (Verne); 100% renewable energy |
| **Surrey, UK** | Ark Data Centres; 4,000 Blackwell Ultra GPUs |

- **22,000+ NVIDIA Blackwell GPUs** planned for deployment
- Target: **5+ gigawatts** by end of 2030

## Compliance & Partnerships
- **Certifications**: SOC 2 Type II (including HIPAA), ISO 27001, ISO 27701, ISO 22301
- **Regulatory alignment**: NIS2, DORA, GDPR
- **NVIDIA partnerships**: Reference Platform Cloud Partner, Exemplar Cloud Partner

---

## Vera Rubin

# Consolidated Summary: Vera Rubin (NVIDIA Vera Rubin NVL72 Platform)

**Note:** The findings pertain to NVIDIA's **Vera Rubin NVL72** accelerated computing platform (named after the astronomer), not the astronomer herself. Below is the consolidated information from both batches.

---

## Overview

- **Nebius announced (January 5, 2026)** that it will offer the **NVIDIA Vera Rubin NVL72** in the US and Europe starting **H2 2026**.
- Nebius will be **"among the first NVIDIA Cloud Partners"** to bring this next-generation platform to market.

## Platform Capabilities

- **Vera Rubin NVL72** is engineered to serve complex AI workloads, including:
  - **Agentic AI**
  - **Advanced reasoning**
  - **Massive-scale mixture-of-experts (MoE) models**
- Designed to push computational limits across **long sequences of tokens for multistep problem-solving** with the **lowest cost per token**.

## Deployment & Integration

- Will be integrated across **Nebius's full-stack infrastructure**, available through both:
  - **Nebius AI Cloud**
  - **Nebius Token Factory**
- Deployed at data centers in the **US and Europe**.
- As of the most recent information, Vera Rubin is **not yet listed** on Nebius's pricing/GPU pages (which currently offer GB300 NVL72, GB200 NVL72, B300, B200, H200, and H100), consistent with its forward-looking H2 2026 availability.

## Key Partnership Agreements

- **NVIDIA–Nebius Strategic Partnership (March 11, 2026):**
  - Includes **early adoption of NVIDIA computing architectures**: the NVIDIA Rubin platform, **NVIDIA Vera CPUs**, and **NVIDIA BlueField storage systems**.
  - Enables Nebius to deploy **more than 5 gigawatts of capacity by end of 2030**.

- **Meta Infrastructure Agreement (March 16, 2026):**
  - Nebius will provide **$12 billion of dedicated capacity** across multiple locations, based on **one of the first large-scale deployments of the NVIDIA Vera Rubin platform**.
  - Delivery begins **early 2027**.
  - Additional capacity up to **$15 billion over five years**.

---

Both batches were fully consistent with no contradictions. Batch 2 provided the additional details about the Meta deal's $15 billion five-year ceiling and the 5 GW capacity target by 2030.

---

## Nvl72

# NVIDIA NVL72 — Consolidated Summary

The **NVL72** designation refers to NVIDIA's rack-scale architecture connecting **72 GPUs into a single, tightly coupled system**. Three generations have been announced or deployed: **GB200 NVL72**, **GB300 NVL72**, and **Vera Rubin NVL72**.

---

## GB200 NVL72 (Blackwell)

### Architecture
- **72 Blackwell GPUs (GB200, 384 GB each) + 36 Grace CPUs** (each with 72 Arm Neoverse V2 cores) in a single rack
- Up to **17 TB LPDDR5X** system memory
- **Fifth-generation NVLink** connects all 72 GPUs into a single NVLink domain with **130 TB/s total bisection bandwidth**
- **No direct GPU-to-GPU connectivity inside a server/compute tray** — all inter-GPU communication routes through external NVSwitch trays in the rack, making all 72 GPUs equivalent from a high-speed connectivity perspective
- Each compute tray contains **4 Blackwell GPUs**; each GPU gets a **dedicated NIC** for cross-rack InfiniBand connectivity (**28.8 Tbit/s InfiniBand** per rack)
- **Liquid-cooled** using a copper cable cartridge designed by NVIDIA

### Parallelism & Scheduling (Training Example)
- Nemotron-4 340B pre-training on 128 GPUs (2 racks): **TP=8** (spanning 2 compute trays within one rack), **PP=4** (spanning the NVLink domain), **DP=4** (across racks via InfiniBand)
- Uses the **Slurm block topology plugin** with `--segment` option for balanced GPU distribution

### Performance
- **25× lower cost and energy consumption** compared with NVIDIA HGX H100
- Storage: up to **180 GB/s per rack** for read operations
- MLPerf Inference v5.1: peak performance on **Llama 3.1 405B** — **855.82 tokens/s** (offline), **596.11 tokens/s** (server mode)
- 1 host with 4× Blackwell GPUs achieved a **54.8% performance gain** over an H200 host with 8× GPUs

### Availability & Pricing
- **Pre-orders opened December 2024** (>22,000 Blackwell GPUs planned)
- **Generally available in Europe** announced June 2025 at NVIDIA GTC Paris
- Deployed at Nebius data centers in **Finland** and **Kansas City**
- Preliminary VM configuration: **4 GPUs per VM**, 112 vCPU, 800 GB RAM, 28.8 Tbit/s InfiniBand, Ubuntu 24.04 LTS
- Pricing: "Pre-order / Contact us"

---

## GB300 NVL72 (Blackwell Ultra)

### Architecture & Networking
- Powered by **Blackwell Ultra GPUs** with **NVLink 5** interconnects for unified GPU memory access
- Integrates **NVIDIA Quantum-X800 InfiniBand** (800 Gbps) networking — **first system globally** to run on this fabric
- Liquid-cooled, rack-scale; purpose-built for the most sophisticated AI workloads

### Performance
- **1,000 tokens/s** generation throughput
- **50× boost in AI Factory output performance**
- **30× performance improvement** for real-time video generation
- Used for **MLPerf Training v5.1** benchmarks

### Availability
- **Europe's first operational production deployment**: Nebius' Finland data center, **December 17, 2025**
- Nebius was the first cloud provider in Europe to deploy Blackwell Ultra systems in production

---

## Vera Rubin NVL72

- Next-generation NVL72 platform
- Announced for **H2 2026** availability in the US and Europe
- First large-scale deployment planned as part of a **$12 billion Meta agreement**

---

## Key Takeaways

| Feature | GB200 NVL72 | GB300 NVL72 | Vera Rubin NVL72 |
|---|---|---|---|
| **GPU generation** | Blackwell | Blackwell Ultra | Vera Rubin |
| **Interconnect** | NVLink 4 (130 TB/s) | NVLink 5 | TBD |
| **Network fabric** | InfiniBand (28.8 Tbit/s) | Quantum-X800 (800 Gbps) | TBD |
| **Cooling** | Liquid | Liquid | TBD |
| **First production** | Dec 2024 (pre-order) / mid-2025 (GA) | Dec 2025 | H2 2026 |

---

## Gb300

# NVIDIA GB300 NVL72 — Consolidated Summary

## Product Overview
- The **GB300 NVL72** is a liquid-cooled, rack-scale system featuring **72 NVIDIA Blackwell Ultra GPUs** connected via **NVLink 5** interconnects.
- Purpose-built to deliver enormous throughput and total cost of ownership (TCO) for the most sophisticated AI workloads.
- Listed as an available product on the Nebius platform alongside other GPU options.

## Deployment & Availability
- **Europe's first operational deployment** brought online at the **Nebius Finland data center**, announced **December 17, 2025** as part of the **Aether 3.1 release**.
- At **GTC 2025 (March 2025)**, Nebius committed to giving customers access to GB300 NVL72-powered instances by end of 2025 — a commitment fulfilled with the December deployment.
- Running on **NVIDIA Quantum-X800 InfiniBand** networking at **800 Gb/s** — described as "Europe's first GB300 NVL72 on this next generation of high-speed fabric."

## Performance Highlights
- **1,000 tokens/s** generation speed
- **50× boost** in AI Factory output
- **30× improvement** for real-time video generation

## Related System: HGX B300 (Single-Host Configuration)
- Also made available to customers as part of the Aether 3.1 release alongside the rack-scale GB300 NVL72.
- Equipped with **270 GB memory per GPU** and higher FP4 performance compared to HGX B200.
- **MLPerf Training v5.1 results** (8 GPUs, single host):
  - Llama-2-70B LoRA: **8.48 min (1st place)**
  - Llama-3.1-8B: **75.84 min (1st place)**
  - Average **12.6% reduction in training time** vs. HGX B200
- Positioned for the "age of AI reasoning" to enable the next wave of accelerated computing for every data center.

## Customer & Ecosystem Adoption
- **Recraft V4**: Tested NVIDIA HGX B300 instances after initially adopting B200.
- **Prime Intellect**: Performed pre-training on HGX B300 systems after proof-of-concept work on GB200 NVL72.
- Nebius published guidance noting the material "will be valuable for people planning to design workloads for NVIDIA GB200 NVL72 or GB300 NVL72 in the future," indicating an active ecosystem preparing for these platforms.

---

## Gb200

# NVIDIA GB200 NVL72 on Nebius — Consolidated Summary

## Product Overview
- **GB200 NVL72** is a liquid-cooled, rack-scale platform designed for heavy model training and exceptionally low-latency inference (especially for reasoning models).
- Built around the **GB200 Grace Blackwell Superchip**, which pairs Blackwell GPUs with Arm-based Grace CPUs.
- "Densely packs and interconnects GPUs using a copper cable cartridge for operational simplicity."
- Nebius highlights that the **Arm architecture (Grace CPU)** is relatively new to the cloud/AI domain; Nebius' dedicated Linux kernel team provides support for compatibility and optimization.

## Specifications (per VM / rack)
| Component | Detail |
|---|---|
| GPUs | 72× GB200, 384 GB each |
| CPUs | 36× Grace CPU, each with 72 Arm Neoverse V2 cores |
| Memory | Up to 17 TB LPDDR5X |
| Networking | 28.8 Tbit/s InfiniBand |
| Storage throughput | Up to 180 GBps per NVL72 rack (read) |
| OS | Ubuntu 24.04 LTS |

## Performance & Efficiency
- **25× lower cost and energy consumption** compared with NVIDIA HGX H100.
- **MLPerf Inference v5.1**: First place for Llama 3.1 405B on GB200 systems — **855.82 tokens/s** (offline), **596.11 tokens/s** (server mode).

## Availability & Deployment
- **Pre-orders opened December 4, 2024** for deployment in the **United States (Kansas City, New Jersey)** and **Finland** from early 2025.
  - Kansas City: targeted H1 2025
  - New Jersey: targeted Summer 2025
- **Generally available in Europe as of June 2025** (announced at NVIDIA GTC Paris).
- Over **22,000 NVIDIA Blackwell GPUs** to be deployed across the Nebius AI-native cloud.

## Pricing
- Listed on the pricing page as **"NVIDIA GB200 NVL72*"** with a preliminary VM configuration of 4 GPUs — price is **"Contact us."**

## Customer Validation
- **Prime Intellect** proof-of-concept delivered "advanced performance out of the box."
- Referenced across multiple Nebius communications (blog posts, managed MLflow GA announcement, customer stories).

---

## B300

# NVIDIA HGX B300 — Consolidated Summary

## Overview
- Part of NVIDIA's **Blackwell Ultra** GPU family.
- Marketed as "built for the age of AI reasoning to enable the next wave of accelerated computing for every data center."

## Performance Benchmarks (MLPerf Training v5.1)
- Benchmarked with **8 GPUs**:
  - **Llama-2-70B LoRA**: 8.48 min — **1st place**
  - **Llama-3.1-8B**: 75.84 min — **1st place**
- **12.6% faster** than HGX B200 on average across these benchmarks.

## Cloud Availability (Nebius AI Cloud)
- Deployed in **production** as part of the **Aether 3.1** release (**December 17, 2025**).
- Connected via **NVIDIA Quantum-X800 InfiniBand** (800 Gbps).
- Nebius claims to be **the first cloud provider in Europe** to deploy NVIDIA Blackwell Ultra systems (both HGX B300 and GB300 NVL72) in production.
- Listed on Nebius AI Cloud product/pricing pages as **"Contact us"** (no self-serve pricing published).
- Sits within a GPU lineup that includes: GB300 NVL72, GB200 NVL72, B300, B200, H200, and H100.

## Customer / Use-Case Evidence
- **Recraft V4**: Moved to testing HGX B300 instances after successfully adopting HGX B200.
- **Prime Intellect**: Pre-training performed on HGX B300 systems.

## Key Differentiators
- Positioned as a successor/upgrade to the HGX B200, with measurable training-speed improvements (~12.6% average).
- Targeting AI reasoning and large-scale model training workloads.

---

## B200

# NVIDIA HGX B200 — Consolidated Summary

## Hardware Specifications
- **GPU:** NVIDIA B200 180GB SXM, based on the **Blackwell architecture** (208 billion transistors, second-generation Transformer Engine, fifth-generation NVLink)
- **Form factor:** Air-cooled, 8 GPUs per baseboard — same form factor as previous Hopper SXMs, enabling seamless integration into existing server racks
- **CPU:** Intel Emerald Rapids
- **Configurations available:** 1x or 8x B200 GPU; 16x or 128x vCPU; 224 or 1,792 GB DDR5
- **Interconnect:** 3.2 Tbit/s InfiniBand

## Availability & Pricing
- **Pre-orders** opened **December 4, 2024**; early adoption announced **March 2024** ("among the first cloud providers adopting NVIDIA B200")
- **Publicly available** as self-service AI clusters since **August 12, 2025**
- Accessible via web console and API (pay-as-you-go)
- **Pricing:**
  - **$3.00/GPU-hour** — commitment/volume pricing
  - **$5.50/GPU-hour** — on-demand self-service pricing

## Performance Benchmarks

### MLPerf Inference v5.1
| Workload | Metric | B200 Result | vs. HGX H200 |
|---|---|---|---|
| Llama 3.1 405B (offline) | tokens/s | 1,660 | **3×** |
| Llama 3.1 405B (server) | tokens/s | 1,280 | **4.3×** |
| Llama 2 70B (server) | tokens/s | 101,611 | **~3×** |
| Llama 2 70B (offline) | tokens/s | 101,246 | **~3×** |

### MLPerf Training v5.1 (Nebius submissions)
| Workload | 8 GPU | 16 GPU | 32 GPU | Scaling |
|---|---|---|---|---|
| Llama-2-70B LoRA | 9.55 min (2nd) | 5.82 min (1st) | 3.10 min (1st) | ~3.1× (8→32) |
| Llama-3.1-8B | 85.37 min (6th) | 51.83 min (1st) | 27.83 min (1st) | ~3.1× (8→32) |
| FLUX.1 | — | — | 93.17 min (1st) | — |

*"Excellent scaling"* — near-linear ~3.1× speed-up from 8 to 32 GPUs across workloads.

### Customer & Partner Benchmarks
- **DataRobot:** Up to **245,000 tokens/s** total throughput on 8× HGX B200 systems
- **TheStage AI:** ~**3.5× faster inference** for diffusion models vs. prior generation
- **PyTorch collaboration:** Up to **41% faster pre-training** of DeepSeek-V3 models on a 256-GPU HGX B200 cluster

## Use Cases
- Building and running **reasoning LLMs, multi-modal models, and agentic AI** (per Nebius product description)

---
