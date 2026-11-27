# ADR-0001: ForgeEmber architecture

- **Status:** Accepted (Phase 0, 2026-11-23). Agents, verifier and policy are designed here and built in Phase 1.
- **Deciders:** Billy Davis (operator, approver). Drafted with Claude.

## Context

A small business receives emails, PDF/scanned invoices, photos and voicemails. A small share of them are fraud: bank-detail-change scams, fake invoices, executive impersonation. We want to screen *every* item, so the per-item cost has to be a fraction of a cent. We also need to act safely: a model must never be able to move money or send mail on its own.

Constraints:
- A $10 credit grant, where going over disqualifies us. Meta offers no hard spend cap (only an email alert).
- The build must use Muse Ember. The window is 9 days.
- Judging rewards real impact, deep use of Meta tooling, technical depth and the demo.

## Decision

A **propose / verify / decide** pipeline. Muse Ember agents produce *proposals with cited evidence*. Deterministic code verifies the evidence, and a single policy module decides. Every model call goes through one budgeted client that reserves its worst-case cost before sending.

```
INPUTS           ENVELOPE            EMBER AGENTS (muse-ember-1.0)        CODE (the only authority)         OUTPUTS
.eml email  -+   id, sha256,   ->    Coordinator: triage, minimal   ->    Sender checks: lookalike    ->    Held for a person (T2):
PDF invoice  |   parts, sniffed      Document: invoice and receipt        domain, impersonation,            payments, bank changes
photo / scan |   mime, text layer      fields from PDFs and photos        Reply-To, injection, file type    Filed and tagged (T0)
voicemail    |   (code reads it      Voicemail: what the caller said      Verify: quotes, totals, IBAN,     Operator notified (T1)
screen rec. -+   first)              Video: what the recording shows      account on file, lookup ids       Receipt per item
                                     Identity: lookup_vendor tool calls   Policy: risk and tiers            Ledger + call trace
                                     Risk + skeptic: on escalation        Budget: reserve, cap, settle      Next step per hold
                                     Handoff: drafts from code's facts
```

## Components

| Module | Role | Authority |
|---|---|---|
| `envelope.py` | Normalizes .eml, PDF, image, audio and video into an `Envelope` (id, sha256, source, mime, kind, received_at, raw bytes). Flags what a reader can't see: white or microscopic PDF text, a text layer drawn invisibly under a page image, and invisible characters (zero-width, bidi controls) | none |
| `ember/client.py` | **The only network module.** Responses API over httpx. Validate → reserve → send → settle. | spends money only through the ledger |
| `ember/prompts/` | Versioned prompts. Version and sha256 are recorded per call. | none |
| `agents/coordinator.py` | Decides which specialists run, and at what reasoning effort | proposes only |
| `agents/specialists.py` | identity, document and payment-change specialists. Each returns evidence-cited findings as strict JSON. | proposes only |
| `agents/skeptic.py` | Argues the opposite conclusion on escalated cases | proposes only |
| `tools.py` | Read-only lookups Ember may call (function tools): `lookup_vendor`. Returns names, domains and usual amounts, never bank details. Arguments are validated like any untrusted input. | read-only |
| `verify.py` | Schema validity, cited quotes exist verbatim in the source, invoice arithmetic, baseline matches | gatekeeper for evidence |
| `baseline.py` | Per-vendor and per-sender baselines: known bank accounts, usual amounts, usual senders. Detects bank changes deterministically. Lookalikes: a domain within 2 letters of a vendor's, the same once look-alike letters from other scripts are read as Latin (punycode decoded first), or containing the vendor's whole domain name; a vendor name spelled with look-alike letters, in an extracted field or in the document's own text | facts |
| `policy.py` | Applies tiers T0/T1/T2 to *verified* findings. **The sole decision authority.** | decides |
| `actions.py` | Executes T0, sends T1 notifications, enqueues T2 holds. Screening an item again refreshes its pending hold but never removes caution: risk and the need for a recorded check only rise, and a next step that asks for a check stays until a person decides | executes policy output only |
| `cases.py` | Links a later message to the one open hold it is about: same vendor, and the hold's invoice number mentioned (verified in the document's own text). No candidate or two candidates: no link. A link can only raise the hold's risk, and a claimed change (new bank details or a new amount) makes it at least MEDIUM. A new amount gets its own next step (both amounts, check the revised charge against the order, approve only one of the holds for that invoice), never the bank-call step Also finds duplicates in the hold history: the same invoice already approved, the same number and amount already waiting, or the same amount under a new number within 14 days (a MEDIUM fact for policy) | proposes caution only |
| `attackgen.py`, `attacks.py` | The attack suite: 10 fictional attacks frozen before any live run, each naming the defense that must fire, and their scoring (`forgeember attacks`) | test only |
| `ordinarygen.py`, `ordinary.py` | The ordinary-mail set: 25 legitimate messages labeled routine or check before any run, scored for interruptions for nothing and missed checks (`forgeember ordinary`) | test only |
| `handoff.py` | What the owner does next with each hold: a next-step line from verified facts naming the vendor's contact details on file, a drafted verification message (written from code's facts only, checked by code, never sent), and the rule that approving a hold marked "check first" needs a written record of how it was checked | advises; never executes |
| `budget/` | Price table, reserve/settle ledger (SQLite WAL + JSONL trace), reports | can refuse any call |
| `batch/runner.py` | Resumable, rate-limited, budget-capped scale runs | bounded by the ledger |
| `console/` | FastAPI + HTMX approval queue for T2 holds | Billy approves here |

