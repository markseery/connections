# How compute products and services are messaged: https://coreweave.com

*Extract concrete, verifiable facts from a company's website: compute products and services, H100, B200, GB200, B300, GB300, NVL72, Vera Rubin*

*Based on 328 stored pages analysed in 1 batch(es) with 1 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: compute_profile).*

---

## Compute Products And Services

CoreWeave offers an extensive portfolio of compute products and services:

**NVIDIA GPU Products Available on CoreWeave:**
- **NVIDIA GB300 NVL72** – First cloud provider to deploy; 4 GPUs per instance, 279GB VRAM, 144 vCPUs, 960GB system RAM, 61.44TB local storage. Up to 10x boost in user responsiveness, 5x improvement in throughput per watt vs. Hopper, 50x increase in output for reasoning model inference. 1.5x more dense FP4 performance and 2x higher NVIDIA Quantum-X800 InfiniBand speeds vs. GB200 NVL72. 6.5x performance improvement on DeepSeek R1 inference (4 GB300 GPUs vs. 16 H100 GPUs).
- **NVIDIA HGX B300** – Now generally available (GA) on CoreWeave. 8 GPUs, 270GB VRAM, 192 vCPUs, 4,096GB system RAM, 61.44TB local storage. Delivers 3.42x higher token generation on Kimi K2.5 and 4.93x faster end-to-end request latency on DeepSeek-R1 vs. HGX H200. 2.1TB HBM3e memory (50% increase over HGX B200). NVIDIA Quantum-X800 InfiniBand networking doubles node-to-node bandwidth. Liquid-cooled.
- **NVIDIA HGX B200** – GA. 8 GPUs, 180GB HBM3e each, 128 vCPUs, 2,048GB system RAM, 61.44TB local storage. On-demand: $68.80/hr. Up to 2x faster per GPU vs. Hopper, up to 15x faster inference on GPT-MoE-1.8T, 3x faster training vs. H100. Intel Emerald Rapids CPUs, NVIDIA BlueField-3 DPU, 8x ConnectX-7 InfiniBand HCAs, 400G NDR non-blocking Quantum-2 InfiniBand.
- **NVIDIA GB200 NVL72** – First cloud provider with GA instances (Feb 2025). 4 GPUs per instance (rack has 72 GPUs total / 36 Grace CPUs), 186GB VRAM, 144 vCPUs, 960GB system RAM, 30.72TB local storage. On-demand: $42.00/hr, inference single GPU: $10.50/hr. Up to 30x faster real-time LLM inference, 4x faster training, 25x lower TCO vs. previous gen. 13.5TB NVLink-connected GPU memory per rack, up to 1.4 exaFLOPS per rack. NVIDIA Quantum-2 InfiniBand 400Gb/s per GPU.
- **NVIDIA RTX PRO 6000 Blackwell Server Edition** – GA. 8 GPUs, 96GB GDDR7 each, 128 vCPUs, 1,024GB system RAM, 7.68TB local storage. On-demand: $20.00/hr, Spot: $9.24/hr, inference: $2.50/hr. 5.3x faster LLM inference and 3.5x faster text-to-image generation vs. L40S. Intel Emerald Rapids CPUs, NVIDIA BlueField DPUs.
- **NVIDIA HGX H200** – 8 GPUs, 141GB HBM3e each, 128 vCPUs, 2,048GB system RAM, 61.44TB local storage. On-demand: $50.44/hr, inference: $6.31/hr. 4.8TB/s memory bandwidth, 1.9x higher performance vs. H100. Intel Emerald Rapids CPUs, BlueField-3 DPUs, 3200Gbps Quantum-2 InfiniBand.
- **NVIDIA HGX H100** – 8 GPUs, 80GB each, 128 vCPUs, 2,048GB system RAM, 61.44TB local storage. On-demand: $49.24/hr, inference: $6.16/hr. Up to 9x faster training, 30x faster inference vs. A100. Quantum-2 InfiniBand 3.2Tbps per node.
- **NVIDIA GH200 Grace Hopper Superchip** – 1 GPU, 96GB HBM3, 72 Arm CPU cores, 480GB system RAM, 7.68TB local storage. On-demand: $6.50/hr.
- **NVIDIA L40S** – 8 GPUs, 48GB each, 128 vCPUs, 1,024GB system RAM, 7.68TB local storage. On-demand: $18.00/hr, inference: $2.25/hr.
- **NVIDIA L40** – 8 GPUs, 48GB each. On-demand: $10.00/hr, inference: $1.25/hr.
- **NVIDIA A100** – 8 GPUs, 80GB each, 128 vCPUs, 2,048GB system RAM, 7.68TB local storage. On-demand: $21.60/hr, inference: $2.70/hr.
- **Legacy/Classic GPUs** (on classic pricing): NVIDIA H100 PCIe ($4.25/hr GPU component), A100 80GB PCIe/NVLINK ($2.21/hr), A100 40GB ($2.06/hr), RTX A6000 ($1.28/hr), A40 ($1.28/hr), RTX A5000 ($0.77/hr), RTX A4000 ($0.61/hr), Quadro RTX 5000 ($0.57/hr), Quadro RTX 4000 ($0.24/hr), Tesla V100 NVLINK ($0.80/hr).

