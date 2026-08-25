# Product Requirements Document (PRD)

## 1. Product Name

**BizRisk AI Agent**

**Product Type:** Agentic AI Business Due-Diligence and Legal Entity Risk Assessment Platform

---

## 2. Product Summary

BizRisk AI Agent allows a user to provide whatever information they have about a business, such as:

- Company name
- GSTIN
- CIN
- Address
- Website
- Director/promoter name
- EPFO establishment information
- Other identifying information

The system automatically researches the business across publicly accessible sources using browser-based AI agents.

The product then:

1. Discovers the likely legal entity.
2. Finds corresponding GST, MCA, EPFO and other public records.
3. Resolves whether those records belong to the same entity.
4. Stores evidence from each source.
5. Identifies inconsistencies and risk indicators.
6. Calculates an explainable Business Risk Score.
7. Generates an evidence-backed Business Risk Report.
8. Runs a QA check before showing the result.

The system does **not declare that a company is fraudulent**.

It identifies:

- inconsistencies;
- anomalies;
- verification failures;
- incomplete information;
- business-risk indicators.

---

# 3. Problem Statement

Business verification is currently fragmented.

A user evaluating a supplier, distributor, manufacturer, vendor or other business partner may need to manually check:

```text
GST Portal
+
MCA
+
EPFO
+
Company website
+
Third-party business databases
+
General web search
```

The user then has to manually determine:

- whether records belong to the same company;
- whether company names match;
- whether addresses match;
- whether GST is active;
- whether incorporation information is consistent;
- whether business activity is consistent;
- whether the company's public claims align with official records;
- whether there are meaningful risk signals.

This process is:

- slow;
- repetitive;
- difficult for non-experts;
- prone to entity-matching errors;
- difficult to audit.

---

# 4. Product Goal

The product should answer:

> **“Based on available public information, what should I know before doing business with this legal entity?”**

The system should transform scattered public information into an evidence-backed due-diligence report.

---

# 5. Core Product Principles

### P1. User can start with incomplete information

The user should not need GSTIN, CIN and company name together.

One strong identifier should be sufficient to begin research.

---

### P2. Browser-first research

The product should not depend on official GST, MCA or EPFO APIs for its core functionality.

Browser-use agents should research:

- official portals;
- government sources;
- reputable third-party databases;
- company websites;
- other public sources.

---

### P3. Evidence before conclusion

No material factual claim should enter the final report unless evidence exists in the Evidence Store.

---

### P4. Resolve identity before calculating risk

The system must not combine records from similarly named businesses without sufficient entity-resolution confidence.

---

### P5. AI identifies signals; deterministic logic scores them

AI can classify and interpret findings.

The final numerical risk score must come from configurable business rules.

---

### P6. Explainable results

Every important risk indicator should answer:

```text
What was found?
Why does it matter?
What evidence supports it?
How confident are we?
```

---

### P7. Risk is not fraud

The product must distinguish between:

```text
Suspicious
Inconsistent
Unverified
Higher risk
```

and:

```text
Fraud confirmed
```

The latter must not be inferred from ordinary public-data inconsistencies.

---

# 6. Target Users

## Primary Users

### Procurement Teams

Need to verify suppliers before onboarding or payment.

### Finance / Credit Teams

Need to understand whether additional verification is required before extending business credit.

### SMEs

Often lack dedicated compliance or due-diligence teams.

### Manufacturers

Need to evaluate distributors, dealers and channel partners.

### Distributors

Need to evaluate manufacturers and brands.

### B2B Platforms

Need a first-level business-verification layer.

---

# 7. Core Jobs to Be Done

### JTBD 1

> Before I start doing business with a company, help me verify that its public identity appears consistent.

### JTBD 2

> Find the official and relevant public records for this business without requiring me to manually search multiple portals.

### JTBD 3

> Tell me which inconsistencies deserve attention.

### JTBD 4

> Show me the evidence behind every important finding.

### JTBD 5

> Tell me whether normal verification is sufficient or whether enhanced due diligence is recommended.

---

# 8. User Input

The user may provide any combination of:

```text
Business name
Trade name
GSTIN
CIN
EPFO establishment code
Website
Address
City
State
Phone
Email
Director/promoter name
Additional comments
```

Example:

```text
Company: ABC Foods
Location: Noida
Website: abcfoods.in
```

Or simply:

```text
09XXXXXXXXXXXXX
```

---

# 9. Primary User Flow

