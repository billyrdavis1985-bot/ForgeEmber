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
> **Results.** 6,458 emails (3,458 real phishing emails from the Nazario corpus) for $1.75 in model calls, $0.27 per 1,000: 3,340 of the 3,458 phishing emails (96.6%) went to review, and 9 of 3,000 synthetic benign emails (from 9 templates) were false alarms. 998 real receipt photos (CORD) in, expense records out: 98.7% of totals read correctly against human labels, for $0.76. All 25 labeled fixtures got the right risk and held actions, including fraud by voicemail, a bank change shown in a screen recording, and a fake vendor caught through a tool call. A suite of 10 attacks, frozen before the run (lookalike letters, hidden PDF text, injections in photos and voicemails, an account number posing as an invoice number), was defended 10 of 10. On 25 tricky but legitimate messages we wrote and labeled before running them, 2 of 18 routine ones interrupted the owner and none of the 7 that needed a check was missed.
>
> **Budget.** Going over $10 disqualifies us, so the $9.00 cap is enforced in code: each call reserves its worst-case cost first and settles at the API's reported usage. Every call is one line in the committed trace, reconciled against Meta's Usage page. Total spent: ⟨$Z⟩.
>
> **Built with:** Muse Ember for every agent; Muse Code (Spark) for code review.

**Meta tooling used** (25% of the score, so name each one): Responses API · reasoning-effort levels (minimal for triage, low for specialists) · strict structured output (`text.format` JSON schema) · native PDF, image, audio and video input · prompt caching (about 46% of input tokens cached, over $0.75 saved) · the input-token count endpoint (bounds audio and video calls before they're made) · function calling (a read-only `lookup_vendor` tool; code checks every answer) · multi-agent on Ember (coordinator, document, voicemail, video, identity, risk and skeptic agents, plus a handoff drafter that writes verification messages from code's facts only) · Muse Code.

## Demo video: 3:00, one supplier payment start to finish

The story is invoice 1047 from Marrowvale Couriers, told the way the Nov 26 review asked. The video opens on the invoice already waiting for approval. A voicemail asks to pay it to new bank details, and we watch what changes on that *same* hold and where the number to call comes from. An unrelated invoice stays separate. Then breadth, scale with the misses said out loud, and the budget. The same story replays without a key at https://billyrdavis1985-bot.github.io/ForgeEmber/cases/invoice-1047.html.

**Don't say** (the README and the reviews depend on this)
- Don't present the false-alarm figure as a real inbox: it's 9 of 3,000 *synthetic* benign emails from 9 templates.
- Don't call Gate A a blind test: `triage.v5`'s 100% routing came after seeing the `triage.v4` failures. The video doesn't need Gate A at all.
- Receipts are **98.7%**, not 99.0%.
- Don't call the ordinary-mail or attack sets independent: we wrote them (labels fixed before running). Say "2 of 18 routine messages interrupted the owner", not a false-alarm rate for real inboxes.
- Say the misses: **118 of the 3,458 real phishing emails were not sent to review.**
- Never show public-corpus items (Nazario emails, CORD photos) on screen: aggregates only ([`DATA.md`](DATA.md)).
- Never show the key, `.env` or the environment.

