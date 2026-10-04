# 2-Minute Demo Script — CentrAlign AI Autonomous Company Task Worker

**Before recording:** `python run.py`, open http://localhost:8000, click **Reset finance sandbox**, scroll to the top.

---

**0:00 – Intro (15s)**
"This is a prototype of a CentrAlign AI employee. It doesn't tell you what to do — it does the work against real systems, adapts when they push back, and proves the result. Everything here is a sandbox: a document repository, and an internal finance app with its own SQLite database."

*(Point at the Finance System table at the bottom — empty.)*

**0:15 – Natural-language task (10s)**
"I give it a normal business request." *(Show the textbox.)* "Find the latest invoice from Acme Corp, extract the details, enter it into finance, verify it and tell me when it's done." **Click RUN TASK.**

**0:25 – Understanding & planning (15s)**
"First it restates the goal and success criteria, and builds a plan. The plan on the right is a guide — it's not a script. Every next action is chosen from the current state and the last observation." *(Point at the loop bar: Goal → Understand → Plan lighting up.)*

**0:40 – Searching & reading (15s)**
"It searches the repository, finds two Acme invoices, and picks the newer one — INV-2026-104 from October, not the September one. It opens it and extracts the number, amount ₹48,500 and due date."

**0:55 – Finance submission + intentional failure (15s)**
"Now it submits to the finance system… and the system rejects it: HTTP 400, *amount must be numeric, remove currency symbols*. This isn't a scripted animation — that's the finance API's real response." *(Expand Details on the red event to show the JSON.)*

**1:10 – Observe, adapt, retry (15s)**
"The agent reads the error and hint, decides to normalise the amount to 48500, and retries. The retry succeeds — record #1842 — and is marked RECOVERED. The Adapt stage in the loop turns amber."

**1:25 – Verification (15s)**
"A 201 isn't proof. It reads the record back, and the runtime compares invoice number, company, amount and due date with the source document: four out of four match. Only then is completion allowed — the controller literally refuses `complete` without verified evidence."

**1:40 – Final evidence (15s)**
"Here's the result card: invoice, company, ₹48,500, due 15 Oct 2026, finance record #1842, VERIFIED, with the field-by-field evidence. And here's the row actually sitting in the finance database."

**1:55 – Close (5s, optional)**
"Same controller handles other tasks — for example a ₹75,000 Globex invoice pauses for human approval before anything is written." *(Click the Globex chip + Run if time allows, show Approve/Reject.)*

---

## Likely technical questions

**1. How is this different from a hard-coded workflow?**
The controller never contains the sequence. Each turn it asks the policy for exactly one action given the current `AgentState` (discovered information, last observations, approval and verification status). The order emerges from observations: the amount is only normalised *after* the 400; on a 409 duplicate the agent switches to verifying the existing record; if nothing is found it fails instead of continuing. With an API key, an LLM makes these decisions through the same interface and the same tool registry.

**2. What stops the LLM from doing something dangerous or lying about success?**
Three runtime guarantees that don't depend on the prompt: (a) only registered tools with Pydantic-validated arguments can run – no shell or code execution; (b) the approval guardrail is checked by the controller before `enter_finance_record` executes, so the model can't skip it; (c) `complete` is rejected unless an independent verification of the stored record against the source has passed. Retries are also bounded.

**3. Isn't demo mode just faking it?**
No – demo mode replaces only the *decision-maker*. The search, file read, HTTP calls to the finance app, SQLite writes, the 400 rejection, the retry and the verification all execute for real; the tests assert the record exists in the database. It exists for reproducible demos/CI and as the fallback when the LLM call fails. The README discloses this explicitly.

**4. How would this generalise to other tasks?**
The controller is domain-agnostic: tools are registered with a schema, a description for the model, a hook that records what was learned, and an optional approval check. Invoice-specific logic (extraction, verification fields, the threshold) lives in the tools. A new workflow – say, updating a CRM contact from an email – means registering new tools and a verifier; the loop, event log, approvals and UI are reused. Already today it handles a different vendor, an approval path, duplicates and "not found" without code changes.

**5. What would you change to put this in production?**
Durable execution (task queue + workers with persisted state so approvals can wait days and survive restarts), authenticated users with per-tool permissions, an immutable audit log, secret management for real system credentials, an evaluation harness with fault injection to measure success rate per model, model fallback chains, and browser/computer-use tools for systems without APIs.
