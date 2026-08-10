The **best real-life business use case is a Property Insurance Claims Intelligence Agent**.

It fits the assignment almost perfectly because every input in your screenshot naturally exists inside a real insurance company, and the example question in the brief is already insurance-oriented:

> “Why did March claims spike — and what does our policy say about water damage?”

So rather than inventing an artificial scenario, you can turn the assignment into a realistic internal AI system for an insurance company.

## Recommended use case: Property Insurance Claims Intelligence Agent

Imagine a mid-sized insurance company offering:

* Home insurance
* Apartment/rental insurance
* Small-business property insurance
* Fire/flood/water-damage coverage

Employees currently have information scattered across policy documents, operational databases, Excel sheets, and scanned claim paperwork.

Your agent becomes the company's **internal claims and policy intelligence assistant**.

A claims manager, analyst, underwriter, or customer-support employee can ask a natural-language question such as:

> **“Why did water-damage claims increase in March 2026, which regions were responsible, and are burst pipes covered under our current HomeSecure policy?”**

The system searches **all four sources**, reasons across them, and returns one evidence-backed answer with citations.

That is an excellent demonstration of **multi-source agentic AI** rather than just another RAG chatbot.

---

# What the business actually looks like

For the project, you could invent a synthetic insurer such as:

### NorthStar Insurance

A property insurance company operating across several cities.

It offers products such as:

* HomeSecure Basic
* HomeSecure Plus
* Landlord Protect
* SME Property Shield

Its employees have four major information sources.

### 1. Policy documents — unstructured text

These are Word/PDF/text policy documents containing things like:

* Coverage rules
* Exclusions
* Deductibles
* Maximum payouts
* Water-damage clauses
* Flood exclusions
* Fire coverage
* Theft coverage
* Claim deadlines
* Eligibility conditions

Example document:

`HomeSecure_Plus_2026.pdf`

Could contain:

> Accidental escape of water from internal plumbing systems is covered subject to a PKR 25,000 deductible.

But perhaps:

> Damage caused by gradual leakage or poor maintenance is excluded.

This becomes your **RAG knowledge base**.

---

### 2. SQL transactions — structured + live

This is your operational insurance database.

For example:

```text
claims
------
claim_id
policy_id
customer_id
claim_type
incident_date
claim_date
city
region
claim_amount
approved_amount
claim_status
cause
property_type
adjuster_id
```

Another table:

```text
policies
--------
policy_id
customer_id
policy_type
plan
start_date
end_date
premium
coverage_limit
city
status
```

And:

```text
payments
--------
payment_id
claim_id
payment_date
payment_amount
payment_status
```

This lets your agent answer quantitative questions:

> How many claims occurred in March?

> Which category increased the most?

> How much was paid for water-damage claims?

> Which city had the largest increase?

> What percentage were rejected?

This part is handled through an **NL → SQL agent/tool**.

---

# 3. Spreadsheets — semi-structured business data

Insurance companies still use a lot of spreadsheets.

You can create things such as:

### `Regional_Renewals_2026.xlsx`

```text
Region | Policy Type | Jan | Feb | Mar | Renewal Rate
Lahore | HomeSecure  | 520 | 505 | 470 | 89%
Karachi| HomeSecure  | 690 | 680 | 642 | 91%
Islamabad | HomeSecure | 260 | 254 | 249 | 95%
```

### `Claims_Targets_Q1.xlsx`

```text
Region
Expected Claims
Actual Claims
Budget
Average Processing Time
Target Processing Time
```

### `Risk_Categories.xlsx`

```text
Postal Zone
Flood Risk
Building Age Category
Risk Score
```

This is useful because spreadsheets frequently contain information that **isn't yet stored in the company's production database**.

Your agent therefore needs a spreadsheet retrieval/analysis tool.

---

# 4. Scanned PDFs — images of text

This is where the project becomes especially interesting.

Insurance companies receive documents such as:

* Handwritten claim forms
* Adjuster inspection reports
* Plumber reports
* Contractor estimates
* Damage assessment reports
* Repair invoices
* Police reports
* Fire department reports
* Signed customer statements

Example:

`CLAIM_CLM10281_INSPECTION.pdf`

Scanned document:

> Inspection identified a burst kitchen supply pipe. No evidence of long-term leakage was observed.

OCR extracts that information.

Your agent can combine it with the policy:

> Policy covers sudden accidental water escape.

And the SQL record:

```text
Claim amount: PKR 420,000
Claim type: Water Damage
Status: Under Review
```

Then conclude:

> Based on Section 4.2 of HomeSecure Plus and the adjuster's finding of a sudden pipe burst, the incident appears potentially covered, subject to the PKR 25,000 deductible and final claims review.

That is a very convincing real-world agent.

---

# The complete business problem

Your project pitch could be:

### Problem

Claims analysts currently have to search across multiple disconnected systems to answer a single business question:

* policy PDFs
* claims databases
* Excel reports
* scanned claim paperwork

This causes:

* slow investigations
* inconsistent answers
* missed evidence
* difficulty explaining decisions
* repetitive manual SQL queries
* poor traceability