**Before recording**
- Rehearse once end to end. Segments 2 and 3 are live, and the links depend on what Ember hears. If a link misses, re-record that segment, or show the same step on the case page (it's the recorded run).
- Set the key before recording (`read -rsp "Paste key, then Enter: " MODEL_API_KEY && export MODEL_API_KEY && echo`), never on camera.
- Big terminal font (Ctrl+= in VS Code), other tabs closed.
- Stop any console that is still running (Ctrl+C in its terminal; an old one keeps the old queue open and holds port 8000).
- Start the hold queue fresh, keeping the old one, then put invoice 1047 in it **off camera**, so the video opens on hold #1 already waiting. The queue is `var/holds.sqlite` (plus its `-wal`/`-shm` files):
  `d=var/archive/$(date +%Y%m%d-%H%M%S) && mkdir -p "$d" && mv var/holds.sqlite* "$d"/`
  `ls var/holds.sqlite* 2>/dev/null || echo "queue is fresh"` must print `queue is fresh`.
  `uv run forgeember screen fixtures/invoices/inv-007-marrowvale-1047.pdf --stage demo --live` must say **hold #1** when vm-005 links to it later.
- Second terminal: `uv run forgeember console` (http://localhost:8000 in the Windows browser). If it says the address is already in use, an old console is still running: `ss -ltnp | grep ':8000'` shows its pid to stop, or use `--port 8010` and open http://localhost:8010. Live calls use the `demo` stage (each item costs well under a cent).
- Rehearsed on Nov 27: vm-005 linked to the waiting 1047 hold and turned it HIGH; vm-007 (invoice 1052) stayed separate.

| # | Time | On screen | Commands / actions |
|---|---|---|---|
| 1 | 0:00–0:20 | Console: hold #1, invoice MVC-1047, LOW, "approve when it's due" | Console, already open |
| 2 | 0:20–1:00 | Play ~5 s of the voicemail, screen it, refresh: hold #1 is now HIGH, with the follow-up note, the call step, "the caller gave 01632960735", the draft. Point at the number's source: the Marrowvale entry in `fixtures/baselines.json` | Case page step 3 (audio player), then `uv run forgeember screen fixtures/voicemails/vm-005-1047-new-bank.mp3 --stage demo --live` → console, refresh, open the draft → `fixtures/baselines.json` |
| 3 | 1:00–1:15 | A voicemail about invoice 1052: its own hold, HIGH; hold #1 untouched | `uv run forgeember screen fixtures/voicemails/vm-007-1052-new-bank.mp3 --stage demo --live` → console, refresh |
| 4 | 1:15–1:35 | Close the loop: approve #1 with no note (refused), type the check, approve; reject the bank change | In hold #1's box: `Called 01632 960481 on file: Marrowvale has not changed banks` |
| 5 | 1:35–1:55 | Montage, ~4 s each: em-001 (lookalike domain), em-008 (the `lookup_vendor` row), at-04 (the doctored PDF), vd-001 (screen recording), the duplicate-invoice case | Replay site → decision trails and Attack suite, then Start here → the same invoice twice |
| 6 | 1:55–2:30 | Results cards: emails (with the misses) and receipt photos | Replay site → Results |
| 7 | 2:30–2:50 | Budget evidence: README budget block, `traces/calls.jsonl`, Meta's Usage page | README → Budget & Trace; the trace file; the Usage page (filtered to Muse Ember) |
| 8 | 2:50–3:00 | The replay link and the repo | The case page's address bar, then the README header |

**Voiceover** (about 420 words: a calm 3 minutes)

1. *(0:00)* "This is the approval queue of a small bakery. Invoice 1047 from Marrowvale, our courier, is waiting. Muse Ember read the PDF. Code checked the totals, the bank account against our records, and the usual amount. All fine, so it says: approve when it's due."

2. *(0:20)* "Then a voicemail: 'We've changed banks, please pay 1047 to the new account.' Ember listens to the audio and writes down what it heard. Code links it to the invoice that's waiting: same invoice number. And the same hold changes: it's now high risk, and the next step is specific: call Marrowvale on 01632 960481. That number comes from our own vendor records, here, not from the voicemail. Code noticed the caller gave a different one. Ember also drafted a message to Marrowvale's address on file. It never saw the voicemail while writing it, so nothing in the call could steer it, and ForgeEmber never sends it."

3. *(1:00)* "A second voicemail, about invoice 1052. That invoice isn't waiting, so it stays separate: its own hold. Invoice 1047 is untouched."

4. *(1:15)* "To approve 1047, I have to say how I checked. Without a note, the approval is refused. I called the number on file; Marrowvale hasn't changed banks. Approve the payment to the account we know, and reject the change."

5. *(1:35)* "The same engine handles the rest: a lookalike domain, a fake vendor caught by a lookup tool, a PDF whose hidden text shows one account while the page shows another, a payee edit in a screen recording, and the same invoice sent twice, where only one gets paid. Ember proposes, code decides, a person acts."

6. *(1:55)* "It's cheap enough to run on everything. 6,458 emails, including 3,458 real phishing emails, for $1.75. 96.6% of the phishing went to review, and 118 didn't: those are the misses. 9 false alarms on 3,000 synthetic benign emails. 998 real receipt photos, read straight from the image: 98.7% of totals correct, for 76 cents."

7. *(2:30)* "Going over ten dollars would disqualify us, so the cap lives in our code. Every call reserves its worst case before it's sent, every call is one line in the trace, and the ledger is reconciled against Meta's Usage page. Total spent: ⟨$Z⟩."

8. *(2:50)* "Under half a cent to screen a voicemail like that one, and every decision you saw replays at this link, with no key."

## Proof of usage (Dec 1)

1. **Usage page screenshot:** Meta's Usage page, Nov 23–Dec 2, filtered to Muse Ember. The Spend card shows $0.00 because existing account credit pays, so capture the token cards (input tokens with the cache hit rate, output tokens, requests). Save it as `docs/assets/meta-usage.png`.
2. **Reconcile** against Meta's figures: price the page's token counts at list prices (input $0.10/M uncached, $0.004/M cached, output $0.50/M; take the top of the rounding range) and record them: `uv run forgeember budget reconcile --meta-usd <that total> --meta-input-tokens <N> --meta-output-tokens <N> --note "..."`. The ledger must come in at or above Meta's number.
3. **Muse Code trajectory:** in Muse Code, run `/export trajectory` and save it as `traces/muse-code-trajectory.json`. Then scan it **before** committing it: `uv run python scripts/check_secrets.py traces/muse-code-trajectory.json`. Real secrets get removed, and any exposed key gets rotated. Lines that only describe a header format are fine.
4. **Trace and summary:** `uv run forgeember budget publish`, then `uv run python scripts/export_trace.py`, then `uv run python scripts/export_usage_trajectory.py` (our spend history; it goes stale after every live run), then `uv run forgeember site build` last, so the site's budget cards match. `traces/usage-trajectory.json` is not the Muse Code trajectory from step 3; keep the two names distinct when describing them.
5. **Refresh the moving figures:** the README's "Results at a glance" Budget row ($ spent so far) becomes the final total; the tooling table's "over 11,700 live calls" and "over $0.75 saved" are written to stay true as spend grows; check them once against `traces/calls.jsonl` after the last run.
6. **Final scan:** `uv run python scripts/check_secrets.py traces/ README.md`.
