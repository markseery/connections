# How compute products and services are messaged: https://nscale.com

*Extract concrete, verifiable facts from a company's website: compute products and services, H100, B200, GB200, B300, GB300, NVL72, Vera Rubin*

*Based on 93 stored pages analysed in 1 batch(es) with 1 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: compute_profile).*

---

## Compute Products And Services

**NVIDIA GPU Products offered by Nscale:**
- **NVIDIA H100** – Available as bare-metal GPU nodes. Referenced as "Best-in-class GPUs, including NVIDIA H100s" for Hugging Face inference. Blog mentions "running three NVIDIA H100 GPUs for one hour uses the same amount of electricity as running a full load in a washing machine."
- **NVIDIA H200** – Listed on GPU Nodes page: "Supercharging AI and HPC workloads with NVIDIA H200 Tensor Core GPUs."
- **NVIDIA GB200 NVL72** – Listed as "Designed for a new type of data center—the AI factory." Performance claims: "4X FASTER LLM Training," "25X EFFICIENCY Energy Efficiency," "30X FASTER LLM Inferencing," "18X FASTER Data Processing" (vs Intel Xeon 8480+).
- **NVIDIA A100** – Listed in FAQ sections as available GPU model. Referenced in blog: "ChatGPT, powered by NVIDIA's A100s."
- **NVIDIA V100** – Listed in GPU Nodes FAQ.
- **NVIDIA Blackwell GPUs** – Commitment to deploy 10,000 NVIDIA Blackwell GPUs in the UK by 2026.
- **NVIDIA Blackwell Ultra GPUs** – Keflavik, Iceland data center "set to host more than 4,600 NVIDIA Blackwell Ultra GPUs for deployment across Verne's Icelandic campus in 2026."
- **NVIDIA Vera Rubin NVL72** – Announced for deployment in 2027, bringing 100,000+ GPUs to Europe.

**AMD GPU Products:**
- **AMD Instinct MI250X** – Used in Glomfjord Svartisen cluster (Top500 list). Each node: 4x AMD Instinct MI250X OAMs (8x GPUs), 2x AMD EPYC 7713 CPUs.
- **AMD Instinct MI300X** – Referenced in benchmarks showing GEMM tuning improvements up to 7.2x on throughput/latency. Listed as available: "NVIDIA H100s and AMD MI300X."
- **AMD Instinct MI210** – Referenced in CPU vs GPU benchmarking blog.
- **AMD MI325X** – Mentioned in Series A blog as upcoming GPU release.

**Non-NVIDIA/AMD Products:**
- **AMD EPYC 9684X** – 96-core 2.55GHz processors used in CPU benchmarking configurations.
- **Intel Xeon 8480+** – Referenced as comparison baseline for GB200 NVL72 data processing (18x faster).

**Compute Services:**
- **On-demand GPU & CPU compute** – Bare-metal nodes and virtual machines.
- **Serverless Inference** – Pay-as-you-go pricing, OpenAI API compatible. Models include GPT OSS 120B/20B, Devstral Small, Qwen 3 235B, Qwen 2.5 Coder variants, Llama 4 Scout, Llama 3.3 70B, DeepSeek R1 variants, Mixtral 8x22B, Flux.1, Stable Diffusion XL. Pricing examples: DeepSeek R1 Distill Qwen 32B at $0.3 per 1M tokens; Llama 4 Scout at $0.09/$0.29 per 1M tokens input/output.
- **Inference Endpoints** – Dedicated endpoints for 100+ open-source models.
- **Fine-tuning Service** – Serverless, pay-as-you-train. Price: up to 20B model size at $0.50/1M tokens. Supports Mistral 7B, Qwen 2/2.5 variants, DeepSeek R1 Distill variants, Meta-Llama-3-8B.
- **Prompt Workbench** – Browser-based prompt engineering with versioned prompts.
- **Managed Slurm** – Via NVIDIA's Slinky, HPC-grade batch scheduling on Kubernetes.
- **Nscale Kubernetes Service (NKS)** – Kubernetes environments provisioned in under two minutes.
- **Training Clusters** – Multi-GPU clusters for distributed training.
- **GPU Nodes** – Bare-metal GPU nodes.

**Performance Claims:**
- 80% lower cost compared to hyperscalers
- 30% faster time to insights
- 40% improved resource utilization
- 7.2x throughput/latency improvement with GEMM tuning on MI300X
- ABI Research scored Nscale 10/10 for maximum distributed cluster scale, 9/10 for GPU availability ("most on-demand jobs typically start within minutes"), 9/10 for Interconnect Bandwidth and Topology
- "20 to 40 percent better throughput without changing hardware" (ABI Research on optimization stack)
- Cost of production "at least 10% lower than competitors"
- Inference platform grown 148x, serving 5,000+ users

