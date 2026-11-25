# Submission package

What goes into the hackathon form, and the script for the 3-minute video. The checklist itself is in [`RULES.md`](RULES.md) and [`PLAN.md`](PLAN.md). The one value left to fill is the final total spent, ⟨$Z⟩: read it from the ledger on Dec 1, after the video's live calls (it was $3.66 on Nov 25).

## Project description (for the form)

**Name:** ForgeEmber

**Tagline:** Screens every email, invoice, receipt photo and voicemail a small business receives, for a fraction of a cent each. Ember proposes; code decides.

**Short description (about 60 words):**

> ForgeEmber screens every email, invoice, receipt photo and voicemail a small business receives with Muse Ember, for a fraction of a cent each. Ember proposes and code decides: every claim quotes its source, code checks quotes, totals, IBANs and the accounts on file, and payments or bank-detail changes are always held for a person. 6,458 emails screened for $1.75; 998 real receipt photos read with 98.7% of totals correct.

**Long description (about 250 words):**

> **The problem.** Small businesses lose money to invoice fraud, "our bank details have changed" emails and fake calls from the boss. They have no security team, and checking every item by hand is too slow. A frontier model on every item has been too expensive.
>
> **What ForgeEmber does.** Every inbound item is screened end to end: emails, PDF invoices, scanned and photographed documents, and voicemails. Code checks the sender first (lookalike domains, executive impersonation, diverted replies, prompt injection, file-type tricks). Muse Ember triages at minimal reasoning effort and calls specialists only when needed: a document specialist reads invoices and receipts straight from PDFs and photos, a voicemail specialist listens to recordings, and an identity specialist calls a read-only lookup tool to check who an email claims to be from. Every claim quotes its source, and code verifies the quotes, the arithmetic, the IBAN checksum and the account on file. Only `policy.py` decides. Payments and bank-detail changes are always held for a named person in an approval console.
>
> **Results.** 6,458 emails (3,458 real phishing emails from the Nazario corpus) for $1.75 in model calls, $0.27 per 1,000: 3,340 of the 3,458 phishing emails (96.6%) went to review, and 9 of 3,000 synthetic benign emails (from 9 templates) were false alarms. 998 real receipt photos (CORD) in, expense records out: 98.7% of totals read correctly against human labels, for $0.76. All 18 labeled fixtures got the right risk and held actions, including fraud by voicemail and a fake vendor caught through a tool call.
>
> **Budget.** Going over $10 disqualifies us, so the $9.00 cap is enforced in code: each call reserves its worst-case cost first and settles at the API's reported usage. Every call is one line in the committed trace, reconciled against Meta's Usage page. Total spent: ⟨$Z⟩.
>
> **Built with:** Muse Ember for every agent; Muse Code (Spark) for code review.

