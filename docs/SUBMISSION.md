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
> **What ForgeEmber does.** Every inbound item is screened end to end: emails, PDF invoices, scanned and photographed documents, and voicemails. Code checks the sender first (lookalike domains, executive impersonation, diverted replies, prompt injection, file-type tricks). Muse Ember triages at minimal reasoning effort and calls specialists only when needed: a document specialist reads invoices and receipts straight from PDFs and photos, a voicemail specialist listens to recordings, and an identity specialist calls a read-only lookup tool to check who an email claims to be from. Every claim quotes its source, and code verifies the quotes, the arithmetic, the IBAN checksum and the account on file. Only `policy.py` decides. Payments and bank-detail changes are always held for a named person, and every hold says what to do next: call the vendor on the number in your records, not one in the message. A later message about the same invoice is linked to its hold, and approving a hold that needs a check requires a written record of how it was checked.
>
> **Results.** 6,458 emails (3,458 real phishing emails from the Nazario corpus) for $1.75 in model calls, $0.27 per 1,000: 3,340 of the 3,458 phishing emails (96.6%) went to review, and 9 of 3,000 synthetic benign emails (from 9 templates) were false alarms. 998 real receipt photos (CORD) in, expense records out: 98.7% of totals read correctly against human labels, for $0.76. All 18 labeled fixtures got the right risk and held actions, including fraud by voicemail and a fake vendor caught through a tool call.
>
> **Budget.** Going over $10 disqualifies us, so the $9.00 cap is enforced in code: each call reserves its worst-case cost first and settles at the API's reported usage. Every call is one line in the committed trace, reconciled against Meta's Usage page. Total spent: ⟨$Z⟩.
>
> **Built with:** Muse Ember for every agent; Muse Code (Spark) for code review.

**Meta tooling used** (25% of the score, so name each one): Responses API · reasoning-effort levels (minimal for triage, low for specialists) · strict structured output (`text.format` JSON schema) · native PDF, image and audio input · prompt caching (45.6% of input tokens cached, $0.63 saved) · the input-token count endpoint (bounds audio calls before they're made) · function calling (a read-only `lookup_vendor` tool; code checks every answer) · multi-agent on Ember (coordinator, document, voicemail, identity, risk and skeptic agents, plus a handoff drafter that writes verification messages from code's facts only) · Muse Code.

## Demo video: 3:00, one supplier payment start to finish

The story is invoice 1047 from Marrowvale Couriers: the invoice arrives, a voicemail asks to pay it to new bank details, the hold escalates with a concrete next step, and you close the loop. Then breadth, scale and budget. The replay site comes first and last, so judges know they can check every decision without a key.

**Don't say** (the README and both reviews depend on this)
- Don't present the false-alarm figure as a real inbox: it's 9 of 3,000 *synthetic* benign emails from 9 templates.
- Don't call Gate A a blind test: `triage.v5`'s 100% routing came after seeing the `triage.v4` failures. The video doesn't need Gate A at all.
- Receipts are **98.7%**, not 99.0%.
- Never show public-corpus items (Nazario emails, CORD photos) on screen: aggregates only ([`DATA.md`](DATA.md)).
- Never show the key, `.env` or the environment.

**Before recording**
- Rehearse once end to end: the live link in segment 3 depends on what Ember hears. If it misses, re-record that segment.
- Set the key before recording (`read -rsp "Paste key, then Enter: " MODEL_API_KEY && export MODEL_API_KEY && echo`), never on camera.
- Big terminal font (Ctrl+= in VS Code), other tabs closed.
- Start the hold queue fresh so the holds read #1 and #2, keeping the old one:
  `mkdir -p var/archive && mv var/holds.db* var/archive/ 2>/dev/null`