**Non-NVIDIA Compute:**
- CPU compute: Intel Emerald Rapids, Intel Sapphire Rapids (4th Gen Xeon), AMD EPYC Genoa, AMD EPYC Milan/Rome, Intel Xeon Ice Lake. CPU-only pricing from $0.0125–$0.035/hr per vCPU.
- NVIDIA Grace ARM CPUs (in GB200/GB300 NVL72 systems).

**Key Performance Claims:**
- 250,000+ high-performance GPUs deployed across 43 data centers.
- 3.1 GW+ of contracted power capacity.
- 96% cluster goodput (vs. 90% industry average).
- 50% fewer interruptions per day.
- 10x faster inference spin-up times.
- Up to 20% higher GPU cluster performance than alternatives.
- Up to 2.5x faster training throughput.
- MFU exceeding 50% on Hopper GPUs (vs. industry 35-45%).
- 51-52% MFU on H100 benchmarks, 3.66 days MTTF at 1,024 GPUs (10x improvement over 0.33 baseline).
- MLPerf Training v5.0: Llama 3.1 405B trained in 27.3 minutes on 2,496 Blackwell GPUs, 2x faster than Hopper at same scale.
- MLPerf Inference v5.0: 800 TPS on Llama 3.1 405B with GB200 (2.86x per-chip vs. H200); 33,000 TPS on Llama 2 70B with H200 (40% improvement over H100).
- SemiAnalysis ClusterMAX™ Platinum rating – only provider to earn it twice.
- NVIDIA Exemplar Cloud validation for both training and inference on GB200 NVL72.

**Platform Services:**
- CoreWeave Kubernetes Service (CKS) – managed Kubernetes on bare metal
- SUNK (Slurm on Kubernetes) – unified training system with topology-aware scheduling, up to 96% goodput, 97-98% ETTR
- CoreWeave Mission Control – fleet/node lifecycle management, observability, security, GPU Straggler Detection, Telemetry Relay
- CoreWeave AI Object Storage (CAIOS) with LOTA – up to 7 GB/s per GPU, exabyte-scale, S3-compatible, zero egress/ingress/request fees, automated usage-based billing (Hot/Warm/Cold tiers reducing costs by 75%+)
- CoreWeave ARENA – production-ready AI lab for workload evaluation
- Tensorizer – fast PyTorch model loading (>5x faster than HuggingFace from zero)
- Serverless RL – first publicly available fully managed reinforcement learning capability
- W&B Inference – serverless inference with pay-per-token
- Dedicated Inference (preview) – custom models on chosen GPU types
- Weights & Biases integration (acquired) – experiment tracking, model management, agent evaluation
- Flex Reservations (preview) and Spot (GA) capacity plans
- Zero Egress Migration ([0]EM) program – covers egress fees from AWS/Azure/GCP
- Direct Connect networking, VPC with BlueField DPUs
- Distributed File Storage
- Networking: NVIDIA Quantum-2 InfiniBand (up to 3200Gbps/node), NVIDIA Quantum-X800 InfiniBand (800Gbps/GPU on B300), SHARP-enabled, non-blocking architecture

**Capacity Plans Pricing Structure:**
- Reservations (steady baseline), Flex Reservations (guaranteed peaks, holding fee + usage), Spot (interruptible, lower cost), On-Demand (best-effort)

**Revenue/Scale:** $5B+ annual revenue in fiscal 2025; $66.8B revenue backlog; 168% YoY revenue growth.

---

## Vera Rubin

CoreWeave mentions the NVIDIA Vera Rubin platform in several contexts:

- **CoreWeave Extends Its Cloud Platform with NVIDIA Rubin Platform** (press release, January 5, 2026): "CoreWeave among the first cloud providers to deploy NVIDIA Rubin, expanding support for large-scale inference, reasoning, and agentic AI." CoreWeave expects to be among the first to deploy the NVIDIA Rubin platform in the second half of 2026. The platform is "designed to support demanding workloads such as agentic AI, drug discovery, genomic research, climate simulation, and fusion energy modeling" and "enables large-scale mixture-of-experts models that require massive and sustained compute."

- Jensen Huang quote: "CoreWeave is helping turn that potential into production as one of the first to deploy it later this year."

- CoreWeave announced it "will add NVIDIA Rubin technology to its AI cloud platform" and the NVIDIA Vera Rubin NVL72 racks are planned for deployment.

- From the NVIDIA HGX B300 blog: "With the NVIDIA HGX B300 and the upcoming NVIDIA Vera Rubin platform, we are effectively enabling a new level of massively scalable real-time generative and agentic AI." — Orian Leitersdorf, Chief Scientist, Decart

