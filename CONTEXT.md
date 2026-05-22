# VA Disability Agent

The domain language used by this project. The agent helps US veterans file VA disability claims; the language here mirrors the way the VA, the CFR, and veterans themselves talk about disability claims — not how the knowledge graph is implemented.

## Language

**Claim**:
The whole filing package submitted to the VA in one go. A single Claim bundles one or more Claimed Conditions and the supporting Evidence for each.
_Avoid_: issue, case, submission

**Claimed Condition**:
A single condition within a Claim, mapped to one Diagnostic Code. The agent's per-condition copy-paste output describes one Claimed Condition.
_Avoid_: saying "claim" when you mean Claimed Condition (the most common drift)

**Rating Percentage**:
The number (0%, 10%, 20%, …, 100%) the VA assigns to a Claimed Condition.
_Avoid_: bare "rating" — always qualify

**Rating Level**:
A tier in the CFR rating schedule: a (Diagnostic Code, Rating Percentage, Criterion) triple. "The 30% Rating Level for DC 5260" means the 30% tier under that Diagnostic Code with its specific Criterion.
_Avoid_: "rating tier", "rating step"

**Combined Rating**:
The single percentage paid on, obtained by combining all of a vet's Rating Percentages via the §4.25 Combined Ratings Table. Not arithmetic addition.
_Avoid_: "total rating", "overall rating"

**Job Code**:
The umbrella term for any branch's military occupational classification — Army/Marine MOS, Navy/Coast Guard Rating, Air Force/Space Force AFSC. Used in internal language. When talking to a specific veteran, the agent uses their branch's term ("your MOS", "your AFSC").
_Avoid_: "job", "role", "specialty"

**Condition**:
The veteran's plain-language description of a medical issue ("bad knee", "ringing in ears"). What the veteran says before the agent has matched it to anything. Used in user-facing language with the veteran.
_Avoid_: "issue", "problem", "complaint"

**Diagnostic Code**:
A specific numbered identifier in the CFR rating schedule (e.g. DC 5260 — Leg, limitation of flexion of). Always written "DC NNNN" or "Diagnostic Code NNNN".
_Avoid_: bare "code", "DX code", "rating code"

**Disability**:
A Diagnostic Code that the VA has formally service-connected and assigned a Rating Percentage to. Implies a VA decision has been issued. A veteran does not have a Disability until the VA grants one — only Conditions and Claimed Conditions.
_Avoid_: using "disability" for an un-granted Condition or Claimed Condition

**Service Connection**:
The VA's determination that a Condition is linked to military service. Binary (yes/no). Independent of severity — a Condition can be service-connected at 0% Rating Percentage.
_Avoid_: using "service-connected" as a synonym for "rated"

**Direct Service Connection**:
Service Connection granted because the veteran shows the Condition started in or during service via medical records, lay statements, or other evidence. The default path; the assumed v1 path.

**Presumptive Service Connection**:
Service Connection granted automatically by a Presumption — the VA presumes the link given specific era + location + Condition (e.g. Agent Orange, Camp Lejeune, PACT Act burn pits). The veteran does not have to prove the in-service link.

**Secondary Service Connection**:
Service Connection granted because a new Condition is caused by an already-service-connected Disability (e.g. service-connected knee injury causes hip pain via altered gait).

**Service Connection by Aggravation**:
Service Connection granted because a pre-existing Condition was made worse by service. Different evidentiary standard than Direct Service Connection.

**Presumption**:
A published VA rule that triggers automatic Service Connection given specific criteria (era, location, exposure, Condition). What enables Presumptive Service Connection.
_Avoid_: "presumptive list" when you mean a single Presumption

**Flare-up**:
A discrete episode where a Condition's severity temporarily worsens beyond its Baseline. Has a frequency, duration, and (if known) trigger. The CFR's word — use this in CFR-vocabulary outputs.
_Avoid_: "worst day" in formal output; use it only when teaching the veteran inline

**Baseline**:
A Condition's severity on a typical, non-flare day.
_Avoid_: "normal", "usual" (too vague)

**Functional Loss**:
What the veteran cannot do because of a Condition — specific activities like kneeling, climbing stairs, driving for an hour, sleeping through the night, working an 8-hour day. §4.40's concept. The most under-reported category because veterans adapt and stop noticing what they avoid.
_Avoid_: "limitation", "restriction" (too vague — Functional Loss is the specific activity, not the degree)

**Functional Loss Probe**:
A specific activity question the agent asks to elicit Functional Loss, chosen based on the veteran's reported Conditions and Job Code (e.g. "Can you kneel for more than 5 minutes?", "Can you sit through a movie without shifting?"). Used because open-ended "what can't you do?" routinely undercounts adapted-around losses.

**Worst-Day Rule**:
The doctrine that a Rating Percentage must account for Flare-ups and Functional Loss, not just Baseline severity. Codified in §4.40, §4.45, and *DeLuca v. Brown*. Surfaced inline by the agent the first time a veteran describes a fluctuating Condition.

**Evidence**:
Any item that supports a Claimed Condition, of one of the specific types below. The umbrella term.

**Service Treatment Record (STR)**:
The medical record kept by the military during the veteran's service. The highest-value Evidence for Direct Service Connection — a Condition appearing in the STR largely establishes the in-service link.
_Avoid_: "military medical records" (ambiguous with post-service military health records)

