# Business Requirements Document (BRD)

## 1. Project Name

**BizRisk AI Agent**

**Project Type:** AI-based Business Due-Diligence and Legal Entity Risk Assessment Platform

---

## 2. Purpose

BizRisk AI Agent will allow a user to provide whatever information they have about a business or legal entity and automatically research that entity across publicly available online sources.

The system will use a graph-based multi-agent architecture to:

1. Understand the information provided by the user.
2. Discover the correct legal entity.
3. Research the entity across official government portals and reputable third-party sources.
4. Resolve and connect records belonging to the same entity.
5. Compare information across sources.
6. Identify inconsistencies, anomalies, and risk signals.
7. Generate an evidence-backed Business Risk Report.

The system will focus on **business due diligence and risk identification**, not on declaring a company fraudulent.

---

# 3. Business Problem

Before working with a new vendor, supplier, distributor, dealer, manufacturer, customer, or business partner, organisations often need to verify:

- whether the business actually exists;
- whether its GST information is valid;
- whether its legal identity matches MCA records;
- whether an EPFO establishment exists;
- whether addresses and company names match;
- whether business activities are consistent;
- whether the company has suspicious or conflicting public information;
- whether additional verification should be performed before extending credit or entering into a business relationship.

Currently this information is scattered across:

- GST Portal;
- MCA;
- EPFO;
- company websites;
- government databases;
- third-party company information platforms;
- general web search.

The user has to search these sources manually and interpret the information.

BizRisk AI Agent will automate this research and generate one consolidated risk report.

---

# 4. Business Objective

The primary objective is to answer:

> **“Based on publicly available information, are there any significant risk signals or inconsistencies associated with this business?”**

The platform should reduce the time required for initial business due diligence from manual research across several websites to a single automated investigation.

---

# 5. Target Users

Primary target users include:

- Procurement teams
- Finance teams
- Vendor onboarding teams
- SMEs
- Manufacturers
- Distributors
- B2B marketplaces
- Credit-control teams
- Sales teams evaluating customers
- Businesses onboarding suppliers or channel partners

---

# 6. Core User Scenario

A user may only know:

```text
ABC Foods
Noida
```

Or may know:

```text
ABC Foods Pvt Ltd
GSTIN: 09XXXXXXXXXXXXX
Website: abcfoods.in
Noida
```

Or:

```text
GSTIN: 09XXXXXXXXXXXXX
```

The system must accept incomplete information.

The agent should attempt to discover:

- Legal business name
- Trade name
- GSTIN
- CIN
- Company registration information
- EPFO establishment
- Business address
- Business activity
- Website
- Other identifiers available publicly

The user should not be required to know all identifiers beforehand.

---

# 7. Proposed Solution

BizRisk will operate as a **sequential multi-agent system with graph-based orchestration**.

High-level workflow:

```text
User Input
    ↓
Intake Agent
    ↓
Discovery Agent
    ↓
Planner Agent
    ↓
Browser Research Agent
    ↓
Evidence Store
    ↓
Entity Resolution Agent
    ↓
Risk Analysis Agent
    ↓
Risk Scoring Engine
    ↓
Report Agent
    ↓
QA Agent
    ↓
Business Risk Report
```

The system must support loops when additional research is necessary.

Example:

```text
Entity Resolution Confidence Low
            ↓
       Planner Agent
            ↓
   Additional Research
            ↓
   Entity Resolution Again
```

---

# 8. Core Product Principle

The system must follow one strict rule:

> **No factual statement should appear in the final report unless supporting evidence has been captured from a source.**

AI can:

- plan;
- research;
- compare;
- classify;
- reason;
- identify inconsistencies;
- explain findings.

AI must not invent missing business information.

---

# 9. Data Sources

## 9.1 Primary Sources

The system should research publicly accessible information from:

### GST

Preferred source:

- Official GST Portal

Possible information:

