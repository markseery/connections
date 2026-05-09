# How compute products and services are messaged: https://lambda.ai

*Extract concrete, verifiable facts from a company's website: compute products and services, H100, B200, GB200, B300, GB300, NVL72, Vera Rubin*

*Based on 371 stored pages analysed in 1 batch(es) with 1 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: compute_profile).*

---

## Compute Products And Services

Lambda offers a comprehensive portfolio of compute products and services across multiple tiers:

**Product Tiers:**
- **Superclusters**: 4,000 to 165,000+ NVIDIA GPUs, single-tenant, shared-nothing architecture, 3-5 year contracts. Described as "purpose-built and production-ready for large-scale training and inference."
- **1-Click Clusters™**: 16 to 2,000+ NVIDIA GPUs, on-demand (2 weeks-12 months) or reserved (1-3 years). Production-ready clusters with NVIDIA Quantum-2 InfiniBand networking.
- **Instances**: 1-8 NVIDIA GPUs, pay-as-you-go, available in minutes.

**GPU Offerings and Pricing (Instances, per GPU/hr):**
- NVIDIA B200 SXM6 (180 GB VRAM): 8x at $5.74, 4x at $5.85, 2x at $5.97, 1x at $6.08
- NVIDIA H100 SXM (80 GB): 8x at $3.44, 4x at $3.55, 2x at $3.67, 1x at $3.78
- NVIDIA H100 PCIe (80 GB): 1x at $2.86
- NVIDIA GH200 (96 GB): 1x at $1.99
- NVIDIA A100 SXM (80 GB): 8x at $2.06
- NVIDIA A100 SXM (40 GB): 8x at $1.48, 1x at $1.48
- NVIDIA A100 PCIe (40 GB): 4x/2x/1x at $1.48
- NVIDIA A10 (24 GB): 1x at $0.86
- NVIDIA A6000 (48 GB): 4x/2x/1x at $0.92
- NVIDIA Quadro RTX 6000 (24 GB): 1x at $0.58
- NVIDIA Tesla V100 (16 GB): 8x at $0.63

**1-Click Cluster Pricing:**
- NVIDIA HGX B200: On-Demand $4.62/GPU/hr
- NVIDIA H100: On-Demand $2.76/GPU/hr
- Reserved pricing (1-3 years) available via sales

**Non-NVIDIA Products:**
- NVIDIA Grace CPU (ARM-based) in GH200 Superchip: 72-core Grace CPU with H100 GPU, NVLink-C2C @ 900 GB/s, up to 576GB memory
- NVIDIA Vera CPU announced as coming to Lambda (88-core ARM CPU for agentic AI/RL workloads)

**Key Performance Claims:**
- NVIDIA HGX B200: "Up to 3x training performance" and "up to 15x inference performance" vs prior generation
- MLPerf Training v5.1: Lambda's GB300 NVL72 outperforms GB200 NVL72 by 27%
- MLPerf Inference v5.1: Up to 15.4% performance gains over prior round's best results
- FlashAttention-2 on H100: Replicating GPT3-175B training estimated at $458,136 using 3-year reserved cluster
- H100 SXM vs PCIe: 49-51% performance premium for SXM

**Networking:**
- NVIDIA Quantum-2 InfiniBand: 400 Gb/s, aggregate 3.2 Tb/s throughput between nodes
- NVIDIA Quantum-X800 InfiniBand: 800 Gb/s per GPU (next gen)
- NVIDIA Quantum-X Photonics: Co-Packaged Optics (CPO) for improved power efficiency, 3.5x power efficiency improvement, 10x higher resilience
- NVIDIA NVLink 5: 1.8 TB/s bandwidth per GPU
- SHARP acceleration for distributed workloads

**Software & Orchestration:**
- Lambda Stack: Pre-installed PyTorch, TensorFlow, JAX, CUDA, cuDNN, NCCL, NVIDIA drivers
- Managed Kubernetes (CNCF-conformant)
- Managed/Unmanaged Slurm
- dstack (open-source container orchestration)
- SkyPilot integration
- S3-compatible storage, no ingress/egress fees
- Storage: $0.20/GB/month

**Security:**
- SOC 2 Type II certified
- Single-tenant, shared-nothing architecture
- Physical safeguards: steel cages, biometric verification, RFID access, 2FA
- AES-256 encryption at rest
- IPsec VPN, network peering

**Infrastructure:**
- Direct-to-chip liquid cooling
- Modular AI factory design
- Data centers across US and Canada
- Contiguous AI factories: 75MW+ campuses
- Vision to reach 3GW+ of AI data center space

**Differentiators:**
- "No hidden fees. No charges for data ingress or egress."
- "Pay flat rates — no lock-in"
- ML engineering support included
- Co-engineering with Lambda's AI experts
- NVIDIA Exemplar Cloud certified (one of first cloud providers)
- 7-time NVIDIA Partner of the Year
- Cloud interconnects: AWS Direct Connect, Google Cloud Interconnect, OCI FastConnect, Azure ExpressRoute