```text
START
  ↓
Enter available business information
  ↓
Start Investigation
  ↓
Entity Discovery
  ↓
Investigation Plan
  ↓
Browser Research
  ↓
Entity Resolution
  ↓
Additional Research if required
  ↓
Risk Analysis
  ↓
Risk Scoring
  ↓
Report Generation
  ↓
QA Validation
  ↓
Business Risk Report
```

---

# 10. Investigation Progress UI

The user should be able to see the current stage without seeing private AI reasoning.

Example:

```text
Investigating ABC Foods...

✓ Input analysed
✓ Potential legal entity discovered
✓ GST record located
✓ MCA information located
○ EPFO verification in progress
✓ Company website identified
✓ Entity identity resolved
○ Risk analysis in progress
○ Final report verification pending
```

Do not expose chain-of-thought.

---

# 11. Multi-Agent Product Architecture

The product will use a sequential graph-based multi-agent workflow.

```text
                   USER
                     │
                     ▼
               Intake Agent
                     │
                     ▼
              Discovery Agent
                     │
                     ▼
               Planner Agent
                     │
                     ▼
          Browser Research Agent
                     │
                     ▼
               Evidence Store
                     │
                     ▼
          Entity Resolution Agent
                     │
              ┌──────┴──────┐
              │             │
        Low Confidence     Resolved
              │             │
              ▼             ▼
         Planner Agent   Risk Agent
              │             │
              └──────►      ▼
                         Risk Engine
                              │
                              ▼
                         Report Agent
                              │
                              ▼
                           QA Agent
                       ┌──────┴──────┐
                       │             │
                     FAIL           PASS
                       │             │
                       ▼             ▼
                    Planner         END
```

---

# 12. Agent Specifications

## 12.1 Intake Agent

### Objective

Convert the user's input into structured investigation data.

### Input

Unstructured or partially structured user information.

### Output

```json
{
  "business_name": null,
  "gstin": null,
  "cin": null,
  "epfo_code": null,
  "website": null,
  "address": null,
  "location": null,
  "people": []
}
```

### Requirements

The Intake Agent must:

- detect identifiers;
- normalize company names;
- identify location information;
- recognize GSTIN/CIN formats;
- preserve original user input;
- avoid inventing missing information.

---

# 12.2 Discovery Agent

### Objective

Find candidate legal entities and identifiers.

### Research channels

- Web search
- GST-related public sources
- MCA-related sources
- EPFO
- Company websites
- Reputable third-party sources

### Output

```json
{
  "candidate_entities": [
    {
      "legal_name": "",
      "trade_name": "",
      "gstin": "",
      "cin": "",
      "location": "",
      "website": "",
      "confidence": 0.82,
      "evidence_ids": []
    }
  ]
}
```

### Product requirement

Discovery results must remain **candidates** until Entity Resolution confirms them.

---

# 12.3 Planner Agent

### Objective

Determine what should be researched next.

### Responsibilities

The Planner should evaluate:

```text
What do we already know?
What is missing?
What conflicts exist?
Which sources should be checked?
Which tasks have the highest value?
Is there enough evidence to continue?
```

### Example plan

```json
{
  "tasks": [
    {
      "id": "T1",
      "type": "GST_VERIFY",
      "priority": 1,
      "target": "09XXXXXXXXXXXXX"
    },
    {
      "id": "T2",
      "type": "MCA_VERIFY",
      "priority": 1,
      "target": "ABC Foods Private Limited"
    },
    {
      "id": "T3",
      "type": "EPFO_SEARCH",
      "priority": 2,
      "target": "ABC Foods"
    }
  ]
}
```

The Planner should not directly perform browser research.

---

# 12.4 Browser Research Agent

### Objective

Execute research tasks.

### Capabilities

The Browser Research Agent should:

- search;
- navigate;
- click;
- enter search information;
- open records;
- extract structured facts;
- record source URLs;
- classify source quality;
- return evidence.

### Source hierarchy

```text
1. Government / official source
2. Government open data
3. Official company source
4. Reputable third-party database
5. General public web
```

The system should prefer higher-authority sources.

---

# 13. Primary Research Sources

## GST Research

Research may include:

```text
GSTIN
Legal name
Trade name
GST status
Registration date
Taxpayer type
Constitution
Principal place of business
Nature of business
Public filing information where available
```

Preferred:

```text
GST official portal
```

Fallback:

```text
Reputable GST information websites
```

---

# 14. MCA Research

Research may include:

```text
CIN
Legal company name
Company status
Incorporation date
Company type
Registered state
Registered office
Authorised capital
Paid-up capital
Business activity
Publicly available director information
```

Preferred:

```text
MCA / government sources
```

Fallback:

```text
Reputable company databases
```

---

# 15. EPFO Research

Research may include:

```text
Establishment name
Establishment code
Establishment status
PAN-linked establishments
Sub-code / branch information
Public establishment details
```

Preferred:

```text
EPFO Establishment Search
```

Fallback information can come from other reliable public sources.

---

# 16. Company Website Research

The Browser Research Agent should attempt to identify the official company website.

Possible attributes:

```text
Company name
Address
Contact information
Business description
Product categories
Operating locations
Management claims
Established/founded year
Certifications claimed
```

Website information is supporting evidence and should not override authoritative government data.

---

# 17. CAPTCHA and Authentication Handling

The product must not bypass:

- CAPTCHA;
- OTP;
- authentication;
- login restrictions;
- paywalls.

If interaction is necessary:

```text
Research reaches CAPTCHA
       ↓
Agent pauses affected task
       ↓
User is asked for intervention
       ↓
Other research may continue
       ↓
User completes verification
       ↓
Research resumes
```

If verification cannot be completed, mark the source:

```text
Unable to verify
```

Do not convert it automatically into a risk signal.

---

# 18. Evidence Store

Every material extracted fact must become an Evidence Record.

### Evidence schema

```json
{
  "evidence_id": "EV-001",
  "investigation_id": "INV-001",
  "entity_id": "ENTITY-001",
  "field": "gst_status",
  "value": "Active",
  "source_name": "GST Portal",
  "source_url": "",
  "source_type": "government",
  "retrieved_at": "",
  "confidence": 1.0,
  "research_agent": "browser_research",
  "supporting_text": ""
}
```

### Required fields

- evidence ID
- entity ID
- field
- extracted value
- source
- URL
- source category
- timestamp
- confidence

---

# 19. Entity Resolution

Entity Resolution is a mandatory stage.

The system must determine whether records collected from different websites belong to the same legal entity.

## Matching signals

### Strong

```text
GSTIN
CIN
PAN relationship
Exact legal name
Exact address
Official website
```

### Supporting

```text
Trade name
City
State
PIN code
Phone
Director
Business activity
Registration dates
```

---

# 20. Entity Confidence

Example:

```text
GST ↔ MCA            97%
GST ↔ Website        91%
MCA ↔ EPFO           89%

Overall Entity Confidence
94%
```

Suggested default:

```text
≥85%     Continue to risk analysis
<85%     Additional research required
```

The threshold must be configurable.

---

# 21. Entity Resolution Failure

If multiple candidates remain plausible:

```text
ABC Foods Pvt Ltd — Confidence 62%

ABC Food Industries Pvt Ltd — Confidence 58%
```

the system must:

1. return to Planner;
2. request additional research;
3. avoid merging records.

If still unresolved, report:

> Entity could not be conclusively identified.

Do not generate a definitive risk assessment.

---

# 22. Risk Analysis Categories

## Identity Risk

Potential signals:

- conflicting legal names;
- conflicting GSTINs;
- address mismatch;
- low entity confidence;
- multiple plausible entities.

---

## Registration Risk

Potential signals:

- inactive/cancelled GST;
- conflicting registration status;
- unusually recent registration;
- incorporation information mismatch.

---

## Compliance Risk

Where publicly available:

- irregular filing information;
- missing filing periods;
- inconsistent compliance status.

---

## Business Activity Risk

Compare:

```text
GST activity
MCA activity
Website activity
Third-party activity
```

Example:

```text
GST:
Electrical equipment wholesale

MCA:
IT consulting

→ Major business activity inconsistency
```

---

# 23. Operational Risk

Potential signals:

- major registered-location mismatch;
- no meaningful public business footprint;
- website inconsistent with official records;
- EPFO establishment does not correspond with the resolved entity;
- operating claims difficult to substantiate.

Absence of EPFO or website information must not automatically imply fraud.

---

# 24. Historical Claim Risk

Example:

```text
Website:
Operating since 1998

MCA:
Current company incorporated in 2024
```

Report:

> The company's public website states operations since 1998, while the current legal entity was incorporated in 2024. This may relate to a predecessor business or brand history and should be independently verified.

Do not automatically flag it as fraud.

---

# 25. Risk Scoring Engine

The LLM must not directly assign the numerical overall risk score.

