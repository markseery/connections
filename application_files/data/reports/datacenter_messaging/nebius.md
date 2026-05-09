# Datacenter Messaging: https://nebius.com

*Extract all messaging at the datacenter level.*

*Based on 748 stored pages analysed in 2 batch(es) with 3 AI calls (namespace: webscrape, model: claude-opus-4-6, profile: datacenter_messaging).*

---

## All Data Center Messaging

# Consolidated Summary: Nebius Data Center Messaging

---

## Overall Strategy & Positioning

- **Core message:** "We master building AI-optimized sustainable data centers."
- **Vertical integration:** Nebius designs and operates its own custom NVIDIA-accelerated servers in energy-efficient data centers — "Cloud rewritten entirely from the ground up" with no legacy systems.
- **Geographic footprint:** Data centers across "both sides of the Atlantic" — Finland, France, Iceland, UK (Europe) and Missouri, New Jersey (US), with a gigawatt-scale facility planned in Independence, MO.
- **Rapid expansion:** "Rapidly expanding our data center footprint is the only way to meet the increasing demands of AI innovators in the United States and Europe."
- **Sustainability embedded at every layer:** Energy efficiency whitepaper covers four layers — model, cluster, fabric, and data center. 2024 Sustainability Report published.

---

## Data Center Locations

### Finland (Mäntsälä) — Own Data Center (eu-north1)
- Located ~60 km from Helsinki; entirely self-built, with meticulous attention to building structure adapted to in-house server and rack designs.
- **ISEG Supercomputer:** Ranked #19 on Top500 (initially #16 at time of debut, #4 in Europe as of Nov 2023). Capacity multiplied 5× since launch.
- **ISEG2:** Ranked #13 on Top500 — "the most powerful commercially available supercomputer in Europe and the second most powerful commercially available supercomputer anywhere in the world." Energy efficiency of 38.189 GFLOPs/watt (#67 on Green500).
- **First cloud in Europe** to deploy NVIDIA Blackwell Ultra systems in production (HGX B300 and GB300 NVL72).
- **Europe's first GB300 NVL72** system running on NVIDIA Quantum-X800 InfiniBand (800 Gb/s end-to-end connectivity).
- **Cooling:** Free cooling system using filtered outdoor air (no chillers, water loops, or refrigerants). Servers run reliably at temperatures up to 40°C. PUE of 1.13 at full capacity — "among the lowest overhead power consumption ratios in the industry."
- **Liquid cooling:** Preparing for 200 kW of heat per rack for next-gen GPUs; liquid cooling components designed by Nebius (with NVIDIA liquid cooling system being installed for Blackwell GPUs).
- **Waste heat recovery:** Pioneered in 2015–2016 (first in Finland). Supplies heat to local district heating covering up to 72% of Mäntsälä's annual heating demand, warming 2,000+ households. Over 50 GWh of server heat reused for city heating in 2022–2024. In 2024 alone, reduced local heating-related emissions by 54% (~3,220 tonnes CO₂ equivalent).
- **Capacity expansion:** Tripling to 75 MW (announced October 2024), enabling up to 60,000 GPUs; expanding toward 300 MW region capacity.
- On-site sauna ("in the very Finnish way").
- *Incident (Feb 26, 2026):* Power infrastructure fault caused by short circuit in cooling cabling triggered UPS overcurrent protection, resulting in brief power interruption to a subset of compute hosts.

### France (Paris) — Colocation (eu-west1)
- Colocation at Equinix's PA10 campus, Saint-Denis district. Announced September 2024.
- Among the first in Europe to offer NVIDIA H200 Tensor Core GPUs.
- First facility equipped solely with Nebius-designed servers from day one — no third-party servers or racks.
- Servers certified under NVIDIA-Certified Systems program.
- Setup and deployment to user-ready state takes only two months.
- Waste heat warms an urban farm on the facility's roof (growing tomatoes and other produce delivered to food-access communities).
- First client workloads deployed November 2024.
- *Incident (Mar 13, 2025):* VPC service release caused networking issues for Managed Kubernetes pods in eu-west1.

### Iceland (Keflavik) — Colocation
- Partnership with Verne, a provider of sustainably powered data centers across the Nordics.
- 10 MW compute cluster.
- Runs entirely on 100% renewable hydroelectric and geothermal energy.
- Expected fully operational by end of March 2025.

### United Kingdom (Surrey) — Colocation
- Purpose-built, liquid-ready data-hall capacity at Ark Data Centres' Longcross Park, Surrey. Announced November 2025.
- Initial Q4 2025 deployment of 4,000 NVIDIA Blackwell Ultra GPUs — "one of the first in Europe of this type of GPU."
- Features NVIDIA Quantum-X800 InfiniBand networking, advanced energy-efficient cooling, resilient on-site power generation.
- "One of the UK's most advanced AI supercomputing platforms."
- Will serve UK startups, research institutes, enterprises, and public-sector organizations including the NHS.
- Supports UK Government's AI Opportunities Action Plan.

### Kansas City, Missouri — Colocation (us-central1)
- Partnership with Patmos, which repurposed the iconic Kansas City Star printing press into a modern AI data center. Announced November 2024.
- First colocation tenant. Scheduled to go live Q1 2025.
- Expandable from initial 5 MW up to 40 MW (~35,000 GPUs at full capacity).
- Houses NVIDIA Blackwell and Hopper H200 GPUs (primarily H200 in initial phase).
- *Incident (Sep 3, 2025):* Routing configuration conflicts between network domains caused 1 hour 45 minute disruption.
- *Incident (Mar 10, 2026):* During scheduled provider-led electrical maintenance, unexpected power failures affected additional racks; multiple unplanned power interruptions over several hours.

