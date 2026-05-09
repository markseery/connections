# Marketing – Factual & Quantitative: https://lambda.ai

*Extract concrete, verifiable facts from a company's website: corporate details, infrastructure specs, customer names, product listings, and quantitative claims.*

*Based on 371 stored pages analysed in 1 batch(es) with 1 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: operational_metrics).*

---

## Number Of Data Centers, Their Locations, Gpus, And Power Capacity

**Data center locations and facilities mentioned:**
- **Mountain View, CA** – ECL facility (MV1), zero-water and zero-emissions off-grid modular data center powered by hydrogen fuel cells. Lambda doubled its footprint from 50% to 100% of the facility. Hosts NVIDIA GB300 NVL72 systems (Supermicro-built, 142 kW per system).
- **Southern California (Vernon, CA)** – Prime Data Centers LAX01, Vernon's first AI-ready data center: 33 MW critical power across 242,000 sq ft and six data halls. Lambda initially leasing 21 MW.
- **Kansas City, MO** – Planned AI Factory launching early 2026 with 24MW capacity initially, potential to scale to 100MW+. More than 10,000 NVIDIA Blackwell Ultra GPUs.
- **Columbus, Ohio** – Cologix COL4 ScalelogixSM data center. NVIDIA HGX B200-accelerated 1-Click Clusters deployed. Cologix portfolio: 500,000 sq ft and 80 MW across four Columbus data centers.
- **Chicago, IL** – EdgeConneX partnership: 23MW single-tenant data center (RFS 2026), plus existing air-cooled sites.
- **Atlanta, GA** – EdgeConneX partnership: Over 30 MW planned (combined with Chicago).
- **Texas** – Lambda Cloud region (persistent storage since April 2022).
- **Washington DC (US-East-2, US-East-3)** – Filesystem S3 Adapter supported regions.
- **California, Illinois** – mentioned as Lambda operational regions.

**Power capacity claims:**
- Contiguous AI factories: **75MW+ campuses** for large clusters and mission-critical workloads.
- Vision to get to **3GW+ of AI data center space**.
- Lambda's megawatt footprint **multiplying by 4× between 2025 and 2026**.
- Pursuing **gigawatt-scale AI factories**.
- Rack power roadmap toward **1 MW rack-scale designs**.
- Individual racks operating at **130 to 240 kW per rack and beyond** (per Ken Patchett, VP of Data Center Infrastructure).
- NVIDIA GB300 NVL72 systems: **142 kW per rack**.

**GPU types offered (operational and planned):**
- NVIDIA GB300 NVL72 (72 Blackwell Ultra GPUs + 36 Grace CPUs per rack)
- NVIDIA HGX B300
- NVIDIA HGX B200 (SXM6) – 180 GB VRAM per GPU
- NVIDIA H200
- NVIDIA H100 SXM and PCIe – 80 GB VRAM
- NVIDIA GH200 Grace Hopper Superchip – 96 GB HBM3
- NVIDIA A100 SXM (40 GB and 80 GB) and PCIe
- NVIDIA A6000, A10, Tesla V100, Quadro RTX 6000
- **Planned:** NVIDIA Vera Rubin NVL72 (production availability planned second half of 2026)

---

## Active And Contracted Megawatts, Terawatts, And Other Measures Of Power

- **Prime Data Centers LAX01 (Vernon, CA):** 33 MW critical power total; Lambda initially leasing **21 MW**.
- **Kansas City, MO:** Launching with **24 MW** capacity, potential to scale to **100MW+**.
- **EdgeConneX Chicago + Atlanta:** Over **30 MW** of AI-enabled data center infrastructure planned.
- **Contiguous AI factory campuses:** **75MW+**.
- **Vision:** Get to **3GW+** of AI data center space.
- **Cologix Columbus:** 80 MW across four data centers (Cologix portfolio total).
- Legacy data centers: **2 to 15 kW per rack**; Lambda's new facilities: **130 to 240 kW per rack**.
- NVIDIA GB300 NVL72 rack: **142 kW** compute power per rack.
- Individual GPU TDPs mentioned: H100 SXM up to 700W; A100 SXM 400W; RTX 3090 350W; RTX 4090 450W.
- Hydrogen-powered systems at ECL Mountain View – **zero-emissions energy**.
- Power delivery: Direct-to-chip liquid cooling improves PUE by over 15%.