Use configurable deterministic rules.

Example:

| Risk Signal | Weight |
|---|---:|
| GST inactive/cancelled | +30 |
| Serious legal identity conflict | +25 |
| Entity confidence below accepted level | +20 |
| Major address mismatch | +10 |
| Major business activity mismatch | +10 |
| Very recent registration | +5 |

Scores must be capped at 100.

---

# 26. Risk Levels

Suggested:

| Score | Risk Level |
|---:|---|
| 0–30 | Low |
| 31–60 | Moderate |
| 61–80 | High |
| 81–100 | Very High |

These values must be configuration driven.

---

# 27. Positive Signals

The report should not contain only negative indicators.

Examples:

```text
✓ GST status appears active
✓ Legal name matched strongly across sources
✓ MCA company identified
✓ Registered state consistent
✓ EPFO establishment identified
✓ Official website consistent with legal identity
```

This provides a balanced due-diligence result.

---

# 28. Report Agent

The Report Agent converts:

```text
Resolved Entity
+
Evidence
+
Risk Signals
+
Positive Signals
+
Risk Scores
+
Unresolved Information
```

into a user-facing report.

It must not perform new browsing.

---

# 29. Final Report Structure

## A. Business Overview

```text
Legal Name
Trade Name
GSTIN
CIN
EPFO
Website
Registered State
Business Activity
```

---

## B. Entity Verification

```text
Entity Match Confidence: 94%
```

Include a source-by-source match summary.

---

## C. Overall Risk

```text
BUSINESS RISK

58 / 100
MODERATE
```

---

## D. Category Scores

Example:

```text
Identity Risk        12/100
Registration Risk    25/100
Compliance Risk      52/100
Consistency Risk     65/100
Operational Risk     35/100
```

---

## E. Major Risk Indicators

Each finding must have:

```text
Severity
Finding
Explanation
Evidence
Confidence
```

---

## F. Positive Indicators

List positive verification signals.

---

## G. Unverified Information

Example:

```text
EPFO information could not be conclusively verified.
```

---

## H. Recommended Action

Possible recommendations:

```text
Standard verification sufficient
```

```text
Additional documentation recommended
```

```text
Enhanced due diligence recommended
```

The product should not recommend rejecting a company solely based on automated public-data analysis.

---

# 30. Evidence Display

For each finding, the user should be able to inspect:

```text
Source
Source type
Value found
URL
Retrieval date
Confidence
```

Example:

```text
GST Status: Active

Source:
GST Portal

Retrieved:
24 August 2026

Confidence:
High
```

---

# 31. QA Agent

The QA Agent must validate the report before release.

### Checks

#### Evidence integrity

Does every material claim have evidence?

#### Entity integrity

Do all records relate to the resolved entity?

#### Scoring integrity

Does the score match configured rules?

#### Source hierarchy

Has low-quality third-party information incorrectly overridden government evidence?

#### Language safety

Does the report incorrectly describe a company as fraudulent?

#### Contradictions

Has any major conflicting evidence been hidden?

---

# 32. QA Failure Flow

```text
Report
  ↓
QA
  ↓
FAIL
  ↓
Classify failure
  ↓
Planner
  ↓
Additional research / correction
  ↓
Risk analysis
  ↓
Report
  ↓
QA
```

A maximum loop count should be configured to prevent endless agent execution.

Example:

```text
Maximum investigation retry loops: 3
```

---

# 33. Investigation Statuses

Suggested statuses:

```text
CREATED
UNDERSTANDING_INPUT
DISCOVERING_ENTITY
PLANNING
RESEARCHING
WAITING_FOR_USER
RESOLVING_ENTITY
ANALYSING_RISK
GENERATING_REPORT
QA_REVIEW
COMPLETED
PARTIALLY_COMPLETED
FAILED
```

---

# 34. Investigation History

Users should eventually be able to see:

| Business | Risk | Confidence | Date | Status |
|---|---:|---:|---|---|
| ABC Foods Pvt Ltd | 58 | 94% | 24 Aug | Complete |
| XYZ Traders | 22 | 97% | 24 Aug | Complete |
| PQR Enterprises | — | 52% | 24 Aug | Unresolved |

For MVP, saved history is a **Should Have**.

---