**Networking:**
- RDMA/InfiniBand/NVLink fabrics, multi-rack topology, low-latency interconnects
- Nokia partnership for switching and optical layers
- Broadcom-powered Ethernet fabric with RoCE (RDMA over Converged Ethernet)

**Storage:**
- VAST Data partnership for storage
- Parallel, AI-optimised storage tiers with GPU-tuned distributed file systems

**Partners/OEMs:** Dell, Nokia, VAST, Lenovo, Broadcom, Microsoft, NVIDIA, OpenAI, Lightning AI, Hugging Face, Singtel

---

## Vera Rubin

- **March 17, 2026**: "Nscale and Microsoft Announce Collaboration with NVIDIA and Caterpillar to Deliver 1.35GW of NVIDIA Vera Rubin NVL72 GPUs at Flagship AI Factory Campus in West Virginia"
- **March 17, 2026**: "Nscale to Deploy NVIDIA Vera Rubin Platform in 2027, Bringing 100,000+ GPUs to Europe"
- Nscale will be "among the first providers globally to deploy NVIDIA Vera Rubin platform" and "the first provider outside of Microsoft itself" to deploy Vera Rubin in Europe.
- Deployment timeline: "bringing advanced architectures including NVIDIA Vera Rubin and NVIDIA Grace Blackwell to our customers in 2027"
- "The NVIDIA Vera Rubin platform represents a significant leap forward in AI supercomputing, purpose-built for frontier AI model development and deployment."
- Quote from NVIDIA: "Deploying agentic and physical AI requires a new class of supercomputing infrastructure capable of delivering scale and efficiency for demanding inference workloads. Nscale's deployment of the NVIDIA Vera Rubin NVL72 platform will provide critical AI infrastructure for European developers to push the frontiers of AI." — Ian Buck, VP and GM, Hyperscale and HPC, NVIDIA
- Vera Rubin described as "a platform that operates as a single AI supercomputer, built from multiple tightly integrated components spanning compute, networking, and security."
- Deployments will support Microsoft and be deployed across UK, Norway and beyond.
- West Virginia Monarch campus: LOI with Microsoft for up to 1.35GW of AI compute using NVIDIA Vera Rubin NVL72 GPUs.

---

## Nvl72

- **NVIDIA GB200 NVL72** – Listed on GPU Nodes page as available product: "Designed for a new type of data center—the AI factory."
- Performance claims for GB200 NVL72: "4X FASTER LLM Training," "25X Energy Efficiency," "30X FASTER LLM Inferencing," "18X FASTER Data Processing" vs Intel Xeon 8480+.
- **NVIDIA Vera Rubin NVL72** – Announced for West Virginia campus: "1.35GW of NVIDIA Vera Rubin NVL72 GPUs" and for Europe: "100,000+ GPUs."
- ABI Research noted Nscale "combines InfiniBand, high-bandwidth Ethernet with Remote Direct Memory Access (RDMA), and NVLink/NVSwitch for NVL72 systems, supported by joint innovation with Nokia across switching and optical layers."
- Vera Rubin NVL72 described as "engineered with the NVIDIA Vera Rubin DSX AI Factory reference design."
- Nscale positioned as "Global Flagship Deployment Partner for NVIDIA Vera Rubin Architecture, NVIDIA DSX AI Factory."
- Quote: "Nscale's deployment of the NVIDIA Vera Rubin NVL72 platform will provide critical AI infrastructure for European developers." — Ian Buck, NVIDIA

---

## Gb300

_No relevant information found across any batch._

---

## Gb200

- **NVIDIA GB200 NVL72** listed as a GPU node product on the GPU Nodes page.
- Performance metrics for GB200 NVL72: "4X FASTER LLM Training at scale," "25X more energy efficiency than legacy solutions," "30X FASTER real-time trillion-parameter large language model (LLM) inference," "18X FASTER at processing data than Intel Xeon 8480+."
- Listed in FAQ: "Our lineup includes models such as the NVIDIA A100, H100, H200, GB200, and V100."
- From CEO's Series A blog (Dec 2024): "the fundamental change to the density requirement at the rack level with the new NVIDIA GB200, B200 and AMD MI325X GPU releases that will render a large number of available DCs unsuitable due to the low rack density and lack of liquid cooling."
- Referenced across multiple solution pages (Model Training, Fine-Tuning, AI Development, Inference) in GPU offering lists.

---

## B300

_No relevant information found across any batch._

---

## B200

- From CEO Josh Payne's Series A blog (December 10, 2024): "the fundamental change to the density requirement at the rack level with the new NVIDIA GB200, B200 and AMD MI325X GPU releases that will render a large number of available DCs unsuitable due to the low rack density and lack of liquid cooling."
- This is the only mention of B200 on the website, referencing it as a next-generation GPU that increases rack density requirements and necessitates liquid cooling.

---
