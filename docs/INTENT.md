# Intent

> Change this file first if the product direction changes. Every other doc follows it.

## What it is

**ForgeEmber screens every email, invoice, attachment and voicemail a small business receives, for a fraction of a cent each.** It turns messy input into verified, routed actions, and holds anything risky (payments, bank-detail changes, outside sends) for a person. It runs on **Muse Ember (`muse-ember-1.0`)** through the Meta Model API. It's Billy Davis's solo entry in the Meta Muse Ember Hackathon (Cerebral Valley), built Nov 23–Dec 2, 2026.

## Who it's for

Small businesses and solo founders with no security team. They get hit hardest by invoice fraud, bank-detail-change scams and executive impersonation, and they can't pay a person to read every message.

## The problem

Fraud arrives in the same channels as real work: an email "from" a known vendor saying the bank account changed, a PDF invoice with a new IBAN, a voicemail "from the CEO" asking for an urgent wire. Checking every item by hand is too slow. A frontier model on every item is too expensive. ForgeEmber is meant to show that Ember makes checking *every* item cheap enough to be the default.

- **Main track:** business inbox and document screening, at scale.
- **Stretch track** (only if time and budget allow): the same engine adapted to research papers, producing structured notes for Billy's research pipeline (RIP). It would show the engine is a general adapter.
- **Headline result we're aiming for:** "Screened N thousand real emails and documents for $X, with measured accuracy on the labeled subset."
- **Hackathon fit:** problem statements (1) *Cheap enough to run on everything* and (2) *Beyond text, under budget*. Prize targets: **Best Frontier Build** and **Scale the Real World**.

## What must stay true

1. **Ember proposes, and code decides.** Ember extracts, classifies, investigates and proposes routes and actions. Deterministic code verifies the evidence, applies policy, enforces the budget and executes.
2. **Only `policy.py` can approve an action.** Tiers: T0 automatic (file, tag, route a note) · T1 notify Billy · T2 hold for human approval (payments, bank-detail changes, any outside send, deletions). **Ember can never execute a T2 action.** Unknown action kinds count as T2.
3. **"Bank account changed" is detected by code**, from per-vendor baselines. Ember is asked only about manipulation in language and context.
4. **Budget:** a hard cap of **$9.00** is enforced in code against a $10.00 grant (going over $10 disqualifies us). Every call reserves its worst case before it's sent and settles at reported usage after. Reasoning tokens bill as output. A failed call keeps its full reservation. The ledger must never come in under Meta's bill.
5. **Only `ember/client.py` touches the network,** and only through the ledger. A live call needs `--live` plus a stage name. CI never makes live calls.
6. **Every model is `muse-ember-1.0`.** Muse Code (Spark) is a coding assistant, not part of the product.
7. **New work only.** No code is copied from ForgeVigil or any earlier project. Ideas may carry over, but code doesn't.
8. **Data rights:** every dataset's license is verified at its primary source before download. If it's unclear, we don't use it. Demo material is our own fictional fixtures.
9. **Secrets:** the API key lives only in Billy's shell (`MODEL_API_KEY`). It's never read into chat, printed, logged, stored or committed.
10. **Solo authorship:** every commit is authored by Billy Davis, with no AI attribution trailers. AI assistance is disclosed in the README.

## Out of scope

- A dashboard as the main feature. The approval console is secondary, and the product is the pipeline.
- Streamlit apps, basic RAG apps, plain image analyzers, education chatbots, mental-health/medical/nutrition advice, job screeners, personality or sports analyzers.
- Executing real payments or sending real external mail. Actions are proposed and held, or routed internally.
- Any model other than Muse Ember in the product path.
- Raising the $9.00 cap. Gates trim scope instead.