- GSTIN
- Legal name
- Trade name
- GST status
- Registration date
- Taxpayer type
- Constitution
- Principal place of business
- Nature of business
- Public return filing information where accessible

---

### MCA

Preferred source:

- MCA website

Possible fallback sources:

- Government Open Data
- Reputable company-information platforms

Possible information:

- CIN
- Legal company name
- Company status
- Incorporation date
- Company type
- Registered state
- Registered address
- Authorised capital
- Paid-up capital
- Principal activity
- Directors where publicly available

---

### EPFO

Preferred source:

- Official EPFO Establishment Search

Possible information:

- Establishment name
- Establishment code
- Establishment status
- Associated PAN-based establishments
- Branch/sub-code information
- Public establishment information

---

## 9.2 Secondary Sources

The system may also research:

- Company official website
- Government portals
- Government tenders
- Regulatory websites
- Reputable company databases
- Business directories
- News sources
- Public corporate profiles

Third-party information must not automatically override official government information.

---

# 10. Source Priority

Sources must be classified by reliability.

### Tier 1 — Highest Authority

- GST
- MCA
- EPFO
- Government regulator
- Government open-data source

### Tier 2 — Strong Supporting Evidence

- Official company website
- Government tenders
- Official regulator listings

### Tier 3 — Third-Party Business Sources

Examples:

- Company-information databases
- Business directories
- Industry databases

### Tier 4 — General Web Sources

- Search results
- News
- Other public pages

If sources conflict, higher-authority evidence should generally receive higher weight.

---

# 11. Browser-First Research Requirement

The platform must not depend on GST, MCA, or EPFO APIs for its core research workflow.

The primary research mechanism should be a **browser-use agent**.

The agent should be capable of:

- opening websites;
- searching;
- entering business information;
- navigating search results;
- opening relevant records;
- extracting structured information;
- following relevant links;
- collecting source URLs;
- recording evidence.

The browser agent may also use third-party websites when official information is unavailable or insufficient.

---

# 12. CAPTCHA, OTP and Restricted Pages

The system must not attempt to bypass:

- CAPTCHA;
- OTP;
- authentication;
- access restrictions;
- paid-document restrictions.

If human interaction is required:

```text
Agent reaches restricted step
        ↓
Research paused
        ↓
User asked to complete verification
        ↓
Research resumed
```

The system should continue with other available sources when possible.

---

# 13. Multi-Agent Requirements

## 13.1 Intake Agent

### Responsibility

Convert the user's unstructured information into structured investigation input.

### Inputs may include

- Business name
- GSTIN
- CIN
- Address
- City/state
- Website
- Phone
- Email
- Director/promoter name
- EPFO code
- Other identifying information

### Output

```json
{
  "company_name": "",
  "gstin": "",
  "cin": "",
  "website": "",
  "location": "",
  "known_identifiers": {}
}
```

---

# 13.2 Discovery Agent

### Responsibility

Discover possible entities and identifiers using browser-based research.

### Expected activities

- Search company name
- Search GST information
- Search MCA information
- Search EPFO
- Discover legal name
- Discover CIN
- Discover GSTIN
- Discover official website
- Discover possible registered locations

### Output

One or more candidate entities.

Example:

```json
{
  "candidate_entities": [
    {
      "name": "ABC FOODS PRIVATE LIMITED",
      "gstin": "09XXXXXXXX",
      "cin": "U15XXXXXXXX",
      "confidence": 0.82
    }
  ]
}
```

Discovery results are **candidate information**, not automatically verified facts.

---

# 13.3 Planner Agent

### Responsibility

Determine what research needs to be performed.

The Planner must:

- inspect current investigation state;
- identify missing information;
- identify conflicts;
- prioritise research;
- create research tasks;
- decide when sufficient evidence exists.

Example:

```text
Task 1:
Verify GST registration.

Task 2:
Find corresponding MCA company.

Task 3:
Check EPFO establishment.

Task 4:
Compare registered addresses.

Task 5:
Validate company website.
```

