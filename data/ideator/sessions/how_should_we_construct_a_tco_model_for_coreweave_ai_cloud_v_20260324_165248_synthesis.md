# Ideator Synthesis

**Topic:** How should we construct a TCO model for CoreWeave AI Cloud vs an On-prem approach? The final result of the conversation MUST be a list of categories and items that MUST be include in the TCO model.

---

# SYNTHESIS: CoreWeave AI Cloud vs. On-Premises TCO Model Deliberation

---

## **1. KEY AGREEMENTS**

### **Framework Structure: 11 Cost Categories (LOCKED)**

All participants converged on a comprehensive category set:

1. **Capital Expenditure** (hardware, networking, cooling, facility)
2. **Operational Staffing & Labor** (engineering, on-call, training)
3. **Power & Facility Costs** (electricity, PUE, cooling, backup systems)
4. **Compute Efficiency & Utilization** (utilization rates, stranded capacity, peak allocation tax)
5. **Network & Multi-Node Orchestration** (InfiniBand, data egress, API overhead)
6. **Scaling & Procurement Friction** (lead times, expansion cycles, hardware refresh)
7. **Time-to-Value & Deployment** (procurement delays, integration labor, ramp-up costs)
8. **Lock-In & Exit Costs** (data export, retraining, customization sunk costs)
9. **Compliance, Security & Data Governance** (certifications, residency, audit costs)
10. **Risk & Contingency** (failure recovery, downtime impact, vendor stability)
11. **Workload-Specific Adjustments** (training vs. inference cost structure variance)

**Rationale for convergence:** These categories separate signal from noise by isolating actual cost drivers (staffing intensity, energy efficiency, procurement delays) from marketing theater.

---

### **Baseline Assumptions (Testable, Not Aspirational)**

Participants locked in defensible baseline assumptions:

| Metric | CoreWeave | On-Premises | Source/Validation |
|--------|-----------|-------------|-------------------|
| **GPU Utilization** | 75% sustained | 55% sustained | Meta/Anthropic production data; sensitivity analysis required |
| **Deployment Timeline** | <4 hours provisioning, 2-4 days to first model | 24-40 weeks (GPU lead 12-16 wks + retrofit 4-8 wks + testing + ramp) | NVIDIA allocation timelines; liquid cooling retrofit research |
| **Staffing Cost** | $0 direct operations | $180K-$250K/engineer fully loaded (~1 FTE per 50 GPUs) | Market labor rates; sensitivity ±$30K |
| **Power Efficiency (PUE)** | 1.22 (liquid-cooled) | 1.45 (air-cooled) | Research: 15-25% facility energy savings delta |
| **Risk Premium (Hardware CapEx)** | 0% | 18% over-allocation to avoid stockout risk | On-prem over-provisioning reality |
| **Data Egress Fees** | $0 | $0.02-$0.10/GB (competitors) | CoreWeave differentiation; on-prem = $0 |

**Critical caveat:** The 75% vs. 55% utilization delta does "all the work" in the model. Participants agreed this *must* be validated with CoreWeave production data before external publication, or modeled as a sensitivity range (70-80% cloud, 50-60% on-prem baseline).

---

### **Market Segmentation: Honest Tier Boundaries (ACCEPTED)**

All participants accepted that CoreWeave doesn't win everywhere:

| Tier | Profile | GPU Scale | Utilization | Horizon | Winner | Confidence |
|------|---------|-----------|-------------|---------|--------|-----------|
| **1** | Startup, episodic workloads | 20-100 | 40-60% | 18-36 mo | **CoreWeave** | High (time-to-value + elasticity) |
| **2** | Mid-scale, mixed training/inference | 200-500 | 60-75% | 36-60 mo | **Toss-up** | Medium (depends on cost of capital, risk tolerance) |
| **3** | Multi-cluster scaling, variable load | 100-1000+ | 45-65% | 24-48 mo | **CoreWeave** | High (procurement friction + stranded capacity) |
| **4** | Enterprise datacenter, sustained | 1000+ | 75%+ | 60-84 mo | **On-Premises** | High (amortization advantage) |