---

## Installed, Active And Future Number Of Gpus

- **Superclusters:** 4,000 to **165,000+ NVIDIA GPUs** per deployment.
- **1-Click Clusters:** 16 to **2,000+ NVIDIA GPUs** per cluster (HGX B200 or H100).
- **Instances:** 1 to 8 GPUs per instance.
- **Kansas City, MO:** More than **10,000 NVIDIA Blackwell Ultra GPUs** at launch, expected to double over time.
- **Microsoft agreement:** "Multibillion-dollar agreement" to deploy AI infrastructure powered by **"tens of thousands of NVIDIA GPUs"** including GB300 NVL72 systems.
- **Gigawatt-Scale AI Factory blog:** First NVIDIA GB300 NVL72 systems stood up with **72 NVIDIA Blackwell Ultra GPUs and 36 NVIDIA Grace CPUs per rack**.
- **10x GH200 demo clusters** mentioned with up to **720 Grace CPUs** and 960GB of H100 GPU memory.
- **DeepChat benchmark:** Testing on **1,024 GPUs across 128 servers** (8x H100 SXM5 per server).
- Lambda serves **over 200,000 AI developers** (referenced in Exemplar Cloud announcement).
- **Private Cloud:** Can scale to **10k+ GPUs**.
- **128K-GPU clusters** referenced as trajectory target (per Ken Patchett in AI Factories podcast).
- **One person, one GPU** stated as Lambda's mission/vision.

---

## Teraflops And Exaflops