**Funding/Scale:**
- Raised over $1.5B Series E (Nov 2025), $480M Series D (Feb 2025), $320M Series C
- Multibillion-dollar agreement with Microsoft for tens of thousands of NVIDIA GPUs
- Over 200,000 AI developers/customers
- Founded 2012

---

## Vera Rubin

Lambda announced NVIDIA Vera Rubin NVL72 coming to the Superintelligence Cloud in a blog post dated January 9, 2026 by Khushboo Goel: "At Lambda, we build supercomputers that enable AI teams to deliver next-generation, frontier models. Today, we're announcing the next evolution of our infrastructure: the NVIDIA Vera Rubin platform, which will serve as the core building block for Lambda Superclusters."

**Vera Rubin NVL72 Specifications:**
- 72 NVIDIA Rubin GPUs within a single NVLink domain
- 36 NVIDIA Vera CPUs integrated into Vera Rubin Superchips
- 18 NVIDIA BlueField-4 DPUs
- Up to 20.7 TB HBM4 per rack with bandwidth targets up to 22 TB/s per GPU
- NVIDIA NVLink 6 providing 3.6 TB/s GPU-to-GPU connectivity within the rack
- External connectivity via NVIDIA BlueField-4 800G DPUs and ConnectX-9 1600G SuperNICs
- 100% direct-to-chip (D2C) liquid-cooled compute and switch trays
- Up to 10x higher token throughput per watt and costs one-tenth as much per million tokens as NVIDIA Blackwell

**Performance claims:** "Vera Rubin NVL72 is a single unified rack with a 72-GPU NVIDIA NVLink domain. It reduces cross-die communication overhead, expands the scale-up memory pool, enables model-parallel training and inference to behave more like single-node runs."

**GTC 2026 Announcements (March 2026):**
- Lambda announced Bare Metal Instances on NVIDIA Vera Rubin NVL72 Superclusters
- Lambda is an early NVIDIA Vera CPU launch partner (88-core ARM CPU)
- Lambda is an early NVIDIA BlueField-4 STX adopter
- Maxx Garrison speaking session: "Deploy Lambda's Bare Metal Instances with NVIDIA Vera Rubin NVL72 & GB300 NVL72"

**Availability:** "Production availability is planned for the second half of 2026."

The GTC 2026 preview blog states: "At Booth #1507, we're showcasing how the Superintelligence Cloud is built in the real world, from power and liquid cooling to rack-scale Superclusters engineered for NVIDIA Vera Rubin NVL72."

---

## Nvl72

**NVIDIA GB300 NVL72** is Lambda's primary rack-scale offering, described as "Rack-scale systems optimized for AI reasoning" on the homepage.

**GB300 NVL72 Specifications (from Gigawatt-Scale AI Factories blog, Oct 22, 2025):**
- 72 NVIDIA Blackwell Ultra GPUs and 36 NVIDIA Grace CPUs per rack
- 37 TB of fast memory (20TB HBM3e)
- 130 TB/s of NVIDIA NVLink Switch bandwidth
- 3.84 TB of NVMe cache per GPU (276 TB per NVL72)
- 142 kW compute power per rack (per ECL deployment)
- Weight: 4,000 pounds per system
- Fully liquid-cooled

**GB300 NVL72 vs GB200 NVL72 improvements:**
- +50% HBM3e capacity (20TB per rack)
- +1.5× higher dense FP4 performance
- 2× faster attention operations

**Deployment milestones:**
- First GB300 NVL72 systems stood up in Lambda's liquid-cooled datacenters (Oct 2025)
- First hydrogen-powered GB300 NVL72 systems deployed at ECL's Mountain View facility with Supermicro (Sept 2025)
- Kansas City AI Factory with 10,000+ NVIDIA Blackwell Ultra GPUs (Oct 2025)
- MLPerf Training v5.1: GB300 NVL72 outperformed GB200 NVL72 by 27% on Llama 2-70B LoRA (1.26 min vs 1.598 min)

**Vera Rubin NVL72** (next generation): See Topic 2 above. 72 Rubin GPUs, 36 Vera CPUs, 20.7TB HBM4, NVLink 6 at 3.6 TB/s. Production availability planned for second half of 2026.

**Superclusters product page lists:** "NVIDIA GB300 NVL72: Rack-scale systems optimized for AI reasoning: 72× Blackwell Ultra GPUs / 36× Grace CPUs per rack, 37 TB fast memory / 130 TB/s NVLink Switch bandwidth"

On the homepage, Superclusters are described as: "Run on single-tenant NVIDIA GB300 NVL72 clusters with NVIDIA Quantum-2 InfiniBand for ultimate security and performance."

Ken Patchett (VP Data Center Infrastructure) stated in an interview: "there'll be smaller data center space with three racks of GB 300, some networking racks and some other types of racks and storage that are there that they leverage and they use."

---

## Gb300

