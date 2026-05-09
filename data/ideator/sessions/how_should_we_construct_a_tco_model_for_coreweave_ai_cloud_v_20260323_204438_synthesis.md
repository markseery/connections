# Ideator Synthesis

**Topic:** How should we construct a TCO model for CoreWeave AI Cloud vs an On-prem approach?

---

# Synthesis: TCO Model Construction for CoreWeave AI Cloud vs. On-Prem

## 1. Key Agreements

**Framework Convergence:**
- **Three-scenario model is the correct approach**: Greenfield datacenter build (12-18 months), colocation deployment (6-8 weeks), and CoreWeave Cloud (hours-to-days). Each scenario requires distinct cost profiles and breakeven calculations.

- **Performance-adjusted costing over raw $/GPU-hour**: The group agreed that utilization rates, goodput, and downtime recovery factors are more meaningful than sticker prices. The formula: `Effective Compute Cost = (GPU hourly rate) ÷ (Goodput %) × (1 + Downtime Recovery Factor)`.

- **"Innovation Tax" is validated by real customer pain**: The Customer's $252K/year in misallocated engineering spend (40% of $630K on infrastructure babysitting) confirmed this is not a narrative—it's a line item.

- **Exit costs must be a published model component**: The group converged on a "Trap Door Index" or "Total Cost of Exit" as defensible differentiation. CoreWeave's Zero Egress Migration and portable Kubernetes/Slurm stack were acknowledged as real advantages.

- **SemiAnalysis ClusterMAX™ Platinum is defensible third-party validation**: This independent rating can be cited for infrastructure maturity claims, but not as a plug-in number for customer-specific performance calculations.

- **Customer-adjustable inputs are more honest than vendor claims**: The "96% goodput" figure should be replaced with a customer-adjustable utilization input derived from their own cluster logs, with ARENA offered as the validation path.

- **Engineering overhead for on-prem is substantial and quantifiable**: 2-3 FTEs at $250K-$400K fully-loaded = $750K-$1.2M annually, plus recruiting costs (3-6 months), onboarding ramp (3 months), and retention risk.

---

## 2. Unresolved Tensions

**Methodology Transparency Gap:**
The "96% goodput" and "50% fewer interruptions" claims lack published benchmark configurations. The Skeptic demanded: "What workload? What cluster size? What baseline?" The compromise—replacing with customer-adjustable inputs—satisfies defensibility but leaves the Customer without an auditable baseline for initial comparison.

**Cloud Overhead Factor Not Quantified:**
The model assumes near-zero infrastructure management on CoreWeave, but the Skeptic challenged this: "When you run on CoreWeave, you're not at 0% infrastructure management—you still need someone to write Slurm scripts, debug data loaders, and manage checkpoint strategies. Is that 5% of engineering time? 15%?" This number is missing from the model.

**Actual Breakeven Not Yet Calculated:**
Despite five rounds of deliberation and the Customer providing concrete parameters (64 H100s, 73% utilization, $630K engineering spend, recent two-week outage), no participant produced the three-scenario breakeven curve with those numbers. The Customer called this out directly: "Run the model. Show me the three-scenario breakeven with *my* parameters, not hypotheticals."

**Migration Playbook Absent:**
The Customer requested "the documented steps a customer used to move off CoreWeave" to validate the portability claims. No actual migration case study or playbook was produced—only assurances that the stack is "standard."

**Utilization Threshold Crossover Point:**
The Skeptic identified that the model lacks a "utilization threshold input that shows *when* the crossover happens. Below 70% sustained utilization, cloud wins on pure math. Above 85% with mature ops, on-prem breaks even." This threshold analysis was not modeled.

**Worst-Case Performance Data:**
The Customer asked for "the *worst-performing* workload in [SemiAnalysis's] test set" because "my distributed fine-tuning jobs with custom data loaders aren't winning any MLPerf awards." No floor performance data was provided.

---

## 3. Recommended Actions