- **NVIDIA GB200 NVL72:** delivers **1.4 exaFLOPS** of AI performance and 30TB of fast memory (from Lambda's blog on Blackwell).
- **NVIDIA HGX B300:** **72 PF FP8 training / 144 PF FP4 inference** per system; **2.1 TB HBM3e** memory.
- **NVIDIA H100 SXM:** TF32 Tensor Core peak theoretical: **156 TFLOPS**; FP16 Tensor Core: **312 TFLOPS**; FP8 Tensor Core (with sparsity): **3,958 TFLOPS**.
- **NVIDIA H100 PCIe:** FP16: **1,979 TFLOPS** (sparse); FP8: **3,958 TFLOPS** (sparse).
- **NVIDIA A100:** TF32 Tensor Core peak: **156 TFLOPS** (for A100 SXM4); FP16 Tensor Core: **312 TFLOPS**.
- **NVIDIA V100:** FP32 peak: **15.7 TFLOPS**; FP16 Tensor Core: **125 TFLOPS** (125.6 TFLOPS referenced).
- **A100 vs V100:** A100 estimated actual performance ~18.1 TFLOPS FP32.
- **Hyperplane-16 V100 server benchmark:** 16x V100 SXM3, NVSwitch with 900GB/s total switching capacity per NVSwitch.
- **Lambda's FlashAttention-2 blog:** FlashAttention-2 enables replicating GPT3-175B training with **242,400 GPU hours** (H100 80GB SXM5), translating to **$458,136** using three-year reserved cluster.

---

## Other Capacity And Performance

**Pricing (selected):**
- 1-Click Clusters: NVIDIA HGX B200 on-demand **$4.62/GPU/hr**; H100 on-demand **$2.76/GPU/hr**. Reserved pricing available for 1-3 year terms.
- Instances: B200 SXM6 8x from **$5.74/GPU/hr**; H100 SXM 8x from **$3.44/GPU/hr**; A100 SXM 80GB 8x **$2.06/GPU/hr**; GH200 1x **$1.99/hr**; A10 1x **$0.86/hr**; RTX 6000 1x **$0.58/hr**.
- Storage: **$0.20/GB/month**, no ingress/egress fees.

**Networking:**
- NVIDIA Quantum-2 InfiniBand: **400 Gb/s** per port; aggregate **3.2 Tb/s** between nodes.
- NVIDIA Quantum-X800 InfiniBand: **800 Gb/s** per GPU; **6.4 Tb/s** bandwidth between nodes.
- NVLink-C2C: **900 GB/s** bidirectional (GH200, GB200).
- NVLink 5 (Vera Rubin NVL72): **1.8 TB/s** bandwidth per GPU.
- NVLink 6 (Vera Rubin): **3.6 TB/s** GPU-to-GPU connectivity.
- Co-packaged optics (CPO): NVIDIA claims **3.5x power efficiency** improvement, **10x higher resilience**, **1.3x faster time to operation**.

**Memory per system:**
- NVIDIA GB300 NVL72: **37 TB fast memory** per rack, **130 TB/s NVLink Switch bandwidth**, **20 TB HBM3e** per rack, **276 TB NVMe** cache per rack (3.84 TB per GPU).
- NVIDIA GH200: up to **576 GB** coherent memory; **900 GB/s** NVLink-C2C.
- NVIDIA H200: **141 GB HBM3e**, **4.8 TB/s** memory bandwidth.
- NVIDIA B200: **180 GB** VRAM per GPU.

**MLPerf benchmarks:**
- MLPerf Training v5.1: Lambda's GB300 NVL72 **outperforms GB200 NVL72 by 27%** on Llama 2-70B LoRA (1.26 min vs 1.598 min).
- MLPerf Inference v5.1: NVIDIA HGX B200 achieved up to **15.4% performance gains** vs prior round's best (Llama 3.1 405B Server scenario).
- Llama 2 70B on 8xB200: **102,725 tokens/s** offline.

**MFU achievements:**
- Ceramic AI on Lambda's NVIDIA HGX B200: **85% MFU** at 32K context; **82% MFU** at 65K context (8B model on 8 Blackwell GPUs).
- Lambda MFU whitepaper: Peak **MFU above 60%**, a **25%+ improvement** over 35-45% industry baseline. **2.11x MFU uplift** for Llama 70B on 16x HGX B200.

**Olmo Hybrid training metrics (on Lambda B200 infrastructure):**
- 512 NVIDIA Blackwell GPUs, 3 trillion tokens, **97% active training time** (99% excluding troubleshooting), **median recovery time under 4 minutes**, completed B200 phase in **6.19 days**.

**Fundraising:**
- Series E: Over **$1.5B** (TWG Global, USIT) – November 2025.
- Series D: **$480M** – February 2025.
- Series C: **$320M** – February 2024.
- Series B: **$44M** – March 2023.
- Series A: **$15M** equity + $9.5M debt – July 2021.

**Named customers/partners:**
- Microsoft (multibillion-dollar agreement), Iambic Therapeutics, Genesis Therapeutics, Pika, Meshy, fal, ServiceNow (Apriel 5B), Ai2/OLMo, Synlico, Nous Research, Voltron Data, Abacus.ai, Leonardo.ai.
- Data center partners: Cologix, EdgeConneX, Prime Data Centers, ECL, Equinix (mentioned historically).
- Hardware partners: NVIDIA, Supermicro, Dell Technologies, AMD.
- Government: U.S. Air Force, Navy, Department of Energy, Department of Defense.
- Seven-time NVIDIA Partner of the Year; NVIDIA Exemplar Cloud status.

**Company facts:**
- Founded in **2012** in San Francisco.
- First office: **Noisebridge** hackerspace in the Mission District.
- Founded by **published ML engineers** (NeurIPS, ICCV).
- **100% dedicated to AI workloads**.
- SOC 2 Type II certified.
- Joined **Open Compute Project (OCP) Advisory Board**.
- Domain changed from lambdalabs.com to **lambda.ai** (March 2025).

---
