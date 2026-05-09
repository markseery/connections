# Batch AI Analysis

**Prompt:** List products,solutions,and services. Summarize within two categories only: Nebius or Technology/Product from a third party. Respone with Nebius category only. The synthesis should have the following categories only: Compute, Storage, Networking, Training, Inference, Fine-Tuning, Reinforcement Learning, Tools & Technology, Integrations & Patnerships, Other

## https://nebius.com

# Nebius Products, Solutions & Services

## Compute

- **Nebius AI Cloud** — Full-stack, AI-native cloud platform for intensive AI/ML workloads, managing the full ML lifecycle (data processing, training, fine-tuning, inference) in one place. Includes platform releases Aether 3.0 and 3.1 with enterprise-grade governance, observability, capacity management, and self-healing infrastructure.
- **NVIDIA GPU Instances & Clusters** — On-demand, reserved, and preemptible access to NVIDIA GPUs: GB300 NVL72, GB200 NVL72, HGX B300 NVL16, HGX B200, B200, H200, H100 SXM, L40S, A100. Clusters scale from a single GPU to thousands, interconnected via InfiniBand. Self-service access to up to 32 GPUs via console with no waitlists.
- **CPU-only Instances** — AMD EPYC Genoa and Intel Ice Lake virtual machines.
- **Bare-Metal Performance** — Non-virtualized GPU access minimizing overhead, maximizing Model FLOPS Utilization (MFU); validated via MLPerf® benchmarks.
- **ISEG2 Supercomputer** — Ranked #13 on the Top500 list; available to all customers for LLM creation, fine-tuning, and HPC.
- **Custom-Designed Servers & Racks** — In-house hardware R&D team designs and assembles server racks (distinct training and inference node types), consuming ~20% less energy than off-the-shelf alternatives; cableless design, smart air-cooling, operable up to 40°C without mechanical cooling.
- **Managed Service for Kubernetes®** — Fully managed container orchestration optimized for multi-host AI workloads with GPU/InfiniBand pre-installed nodes, autoscaling, load balancer support, taint-based autohealing.
- **Managed Soperator (Slurm-on-Kubernetes)** — Fully managed Slurm-on-Kubernetes solution for one-click AI training cluster provisioning with pre-installed libraries/drivers, fault-tolerant training, and topology-aware scheduling.
- **Soperator (Open Source)** — World's first open-source Kubernetes operator for Slurm (Apache 2.0), bridging Slurm scheduling with Kubernetes scalability and self-healing.
- **Serverless Jobs** — Serverless GPU compute for training, batch pipelines, and containerized workloads without cluster management; automatic GPU provisioning.
- **Serverless Endpoints** — On-demand model serving endpoints for development, evaluation, and production use without infrastructure management.
- **Standalone Applications** — Pre-provisioned cloud servers (e.g., JupyterLab with NVIDIA GPUs) requiring no cluster setup, with persistent storage.
- **Managed Service for Apache Spark™** — Fully managed large-scale distributed data processing.
- **Explorer Tier** — Discounted entry pricing: NVIDIA H100 SXM at $1.50/GPU-hour for the first 1,000 GPU hours/month.
- **GPU Auctions** — Descending-price auction mechanism for GPU compute access.
- **Commitment Discounts (CVoS)** — Reserved capacity pricing (3-month to 3-year terms) with up to 35% savings.
- **Capacity Blocks** — Real-time dashboard showing exactly how much reserved GPU compute is available, enabling transparent resource planning.
- **Node Health Monitoring & Auto-Repair** — Proactive detection and automatic repair/replacement of unhealthy nodes during training, with hardware monitoring system sending notifications (email, API, Slackbot).
- **Pre-installed Driver Images** — AI/ML-ready VM images with NVIDIA GPU and InfiniBand drivers for up to 3× faster node startup.
- **Integrated Monitoring & Observability** — AI-specific and system performance dashboards (CPU, GPU, RAM, NVLink, InfiniBand, Ethernet metrics) for clusters and VMs; Grafana dashboards out-of-the-box; custom metrics and logs upload capability.
- **Multi-Region Data Centers** — In-house designed, AI-optimized data centers in Finland (Mäntsälä, owned), France (Equinix colocation), Missouri/Kansas City (Patmos colocation), New Jersey/Vineland (DataOne, up to 300 MW), Iceland (Verne colocation), UK (Ark Data Centres); Independence, Missouri gigawatt-scale AI factory planned. Liquid cooling, closed-loop water cooling, behind-the-meter power generation.
- **UK Cloud Region** — 4,000 NVIDIA Blackwell Ultra GPUs at Ark Data Centres, Longcross Park, Surrey.

