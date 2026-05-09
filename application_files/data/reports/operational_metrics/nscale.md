# Marketing – Factual & Quantitative: https://nscale.com

*Extract concrete, verifiable facts from a company's website: corporate details, infrastructure specs, customer names, product listings, and quantitative claims.*

*Based on 93 stored pages analysed in 1 batch(es) with 1 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: operational_metrics).*

---

## Number Of Data Centers, Their Locations, Gpus, And Power Capacity

**Nscale-operated data centers:**
- **Glomfjord, Norway** – Arctic Circle location; 100% renewable hydropower; 30MW operational capacity, expandable to 60MW; 120MW hydro power plant nearby; adiabatic cooling
- **Narvik, Norway** – Stargate Norway (partnership with Aker and OpenAI); 230MW capacity with plans to expand by an additional 290MW; powered by renewable hydropower; targets 100,000 NVIDIA GPUs
- **Loughton, United Kingdom** – London-proximate AI campus; ability to scale power allocation up to 90MW; direct-liquid cooling; waste heat and water reuse
- **Ward County, Texas, USA** – ~240MW AI data center developed with Ionic Digital; plans to expand to 1.2GW; closed-loop direct-liquid cooling and rear-door heat-exchangers
- **Monarch Compute Campus, Mason County, West Virginia, USA** – Up to 2,250 acres; America's first state-certified AI microgrid; power runway scalable to over 8GW; initial 2GW expected online first half of 2028, expansion to ~8GW planned for 2031; 1.35GW LOI signed with Microsoft for NVIDIA Vera Rubin NVL72 GPUs

**Partner-run data centers:**
- **Sines, Portugal** – Start Campus data center; designed for GW-scale AI deployments; 100% renewable energy with seawater cooling; hyperscale site on Portugal's Atlantic coast
- **Keflavik, Iceland** – One of Iceland's largest liquid-cooled GPU installations; set to host more than 4,600 NVIDIA Blackwell Ultra GPUs (deployed across Verne's Icelandic campus in 2026); 100% renewable energy from geothermal and hydropower

**Available data centers (listed but less detail):**
- Stavanger, Norway
- Oslo, Norway
- Blönduós, Iceland
- Slough, United Kingdom
- North Carolina, USA

**GPU types mentioned:**
- NVIDIA Vera Rubin NVL72 (deployment planned 2027, 100,000+ GPUs to Europe)
- NVIDIA Blackwell Ultra GPUs (4,600+ at Keflavik)
- NVIDIA GB200 NVL72
- NVIDIA H100, H200, GB200
- AMD Instinct MI250X, MI300X, MI210
- Commitment to deploy 10,000 NVIDIA Blackwell GPUs in UK by 2026

**Total data center count:** 11 locations listed (4 Nscale-operated + 2 partner-run + 5 available), plus the West Virginia Monarch campus

---

## Active And Contracted Megawatts, Terawatts, And Other Measures Of Power

- **Glomfjord:** 30MW operational, expandable to 60MW
- **Narvik (Stargate Norway):** 230MW initial capacity, plans to expand by additional 290MW (total potential ~520MW)
- **Loughton, UK:** Up to 90MW
- **Ward County, Texas:** ~240MW, plans to expand to 1.2GW
- **Monarch Compute Campus, West Virginia:** Initial 2GW expected online by first half 2028; expansion to ~8GW planned for 2031; LOI with Microsoft for 1.35GW of AI compute; Caterpillar G3500 series natural gas generators at 2GW scale
- **Sines, Portugal:** Designed for GW-scale deployments
- **Pipeline:** 1.3GW pipeline of greenfield data center sites across Europe and North America (mentioned in Series A announcement, December 2024); expectation of 250MW commissioned by Q4 2026, with potential to expand to 1GW+ by 2029
- **West Virginia campus current capacity:** Over 1GW (existing before Monarch expansion)
- **$1.4 billion GPU-backed Delayed Draw Term Loan** to finance multiple cluster deployments in Norway, Portugal, Iceland, and UK
- **Energy sourcing:** 100% renewable energy at Glomfjord (hydropower), Narvik (hydropower), Sines (renewable + seawater cooling), Keflavik (geothermal + hydropower); West Virginia uses Caterpillar natural gas generators as on-site microgrid (pursuing carbon sequestration to offset emissions)
- Power demands noted as climbing toward 150kW per rack, with predictions for 1MW+ racks

---

## Installed, Active And Future Number Of Gpus

