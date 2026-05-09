# Ideator Synthesis

**Topic:** How should we construct a TCO model for CoreWeave AI Cloud vs an On-prem approach?

---

# Synthesis: TCO Model Construction for CoreWeave AI Cloud vs On-Prem

## Key Agreements

**Model Structure**
The participants converged on a three-layer framework: **capital efficiency** (datacenter build, hardware, power, cooling), **operational drag** (staffing, maintenance, lifecycle management, utilization rates), and **opportunity cost** (time-to-market, capital tied up in non-liquid assets). All agreed that most enterprises dramatically underestimate the second and third categories.

**Honest Boundary Conditions**
The group agreed that on-prem can win in narrow scenarios: 85%+ sustained utilization, homogeneous GPU fleet, 3+ year horizon, existing datacenter capacity, and qualified ops staff already in place. The Finance Director explicitly stated this should be modeled transparently: "pretending [on-prem wins don't exist] kills credibility instantly."

**Target Customer Profile**
The Customer's profile crystallized as the ideal CoreWeave use case: Series B startup, 18 months runway, variable workloads (32 GPUs for week-long bursts, 128 GPUs for month-long runs, idle periods in between), one DevOps person underwater, and no capital for infrastructure build. This translates to roughly **55,000-60,000 GPU-hours monthly** at peak utilization of 30-40%.

**Competitive Reality**
Lambda emerged as a direct competitor with transparent pricing ($2.76/hour H100 on-demand, $4.62/hour B200), "no lock-in, no ingress/egress fees" positioning, and managed Kubernetes or Slurm. The group agreed the TCO model must survive comparison to Lambda, not just on-prem.

**Utilization as the Critical Variable**
All participants agreed that utilization assumptions determine the TCO outcome. The model should use ranges: 40-70% typical for on-prem with variable workloads (factoring in maintenance, failed nodes, scheduling gaps), versus CoreWeave's claimed 96% goodput during active jobs.

---

## Unresolved Tensions

**Pricing Transparency Gap**
The most significant unresolved issue: Lambda publishes $2.76/hour for H100 on-demand. CoreWeave requires a "TCO consultation" to discover pricing. The Customer explicitly flagged this as competitive friction: "If CoreWeave's rate requires a consultation to discover, that's already friction I can't afford." The Finance Director provided a framework (Flex + Spot delivering 15-25% below on-demand, roughly $2.20-$2.40 effective) but not a published rate card. The Skeptic noted: "The one that makes them fill out a form to see pricing is already losing."

**Developer Experience Quantification**
The Customer repeatedly asked for specifics: Can existing Helm charts work? What's the support SLA for 11pm Saturday failures? Is there a phone number reaching a GPU engineer at midnight? CoreWeave claims Mission Control handles lifecycle management, but the operational support ratio remains undefined. Lambda claims "experts included" without quantification.

**Validation of Performance Claims**
CoreWeave claims 96% cluster goodput, 50% fewer interruptions per day, and SemiAnalysis Platinum ClusterMAX rating (twice). The Skeptic demanded: "96% goodput under what conditions? What workload mix? What cluster size?" While the Strategist noted these are third-party validated, the operational conditions remain unspecified.

**The "Managed" Boundary**
Both Lambda and CoreWeave claim managed Kubernetes and Slurm. The Customer's experience is that "managed" often means "you still debug our configuration." The operational burden delta between platforms—what the customer actually has to do versus what the provider handles—remains unquantified.

---

## Recommended Actions

**Immediate Data Requirements**
1. **CoreWeave rate cards**: H100 and B200 pricing for on-demand, reserved, Flex, and Spot
2. **Support SLA specifics**: Response time guarantees, escalation paths, 24/7 engineer availability
3. **Workload validation**: The Customer should run a trial workload on CoreWeave ARENA to measure actual performance vs. claims

**Model Construction Steps**
1. Build a **three-way comparison matrix**: On-Prem vs. Lambda vs. CoreWeave
2. Include explicit line items for:
   - Per-GPU-hour cost (with commitment tier breakdowns)
   - Operational headcount requirement
   - Egress/data transfer fees
   - Interruptions per day × MTTR × engineer time cost
   - Capital drag (time cost of capital on on-prem investment)
3. Model sensitivity across utilization rates: 30%, 50%, 70%, 85%

**Competitive Positioning**
1. Differentiate on **risk**, not price: Frame TCO around "what happens when things go wrong"
2. Leverage the ARENA trial program as proof mechanism before commitment
3. Address the transparency gap—consider publishing baseline rates or a pricing calculator

---

## Notable Quotes

> "A TCO model isn't a spreadsheet—it's a story. It's the financial proof point for a much more visceral truth: building your own AI infrastructure is like constructing a power plant just to charge your phone."
> — **The Creative**

> "I've built GPU clusters. I've dealt with the Nvidia supply chain roulette, the liquid-cooling leaks, the infiniband topology nightmares at 2am. So when we talk TCO, I'm not interested in 'managed platform' hand-waving."
> — **The Skeptic**

> "Lock-in dressed up as convenience—that's the *real* fear. Not the invoice, but the trap. I've watched teams get stuck on platforms because migrating their data and workloads would take months."
> — **The Customer**

> "If we're building a defensible TCO model, we can't just parrot the website... What we can quantify is the fully-loaded cost of a six-month build-out delay: 6 ML engineers burning salary while waiting for hardware, the opportunity cost of that capital sitting idle."
> — **The Strategist**

> "CoreWeave doesn't publish a rate card because we price based on workload profile, cluster size, and commitment level—not a one-size-fits-all sticker price... Flex + Spot on a 55,000 GPU-hour/month profile with 30% spot eligibility should land you around $2.20-$2.40 effective per GPU-hour."
> — **CoreWeave Finance Director**

---

## Overall Assessment

**Discussion Quality: Strong with One Critical Gap**

The deliberation successfully moved from abstract positioning to concrete scenario modeling. The Customer's real-world constraints (18-month runway, variable workloads, lean team) grounded the discussion in practical decision-making. The three-layer TCO framework (capital efficiency, operational drag, opportunity cost) provides a defensible structure. The competitive analysis with Lambda introduced market reality that sharpened the positioning.

**What Was Missing**

The Finance Director's framework-level pricing guidance ($2.20-$2.40 effective) is valuable, but CoreWeave's lack of published rates remains an unforced competitive error. The Customer articulated this clearly: their CFO will see Lambda's $2.76 on a pricing page and compare it against a "request consultation" form. Without a rate card, the TCO model cannot reach a conclusion—it can only define the framework.

Additionally, the discussion did not resolve the developer experience questions: specific Helm chart compatibility, support SLAs with response times, and the actual boundary between "managed" and "customer-managed" operations. The Customer's question about 11pm Saturday failures deserves a concrete answer.

**Critical Path Forward**

The TCO model cannot be completed without CoreWeave's pricing data. The framework is ready, the scenario is defined, and the competitive benchmark (Lambda at $2.76) is established. What remains is the CoreWeave column in the comparison matrix.