- From the NVIDIA/CoreWeave collaboration announcement (January 26, 2026): CoreWeave intends to be "an early adopter of NVIDIA computing architectures, including multiple generations of NVIDIA GPUs alongside Vera CPUs and BlueField storage systems."

- CoreWeave's press release on advancing its AI-native platform (March 16, 2026): "CoreWeave also expects to be among the first cloud providers to deploy the NVIDIA Vera Rubin NVL72 platform."

---

## Nvl72

Extensive mentions of rack-based NVL72 offerings across multiple NVIDIA product generations:

**NVIDIA GB200 NVL72:**
- CoreWeave was the first cloud provider with GA instances (February 4, 2025). Rack-scale design connecting 36 NVIDIA Grace CPUs and 72 Blackwell GPUs in a liquid-cooled rack.
- Up to 130kW per rack, 85%/15% liquid-to-air cooling ratio.
- 13.5TB of NVLink-connected GPU memory per rack, up to 1.4 exaFLOPS per rack.
- NVIDIA Quantum-2 InfiniBand networking, 400Gb/s bandwidth per GPU, rail-optimized topology.
- NVIDIA BlueField-3 DPUs for multi-tenant networking.
- Instances available as bare-metal through CKS, 4 GPUs per instance unit.
- On-demand pricing: $42.00/hr (North America). Inference single GPU: $10.50/hr.
- Up to 30x faster real-time trillion-parameter LLM inference, 4x faster training vs. previous gen, 25x lower TCO.
- CoreWeave achieved NVIDIA Exemplar Cloud validation for both training and inference on GB200 NVL72.
- MLPerf Training v5.0: 2,496 Blackwell GPUs across 39 racks (34x larger than next CSP submission), Llama 3.1 405B trained in 27.3 minutes.
- MLPerf Inference v5.0: 800 TPS on Llama 3.1 405B, 2.86x per-chip speedup over H200.
- Customer deployments: IBM (thousands of GB200s for Granite models, "one of the first GB200 NVL72-enabled AI supercomputers"), Cohere (3x faster training, unlocking North agentic AI platform), Mistral AI (2.5x faster training speeds).

**NVIDIA GB300 NVL72:**
- First cloud provider to deploy (July 3, 2025). Housed within Dell's integrated rack scale system, at Switch data center.
- Up to 10x boost in user responsiveness, 5x improvement in throughput per watt vs. Hopper, 50x increase in output for reasoning model inference.
- 1.5x more dense FP4 performance, 2x higher NVIDIA Quantum-X800 InfiniBand speeds vs. GB200 NVL72.
- 21TB HBM3e high-bandwidth GPU memory per rack.
- Fifth-generation NVIDIA NVLink with 130TB/s aggregate bandwidth.
- NVIDIA Quantum-X800 InfiniBand switches, ConnectX-8 SuperNICs, 800Gb/s per GPU.
- 4 GPUs in a GB300 node (vs. 8 in H100 nodes).
- Production-ready instances benchmarked: 6.5x performance improvement on DeepSeek R1 inference (4 GB300 GPUs vs. 16 H100 GPUs in TP16).
- Custom-designed Rack LifeCycle Controller (RLCC), Cabinet Wrangler and Cabinet Details dashboards.
- Pricing: Contact sales.
- Customer: Poolside partnership with 40,000+ GPUs including GB300 NVL72 systems.

**NVIDIA Vera Rubin NVL72:**
- Planned deployment by CoreWeave, expected in second half of 2026.

**AI Cloud Horizons podcast Episode 4:** "IBM and CoreWeave built one of the first NVIDIA GB200 NVL72-powered AI supercomputers" with discussion of rack power density, cooling, and observability challenges.

---

## Gb300

- **NVIDIA GB300 NVL72** – CoreWeave is the first cloud provider to deploy (July 3, 2025).
- Rack-scale system with NVIDIA Blackwell Ultra GPUs, Dell integrated rack scale system.
- Specifications: Up to 10x improved user responsiveness, 5x throughput per watt improvement vs. Hopper, 50x increase in reasoning model inference output.
- 1.5x more dense FP4 performance and 2x higher Quantum-X800 InfiniBand speeds vs. GB200 NVL72.
- 21TB HBM3e per rack, 130TB/s aggregate NVLink bandwidth.
- NVIDIA Quantum-X800 InfiniBand switches, ConnectX-8 SuperNICs, 800Gb/s per GPU.
- 4 GPUs per node, 279GB VRAM per instance, 144 vCPUs, 960GB system RAM, 61.44TB local storage.
- Pricing: Contact sales (both North America and Europe).
- Benchmark: 6.5x higher raw throughput per GPU on DeepSeek R1 vs. H100 (TP4 vs. TP16).
- CAIOS achieved 7+ GB/s per GPU on GB300 (Blackwell Ultra) nodes using Infin

---

## Gb200

_No relevant information found across any batch._

---

## B300

_No relevant information found across any batch._

---

## B200

_No relevant information found across any batch._

---