# 35. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| PRD-F01 | Accept incomplete business information | P0 |
| PRD-F02 | Understand and structure user input | P0 |
| PRD-F03 | Discover candidate entities | P0 |
| PRD-F04 | Generate investigation plan | P0 |
| PRD-F05 | Perform browser-based research | P0 |
| PRD-F06 | Research GST-related information | P0 |
| PRD-F07 | Research MCA-related information | P0 |
| PRD-F08 | Research company website | P0 |
| PRD-F09 | Research EPFO information when possible | P1 |
| PRD-F10 | Support third-party fallback sources | P0 |
| PRD-F11 | Store source-backed evidence | P0 |
| PRD-F12 | Perform entity resolution | P0 |
| PRD-F13 | Calculate entity confidence | P0 |
| PRD-F14 | Re-plan when confidence is insufficient | P0 |
| PRD-F15 | Detect cross-source inconsistencies | P0 |
| PRD-F16 | Generate deterministic risk score | P0 |
| PRD-F17 | Generate Business Risk Report | P0 |
| PRD-F18 | QA every final report | P0 |
| PRD-F19 | Show investigation progress | P1 |
| PRD-F20 | Handle CAPTCHA using human-in-loop | P1 |
| PRD-F21 | Save previous investigations | P1 |
| PRD-F22 | Export report | P2 |

---

# 36. MVP Scope

## P0 — Must Launch

### Input

- Business name
- GSTIN
- CIN
- Website
- Location
- Free-text information

### Research

- General web
- GST-related sources
- MCA-related sources
- Company website
- Third-party fallback

### Intelligence

- Entity discovery
- Entity resolution
- Legal-name comparison
- Address comparison
- Registration-date comparison
- Activity comparison
- GST-status analysis
- Evidence collection

### Output

- Entity Confidence
- Risk Score
- Risk Level
- Major Findings
- Positive Signals
- Unverified Information
- Recommendation
- Evidence

---

# 37. P1 — Immediately After MVP

- EPFO deeper research
- Human-in-loop CAPTCHA flow
- Investigation history
- Rich source viewer
- More configurable scoring rules
- Retry controls
- Better research progress UI

---

# 38. Not in MVP

Do not include initially:

- GSTR-1/GSTR-2B/GSTR-3B analysis
- invoice-level fraud analysis
- e-way bill analysis
- director relationship graphs
- UDYAM deep integration
- litigation intelligence
- sanctions intelligence
- credit bureau data
- bank data
- financial statement analysis
- predictive fraud ML
- full KYC
- continuous monitoring
- automatic CAPTCHA solving
- autonomous OTP access
- paid government-document purchasing

---

# 39. Product UI

## Screen 1 — Start Investigation

```text
Business Due-Diligence

Provide whatever information you have.

Business Name
[________________________]

GSTIN / CIN / Other ID
[________________________]

Website
[________________________]

Location
[________________________]

Additional Details
[________________________]

[ Start Investigation ]
```

---

# 40. Screen 2 — Research Progress

```text
Researching ABC Foods

✓ Input understood
✓ Possible entity found
✓ GST information located
✓ MCA information located
○ Company website being analysed
○ Entity verification pending
○ Risk analysis pending
```

If human intervention is required:

```text
Action Required

The source requires manual verification.

[ Continue Verification ]
```

---

# 41. Screen 3 — Risk Report

```text
ABC FOODS PRIVATE LIMITED

ENTITY CONFIDENCE
94%

BUSINESS RISK
58 / 100
MODERATE
```

### Identity

```text
GSTIN           09XXXXXXXXXXXXX
CIN             UXXXXXXXXXXXX
GST Status      Active
State           Uttar Pradesh
Website         abcfoods.in
```

### Key Findings

```text
MEDIUM
Address differs across official and company sources.

MEDIUM
Current legal entity incorporation date differs
from the operating history claimed on the website.

LOW
Business activity descriptions are not completely
consistent across sources.
```

### Positive Signals

```text
✓ GST active
✓ MCA identity discovered
✓ Strong legal-name match
✓ Website associated with resolved entity
```

---

# 42. Error Handling

## Source unavailable

```text
GST source temporarily unavailable.

Investigation continued using available sources.
```

---

## Entity unresolved

```text
We found multiple businesses matching the supplied information.

Entity could not be resolved with sufficient confidence.
```

Do not produce a normal risk score.

---

## Insufficient information

```text
Available public information is insufficient for
a reliable risk assessment.
```

---

## Source conflict

Preserve both values.

Example:

```text
Registered Address

GST:
Noida, Uttar Pradesh

MCA:
Ghaziabad, Uttar Pradesh

Status:
Conflict detected
```