### Solution

Build an **AI Claims Intelligence Agent** capable of querying heterogeneous enterprise data sources and producing a single grounded, cited answer.

The important part is:

> **The system doesn't merely retrieve documents. It determines which sources are necessary, executes the appropriate tools, reconciles the evidence, and produces an auditable answer.**

That is what makes it an **agentic system**.

---

# Example queries for your demo

You should prepare around 15–30 evaluation questions.

A particularly strong main demo would be:

> **“Why did property claims increase significantly in March 2026, which claim category contributed most to the increase, and does our HomeSecure Plus policy cover the primary cause?”**

The agent might determine:

SQL:

```text
February claims: 742
March claims: 1,094
Increase: +47.4%
```

SQL:

```text
Water damage:
Feb = 124
Mar = 398
Increase = +221%
```

Spreadsheet:

```text
Northern Region maintenance inspection compliance:
Jan: 94%
Feb: 91%
Mar: 72%
```

Scanned reports:

```text
31 of sampled 45 claims mention burst/frozen pipes.
```

Policy RAG:

```text
Section 4.2:
Sudden and accidental escape of water from fixed plumbing is covered.
```

Agent response:

> Claims increased 47.4% between February and March, primarily due to water-damage claims, which increased from 124 to 398. Northern Region accounted for 42% of this increase. Inspection reports frequently attribute the incidents to sudden pipe failures. Under HomeSecure Plus §4.2, sudden accidental escape of water from fixed plumbing is covered, although gradual leakage remains excluded.

And underneath:

```text
Sources
• claims database — Feb/Mar 2026 aggregation
• claims database — water_damage category
• Regional_Risk_March.xlsx — Northern Region
• Inspection Report CLM-1088
• HomeSecure Plus Policy §4.2
```

**That is exactly what the assignment image is trying to get you to build.**

---

# Architecture I recommend

Don't make five independent agents.

For an assignment like this, I would use:

```text
                         USER
                           │
                           ▼
                 ┌───────────────────┐
                 │ Orchestrator Agent│
                 └─────────┬─────────┘
                           │
                  Query understanding
                  + planning / routing
                           │
       ┌───────────────────┼────────────────────┐
       │                   │                    │
       ▼                   ▼                    ▼
 Policy/RAG Tool        SQL Tool          Spreadsheet Tool
       │                   │                    │
       │                   │                    │
 Vector DB            PostgreSQL            Pandas/OpenPyXL
       │
       │
       └───────────────┐
                       │
                       ▼
                    OCR Tool
                       │
                  scanned PDFs
                       │
                       ▼
               Evidence Collector
                       │
                       ▼
              Evidence Reconciliation
                       │
                       ▼
                Answer Synthesis
                       │
                       ▼
             CITED FINAL RESPONSE
```

Conceptually:

```text
Question
   ↓
Planner
   ↓
Which information is required?
   ↓
┌─────────┬────────┬──────────┬─────────┐
│   RAG   │  SQL   │   XLSX   │   OCR   │
└─────────┴────────┴──────────┴─────────┘
                ↓
           Evidence Store
                ↓
          Verification
                ↓
          Final Synthesis
                ↓
       Answer + citations
```

## One orchestrator + tools is better here

I would implement **one intelligent orchestrator agent with specialized tools**, instead of turning every source into a fully autonomous agent.

For example:

```python
tools = [
    search_policy_documents(),
    execute_readonly_sql(),
    analyze_spreadsheet(),
    search_scanned_documents()
]
```

The LLM decides:

```text
Question requires:
✓ SQL
✓ Policy RAG
✓ OCR
✗ Spreadsheet
```

Then executes only what is necessary.

That makes the architecture easier to:

* debug
* evaluate
* trace
* control cost
* explain to your supervisor

---

# One additional layer I strongly recommend

Add an internal **Evidence Object**.

Every tool converts its output into the same structure:

```json
{
  "source_type": "sql",
  "source": "claims_database",
  "evidence": "398 water damage claims occurred in March 2026",
  "reference": "query_023",
  "confidence": 1.0
}
```

Document result:

```json
{
  "source_type": "policy",
  "source": "HomeSecure Plus 2026",
  "evidence": "Sudden escape of water from fixed plumbing is covered.",
  "reference": "Section 4.2",
  "confidence": 0.97
}
```

OCR result:

```json
{
  "source_type": "scanned_report",
  "source": "CLM-1028 inspection report",
  "evidence": "Damage resulted from sudden rupture of kitchen pipe.",
  "reference": "page 2",
  "confidence": 0.91
}
```

Then your final LLM receives **evidence**, rather than arbitrary outputs from four systems.

This gives you much cleaner citations.

---

# Suggested implementation stack

A practical stack could be:

```text
Frontend
Next.js / React
        ↓
Backend
FastAPI
        ↓
Agent
LangGraph
        ↓
LLM Gateway
OpenAI / Gemini / Claude / Groq
        ↓
──────────────────────────────────
Policy RAG → Qdrant / pgvector
SQL        → PostgreSQL
Excel      → Pandas + OpenPyXL
OCR        → PaddleOCR / Docling
──────────────────────────────────
        ↓
LangSmith
Tracing + Evaluation
```