**Lay Statement**:
The veteran's own first-person written account of when a Condition began, how it has progressed, and how it affects daily life. The agent's drafted claim narrative becomes the Lay Statement the veteran submits.
_Avoid_: "personal statement", "personal narrative", "veteran statement"

**Buddy Statement**:
A written statement from someone who served with the veteran or otherwise witnessed in-service events. Peer corroboration for events the STR may not capture.
_Avoid_: "buddy letter" (used colloquially but "statement" is the VA's term)

**Private Medical Record**:
Civilian (non-VA) medical records from after service — doctor's notes, imaging, prescriptions. Bridges in-service onset to current severity.

**C&P Exam**:
The VA-ordered Compensation & Pension medical examination conducted after a Claim is filed, used to determine current severity. The VA generates this — the veteran does not provide it. The agent's role is to prepare the veteran for the C&P, not to produce one.
_Avoid_: "medical exam", "VA exam"

**C&P Exam Preparation**:
The agent's per-Claimed-Condition guidance to the veteran on what the C&P examiner will measure, what to describe (with Flare-up severity and Functional Loss in mind), and what records to bring. Produced alongside the claim narrative at the end of a session.

**DBQ (Disability Benefits Questionnaire)**:
A standardized VA form used to capture C&P Exam findings. A private provider can fill one out as Evidence (a "private DBQ"), which the v1 agent does *not* attempt to generate.

**Pyramiding**:
The §4.14 rule prohibiting the same symptom from being rated under more than one Diagnostic Code. Claiming the same symptom under two DCs is a way to lose both at appeal. The agent must check candidate Diagnostic Codes for Pyramiding conflicts and surface them as a Weakness.
_Avoid_: "double-dipping" (inaccurate moral connotation)

**Bilateral Factor**:
The §4.26 rule that grants an additional ~10% (multiplicatively) to the Combined Rating when matching-limb Disabilities exist on both sides (e.g. left knee + right knee). Not additive — applied as a step inside the §4.25 combined-ratings calculation. The agent prompts proactively when only one side of a paired Anatomy has been claimed.

**Body System**:
One of the CFR's top-level organizational categories: musculoskeletal, mental, hearing, respiratory, cardiovascular, digestive, skin, neurological, endocrine, eye, dental, genitourinary, hemic/lymphatic, gynecological. Closed set, derived from §4.71a–§4.150 headings.

**Anatomy**:
A specific anatomical structure or location within a Body System — knee, lumbar spine, left ear, right shoulder. Includes left/right laterality where applicable; "left knee" and "right knee" are distinct Anatomy entries that share a parent.
_Avoid_: "body part" (ambiguous about laterality), "region" (overloaded with deployment regions)

**Original Claim**:
The first Claim a veteran files for a given Condition. Requires Evidence of Service Connection and current severity. The v1 default and only supported Claim Type.

**Increased Rating Claim**:
A Claim filed for a Condition that already has Service Connection, asserting it has worsened and warrants a higher Rating Percentage. Skips Service Connection evidence; focuses on current severity, Flare-ups, and Functional Loss. Deferred to v2.

**Secondary Claim**:
A Claim asserting a new Condition was caused by an already-service-connected Disability (the Primary Condition). Requires medical evidence of the causal link. Deferred to v2.

**Primary Condition**:
The already-service-connected Disability that causes a Secondary Condition in a Secondary Claim.

**Reopened Claim**:
A Claim re-litigating a previously denied Condition with new and relevant Evidence (the "new and relevant" standard since 2019). Deferred to v2.
_Avoid_: "supplemental claim" (different VA procedural term about appeals)

**DD-214**:
The document issued at military separation capturing Job Code(s), Service Period dates, decorations, Deployments by region, and Discharge Characterization. The single most useful artifact a veteran can produce — every other Service History field can be inferred from it. v1 collects it via question; PDF/image upload is a v2 enhancement.

**Service Period**:
A dated range during which a veteran was on active duty (start, end, branch). A veteran may have multiple Service Periods.
_Avoid_: "tour" (ambiguous between Service Period and Deployment), "stint" (informal)

**Era**:
A named period the VA recognizes for Presumption purposes (Vietnam Era, Gulf War Era, Post-9/11 Era, Cold War, Korea). Derived from Service Period dates. The agent identifies the veteran's Era — not assumed self-evident.

**Deployment**:
A named operation or location the veteran was assigned to (Operation Enduring Freedom, Iraq, Thailand, Korea DMZ, Camp Lejeune residence). Multiple per veteran. Required for location-based Presumptions.

**Discharge Characterization**:
The type of separation — Honorable, General Under Honorable Conditions, Other Than Honorable, Bad Conduct, Dishonorable, or Uncharacterized. Affects benefit eligibility entirely. The agent asks about it, warns the veteran of any limitations it imposes, but does not gate help.

**Severity Label**:
A categorical descriptor of a symptom or Condition's intensity — mild, moderate, severe, very severe, occasional, frequent, constant. Used in SymptomReport and Criterion properties and in everyday speech.
_Avoid_: bare "severity" without qualifying which sense

**Navy Rating**:
A Navy or Coast Guard enlisted Job Code (e.g. HM, AT, BM). Always qualified with "Navy" to avoid collision with Rating Percentage / Rating Level / Combined Rating.
_Avoid_: bare "rating" for this sense