The Planner should not perform browser research itself.

---

# 13.4 Browser Research Agent

### Responsibility

Execute research tasks created by the Planner.

It should:

- navigate websites;
- search;
- extract evidence;
- collect URLs;
- identify source type;
- record retrieval time;
- return structured results.

Example research instruction:

```text
Verify GST information for:
09XXXXXXXXXXXX

Required:
Legal name
Trade name
Status
Registration date
Address
Nature of business
```

---

# 13.5 Entity Resolution Agent

### Responsibility

Determine whether records found across multiple sources belong to the same entity.

It should compare:

- Legal name
- Trade name
- GSTIN
- PAN information where derivable
- CIN
- Address
- State
- PIN code
- Website
- Phone
- Directors
- Business activity
- Registration dates

### Output

```text
GST ↔ MCA: 96%
GST ↔ Website: 91%
MCA ↔ EPFO: 87%

Overall Entity Confidence: 93%
```

If confidence falls below the configured threshold, the investigation must return to the Planner.

Suggested MVP threshold:

**85%**

---

# 13.6 Risk Analysis Agent

### Responsibility

Analyse verified information and identify risk signals.

It should not independently browse the internet.

It receives structured evidence from previous stages.

Potential risk categories:

### Identity Risk

- Legal name mismatch
- Conflicting GSTIN
- Multiple candidate entities
- Address inconsistency
- Low entity-resolution confidence

### Registration Risk

- GST inactive
- GST cancelled
- Recently registered business
- Company status inconsistency
- Conflicting incorporation information

### Compliance Risk

Where information is publicly available:

- irregular GST return filing;
- missing filing periods;
- inconsistent registration status.

### Operational Risk

- no meaningful business footprint;
- website claims inconsistent with legal records;
- EPFO establishment inconsistency;
- business location mismatch.

### Activity Risk

- GST activity differs significantly from MCA activity;
- company website describes unrelated business;
- conflicting sector classifications.

### Public Information Risk

- significant contradictions between authoritative and public claims.

---

# 13.7 Risk Scoring Engine

The numerical risk score must be calculated using deterministic business rules.

The LLM must not arbitrarily generate the numeric score.

Example:

```text
GST inactive                         +30
Major identity conflict              +25
Entity confidence below threshold    +20
Major address discrepancy            +10
Business activity mismatch           +10
Very recent registration             +5
```

Example result:

```text
Risk Score: 65/100
Risk Level: High
```

Suggested categories:

| Score | Classification |
|---|---|
| 0–30 | Low |
| 31–60 | Moderate |
| 61–80 | High |
| 81–100 | Very High |

Thresholds and weights must remain configurable.

---

# 13.8 Report Agent

### Responsibility

Generate the final user-facing Business Risk Report using:

- resolved entity;
- verified evidence;
- risk signals;
- risk score;
- positive signals;
- unresolved information.

The Report Agent must not perform new research.

---

# 13.9 QA Agent

### Responsibility

Perform final quality and evidence validation.

It must verify:

- every factual claim has supporting evidence;
- sources belong to the correct entity;
- official sources have appropriate priority;
- unresolved information is clearly marked;
- the report does not describe risk as proven fraud;
- risk score matches deterministic scoring rules;
- no unsupported conclusion has been introduced.

If QA fails:

```text
QA Failure
   ↓
Planner
   ↓
Additional Research
   ↓
Analysis
   ↓
Report Regeneration
```

---

# 14. Evidence Store

Every extracted fact must be stored as an independent evidence record.

Example:

```json
{
  "entity_id": "ENTITY-001",
  "field": "gst_status",
  "value": "Active",
  "source_name": "GST Portal",
  "source_type": "government",
  "source_url": "...",
  "retrieved_at": "2026-08-24T14:00:00Z",
  "confidence": 1.0
}
```

Required evidence attributes:

- Entity ID
- Field
- Extracted value
- Source name
- Source URL
- Source type
- Retrieval date/time
- Confidence
- Agent that extracted the evidence

Optional:

- screenshot
- supporting text
- page title

---

# 15. Investigation State

Each investigation should maintain persistent structured state.

Example:

```json
{
  "investigation_id": "",
  "user_input": {},
  "candidate_entities": [],
  "resolved_entity": {},
  "known_identifiers": {},
  "research_tasks": [],
  "completed_tasks": [],
  "evidence": [],
  "conflicts": [],
  "risk_signals": [],
  "positive_signals": [],
  "unresolved_questions": [],
  "entity_confidence": 0,
  "risk_score": 0,
  "status": ""
}
```

The graph should use this shared state between agents.

---

# 16. Required Risk Report

The final report should contain:

## Business Identity

```text
Legal Name:
Trade Name:
GSTIN:
CIN:
EPFO:
Website:
Registered State:
```

## Entity Confidence

Example:

```text
Entity Match Confidence: 94%
```

## Overall Business Risk

Example:

```text
Risk Score: 58/100
Risk Level: Moderate
```

## Risk Categories

Example:

```text
Identity Risk       15/100
Registration Risk   20/100
Compliance Risk     55/100
Consistency Risk    68/100
Operational Risk    42/100
```

## Major Risk Signals

Example:

```text
HIGH
GST and MCA addresses differ significantly.

MEDIUM
Website establishment claim predates current
legal entity incorporation by 12 years.

MEDIUM
Business activity information differs between
GST and MCA records.
```

## Positive Signals

Example:

```text
GST registration appears active.

MCA entity was successfully identified.

Company name strongly matches across sources.
```

## Missing/Unverified Information

Example:

```text
EPFO record could not be conclusively verified.
```

## Recommendation

Example:

```text
Additional verification recommended before
providing substantial credit.
```

## Evidence Sources

Each significant finding must reference its supporting source.

---

# 17. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Accept incomplete business information | Must Have |
| FR-02 | Extract structured information from user input | Must Have |
| FR-03 | Discover possible legal entities | Must Have |
| FR-04 | Perform browser-based internet research | Must Have |
| FR-05 | Research GST information | Must Have |
| FR-06 | Research MCA information | Must Have |
| FR-07 | Research EPFO information when available | Should Have |
| FR-08 | Use third-party sources as fallback | Must Have |
| FR-09 | Store evidence with source information | Must Have |
| FR-10 | Perform entity resolution | Must Have |
| FR-11 | Calculate entity-confidence score | Must Have |
| FR-12 | Detect data conflicts | Must Have |
| FR-13 | Identify business risk signals | Must Have |
| FR-14 | Calculate deterministic risk score | Must Have |
| FR-15 | Generate evidence-backed report | Must Have |
| FR-16 | Perform final QA validation | Must Have |
| FR-17 | Re-plan automatically when information is insufficient | Should Have |
| FR-18 | Support human intervention for CAPTCHA/OTP | Should Have |
| FR-19 | Save investigation history | Should Have |
| FR-20 | Display research progress | Should Have |

---

# 18. Non-Functional Requirements

## Reliability

The system must clearly distinguish:

- verified information;
- probable information;
- conflicting information;
- unavailable information.

## Explainability

Every important risk must explain:

```text
What was detected?
Why is it relevant?
Which sources support it?
```

## Auditability

Research results must be reproducible from stored evidence.

## Security

The system must not expose:

- authentication credentials;
- user private information;
- restricted government data.

## Performance

Initial MVP target:

**Complete a standard investigation within a practical interactive session**, depending on website response times and access restrictions.

The system should expose progress rather than appearing inactive during browser research.

## Extensibility

Additional sources should be addable later without redesigning the complete workflow.

Examples:

- UDYAM
- FSSAI
- court/public litigation information
- director networks
- financial statements
- sanctions/watchlists
- business credit information