## Storage

- **Nebius Object Storage** — Fully S3-compatible object storage with two classes:
  - *Standard class* — Capacity-focused, cost-efficient, unlimited scalability for static/unstructured data.
  - *Enhanced class* — Performance-focused, up to 10 GB/s write throughput per client (2 GiB/s per GPU), unlimited capacity; optimized for streaming datasets/weights to GPU and checkpointing. Hardware-accelerated networking offloaded to NVIDIA ConnectX-8 SuperNICs.
- **Nebius Shared Filesystem** — High-speed all-flash NVMe shared filesystem delivering 500+ GB/s aggregate read performance (up to 100 GBps and 1M IOPS aggregated read); optimized for parallel AI computation and PyTorch workloads with parallel downloading and extended chunk sizes.
- **Network Storage Volumes (Block Storage)** — Network disks mounted to VMs for cloud-native elasticity, quick VM restart on failure, with and without data replication.
- **Container Registry** — Managed Docker image storage with automatic replication, fault-tolerant storage, HTTPS security, access control, and Docker Registry HTTP API V2 compatibility.
- **Managed Service for PostgreSQL®** — Fully managed relational database with pg_vector extension for vector embeddings/RAG, automated backups, HA configuration, and monitoring dashboards.
- **Managed Service for MySQL®** — Fully managed relational database clusters.
- **Managed Service for ClickHouse®** — Managed analytical database (Preview).
- **Managed Service for Redis™** — Managed in-memory NoSQL clusters (Preview).
- **Managed Service for OpenSearch** — Managed OpenSearch server clusters (Preview).

## Networking