| Priority | Action | Owner | Timeline |
|----------|--------|-------|----------|
| **1** | Run three-scenario TCO curve with Customer's actual parameters: 64 H100s, 73% utilization, $630K engineering spend, $252K innovation tax, recent two-week outage | Finance Director + Strategist | Immediate |
| **2** | Replace "96% goodput" with customer-adjustable utilization input (range: 85-96%) derived from SemiAnalysis cluster health scoring | Strategist | Within model |
| **3** | Add Cloud Overhead Factor to the model (estimated 5-15% of engineering time) to make Innovation Tax comparison honest | Skeptic | Within model |
| **4** | Publish "Trap Door Index" (Total Cost of Exit) as a standard TCO component with line items: data egress, workflow migration, asset liquidation, knowledge transfer | Creative + Strategist | Next revision |
| **5** | Add SemiAnalysis ClusterMAX methodology to TCO appendix as baseline for infrastructure maturity claims | Strategist | Before customer presentation |
| **6** | Produce actual migration playbook with documented steps from a customer exit case study | Finance Director | 2 weeks |
| **7** | Include MTTR and failure recovery cost comparison in the model | Skeptic | Within model |
| **8** | Add "Time-to-Scale Cost" formula: `(Revenue at stake) × (Days of delay) × (Probability of competitive capture)` | Creative | Within model |
| **9** | Model utilization threshold crossover point (at what % utilization does on-prem break even?) | Skeptic | Within model |

---

## 4. Notable Quotes

| Participant | Quote |
|-------------|-------|
| **CoreWeave Finance Director** | "With new architectures dropping every 18-24 months, your ROA plummets fast. CoreWeave's model converts that CapEx into OpEx, preserving capital for your core business." |
| **The Customer** | "When I'm staring at a 3am PagerDuty alert because some node decided to drift off into the ether, I don't care about your theoretical utilization percentages. I care about whether my engineers are wasting half their week debugging infrastructure instead of shipping models." |
| **The Strategist** | "If a customer's on-prem cluster sits at 85% effective utilization due to failures, throttling, and queue management, they're bleeding 15% of their CapEx into the void. That's the number investors should see." |
| **The Creative** | "Building on-prem AI infrastructure isn't an investment; it's an **Innovation Tax**. You aren't just buying servers; you're volunteering to become a power utility, a cooling expert, and a hardware logistics manager." |
| **The Skeptic** | "I've built GPU clusters from scratch. I've also watched finance teams build TCO models that were complete fiction because they wanted the answer to come out a certain way." |

---

## 5. Overall Assessment

**Discussion Quality: Strong (7.5/10)**

The deliberation progressed from marketing-level positioning to a defensible framework with genuine methodological rigor. The Skeptic's persistent challenges elevated the conversation from sales pitch to decision tool. The Customer's real-world data grounded the abstract debate in concrete terms, and the Creative's framing ("Innovation Tax," "Trap Door Index") provided memorable positioning that survived scrutiny.

**Strengths:**
- Multi-perspective balance: financial rigor (Finance Director), customer skepticism (Customer), strategic differentiation (Strategist), emotional resonance (Creative), and methodological challenge (Skeptic)
- Real numbers introduced and incorporated ($252K innovation tax, 73% utilization, $574K total annual gap)
- Exit cost framework developed with line-item specificity
- Third-party validation (SemiAnalysis) properly positioned as infrastructure maturity signal, not performance plug-in

**Critical Gaps:**
- **The model was never actually run.** After five rounds, the Customer's request for a three-scenario breakeven with their parameters remains unfulfilled. This is the single most important action item.
- **Cloud overhead factor absent.** The model still assumes zero infrastructure burden on cloud, which the Skeptic correctly identified as fictional.
- **Migration playbook missing.** Portability claims are unproven without a documented exit case study.
- **Worst-case performance data not provided.** The Customer needs floor performance, not ceiling.

**What Was Missing:**
A dedicated modeler or analyst who could have produced the actual TCO curves in real-time. The conversation converged on the right framework but stalled at execution. The Customer's final frustration—"Run the model. If you can't give me the three-scenario breakeven with *my* parameters by end of this conversation, that tells me everything I need to know about whether this TCO model is a decision tool or a marketing asset"—is the most important signal from this deliberation.

**Verdict:** The group designed a defensible TCO framework but failed to deliver the spreadsheet the Customer needs to make a decision. The next conversation must begin with the model outputs, not the model design.
