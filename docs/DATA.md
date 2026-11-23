# Data register

**Rule:** a dataset's license is verified at its **primary source** (the authors' own page or repo) and recorded here *before* it's downloaded. If the license is unclear, we don't use the dataset. Raw corpora live in `data/` (gitignored). Public-corpus results appear only as aggregate statistics. The demo and video use only our own fictional fixtures.

Checked 2026-11-23. **No dataset has been downloaded yet.** The statuses below decide what may be.

| Dataset | Primary source | License (as stated at source) | Status | Planned use |
|---|---|---|---|---|
| **Nazario phishing corpus** (2005–2025 mboxes) | https://monkey.org/~jose/phishing/ (`README.txt`, `LICENSE.txt` dated 2026-02-20) | **CC BY 4.0**: "enabling derivative works and commercial use but does require attribution." Full CC BY 4.0 legal text in `LICENSE.txt`. | ✅ **Usable with attribution.** Skip `private-phishing4.mbox` (marked private). Later mailboxes aren't anonymized, so keep them local and show aggregates only. | The phishing (positive) class at scale; recall measurement |
| **CORD** receipt dataset (v2: 1,000 receipts, 800/100/100) | https://github.com/clovaai/cord (README) | **CC BY 4.0** ("This work is licensed under a Creative Commons Attribution 4.0 International License") | ✅ **Usable with attribution** (the README is the license statement; the repo has no separate LICENSE file) | Receipt field-extraction accuracy (Indonesian receipts) |
| CMU Enron email dataset (May 7, 2015 version) | https://www.cs.cmu.edu/~enron/ | **No license statement.** The page says the data "was originally made public... by the Federal Energy Regulatory Commission" and is distributed "as a resource for researchers," asking users to "be sensitive to the privacy of the people involved." | ⛔ **Not used** unless Billy decides otherwise. No explicit license, and the messages are real people's email. | (would be) the benign business-mail class |
| Apache SpamAssassin public corpus (6,047 msgs, ~31% spam) | https://spamassassin.apache.org/old/publiccorpus/readme.html | **No license grant.** "Copyright for the text in the messages remains with the original senders." | ⛔ **Not used** | (would be) the ham/spam class |
| FUNSD (scanned forms) | https://guillaumejaume.github.io/FUNSD/work | **Non-commercial, research and educational use only.** The images' copyright is governed separately (from RVL-CDIP). | ⛔ **Not used.** Restrictive terms, and a prize competition isn't clearly non-commercial. | none |
| SROIE (ICDAR 2019 scanned receipts) | https://rrc.cvc.uab.es/?ch=13 | **Not verified.** The site's TLS certificate chain failed validation from our environment, and we won't bypass TLS. | ⛔ **Not used** unless the license is verified another way | none |
| Hugging Face phishing/ham compilations (e.g. tagged MIT, Apache-2.0, CC BY 4.0, LGPL-3.0) | huggingface.co dataset cards | The tags are set by uploaders. Most compile Enron, SpamAssassin, Nazario, CEAS or TREC data, whose upstream licenses are missing or restrictive. | ⛔ **Not used.** An uploader's tag can't fix unclear upstream provenance. | none |
| **ForgeEmber fixtures** (generated) | this repo, `scripts/gen_fixtures.py` | MIT (ours) | ✅ Generated: 13 items (`fixtures/manifest.json`); voicemails pending | Fraud and normal-business fixtures with known answers; the demo and video |

## Gap: benign business mail at scale

With Enron and SpamAssassin excluded, we have **no clearly licensed corpus of real benign business email**. Options for Billy to decide (see the questions in BUILD_LOG):

1. Generate benign business mail ourselves, procedurally in code from a seed (free, ours, known labels). Precision would then be measured on synthetic negatives, and we'd say so.
2. Accept the Enron corpus as public-record data despite the missing license. This conflicts with the "unclear license, don't use" rule, so it needs Billy's explicit decision.
3. Keep looking for a benign-mail corpus with an explicit permissive license at its primary source.

## Attribution (for datasets we use)

- Nazario phishing corpus: Jose Nazario, https://monkey.org/~jose/phishing/, CC BY 4.0.
- CORD: Park et al., "CORD: A Consolidated Receipt Dataset for Post-OCR Parsing" (2019), https://github.com/clovaai/cord, CC BY 4.0.