**NVIDIA GB300 NVL72** is prominently featured throughout the website as a core product offering.

**Homepage description:** "NVIDIA GB300 NVL72: Rack-scale systems optimized for AI reasoning"

**Technical specifications (from various pages):**
- Part of "Blackwell Ultra" architecture with "7X more AI compute than NVIDIA Hopper generation"
- 20TB of HBM3e memory per rack (72-GPU NVLink domain)
- 142 kW per system (as deployed at ECL)
- +50% HBM3e capacity vs GB200 NVL72
- +1.5× higher dense FP4 performance vs GB200 NVL72
- 2× faster attention operations vs GB200 NVL72
- Second-generation Transformer Engine with dynamic range management and fine-grain scaling
- Configurable parallel file storage, optional managed orchestration with Kubernetes or Slurm

**NVIDIA GB300 Grace Blackwell Ultra Desktop Superchip** (announced at GTC 2025): "Lambda is proud to be able to partner with NVIDIA and offer the new NVIDIA GB300 Grace Blackwell Ultra Desktop Superchip in our Vector Pro system. The new Grace Blackwell platform brings the ARM architecture to deskside systems and offers an unprecedented, massive 784GB of coherent memory under your desk."

**MLPerf Training v5.1 results (Nov 2025):** Lambda's GB300 NVL72 cluster with 72× GB300 279 GB:
- Llama 3.1 8B: 14.25 minutes
- Llama 2-70B LoRA: 1.26 minutes (1.27× faster than best GB200 NVL72, 1.6× faster than best 64× B200)

**Deployments:**
- First hydrogen-powered GB300 NVL72 at ECL Mountain View (Supermicro-built, Sept 2025)
- Production-scale GB300 NVL72 Supercluster with NVIDIA Quantum-X Photonics (10,000+ GPUs, announced at GTC 2026)
- Kansas City AI Factory: 10,000+ NVIDIA Blackwell Ultra GPUs
- Microsoft agreement: includes GB300 NVL72 systems

**Blog quote (Oct 2025):** "Lambda is building gigawatt-scale AI factories on NVIDIA GB300 NVL72 systems as the compute backbone for the next generation of training and inference."

---

## Gb200

**NVIDIA GB200** is mentioned in several contexts:

**GB200 Grace Blackwell Superchip** (announced March 2024): "The NVIDIA GB200 Grace Blackwell Superchip combines two Blackwell GPUs and one NVIDIA Grace CPU. This scales up to the GB200 NVL72, a 72-GPU NVIDIA NVLink-connected system in a liquid-cooled rack that acts as a single massive GPU, delivering 1.4 exaFLOPS of AI performance and 30TB of fast memory."

**Performance claims:** "GB200 delivers 30X faster real-time LLM inference and 4X faster training performance for large language models like GPT-MoE-1.8T compared to the NVIDIA Hopper architecture generation."

**GB200 NVL72** referenced as the predecessor to GB300 NVL72:
- MLPerf Training v5.0: Best GB200 NVL72 result was 1.598 minutes for Llama 2-70B LoRA (from Oracle)
- GB300 NVL72 outperformed GB200 NVL72 by 27% in MLPerf Training v5.1

**GTC 2025 announcement:** "NVIDIA GB300 NVL72 and GB200 NVL72 systems will be available through Lambda's On-Demand & Reserved Cloud" (referring to Blackwell Ultra platform coming to Lambda Cloud)

**2025 AI Wrapped blog:** Referenced GB300 NVL72 with "20TB of HBM3e high-bandwidth memory" as the context for expanded memory needs. Also noted: "Some of these advanced models that ran fine on NVIDIA HGX H100 system with 80GB per GPU now may need more advanced configurations, such as the rack-scale architecture of NVIDIA GB300 NVL72."

The "Get Into The ARMs Race" blog (Dec 2024) described: "One of the most exciting Blackwell elements is the NVIDIA GB200 Blackwell Superchip, which connects two NVIDIA B200 Tensor Core GPUs to the NVIDIA Grace CPU over a 900GB/s ultra-low-power NVLink chip-to-chip interconnect."

---

## B300

**NVIDIA HGX B300** is featured as a Supercluster-tier product:

**Homepage description:** "NVIDIA HGX B300: Peak performance per watt for the largest training runs"

**Superclusters page specifications:**
- 72 PF FP8 training / 144 PF FP4 inference
- 2.1 TB HBM3e memory
- NVIDIA ConnectX-8 SuperNICs

**GTC 2025 announcement (March 2025):** "HGX B300 NVL16 and GB300 NVL72, based on the NVIDIA Blackwell Ultra architecture, are built for the age of reasoning with 7X more AI compute than NVIDIA Hopper generation, increased memory to support growing models and MoE architectures, and networking platform integration with NVIDIA Quantum-X800 InfiniBand with double the bandwidth of previous generations."

**B300 NVL16 specifications (from GTC 2025 blog):**
- 2.3TB of HBM3e

---

## B200

_No relevant information found across any batch._

---