---

# 19. Business Rules

### BR-01

A company must never be labelled fraudulent solely based on public-data inconsistencies.

### BR-02

Use terminology such as:

- Risk signal
- Inconsistency
- Anomaly
- Verification required
- Unverified
- Higher-risk pattern

Avoid:

- Fraudster
- Fake company
- Scam company

unless authoritative evidence explicitly establishes such a status.

### BR-03

Official government evidence receives greater reliability weight than generic third-party information.

### BR-04

Multiple independent sources should increase evidence confidence.

### BR-05

Conflicting sources must be preserved rather than silently selecting one value.

### BR-06

Entity resolution must occur before final risk analysis.

### BR-07

The report must clearly distinguish between:

**Current legal entity age**

and

**claimed business/brand operating history.**

### BR-08

Missing data alone should not automatically create a high-risk score unless the missing data represents an expected mandatory condition.

---

# 20. MVP Scope

The first version should include only:

### Sources

- GST
- MCA
- EPFO
- Official company website
- Reputable third-party business sources

### Agents

- Intake
- Discovery
- Planner
- Browser Research
- Entity Resolution
- Risk Analysis
- Report
- QA

### Core checks

1. Legal-name consistency
2. GST status
3. Company identity verification
4. Registration/incorporation dates
5. Address consistency
6. Business activity consistency
7. EPFO establishment presence where applicable
8. Website/legal-record consistency
9. Entity confidence
10. General public-footprint anomalies

---

# 21. Out of Scope for MVP

The following should not be included initially:

- Invoice-level GST fraud detection
- GSTR-1/GSTR-2B/GSTR-3B reconciliation
- E-way bill analysis
- Bank transaction analysis
- Credit bureau data
- Full financial-statement analysis
- Director network graph analysis
- Court-case intelligence
- Sanctions screening
- Automated CAPTCHA bypass
- OTP bypass
- Paid government-document retrieval
- Automatic fraud declaration
- Complex predictive fraud ML
- Real-time continuous monitoring

These can be introduced in later phases.

---

# 22. User Experience

## Input Screen

```text
Business Name
[________________________]

GSTIN, CIN or other identifier
[________________________]

Website
[________________________]

Location
[________________________]

Additional information
[________________________]

[ Start Investigation ]
```

Only one useful piece of information should be required to begin research.

---

# 23. Research Progress Experience

While agents work, the user should see progress.

Example:

```text
Investigating ABC Foods...

✓ Input analysed
✓ Potential entity discovered
✓ GST record located
✓ MCA record located
○ EPFO research in progress
✓ Official website identified
✓ Entity resolution completed
✓ Risk analysis completed
✓ Report verified
```

The UI should not expose internal chain-of-thought reasoning.

---

# 24. Example Final Output

## ABC FOODS PRIVATE LIMITED

**Entity Confidence: 94%**

**Business Risk Score: 58/100 — MODERATE**

### Verified Identity

```text
GST Status          Active
MCA Entity          Found
EPFO Establishment  Found
State               Uttar Pradesh
Website             abcfoods.in
```

### Major Findings

**Medium Risk — Address Inconsistency**

GST and MCA records identify Noida, while the company website identifies its primary business location in Delhi.

**Medium Risk — Establishment Date Difference**

The current company was incorporated in 2022, while the website claims operations since 2008.

This does not establish wrongdoing but should be verified to determine whether the claim relates to a predecessor business.

**Low Risk — Business Activity Difference**

GST and MCA activity descriptions differ moderately but remain within broadly related sectors.

### Positive Signals

- GST registration appears active.
- MCA legal entity identified.
- Legal name strongly matches across sources.
- EPFO establishment found.

### Recommendation

**Standard due diligence with verification of historical business claims is recommended before extending significant credit.**

---

# 25. Success Metrics

Initial product success should be measured using:

### Research Completion Rate