- Second terminal: `uv run forgeember console` (http://127.0.0.1:8000). Live calls use the `demo` stage (each item costs well under a cent).

| # | Time | On screen | Commands / actions |
|---|---|---|---|
| 1 | 0:00–0:15 | Replay site home: results cards and decision trails | Replay site (hosted link if it's up, else `site/index.html`) |
| 2 | 0:15–0:35 | Invoice 1047 screened, then hold #1 in the console: LOW, "approve when it's due" | `uv run forgeember screen fixtures/invoices/inv-007-marrowvale-1047.pdf --stage demo --live` → console, refresh |
| 3 | 0:35–1:15 | Play ~5 s of the voicemail; screen it; console: hold #1 now HIGH with the call step, the caller's other number, the draft and the follow-up | Replay site → vm-005 (audio player), then `uv run forgeember screen fixtures/voicemails/vm-005-1047-new-bank.mp3 --stage demo --live` → console, refresh, open the draft |
| 4 | 1:15–1:35 | Close the loop: type the verification, **Approve** #1, **Reject** #2 | In hold #1's box: `Called 01632 960481 on file: Marrowvale has not changed banks` |
| 5 | 1:35–1:55 | Montage, ~4 s each: em-001, em-008 (the `lookup_vendor` row), inv-003 (the scan), vm-003 | Replay site → Decision trails |
| 6 | 1:55–2:25 | Results cards: emails and receipt photos | Replay site → Results |
| 7 | 2:25–2:50 | Budget evidence: README budget block, `traces/calls.jsonl`, Meta's Usage page | README → Budget & Trace; the trace file; the Usage page (filtered to Muse Ember) |
| 8 | 2:50–3:00 | Repo and replay-site links | Title card or the README header |

**Voiceover** (about 400 words: a calm 3 minutes)

1. *(0:00)* "This is ForgeEmber's replay of real runs. No key needed. It screens every email, invoice, receipt photo and voicemail a small business receives, with Muse Ember, for a fraction of a cent each. Let me follow one supplier payment from start to finish."

2. *(0:15)* "Invoice 1047 arrives from Marrowvale, our courier. Ember reads the PDF. Code checks the totals, the bank account against our records, and the usual amount. All fine, so the payment waits for me with one line: approve when it's due."

3. *(0:35)* "Then a voicemail: 'We've changed banks, please don't pay 1047 to the old account.' Ember listens to the audio and writes down what it heard. Code links it to the open invoice: same supplier, same invoice number. The payment jumps to high risk, and the next step is specific: call Marrowvale on the number in our records. Code also notices that the caller gave a different number. Ember drafted a message to Marrowvale's address on file. It never saw the voicemail while writing it, so nothing in the call could steer it, and ForgeEmber never sends it."

4. *(1:15)* "I call the number on file. Marrowvale hasn't changed banks. I record that, approve the payment to the account we know, and reject the change. Without that note, the approval is refused."

5. *(1:35)* "The same engine handles the rest: a lookalike domain, a fake vendor caught by a lookup tool, a scanned invoice, and a voicemail from someone using the owner's name. Ember proposes, code decides, a person acts."

6. *(1:55)* "And it's cheap enough to run on everything. 6,458 emails, including 3,458 real phishing emails, for $1.75: 96.6% of the phishing went to review, with 9 false alarms on 3,000 synthetic benign emails. 998 real receipt photos, read straight from the image: 98.7% of totals correct, for 76 cents."

7. *(2:25)* "Going over ten dollars would disqualify us, so the cap lives in our code. Every call reserves its worst case before it's sent, every call is one line in the trace, and the ledger is reconciled against Meta's Usage page. Total spent: ⟨$Z⟩."

8. *(2:50)* "Ember makes checking every item cheap enough to be the default. It's open source, with a keyless replay of every decision."

## Proof of usage (Dec 1)

1. **Usage page screenshot:** Meta's Usage page, Nov 23–Dec 2, filtered to Muse Ember. Save it as `docs/assets/meta-usage.png`.
2. **Reconcile** against that total: `uv run forgeember budget reconcile --meta-usd <the total on that page>`. The ledger must come in at or above Meta's number.
3. **Muse Code trajectory:** in Muse Code, run `/export trajectory` and save it as `traces/muse-code-trajectory.json`. Then scan it **before** committing it: `uv run python scripts/check_secrets.py traces/muse-code-trajectory.json`. Real secrets get removed, and any exposed key gets rotated. Lines that only describe a header format are fine.
4. **Trace and summary:** `uv run python scripts/export_trace.py`, then `uv run python scripts/render_budget_readme.py`.
5. **Final scan:** `uv run python scripts/check_secrets.py traces/ README.md`.