- **NVIDIA InfiniBand Fabric** — Non-blocking NVIDIA Quantum InfiniBand interconnect delivering up to 3.2 Tbit/s per 8-GPU host (400 Gbit/s per GPU) for direct GPU-to-GPU communication across multi-node clusters.
- **NVIDIA Quantum-X800 InfiniBand Platform** — Next-generation 800 Gb/s end-to-end connectivity with ultra-low latency; deployed with GB300 NVL72 systems (Europe's first).
- **NVIDIA Quantum-2 InfiniBand** — Interconnect fabric used for large-scale Hopper GPU training clusters (512–1,024 GPUs).
- **NVIDIA NVLink 5 Interconnects** — High-bandwidth, low-latency GPU-to-GPU communication within NVL72 racks.
- **NVIDIA ConnectX-8 SuperNICs** — Hardware-accelerated network offloading operations from CPUs for boosted throughput.
- **VPC (Virtual Private Cloud)** — Isolated virtual networks, subnets, IP pools, private/public IP address management, routing rules, and remote/internet access configuration; supports Kubernetes pod networking and inter-datacenter routing.
- **Network Load Balancer** — Built-in network balancing for resilient production workloads and inference endpoints.
- **Multi-Region Routing** — Traffic routing across regions for low-latency inference delivery.
- **Cache-Aware Routing** — Routing strategy for distributed inference across vLLM replicas that reduced average inference step time ~50%, total runtime ~36%, and P95 latency from >1 minute to <20 seconds.

## Training

- **Distributed Multi-Node Training Infrastructure** — End-to-end managed environment for large-scale distributed training across thousands of NVIDIA GPUs with managed Kubernetes or Slurm orchestration on InfiniBand fabric; fault-tolerant with node health monitoring, auto-repair, automatic recovery, and rapid checkpoint read/write.
- **TractoAI** — End-to-end solution for data processing and distributed training; supports PyTorch, Hugging Face, NanoGPT; scales to hundreds of GPUs; includes checkpoint storage, monitoring (W&B integration), and serverless execution with usage-based pricing.
- **Fault-Tolerant Training Clusters** — Production clusters achieving 169,800 GPU hours (56.6 hours) of stable operation on 3,000-GPU configurations with automated fault handling and proactive node reallocation.
- **MLPerf® Training v5.0 & v5.1 Benchmark Results** — Industry-leading performance training Llama 3.1 405B on 512 and 1,024 NVIDIA Hopper GPU clusters; leading performance on Blackwell and Blackwell Ultra systems for training and fine-tuning GenAI models.
- **NVIDIA Exemplar Cloud Status (H200 Training)** — Validated performance, resiliency, and scalability meeting NVIDIA's rigorous standards for training workloads.
- **Cluster Configurations for LLM-Scale Training** — Pre-configured clusters (e.g., 512× H100 GPUs) for training models at Chinchilla/70B-parameter scale and beyond (trillion-parameter MoE models).
- **High-Speed Dataset Streaming** — Storage designed to feed datasets to GPU clusters at maximum speed for faster training cycles.
- **Long-Context Training** — Support for training with up to 131K token contexts.
- **Physical AI / Robotics Training** — Vision-language-action (VLA) model training; simulation rollouts, synthetic data generation, trajectory collection, and parameter sweeps for robotics workloads.
- **Data Preparation Pipeline for LLMs** — Established pipeline and tooling for data collection, cleaning, pseudolabeling, and preparation for training large language models.
- **In-House LLM R&D** — Internal distributed training team that stress-tests and specializes the platform; trains foundational models (e.g., 20B parameter model with Recraft); researches LLM architectures (transformers, RNNs, SSMs, MoE).

## Inference

- **Nebius Token Factory** (formerly Nebius AI Studio) — Enterprise-grade managed inference platform with:
  - **60+ Open-Source Model Catalog** — Text-to-text, vision, image, embeddings, guardrails models including DeepSeek (R1, V3), GPT-OSS (120B, 20B), Llama families (3.1, 3.3, 4 Scout/Maverick, Nemotron-Ultra-253B), Qwen families (Qwen3-235B-A22B, Qwen3-Coder-480B, Qwen3-32B, Qwen2-VL-72B), Mistral, Kimi-K2-Instruct, devstral, Gemma, NVIDIA Nemotron 3 Super (120B MoE), NVIDIA Nemotron Nano 2 VL, and others.
  - **Shared and Dedicated Tiers** — Transparent $/token pricing with Fast (low-latency) and Base (cost-efficient) flavors; dedicated zero-retention endpoints with isolated model hosting and strict access control.
  - **Dedicated Endpoints** — User-defined GPU type, GPUs per replica, autoscaling (min/max replicas), region selection (EU/US), lifecycle management; sub-second latency targets, 99.9% uptime, guaranteed performance.
  - **Custom Weights Hub** — Upload and deploy custom model weights to dedicated endpoints.
  - **Batch & Async API** — Process 10GB+ datasets at up to 50% lower cost vs. real-time inference; fast models at base model pricing.
  - **Text-to-Image Generation** — FLUX schnell, FLUX dev, SDXL 1.0; up to 2000×2000 resolution; ~1.8s per image; from $0.0013/image; unlimited rate limits.
  - **Speculative Decoding** — Built-in speculative decoding for latency reduction; custom speculator pipeline deployment.
  - **Function Calling & Structured Outputs** — Native function calling, structured JSON/SQL/code output enforcement, and built-in safety guardrails for agentic AI applications.
  - **Adaptive Burst Rate Limits** — Automatically scales traffic spikes into unused capacity, eliminating 429 errors.
  - **OpenAI-Compatible API** — API compatibility with OpenAI format for seamless developer adoption.
  - **Playground** — Web interface to try and compare AI models without code.
  - **Per-Token API Access** — Serverless, pay-per-token billing model; up to 4.5× faster performance and up to 50% lower pricing than leading providers.
  - **Zero-Retention Inference** — Optional zero-retention data flow for privacy-sensitive workloads.
- **TractoAI LLM Batch Inference** — Distributed batch inference for throughput optimization on CPUs and GPUs.
- **Inference Frontier Program** — Year-round builder-to-builder initiative for sharing production inference architectures, optimizations, and engineering tradeoffs.
- **Real-time Model Inference on Kubernetes** — Deploy production models on GPU nodes with native load balancing between CPU-only instances.

## Fine-Tuning

- **Token Factory Fine-Tuning (GA)** — Generally available fine-tuning across 30+ open-source models (DeepSeek V3, GPT-OSS 120B, Qwen3 Coder 480B, Llama 3 series 1B–70B, Qwen series 1.5B–72B, DeepSeek Coder, Mistral, and others):
  - **LoRA Fine-Tuning** — Parameter-efficient adapter-based customization for all supported models.
  - **Full Fine-Tuning** — Complete model weight updates for models under 20B parameters.
  - **Custom Checkpoint Deployment** — Deploy fine-tuned checkpoints directly on Token Factory endpoints with guaranteed performance and per-token pricing; download checkpoints for local use.
  - **Structure-Aware Decoding** — Outputs conform to custom schemas (JSON, SQL, code, internal formats).
  - **Reasoning-Aligned Templates** — Training configurations aligned with reasoning objectives for consistent production behavior.
  - **Long-Context Fine-Tuning** — Support for fine-tuning with extended context lengths.
- **Model Distillation Pipelines** — End-to-end knowledge distillation: batched data generation from teacher models (e.g., Qwen3-235B-A22B), fine-tuning student models (e.g., Qwen3-4B with LoRA), comparative evaluation using frontier models as evaluators; 3–5× faster serving from distilled models.
- **TractoAI Fine-Tuning** — Fine-tune or distill open-source models (DeepSeek, Llama, Flux) with dynamic compute at scale.
- **Fine-Tuning on AI Cloud Infrastructure** — Scalable compute (including L40S GPUs for cost-effective fine-tuning) with JupyterLab-based workflows, dataset preparation tools, and evaluation pipelines.
- **Multi-Node Fine-Tuning via SkyPilot + Kubernetes** — Distributed multi-node fine-tuning of LLMs (e.g., Llama 3.1-8B) using Managed Kubernetes and SkyPilot.
- **Post-Training from Production Logs** — Continuous improvement loop: capture production inference logs → transform to structured training datasets → run post-training → deploy improved models.
- **LLM Alignment Pipeline** — Internal fine-tuning pipeline for alignment shared publicly by the LLM R&D team.

## Reinforcement Learning

- **SWE-rebench-V2** — Large-scale multilingual RL training dataset with 32,000+ executable tasks across 20 programming languages, each with pre-built Docker environments; 100,000+ additional PR-derived tasks; designed for training autonomous software engineering agents at scale.
- **Reasoning Critics for Software Engineering Agents** — RL-finetuned reasoning language models used as critics for Q-value estimation; regression-based and reasoning critic models; parallel and lookahead search methods; bootstrapped critics improving agent trajectories through iterative search and retraining cycles.
- **Critic-Guided Search for SWE Agents** — Reinforcement-style approach using critic models to guide search-based methods on top of coding agents, achieving 40.6% on SWE-bench Verified (state-of-the-art among open-weight