### New Jersey (Vineland) — Own Data Center (300 MW greenfield)
- Partnership with DataOne for construction; first phase going live summer 2025.
- Built using Nebius' own design; DataOne's expertise enables construction in just 20 weeks.
- Behind-the-meter electricity and advanced energy technology for sustainability and operational reliability.
- Expandable up to 300 MW total. Previously committed to 100 MW by end of 2025, prepared to accelerate.
- Dedicated solely to NVIDIA Blackwell-architecture GPUs.
- Microsoft agreement: Nebius will provide dedicated capacity to Microsoft from this facility starting later in 2025.
- "Our first major data center in the US."

### Independence, Missouri — Planned Gigawatt-Scale AI Factory
- Nebius is considering developing a state-of-the-art, sustainably designed AI factory on ~400 acres.
- Potential capacity of up to 1.2 GW — described as "first gigawatt-scale AI factory."
- Multi-building campus; multi-billion-dollar investment.
- ~1,200 skilled construction jobs, ~130 permanent high-tech positions.
- Closed-loop water cooling design with minimal annual water needs ("comparable to a restaurant").
- Connects to Independence Power & Light (IPL); "No increase to residential power rates — Nebius will pay the full cost for power and necessary infrastructure upgrades."
- City Council approved Chapter 100 industrial development incentive plan (March 2026).
- PILOT payments projected to deliver over $650 million to city, school districts, and other taxing jurisdictions over 20 years.
- Community benefits plan: STEM/AI literacy programs, workforce development, first responder support, Community Engagement Panel.

---

## Hardware & Infrastructure

**GPU Systems Available:**
- NVIDIA GB300 NVL72, GB200 NVL72, HGX B300, HGX B200, HGX H200, HGX H100, L40S, RTX PRO 6000 Blackwell Server Edition
- GB200 NVL72: 72 Blackwell GPUs connected via fifth-generation NVLink (130 TB/s aggregate), liquid-cooled rack-scale system
- Plans for NVIDIA Vera Rubin NVL72 from H2 2026
- Over 22,000 NVIDIA Blackwell GPUs to be deployed
- "Thousand-GPU clusters are available now in our data centers in Europe and the US"

**Networking:**
- NVIDIA Quantum-2 InfiniBand: up to 3.2 Tbit/s per host
- NVIDIA Quantum-X800 InfiniBand: 800 Gb/s end-to-end connectivity
- Rail-optimized fat-tree InfiniBand fabric topology per NVIDIA recommendations

**Custom Hardware Design:**
- In-house hardware R&D team designs and assembles servers and racks
- Cableless design eliminates ~7 meters of cable per server; tool-free assembly
- Custom servers consume ~20% less energy than off-the-shelf equivalents (~10 GWh energy saved in 2024 alone)
- Three-stage acceptance testing: hardware burn-in, NVIDIA reference architecture validation, long-haul stress tests

**Storage:**
- AI-tailored shared filesystem: up to 180 GBps per GB200 NVL72 rack for reads; up to 1 TB/s aggregate read throughput
- Object storage: 2 GB/s per GPU
- Aggregated storage performance up to 100 GBps and 1M IOPS for reads
- Shared filesystem scales performance linearly up to 4 PB

---

## Partnerships & Commercial Agreements

- **NVIDIA:** Reference Platform Cloud Partner; among first NVIDIA Exemplar Cloud Partners on H200 GPUs for training workloads. $2 billion strategic investment in Nebius. Joint goal to deploy more than 5 GW of NVIDIA systems by end of 2030.
- **Microsoft:** Multi-year agreement for dedicated capacity from Vineland, NJ data center.
- **Meta:** Agreement valued at up to ~$12–27 billion over 5 years for dedicated capacity across multiple locations based on NVIDIA Vera Rubin platform. *(Note: Batch 1 cited $12 billion; Batch 2 cited ~$27 billion — the higher figure may reflect an expanded or updated agreement.)*
- **Contract backlog** exceeding $20 billion including multi-year agreements with Microsoft and Meta.
- **SemiAnalysis** gold medal in GPU Cloud ClusterMAX™ Rating System.
- **MLPerf** benchmark leading results on NVIDIA Blackwell and Blackwell Ultra systems.

---

## Pricing (Committed Rates)

| GPU | Committed | On-Demand |
|-----|-----------|-----------|
| NVIDIA B200 | $3.00/hr | $5.50/hr |
| NVIDIA H200 | $2.30/hr | $3.50/hr |
| NVIDIA H100 | $2.00/hr | $2.95/hr |

- Commitment discounts up to 35% on on-demand rates.

---

## Reliability & Performance

- **MTBF:** 167,000–169,800 GPU hours (56.6 hours) on a 3,000-GPU production cluster (slight variation across references; both figures cited in different contexts).
- **SLA:** 99.9% uptime for Token Factory dedicated endpoints.

---

## Compliance & Security

- SOC 2 Type II (including HIPAA), ISO 27001, ISO 22301, ISO 27701, ISO 27018, ISO 27799, ISO 27032
- NIS2 and DORA aligned
- GDPR and CCPA compliant
- HIPAA-compliant with tenant-level isolation

---

## Sustainability Highlights

- Free cooling (Finland), waste heat recovery (Finland — 50+ GWh reused; France — urban farm), 100% renewable energy (Iceland)
- PUE of 1.13 at full capacity (Finland)
- Custom server design saves ~20% energy vs. off-the-shelf
- Closed-loop water cooling planned for Independence, MO facility
- Behind-the-meter sustainable energy approach for New Jersey
- "Data centers have been significant energy consumers for more than a decade. In 2010, they already accounted for just over 1% of global electricity use."

---

## Corporate Offices

- San Francisco (Ferry Building), Dallas (Dallas Parkway), New York, Amsterdam, Tel Aviv; expanding to Singapore.

---