**Meta tooling used** (25% of the score, so name each one): Responses API · reasoning-effort levels (minimal for triage, low for specialists) · strict structured output (`text.format` JSON schema) · native PDF, image and audio input · prompt caching (45.6% of input tokens cached, $0.63 saved) · the input-token count endpoint (bounds audio calls before they're made) · function calling (a read-only `lookup_vendor` tool; code checks every answer) · multi-agent on Ember (coordinator, document, voicemail, identity, risk and skeptic agents) · Muse Code.

## Demo video: 3:00, recorded in segments

Show only our fictional fixtures and aggregate numbers. Public-corpus items (Nazario emails, CORD photos) never appear on screen ([`DATA.md`](DATA.md)). The voiceover is about 440 words, which fits 3 minutes at a calm pace. Record each segment separately, then join them.

**Before recording**
- Set the key in the terminal before you start recording (`read -s MODEL_API_KEY && export MODEL_API_KEY`). Never on camera, and never open `.env` or print the environment.
- Make the terminal font big (Ctrl+= in VS Code) and close other tabs.
- Live calls in the video use the `demo` stage ($0.73 left; each screened item costs about $0.001).
- Start the console in a second terminal: `uv run forgeember console` (http://127.0.0.1:8000).

| # | Time | On screen | Commands / actions |
|---|---|---|---|
| 1 | 0:00–0:20 | The em-001 email: sender, subject, "we have moved our banking" | Replay site → Decision trails → em-001, top of the page |
| 2 | 0:20–0:40 | "How it works" steps, then the architecture diagram | Replay site index, then README → Architecture |
| 3 | 0:40–1:15 | Live screen of em-001, then its hold in the console and its decision trail | `uv run forgeember screen fixtures/emails/em-001-bank-change-lookalike.eml --stage demo --live` → console → open the item → scroll 2 → 5 → type your name → **Reject** |
| 4 | 1:15–1:55 | Voicemail vm-002: play ~5 seconds, "what Ember heard", account check, held change. Then em-008: the `lookup_vendor` call and the domain check | Console or replay site → vm-002, then em-008 |
| 5 | 1:55–2:25 | Scale: a short batch run, then the results cards | `uv run forgeember batch data/benign-v1 data/nazario/eml --stage demo --live --limit 30 --workers 8 --no-resume`, then replay site → Results |
| 6 | 2:25–2:50 | Budget evidence: README budget block, `traces/calls.jsonl`, Meta's Usage page | README → Budget & Trace; the trace file; the Usage page (filtered to Muse Ember) |
| 7 | 2:50–3:00 | Repo and replay-site links | Title card or the README header |

**Voiceover**

1. *(0:00)* "This email looks like it's from our stationery supplier: 'We have moved our banking to a new provider. Please update your records.' Small businesses lose real money to exactly this. ForgeEmber screens every email, invoice, receipt photo and voicemail a business receives, with Muse Ember, for a fraction of a cent each."

2. *(0:20)* "The rule is simple: Ember proposes, code decides. Code checks the sender first. Ember triages at minimal reasoning effort and calls specialists only when needed. Every claim must quote its source, and code checks the quotes, the totals, the IBAN checksum and the account on file. Only the policy code can approve anything, and payments and bank changes always wait for a person."

3. *(0:40)* "Let's screen it live. Code spots that the domain imitates our real supplier. Ember flags the bank change and quotes the exact sentence, and code confirms the quote is really in the email. Policy says high risk and holds the bank change. In the console I can see the whole trail and what each step cost, under a fifth of a cent in total, and I reject it under my own name."

4. *(1:15)* "It's not just text. Here a 'supplier' leaves a voicemail reading out new bank details. Ember listens to the audio directly and writes down what it heard. Code compares the account with the one we have on file: it doesn't match, so the change is held. A voice can never confirm a payment. And when an email claims to be our supplier from an address we've never seen, Ember calls a lookup tool against our records, and code checks the answer: right name, wrong domain, held."

5. *(1:55)* "And it's cheap enough to run on everything. We screened 6,458 emails, including 3,458 real phishing emails from the Nazario corpus, for $1.75: 27 cents per thousand. 96.6% of the phishing went to review, with 9 false alarms on 3,000 synthetic benign emails. Then 998 real receipt photos, image in, expense record out, with no OCR library: 98.7% of totals read correctly against human labels, for 76 cents."

6. *(2:25)* "Going over ten dollars would disqualify us, and Meta's alert only sends an email, so the cap lives in our code. Every call reserves its worst case first and settles at real usage. Every call is one line in the trace, and we reconcile it against Meta's own Usage page. Total spent: ⟨$Z⟩ of ten dollars."

7. *(2:50)* "Ember makes checking every item cheap enough to be the default. It's all open source, with a keyless replay of every decision."

## Proof of usage (Dec 1)

1. **Usage page screenshot:** Meta's Usage page, Nov 23–Dec 2, filtered to Muse Ember. Save it as `docs/assets/meta-usage.png`.
2. **Reconcile** against that total: `uv run forgeember budget reconcile --meta-usd <the total on that page>`. The ledger must come in at or above Meta's number.
3. **Muse Code trajectory:** in Muse Code, run `/export trajectory` and save it as `traces/muse-code-trajectory.json`. Then scan it **before** committing it: `uv run python scripts/check_secrets.py traces/muse-code-trajectory.json`. Real secrets get removed, and any exposed key gets rotated. Lines that only describe a header format are fine.
4. **Trace and summary:** `uv run python scripts/export_trace.py`, then `uv run python scripts/render_budget_readme.py`.
5. **Final scan:** `uv run python scripts/check_secrets.py traces/ README.md`.
