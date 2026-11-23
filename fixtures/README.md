# fixtures/

Our own fictional, generated inputs with known answers built in at generation time
(`scripts/gen_fixtures.py`, Phase 1): invoices as PDFs and images, bank-change emails,
CEO-impersonation emails and voicemails, and normal business mail. No real people, companies
or bank details. Because we generate them, we own them, and no hand-labeling is needed.

## Current set (`fixtures.v1`, 13 items)

| Id | File | Scenario | Expected |
|---|---|---|---|
| inv-001 | invoices/inv-001-fennimore.pdf | routine invoice, known vendor, account on file | invoice · low · hold pay |
| inv-002 | invoices/inv-002-fennimore-new-bank.pdf | known vendor announces new bank details | invoice · high · hold pay + bank change |
| inv-003 | invoices/inv-003-kestrel-scan.png | scanned invoice image, known vendor | invoice · medium · hold pay |
| inv-004 | invoices/inv-004-orrin-unknown-vendor.pdf | vendor with no history | invoice · medium · hold pay |
| inv-005 | invoices/inv-005-marrowvale-bad-total.pdf | totals don't add up | invoice · high · hold pay |
| inv-006 | invoices/inv-006-marrowvale-bad-iban.pdf | IBAN fails its checksum | invoice · high · hold pay |
| em-001 | emails/em-001-bank-change-lookalike.eml | bank change from a lookalike vendor domain | bank_change_request · high |
| em-002 | emails/em-002-ceo-impersonation.eml | owner impersonation, urgent secret transfer | executive_request · high · hold pay |
| em-003 | emails/em-003-maintenance-notice.eml | routine vendor notice | normal_business · low |
| em-004 | emails/em-004-newsletter.eml | newsletter | marketing · low |
| em-005 | emails/em-005-invoice-attached.eml | vendor emails an invoice PDF | invoice · low · hold pay |
| em-006 | emails/em-006-reply-to-mismatch.eml | replies diverted to another domain | payment_request · medium · hold pay |
| em-007 | emails/em-007-prompt-injection.eml | invoice with instructions aimed at AI reviewers | invoice · high · hold pay |

Full labels, including expected invoice fields, are in `manifest.json`. Regenerate with `uv run python scripts/gen_fixtures.py`. Voicemail audio is not generated yet.