Because **LangSmith is explicitly mentioned in your brief**, LangGraph is particularly natural for the orchestration layer.

---

# Example LangGraph workflow

Your graph could resemble:

```text
START
  ↓
classify_question
  ↓
create_plan
  ↓
┌──────────────────────────────┐
│ Are policy documents needed? │──→ policy_rag
│ Is database needed?          │──→ sql_agent
│ Are spreadsheets needed?     │──→ spreadsheet_agent
│ Are scanned docs needed?     │──→ ocr_retrieval
└──────────────────────────────┘
                ↓
        collect_evidence
                ↓
       evidence_sufficiency
          ↓          ↓
       enough       missing
          ↓            │
      synthesize ←─────┘
          ↓
     verify_citations
          ↓
        FINAL
```

You can even have the **missing** branch loop back and call another tool.

That satisfies the screenshot's:

> `workflow? · loop?`

requirement nicely.

---

# Gateway/model routing

Your assignment also explicitly mentions:

> model choice, cost, fallback

So demonstrate that too.

For example:

```text
Simple query classification
        ↓
cheap/fast model

SQL generation
        ↓
medium model

Cross-source reasoning
        ↓
strong model

Model unavailable?
        ↓
fallback model
```

Conceptually:

```text
Router       → GPT-5 mini / Gemini Flash
Tool calls   → GPT-5 mini
Final answer → stronger reasoning model
Fallback     → alternate provider/model
```

You don't have to spend much money to demonstrate the principle.

---

# Synthetic dataset I would build

Don't generate millions of rows.

A strong university project dataset could be:

### Policy documents

Around:

```text
10–20 policies
```

Each 5–15 pages.

Include:

* HomeSecure Basic
* HomeSecure Plus
* Landlord Protect
* Business Property
* Fire Protection
* Flood Protection
* exclusions
* endorsements

---

### SQL database

Around:

```text
5,000–20,000 synthetic claims
2,000–5,000 customers
3,000–8,000 policies
5,000–15,000 payments
```

Generate transactions across:

```text
Jan–Jun 2026
```

Intentionally create useful trends such as:

```text
March water damage spike
April theft spike
specific region unusually high rejection rate
one policy product with increased claims
```

This gives the agent something meaningful to discover.

---

### Spreadsheets

Maybe 5–10 workbooks:

```text
Regional_Risk_Report.xlsx
Renewals_Q1.xlsx
Claims_Targets.xlsx
Adjuster_Performance.xlsx
Broker_Portfolio.xlsx
Loss_Ratio_Report.xlsx
```

Make some of them intentionally messy:

```text
merged cells
multiple sheets
totals
comments
missing values
inconsistent headings
```

That makes the assignment more realistic.

---

### Scanned PDFs

Approximately:

```text
50–100 documents
```

Types:

```text
claim forms
inspection reports
repair invoices
plumber reports
damage assessments
```

You can generate clean PDFs and convert them into images/scanned PDFs yourself.

No private or real customer data is needed.

---

# Your evaluation dataset

This will matter because the screenshot explicitly says:

> **evals you designed and ran**

Build approximately:

```text
30–50 evaluation questions
```

Split them into categories.

| Question type    | Example                                                         |
| ---------------- | --------------------------------------------------------------- |
| Policy only      | Is gradual water leakage covered?                               |
| SQL only         | How many fire claims occurred in March?                         |
| Spreadsheet only | Which region missed its renewal target?                         |
| OCR only         | What caused damage in CLM-1098?                                 |
| Policy + SQL     | How many March claims fall under water coverage?                |
| SQL + Excel      | Which high-claim region missed inspection targets?              |
| Policy + OCR     | Is CLM-1098 likely covered?                                     |
| All four         | Why did March claims spike and are the major incidents covered? |

The **all-four category** becomes your flagship demo.

---

# Important safety rule for the business use case

Your system shouldn't say:

> **“Claim approved.”**

Instead:

> **“The available evidence appears consistent with coverage under §4.2; final determination remains with the claims team.”**

That makes the system realistically an **employee decision-support agent**, rather than replacing regulated business decisions.

---

# What I would call the project

My favorite is:

## **VeriClaim AI**

**VeriClaim — Evidence-Grounded Insurance Intelligence**

It communicates exactly what makes the project different:

* Verifiable
* Claims
* Evidence
* AI

A presentation title could be:

> **VeriClaim**
> *A Multi-Source Agentic Intelligence System for Insurance Claims*

That's strong enough for an academic project while still sounding like an actual SaaS/business product.

That is a **very defensible real-world interpretation of the exact assignment you were given**. It preserves all four prescribed inputs, naturally requires routing/tool use, supports multi-step reasoning, makes citations meaningful, gives you rich synthetic data to generate, and gives you clear quantitative evaluations rather than building an arbitrary chatbot.