Percentage of investigations where the system successfully finds enough information to create a report.

### Entity Resolution Accuracy

Percentage of tested cases where the correct GST/MCA/EPFO records are linked.

### Evidence Coverage

Percentage of final factual statements supported by stored evidence.

Target:

**100% for material factual claims.**

### False Entity Merge Rate

Percentage of investigations where records belonging to different businesses are incorrectly combined.

This should be treated as a critical quality metric.

### Investigation Time

Average time required to create a usable due-diligence report compared with manual research.

### User Verification Rate

Percentage of reports where users consider the result useful for making a verification decision.

---

# 26. Recommended Technical Architecture

```text
Frontend
    ↓
Backend API
    ↓
Graph Orchestration Layer
    ↓
┌──────────────────────────────┐
│ Multi-Agent Layer            │
│                              │
│ Intake                       │
│ Discovery                    │
│ Planner                      │
│ Browser Research             │
│ Entity Resolution            │
│ Risk Analysis                │
│ Report                       │
│ QA                           │
└──────────────────────────────┘
    ↓
Browser Automation
    ↓
Public Internet Sources
    ↓
Evidence Store
    ↓
Risk Engine
    ↓
Report
```

Suggested components:

```text
Backend:
Python / FastAPI

Agent Orchestration:
LangGraph-style graph workflow

Browser Automation:
Browser Use

Database:
PostgreSQL

Optional Vector Search:
pgvector

Risk Engine:
Python deterministic rules

LLM:
Planning
Entity resolution assistance
Conflict analysis
Report generation
QA
```

---

# 27. Graph State Transitions

```text
START
  ↓
INTAKE
  ↓
DISCOVERY
  ↓
PLANNER
  ↓
RESEARCH
  ↓
ENTITY RESOLUTION
  │
  ├── Confidence insufficient
  │        ↓
  │     PLANNER
  │        ↓
  │     RESEARCH
  │
  └── Confidence sufficient
           ↓
       RISK ANALYSIS
           ↓
       RISK ENGINE
           ↓
         REPORT
           ↓
           QA
        /      \
      FAIL     PASS
       ↓         ↓
    PLANNER     END
```

---

# 28. Acceptance Criteria

The MVP will be considered successful when:

1. A user can start an investigation using incomplete business information.
2. The system can discover potential legal entities using browser research.
3. GST and MCA records can be researched where publicly accessible.
4. EPFO records can be researched when identifiable.
5. Every extracted fact can be traced to its source.
6. The system can distinguish multiple similarly named businesses.
7. Entity-resolution confidence is calculated before risk analysis.
8. At least the defined MVP risk checks are implemented.
9. Risk scores are calculated through deterministic rules.
10. The Report Agent cannot introduce unsupported factual claims.
11. QA verifies evidence before the report is returned.
12. CAPTCHA/OTP restrictions are never bypassed.
13. The report clearly distinguishes risk indicators from proven fraud.
14. The system can return a useful report even when some sources are unavailable.

---

# 29. Future Scope

After validating the MVP, the platform can expand into:

### Phase 2

- UDYAM verification
- FSSAI and industry-specific licences
- Better business-history analysis
- PDF report export
- Saved investigations
- Comparison between multiple vendors

### Phase 3

- Director/company relationship graph
- Shared-address analysis
- Related-company discovery
- Neo4j-based entity networks
- Advanced anomaly detection

### Phase 4

- Invoice verification
- User-authorised GST return analysis
- Financial risk intelligence
- Continuous business monitoring
- Alerts when company information changes

---

# 30. Final Product Definition

**BizRisk AI Agent is an agentic business due-diligence platform that researches a legal entity across public government and web sources, resolves its identity, compares information across sources, detects inconsistencies and risk signals, and produces an evidence-backed Business Risk Report.**

The initial product should focus on:

> **Discover → Verify → Resolve → Compare → Score → Report**

rather than attempting to predict or declare fraud.