---

# 43. Non-Functional Requirements

## Reliability

Material facts must be source-backed.

## Explainability

Risk findings must be understandable to a non-technical user.

## Traceability

Every finding should be traceable to evidence.

## Modularity

New research sources should be pluggable.

## Recoverability

The graph should resume from failed research nodes where possible instead of restarting the complete investigation.

## Cost Control

Configure:

```text
Maximum browser actions
Maximum research tasks
Maximum LLM calls
Maximum graph retries
Maximum research duration
```

## Security

Never store:

- CAPTCHA values;
- OTPs;
- user credentials;

unless explicitly required by a future secure authentication design.

---

# 44. Technical Product Requirements

Suggested stack:

```text
Frontend
Next.js

Backend
FastAPI

Agent Graph
LangGraph

Browser Research
Browser Use

Database
PostgreSQL

Evidence Search
PostgreSQL / pgvector if required

Risk Engine
Python

Background Execution
Celery / task queue if needed
```

---

# 45. Core Data Objects

## Investigation

```text
Investigation ID
User ID
Input
Status
Created At
Updated At
Resolved Entity ID
Entity Confidence
Risk Score
Risk Level
```

---

## Entity

```text
Entity ID
Canonical Name
Trade Name
GSTIN
CIN
EPFO Code
Website
Address
State
Business Activity
```

---

## Evidence

```text
Evidence ID
Entity ID
Source
Source Type
URL
Field
Value
Confidence
Timestamp
```

---

## Research Task

```text
Task ID
Investigation ID
Task Type
Objective
Priority
Status
Source
Result
```

---

## Risk Signal

```text
Signal ID
Category
Severity
Description
Evidence IDs
Risk Weight
Confidence
```

---

# 46. Product Analytics

Track:

### Investigation Completion Rate

```text
Completed investigations
÷
Started investigations
```

### Entity Resolution Rate

Percentage of investigations successfully resolved.

### Evidence Coverage

Percentage of material report claims with valid evidence.

Target:

```text
100%
```

### Research Source Success Rate

Measure success separately for:

```text
GST
MCA
EPFO
Official websites
Third-party websites
```

### False Entity Merge Rate

This is a critical metric.

Target should be as close to zero as practically possible.

### Average Investigation Cost

Track browser and LLM cost per investigation.

### Average Investigation Time

Used to identify slow research sources.

---

# 47. MVP Acceptance Criteria

The MVP is ready when all of the following work:

- [ ] User can submit incomplete company information.
- [ ] Intake Agent structures the information.
- [ ] Discovery Agent identifies candidate entities.
- [ ] Planner creates research tasks.
- [ ] Browser Research Agent executes those tasks.
- [ ] Source-backed evidence is stored.
- [ ] GST-related information can be researched where publicly available.
- [ ] MCA-related information can be researched.
- [ ] Company website can be identified and analysed.
- [ ] Third-party sources can be used as fallback.
- [ ] Entity Resolution Agent calculates match confidence.
- [ ] Low-confidence cases loop back for research.
- [ ] Cross-source inconsistencies are detected.
- [ ] Risk Engine calculates deterministic scores.
- [ ] Report Agent generates an evidence-backed report.
- [ ] QA Agent validates the final report.
- [ ] Unsupported factual claims are prevented.
- [ ] Unresolved entities do not receive misleading risk scores.
- [ ] The system never bypasses CAPTCHA/OTP.
- [ ] The system does not label a business fraudulent without authoritative evidence.

---

# 48. MVP Success Definition

The first version does not need to determine whether a company is fraudulent.

It needs to reliably answer:

> **What public information exists about this business, does that information appear to belong to the same legal entity, what does not match, and what should the user verify before doing business with it?**

The MVP succeeds when it can transform:

```text
Partial Business Information
```

into:

```text
Resolved Legal Entity
        +
Verified Public Evidence
        +
Cross-Source Comparison
        +
Risk Indicators
        +
Explainable Risk Score
        +
Recommended Verification Action
```

with every important conclusion supported by evidence.

---

# 49. Product Definition

**BizRisk AI Agent is a graph-based multi-agent business due-diligence platform that autonomously researches legal entities through browser-based public-source investigation, resolves their identity across multiple sources, identifies inconsistencies and business-risk indicators, and generates an explainable, evidence-backed Business Risk Report.**

The core product workflow is:

> **Discover → Plan → Research → Resolve → Verify → Analyse → Score → Report → QA**