**Strategic shift:** Finance Director explicitly committed to publicly admitting "on-prem wins for Tier 4." This honesty became positioned as *credibility lever*, not weakness.

---

### **Three Customer Decision Profiles (ADOPTED)**

Framework will include explicit profiles instead of generic "all customers":

- **Profile A (Episodic/Startup)**: 50-GPU cluster, 62% utilization with 15%-89% spikes, 18-month horizon
- **Profile B (Mid-Scale)**: 250-GPU mixed load, 65% average utilization, 36-month horizon
- **Profile C (Enterprise)**: 1000-GPU sustained inference, 78% utilization, 60-month horizon

For each profile, the 11 categories are modeled with *different weightings*:
- Profile A: Categories 6, 7, 8 matter most (scaling friction, time-to-value, exit costs)
- Profile B: Categories 2, 3, 4 matter most (staffing, power, utilization)
- Profile C: Categories 1, 4, 5 matter most (capex, utilization, network efficiency)

---

### **Structural Honesty on Hidden Costs**

Participants unified around making *invisible costs visible*:

| Hidden Cost | On-Premises Reality | Modeling Approach |
|-------------|-------------------|-------------------|
| **Stranded Capacity** | 15-20% peak allocation reserve unused 90% of time | Explicit "peak allocation tax" line item |
| **Procurement Lead Time** | 8-16 weeks GPU allocation (market constraint) | Quantified as opportunity cost, not footnote |
| **Staffing Scale Tax** | 1 FTE per 50-GPU cluster; doesn't scale linearly below 10 GPUs | Sensitivity: show FTE count at each scale level |
| **Cooling Retrofit Cost** | $50K-$150K per row; mandatory for B200 (1000W TDP) | Explicit capex line; not buried in "facility prep" |
| **Risk Premium Capital** | 18% over-provisioning to avoid GPU shortage risk | Separate line item showing "unused capacity cost" |
| **Organizational Friction** | Months of planning, hiring, training before first model runs | Folded into "time-to-utilization cost," not as soft narrative |

---

### **Workload Variance is Default Reality, Not Edge Case**

The Customer's actual workload—15% preprocessing / 89% training spike / 22% tuning—became the *canonical* case, not a special scenario. Agreement that "steady-state utilization" is the outlier for AI labs.

**Implication:** On-prem's amortization logic breaks under variance. CoreWeave's elastic billing *enables* variance without penalty. This is CoreWeave's most defensible advantage, distinct from "we're cheaper everywhere."

---

## **2. UNRESOLVED TENSIONS**

### **Tension 1: Utilization Baseline Credibility Gap**

**The disagreement:** Finance Director claimed 75% CoreWeave vs. 55% on-prem baseline. Skeptic accepted it as "testable" but demanded sources or sensitivity modeling.

**Status:** PARTIALLY RESOLVED
- Finance Director committed to publishing sources (Meta/Anthropic production data) or modeling as sensitivity range
- **Remaining gap:** CoreWeave production utilization data is *not publicly available*. Model will either need actual production data or explicit disclaimer: "estimated 75% based on published case studies; will refine with CoreWeave production data"
- **Risk if unresolved:** If real CoreWeave utilization is 70%, model margin compresses. If 68%, competitiveness margin becomes fragile.

**Recommendation:** Delay external publication until CoreWeave can validate utilization rate with 2-3 customer case studies (anonymized).

---

### **Tension 2: What Counts as "Utilization"?**

**The disagreement:** Ambiguity between GPU kernel time (active compute) vs. cluster wall-clock allocation (provisioned time).

The Customer noted: *"I run 62% cluster utilization but probably 50% GPU kernel utilization due to I/O waits."*