## Authority rules

1. Ember output is **data, never instructions.** Text inside an email or PDF that tells the model to do something is evidence of manipulation, not a command.
2. A finding reaches policy only after `verify.py` passes it. Unverifiable claims are dropped or downgraded, never trusted.
3. `policy.decide` is the only function that maps a finding to an action tier. Unknown action kinds count as **T2**.
4. **T2 actions are never executed by the system.** They're held for Billy in the console.
5. `ember/client.py` is the only importer of network libraries. This is enforced by `tests/test_guardrails.py`.
6. The ledger can refuse any call. Agents must treat `BudgetExceeded` as a normal outcome: degrade (lower effort, skip the skeptic) or stop.

## Data flow, per item

1. Ingest raw bytes, then build an `Envelope` (sha256 for dedup and audit).
2. Code computes baselines and diffs first: known vendor? bank details changed? amount outside the usual range?
3. The coordinator (Ember, `minimal`/`low` effort) classifies the item and picks specialists, given the envelope plus code's baseline diff.
4. Specialists (Ember) extract fields and findings with exact-quote evidence, using structured output. They may call read-only tools.
5. `verify.py` checks quotes against the source text, arithmetic and baseline consistency.
6. If the case is escalated (risk signals present), the skeptic argues the opposite. Gate C decides whether this runs only on escalations.
7. `policy.decide` sets the tier, then `actions.py` routes T0, notifies T1 and holds T2.
8. Every model call is one ledger row and one trace line. Every decision is one receipt.

## Agent roles and the tools each may call