- **Narvik (Stargate Norway):** Target 100,000 NVIDIA GPUs
- **Europe (Vera Rubin deployment):** 100,000+ GPUs to Europe in 2027 (NVIDIA Vera Rubin platform)
- **Keflavik, Iceland:** More than 4,600 NVIDIA Blackwell Ultra GPUs for deployment in 2026
- **UK:** Commitment to deploy 10,000 NVIDIA Blackwell GPUs by 2026
- **West Virginia/Microsoft:** 1.35GW of NVIDIA Vera Rubin NVL72 GPUs (exact GPU count not specified)
- **Fleet Operations:** Building toward "several hundred thousand GPUs over the next three years"; described as "running tens of thousands of GPUs globally for enterprise customers" currently
- **Nidhi Chappell (President AI Infrastructure):** Former Head of AI Infrastructure at Microsoft where she "managed 2m GPUs"
- **xAI reference (blog context):** Mentioned that xAI deployed 100,000 H100 GPU clusters in a single facility (third-party reference)
- **Inference service:** Grown 148x, now serving more than 5,000 users
- **GPU models offered:** NVIDIA A100, H100, H200, GB200/GB200 NVL72, Vera Rubin NVL72; AMD Instinct MI250X, MI300X

---

## Teraflops And Exaflops

- **GPT-1 training:** approximately 0.96 petaflop/s-days (pfs-days) of resources (blog reference)
- **GPT-3 training:** 3,630 pfs-days of resources (blog reference)
- **GPU performance claims:**
  - NVIDIA GB200 NVL72: 4x faster LLM training, 25x energy efficiency vs. legacy, 30x faster LLM inferencing, 18x faster data processing than Intel Xeon 8480+
  - GEMM tuning on AMD MI300X: Improves throughput and latency by up to 7.2x
  - ~33% latency reduction with Gradlib GEMM tuning on MI300X
- **Top500 list:** Nscale's Svartisen Cluster made the Top500 list (based on HPL benchmark, FP64 mode); also ran mixed precision Linpack MXP for AI-reflective performance. Specific TFLOPS/PFLOPS figures not quoted.

---

## Other Capacity And Performance

- **Series C funding:** $2 billion, valuing Nscale at $14.6 billion (March 2026); Series B: $1.1 billion (largest in European history); Pre-Series C SAFE: $433 million; Series A: $155 million
- **$1.4 billion Delayed Draw Term Loan** backed by GPUs for European cluster deployments
- **ABI Research ranking:** #1 overall neocloud provider (out of 14); scored perfect 10 for maximum distributed cluster scale; 9/10 for GPU availability; 9/10 for Interconnect Bandwidth and Topology; 9/10 for ISV Partner and Co-Development Innovation
- **Platform optimization:** "20 to 40 percent better throughput without changing hardware" (ABI Research observation)
- **Cost claims:** 80% lower cost vs. hyperscalers; 30% faster time to insights; 40% improved resource utilization; cost of production at least 10% lower than competitors
- **PUE target:** ~1.1 at Glomfjord (vs. industry 1.3 considered inefficient)
- **Waste heat reuse:** ~85% of waste heat at Glomfjord repurposed (to fish farms, potentially swimming pools, road heating)
- **Cooling:** Water drawn from fjord at 6-9°C, exits at ~34°C; direct-to-chip liquid cooling; closed-loop systems
- **Operational:** Up to 60% lower energy use while maintaining 100% uptime (through SOPs)
- **Kubernetes provisioning:** Isolated environments in under two minutes
- **Networking:** RDMA/InfiniBand/NVLink fabrics; Nokia partnership for switching and optical layers; InfiniBand + high-bandwidth Ethernet with RDMA + NVLink/NVSwitch for NVL72 systems
- **GPU DDTL:** Oversubscribed, led by PIMCO, Blue Owl, LuminArx Capital Management
- **Employees/hiring:** Plans to hire 100 AI specialists in the UK over 12 months
- **Key partners/customers mentioned:** Microsoft, NVIDIA, OpenAI, Aker ASA, Dell, Nokia, VAST, Verne, Ionic Digital, Caterpillar, Singtel, Lightning AI, Hugging Face, AMD, Lenovo, Armada, Fidelis New Energy, 8090 Industries
- **Board members:** Sheryl Sandberg, Susan Decker, Nick Clegg (joined March 2026)
- **Leverage:** 60-70% compared to market average of 80-90%
- **Inference benchmarks (CPU vs GPU):** Mistral 7B Instruct: GPU avg 62.8 tokens/s, CPU avg 17.65 tokens/s
- **Svartisen Cluster specs:** AMD MI250X OAMs (8x GPUs per node), 2x AMD EPYC 7713 CPUs, Ethernet fabric with lossless RDMA (RoCE) powered by Broadcom
- **London office:** 16 New Burlington Place, Mayfair

---