**Status:** UNRESOLVED in framework; flagged for spreadsheet phase
- If CoreWeave is billing on cluster allocation and on-prem cost is amortized over kernel utilization, the metrics don't align.
- Skeptic explicitly demanded: "Define what 'utilization' means in your model before we build the spreadsheet."

**Recommendation:** Spreadsheet must include *both* metrics:
1. **Cluster utilization** (wall-clock time GPU is powered and allocated)
2. **Kernel utilization** (time GPU is actively executing compute)

Model breakeven curves for both; acknowledge the gap.

---

### **Tension 3: Time-to-Value Cost Circularity**

**The disagreement:** Finance Director proposed "cost per month of delayed deployment" as a major line item. Customer objected: "Not all customers are revenue-gated on GPU access. For me, time cost is near-zero because I'm exploratory."

**Status:** PARTIALLY RESOLVED
- Framework now separates two distinct costs:
  1. **Revenue impact of deployment delay** (for revenue-generating systems; low applicability)
  2. **Time-to-learning cost** (faster iteration → faster model improvement; more universal)
- **Remaining gap:** How to quantify "time-to-learning" without overstating it? What's the *real* business value of month-earlier model launch?

**Recommendation:** Model time-to-value as *sensitivity parameter, not headline driver.* Show: "At your revenue model, deployment delay costs $X. If you care about that, it tips CoreWeave. If you don't, it's noise." Let customer decide.

---

### **Tension 4: Cooling Infrastructure Reality for Retrofit Scenarios**

**The disagreement:** Research shows $50K-$150K per row retrofit cost for liquid cooling (mandatory for B200s). Finance Director included this. But underlying question: **How many of CoreWeave's actual customer conversations are against enterprises that already have on-prem infrastructure (retrofit scenario) vs. greenfield builds?**