| Agent | Reasoning effort | May call | Output |
|---|---|---|---|
| Coordinator | `minimal` / `low` | none (it plans; code dispatches) | route plan + chosen specialists + effort |
| Identity specialist | `low`, emails asking for money or a bank change from a sender code doesn't know (not in bulk mode) | `lookup_vendor` | who the item claims to be from (quoted) and the vendor id a lookup returned. Code checks the quote, that the id came from a lookup in this conversation, and whether the sender's domain is on file for that vendor; a mismatch is HIGH |
| Document specialist | `low` | `lookup_vendor` | invoice/receipt fields with page/quote evidence |
| Payment-change specialist | `medium` on escalation | `lookup_vendor`, `search_notes` | manipulation signals (urgency, secrecy, authority) with quotes |
| Voicemail specialist | `low`, every audio part | none | transcript, caller, request, money asked for, account read out. Code compares the account with the one on file; a spoken money or bank request stays unconfirmed (at least MEDIUM, always held) |
| Video specialist | `low`, every video part (mp4 inline; tokens counted first) | none | what the recording shows: description, on-screen text, whose payment details change, the details entered. Code compares any account shown with the one on file; a recording is a claim, so a request it shows is at least MEDIUM and always held |
| Handoff drafter | `minimal`, at most one per item, only when a vendor with an address on file should be asked to confirm | none | a short verification message. It never sees the message under review (only code's facts), so nothing in that message can steer it; code rejects drafts with links, addresses or long numbers |
| Skeptic | `medium`/`high`, escalations only | `search_notes` | counter-argument + what evidence would change it |
| Baseline builder (1M context) | `low` | none | vendor profile from a whole email history in one call |

Proposed actions (for example `propose_hold_payment`) are function calls that `policy.py` intercepts. They're never executed directly. `tool_choice` on Meta's API supports only `"auto"`, so code must handle both a tool call and a plain answer.

## Phase 1 implementation notes (2026-11-23)

- **Call plan per item:** code findings first (no model call). Then triage on every item at `minimal` effort; the document specialist only when there's a document or an invoice; the voicemail specialist on every recording; the identity specialist (with tool calls) when an unknown sender asks for money; the risk specialist only on escalation (`medium` effort if code already found a red flag); the skeptic on escalations only. A routine text email costs one call.
- **Facts go to Ember, not tools (for now):** code's baseline findings go into the prompt as "FACTS FROM OUR RECORDS". The read-only lookup tools remain planned for the identity specialist if evaluation shows they help.
- **Code-level injection detection:** text addressed to automated reviewers is flagged HIGH by regex. No model output can lower it.
- **Media bounds:** PDF, image and audio calls are bounded by Meta's token-count endpoint, plus a margin, before reserving. The count call itself is reserved and settled as billed input until the Usage page proves counts free; if the count endpoint fails, screening falls back to a static bound and says so.
- **Incomplete screening** (budget stop, invalid output, API error) is decided conservatively: at least MEDIUM risk, and money actions stay held.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Let the model decide and act ("agentic autopilot") | One prompt injection in a vendor email could trigger a payment. Unacceptable for the target user. |
| OCR library + classic classifier | Loses the multimodal story and the language-manipulation signal. Ember reads PDFs, images and audio natively. |
| A larger model (Muse Spark/Wonder) for everything | 8–40× the cost per token, so it can't screen every item on $9. Ember is the required model anyway. |
| Server-side conversation state (`previous_response_id`) | `usage` then reports only the new turn, which makes cost accounting less exact. We stay stateless with `store: false`. |
| Trusting Meta's spend alert | It only sends an email and doesn't stop usage. The cap has to be in our code. |
| A vendor SDK (OpenAI client) | Not needed. Raw httpx keeps the one network boundary small and auditable. |
| A Streamlit or dashboard-first product | Excluded by the hackathon's "avoid" list, and the value is the pipeline. |

## Consequences

- **Positive:** safety comes by construction (no model can execute T2). Spend is provably bounded. Every number in the README is reproducible from the trace. Per-item cost is low, because bulk calls run at low effort with cached shared prefixes.
- **Negative:** reserving worst-case costs makes us conservative. Large `max_output_tokens` values can block calls while the stage still has real headroom. Mitigations: tight output budgets per role, and settlement frees unused reservation immediately.
- **Risks:** Ember's audio quality is unverified until the first live voicemail run. A voice proves nothing about identity, so code never lets a recording confirm a payment or a bank change. The token cost of media must be measured. Prompt injection inside documents can still mislead *proposals*, which is why verify and policy hold authority.