**Status:** ACKNOWLEDGED but NOT MODELED
- Customer flagged: "Most of your sales conversations are Path B—existing on-prem operators considering cloud—not Path A greenfield builders."
- Finance Director didn't build separate retrofit-cost scenarios into the model.
- **Implication:** If 70% of prospects are retrofit scenarios (where $200K-$600K liquid cooling retrofit is *incremental* capex they haven't budgeted), CoreWeave's competitive advantage is **higher** than modeled for greenfield scenarios.

**Recommendation:** Spreadsheet should include **two on-prem pathways:**
- **Greenfield:** Full capex + procurement timeline applies
- **Retrofit:** Existing H100 → upgrade to B200 with liquid cooling. Show retrofit cost explicitly as decision point.

---

### **Tension 5: Stranded Capacity Tax Is Defensible Only If Real**

**The disagreement:** Skeptic pushed back on "peak allocation tax" as circular reasoning. *"You're only stranded if you provision for peak load. Many enterprises run steady-state and accept longer latencies during spikes. For them, stranded capacity cost is zero."*

**Status:** PARTIALLY RESOLVED
- Framework now models stranded capacity as workload-dependent (Tier 1 episodic workloads = high stranded cost; Tier 4 steady-state = low stranded cost)
- **Remaining gap:** How much of on-prem reality actually *does* overprovision? Is 18% risk premium realistic, or is it inflated?

**Recommendation:** Spreadsheet should show stranded capacity cost *separately by workload profile*, with sensitivity: "If your on-prem team actually only reserves 10% peak capacity (vs. 18%), adjust stranded cost downward by [X]%."

---

### **Tension 6: Architecture Adaptation Labor Cost**

**The disagreement:** Skeptic flagged "customers need to refactor workloads for cloud elasticity." Finance Director folded this into "integration and testing labor." But what if it's $50K-$200K in engineering effort?

**Status:** ACKNOWLEDGED but UNDERSPECIFIED
- Framework includes "integration overhead" but doesn't break out "architecture refactoring for elasticity."
- **Implication:** Cloud-native customers (already designed for elasticity) see full advantage. On-prem-native customers (monolithic training scripts) face hidden refactoring cost.

**Recommendation:** Spreadsheet should include line item: "Architecture adaptation labor (hours to refactor for cloud elasticity patterns)." Default: 40-80 hours ($6K-$12K at $150/hour). Sensitivity: range 0-200 hours for different customer maturity levels.

---

### **Tension 7: Egress Fees as Magnitude of Cost**

**The disagreement:** Customer flagged data egress as "not marginal." Finance Director modeled it as "material only for heavy export scenarios."

**Status:** PARTIALLY RESOLVED by scenario modeling
- Light export (quarterly): <$10K annually
- Moderate export (monthly): $10K-$50K annually
- Heavy export (continuous): $50K-$200K+ annually

**Remaining gap:** What's CoreWeave's actual usage distribution? Are most customers light exporters (making egress a non-factor) or moderate exporters (making CoreWeave's $0 egress material)? Without that data, "zero data egress fees" could be a differentiator or a non-issue depending on customer mix.

---

### **Tension 8: Sensitivity Analysis Scope**

**The disagreement:** Should model show sensitivity on *all* assumptions, or only the "top 3 that do 80% of the work"?

**Status:** PARTIALLY RESOLVED
- Skeptic requested: "Which three assumptions are doing 80% of the work?"
- **Likely answer:** (1) Utilization baseline, (2) Staffing cost per engineer, (3) Power cost per kWh
- **Remaining gap:** Need to validate this empirically once spreadsheet is built.

---

## **3. RECOMMENDED ACTIONS**

### **Phase 1: Validate Assumptions (2-3 weeks)**

1. **CoreWeave Production Utilization Data**
   - Extract 3-5 customer case studies (anonymized) showing actual sustained GPU utilization rates
   - Separate by workload type (training, inference, fine-tuning)
   - Document measurement methodology (kernel time vs. wall-clock)
   - **Owner:** CoreWeave Finance Director + Product Analytics
   - **Decision criteria:** If ≥70% confirmed, baseline is defensible. If <65%, revise model to 65% + sensitivity range.

2. **On-Premises Utilization Benchmarks**
   - Source published data from enterprises running 500+ GPU clusters (Meta, Anthropic, internal customer case studies)
   - **Owner:** Finance Director + Sales team
   - **Decision criteria:** Validate 55% baseline or adjust.

3. **Liquid Cooling Retrofit Cost Validation**
   - Confirm $50K-$150K per row from CoreWeave partners or direct implementation experience
   - Specify: hours per row, material costs, electrical/plumbing assumptions
   - **Owner:** Operations + partner accounts

4. **Staffing Cost Validation**
   - Confirm $180K-$250K fully loaded cost per GPU infrastructure engineer (or adjust based on market data)
   - Breakpoint analysis: at what cluster size does staffing become sub-linear?
   - **Owner:** HR + Finance

---

### **Phase 2: Build Spreadsheet Model (2-3 weeks)**

**Owner:** Finance Director + Finance Operations

**Deliverables:**
1. **Main TCO Worksheet**
   - Rows: 11 cost categories
   - Columns: CoreWeave baseline, On-prem baseline, Customer inputs (with validation)
   - Outputs: Total cost per 3-year / 5-year / 7-year horizon; cost per GPU-hour; utilization breakeven curve

2. **Three Customer Profile Worksheets (A, B, C)**
   - Pre-populated assumptions for each profile
   - Cost breakdown showing which categories drive decision for each profile
   - Winner (CoreWeave vs. On-Premises vs. Toss-up)

3. **Sensitivity Dashboard**
   - Interactive sensitivity on: utilization ±10%, power cost ±$0.05/kWh, staffing cost ±$30K
   - Show which assumptions flip the decision
   - Identify "top 3" cost drivers by profile

4. **Assumptions & Sources Tab**
   - Every number includes source citation or explicitly flags as "estimate/needs validation"
   - Methodology documentation for utilization measurement
   - Baseline justification

---

### **Phase 3: Validate & Pressure Test (1 week)**

**Participants & responsibilities:**
- **
