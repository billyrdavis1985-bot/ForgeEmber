"""Screen one item end to end: code checks -> Ember proposals -> verification -> policy -> actions.

Call plan per item (cheapest path first):
  1. Code: sender vs. vendor baselines, lookalike domains, executive impersonation, Reply-To,
     attachment type mismatch. No model call.
  2. Coordinator (triage, minimal effort): every item.
  3. Document specialist (low): only if there is a document or an invoice/payment. A receipt's
     extraction is the expense record (vendor, date, total); it never creates a payment.
  3b. Voicemail specialist (low): every audio part. Code compares any account read out with
     the vendor's account on file, and treats a spoken money request as unconfirmed.
  4. Risk specialist (low; medium when code already found a red flag): only on escalation.
  5. Skeptic (medium): escalated cases only, by default (Gate C).
Then verify.py checks every quote and number, and policy.decide sets risk and actions.

A budget refusal mid-item stops further calls. The item is still decided, conservatively:
screening incomplete means at least MEDIUM risk, and money actions are held regardless.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from forgeember.agents.base import AgentResult, AgentSpec, Estimator, bounded_estimator, run_agent
from forgeember.agents.coordinator import TRIAGE
from forgeember.agents.inputs import item_content, primary_kind
from forgeember.agents.skeptic import SKEPTIC, skeptic_note
from forgeember.agents.specialists import DOCUMENT, RISK, VOICEMAIL
from forgeember.baseline import (
    BaselineStore,
    Organization,
    VendorProfile,
    account_key,
    iban_is_valid,
)
from forgeember.budget.ledger import BudgetExceeded, CallRecord, InputKind
from forgeember.ember.client import EmberClient
from forgeember.envelope import Envelope
from forgeember.policy import Decision, Facts, decide
from forgeember.schemas import (
    MONEY_CATEGORIES,
    InvoiceExtraction,
    RiskAssessment,
    SkepticView,
    TriageResult,
    VoicemailNote,
)
from forgeember.verify import Check, CheckStatus, verify_invoice, verify_signals

# Text addressed to automated reviewers is a manipulation attempt, whoever it claims to be from.
INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+instructions\b"),
    re.compile(
        r"(?i)\b(note|message|instructions?)\s+(to|for)\s+(the\s+)?"
        r"(ai|llm|model|assistant|automated\s+\w+|ai\s+assistant)\b"
    ),
    re.compile(r"(?i)\bsystem\s+(note|prompt|message|instruction)\b"),
    re.compile(
        r"(?i)\b(mark|classify)\s+(it|this|the\s+\w+)(\s+\w+)?\s+as\s+"
        r"(verified|approved|safe|low[\s-]risk|normal_business)\b"
    ),
)


def injection_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def injection_attempt(env: Envelope) -> bool:
    texts = [p.text for p in env.parts if p.text]
    if env.headers is not None:
        texts.append(env.headers.subject)
    return any(injection_text(text) for text in texts)


class SkepticMode(StrEnum):
    OFF = "off"
    ESCALATED = "escalated"
    # No ALWAYS: a skeptic with no verdict to argue against spends a call whose
    # output can't affect the decision. Gate C can add it back with honest framing.


@dataclass(frozen=True, slots=True)
class CodeFindings:
    """What code knows before any model call."""

    sender_vendor: VendorProfile | None = None
    lookalike_of: VendorProfile | None = None
    impersonated_executive: str | None = None
    reply_to_mismatch: bool = False
    attachment_mime_mismatch: bool = False
    internal_sender: bool = False
    injection_attempt: bool = False

    @property
    def red_flag(self) -> bool:
        return bool(
            self.lookalike_of
            or self.impersonated_executive
            or self.reply_to_mismatch
            or self.attachment_mime_mismatch
            or self.injection_attempt
        )

    def as_prompt_facts(self, org: Organization) -> list[str]:
        """Facts for the model. The org roster lets it spot executive requests that
        the exact-match code check misses (nicknames, initials, typos)."""
        facts = [f"The recipient organization is {org.name} (domains: {', '.join(org.domains)})."]
        if org.executives:
            names = ", ".join(f"{e.name} <{e.email}>" for e in org.executives)
            facts.append(f"Our executives: {names}.")
        if self.sender_vendor:
            facts.append(
                f"The sender's domain belongs to a known vendor: {self.sender_vendor.name}."
            )
        if self.lookalike_of:
            facts.append(
                f"The sender's domain is NOT on file; it closely resembles the domain of "
                f"known vendor {self.lookalike_of.name}."
            )
        if self.impersonated_executive:
            facts.append(
                f"The display name matches our executive {self.impersonated_executive}, "
                "but the address is not theirs."
            )
        if self.reply_to_mismatch:
            facts.append("Replies would go to a different domain than the sender's.")
        if self.attachment_mime_mismatch:
            facts.append("An attachment's declared file type does not match its content.")
        if self.internal_sender:
            facts.append("The sender is on our own domain.")
        if self.injection_attempt:
            facts.append(
                "The item contains text addressed to automated reviewers (prompt injection)."
            )
        return facts


def code_findings(env: Envelope, baselines: BaselineStore) -> CodeFindings:
    mismatch = any(p.mime_mismatch for p in env.parts)
    injected = injection_attempt(env)
    h = env.headers
    if h is None:
        return CodeFindings(attachment_mime_mismatch=mismatch, injection_attempt=injected)
    exec_ = baselines.impersonated_executive(h.from_name, h.from_addr)
    reply_mismatch = bool(
        h.reply_to_domain
        and h.reply_to_domain != h.from_domain
        and not baselines.is_internal(h.reply_to_domain)
    )
    return CodeFindings(
        sender_vendor=baselines.vendor_by_domain(h.from_domain),
        lookalike_of=baselines.lookalike_vendor(h.from_domain),
        impersonated_executive=exec_.name if exec_ else None,
        reply_to_mismatch=reply_mismatch,
        attachment_mime_mismatch=mismatch,
        internal_sender=baselines.is_internal(h.from_domain),
        injection_attempt=injected,
    )


@dataclass
class ScreenResult:
    envelope: Envelope
    code: CodeFindings
    triage: TriageResult | None = None
    extraction: InvoiceExtraction | None = None
    risk: RiskAssessment | None = None
    skeptic: SkepticView | None = None
    voicemail: VoicemailNote | None = None
    checks: list[Check] = field(default_factory=list)
    facts: Facts | None = None
    decision: Decision | None = None
    calls: list[CallRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    budget_stopped: bool = False
    retryable: bool = False  # an agent hit a transient API error: worth retrying the item
    media_fallback: bool = False  # attachments replaced by code-extracted text
    notes: list[str] = field(default_factory=list)

    @property
    def cost_nusd(self) -> int:
        return sum(c.cost_nusd or 0 for c in self.calls)

    def receipt(self, outcome: Any = None) -> dict[str, Any]:
        env, d = self.envelope, self.decision
        h = env.headers
        return {
            "schema": "forgeember.receipt.v2",
            "item_id": env.id,
            "source": env.source,
            "headers": None
            if h is None
            else {
                "from": f"{h.from_name} <{h.from_addr}>",
                "reply_to": h.reply_to,
                "subject": h.subject,
                "date": h.date,
            },
            "triage": self.triage.model_dump() if self.triage else None,
            "risk_assessment": self.risk.model_dump() if self.risk else None,
            "sha256": env.sha256,
            "kind": env.kind.value,
            "parts": [{"name": p.name, "mime": p.mime, "sha256": p.sha256} for p in env.parts],
            "category": self.facts.category if self.facts else None,
            "risk": d.risk.value if d else None,
            "reasons": list(d.reasons) if d else [],
            "actions": [asdict(a) for a in d.actions] if d else [],
            "outcome": asdict(outcome) if outcome is not None else None,
            "checks": [asdict(c) for c in self.checks],
            "facts": asdict(self.facts) if self.facts else None,
            "extraction": self.extraction.model_dump() if self.extraction else None,
            "voicemail": self.voicemail.model_dump() if self.voicemail else None,
            "ember_verdict": self.risk.verdict if self.risk else None,
            "skeptic": self.skeptic.model_dump() if self.skeptic else None,
            "calls": [
                {
                    "call_id": c.call_id,
                    "agent_role": c.agent_role,
                    "prompt_version": c.prompt_version,
                    "reasoning_effort": c.reasoning_effort,
                    "cost_nusd": c.cost_nusd,
                    "outcome": c.outcome,
                }
                for c in self.calls
            ],
            "cost_nusd": self.cost_nusd,
            "errors": self.errors,
            "budget_stopped": self.budget_stopped,
            "media_fallback": self.media_fallback,
            "notes": self.notes,
        }


# Document items whose fields we extract in bulk mode: money requests, and receipts (the
# extraction is the expense record).
DOCUMENT_CATEGORIES = MONEY_CATEGORIES | {"receipt"}


class _BudgetStop(Exception):
    pass


@dataclass
class Screener:
    client: EmberClient
    baselines: BaselineStore
    estimator: Estimator = bounded_estimator
    skeptic_mode: SkepticMode = SkepticMode.ESCALATED
    # Bulk mode (scale runs): triage's verified signals are enough to flag an item, so the
    # risk agent runs only for money, bank-change or code-red-flag items, and extraction only
    # for money items, receipt documents, or when triage asks for it. Trade-off: a fake invoice
    # image that triage routes as "other" isn't extracted. The default (bulk=False) keeps the
    # strict plan.
    bulk: bool = False
    bulk_text_limit: int = 6_000  # chars per text part sent to the model in bulk mode

    def _run(
        self,
        spec: AgentSpec,
        env: Envelope,
        result: ScreenResult,
        facts: list[str],
        note: str | None = None,
    ) -> Any:
        limit = self.bulk_text_limit if self.bulk else None

        def attempt(media_as_text: bool) -> AgentResult:
            try:
                return run_agent(
                    self.client,
                    spec,
                    input_items=item_content(env, facts, note, limit, media_as_text=media_as_text),
                    input_kind=primary_kind(env),
                    item_id=env.id,
                    estimator=self.estimator,
                )
            except BudgetExceeded as exc:
                result.errors.append(f"{spec.role}: budget refused ({exc.scope})")
                result.budget_stopped = True
                raise _BudgetStop from exc

        out: AgentResult = attempt(result.media_fallback)
        if (
            out.error
            and out.status_code is not None
            and out.status_code != 429
            and env.media_parts
            and not result.media_fallback
        ):
            # The API failed on this item with its attachments (seen live: crafted phishing
            # PDFs that reliably return 503). Screen the text our code extracted instead, and
            # use text-only input for the rest of this item.
            result.media_fallback = True
            result.notes.append(
                f"{spec.role}: model API failed on an attachment "
                f"(HTTP {out.status_code}); screened extracted text instead"
            )
            out = attempt(True)
        if out.record is not None:
            result.calls.append(out.record)
        if out.error:
            result.errors.append(out.error)
            result.retryable = result.retryable or out.retryable
        return out.value

    def screen(self, env: Envelope) -> ScreenResult:
        code = code_findings(env, self.baselines)
        result = ScreenResult(envelope=env, code=code)
        facts_text = code.as_prompt_facts(self.baselines.organization)
        has_document = any(p.kind in (InputKind.PDF, InputKind.IMAGE) for p in env.parts)
        try:
            result.triage = self._run(TRIAGE, env, result, facts_text)
            category = self._category(result.triage, has_document)
            wants_doc = result.triage.needs_document_extraction if result.triage else False
            if self.bulk:  # documents only when money is involved, for receipts, or on request
                extract = (
                    wants_doc
                    or category == "invoice"
                    or (has_document and category in DOCUMENT_CATEGORIES)
                )
            else:  # strict plan: every document part is extracted
                extract = has_document or wants_doc or category == "invoice"
            if extract:
                # Code decides: a document part always gets extraction, whatever triage said.
                result.extraction = self._run(DOCUMENT, env, result, facts_text)
            if any(p.kind is InputKind.AUDIO for p in env.parts):
                # Code decides: every recording is listened to, whatever triage said.
                result.voicemail = self._run(VOICEMAIL, env, result, facts_text)
            if self._needs_risk(result, category, code):
                result.risk = self._run(RISK, env, result, facts_text)
                if result.risk is not None and self.skeptic_mode is not SkepticMode.OFF:
                    result.skeptic = self._run(
                        SKEPTIC, env, result, facts_text, note=skeptic_note(result.risk.verdict)
                    )
        except _BudgetStop:
            pass
        self._verify_and_decide(result)
        return result

    @staticmethod
    def _category(triage: TriageResult | None, has_document: bool) -> str:
        if triage is not None:
            return triage.category
        return "invoice" if has_document else "other"  # conservative: documents get a held payment

    def _needs_risk(self, result: ScreenResult, category: str, code: CodeFindings) -> bool:
        t = result.triage
        return bool(
            # Signals, not the escalate flag: an invoice "asks to move money" by definition.
            # In bulk mode a signal alone doesn't buy a second call: verified triage signals
            # already flag the item.
            (t is not None and t.risk_signals and not self.bulk)
            or category in ("bank_change_request", "payment_request", "executive_request")
            or code.red_flag
            or (result.extraction is not None and result.extraction.claims_bank_change)
            or (
                result.voicemail is not None
                and (result.voicemail.claims_bank_change or result.voicemail.asks_for_payment)
            )
        )

    def _verify_and_decide(self, result: ScreenResult) -> None:
        env, code, ext = result.envelope, result.code, result.extraction
        has_document = any(p.kind in (InputKind.PDF, InputKind.IMAGE) for p in env.parts)
        triage_category = result.triage.category if result.triage else None
        category = self._category(result.triage, has_document)
        if ext is not None and ext.is_invoice and category not in MONEY_CATEGORIES:
            # A verified invoice is a payment request whatever the triage route said;
            # a bank-change claim from that route is preserved into the facts below.
            category = "invoice"
        vm = result.voicemail
        if (
            vm is not None
            and vm.asks_for_payment
            and category not in MONEY_CATEGORIES
            and category != "bank_change_request"
        ):
            category = "payment_request"  # a spoken request for money, whatever the route
        checks: list[Check] = []
        if result.triage:
            checks += verify_signals(env, result.triage.risk_signals, "triage")
        if result.risk:
            checks += verify_signals(env, result.risk.signals, "risk")
        if ext is not None and ext.is_invoice:
            checks += verify_invoice(env, ext)
        if result.voicemail is not None:
            checks.append(
                Check(
                    "voicemail.recording",
                    CheckStatus.UNVERIFIABLE,
                    "code can't check what a recording says: the note is the caller's claims",
                )
            )
        result.checks = checks

        def status_of(prefix: str) -> set[CheckStatus]:
            return {c.status for c in checks if c.name.startswith(prefix)}

        vendor = None
        bank_status = amount_status = None
        iban_valid = None
        if ext is not None and ext.is_invoice:
            vendor = self.baselines.vendor_by_name(ext.vendor_name) or code.sender_vendor
            key = (
                account_key(
                    iban=ext.bank.iban,
                    account_number=ext.bank.account_number,
                    routing_number=ext.bank.routing_number,
                )
                if ext.bank
                else None
            )
            bank_status = self.baselines.bank_status(vendor, key)
            amount_status = self.baselines.amount_status(vendor, ext.total)
            iban_valid = iban_is_valid(ext.bank.iban) if ext.bank and ext.bank.iban else None
        elif vm is not None:
            # The caller's claimed company decides which account on file the details must match.
            vendor = (
                self.baselines.vendor_by_spoken_name(vm.caller_organization) or code.sender_vendor
            )
            if vm.bank is not None:
                key = account_key(
                    iban=vm.bank.iban,
                    account_number=vm.bank.account_number,
                    routing_number=vm.bank.routing_number,
                )
                bank_status = self.baselines.bank_status(vendor, key)
                iban_valid = iban_is_valid(vm.bank.iban) if vm.bank.iban else None
            if vm.amount:
                amount_status = self.baselines.amount_status(vendor, vm.amount)
        else:
            vendor = code.sender_vendor
        # Parts nobody could read: video (not screened yet), audio the voicemail specialist
        # didn't return a note for, and unknown binaries (no text layer, never sent to the
        # model). PDF/image scans are covered by the UNVERIFIABLE invoice checks above.
        blind_media = any(
            p.kind is InputKind.VIDEO
            or (p.kind is InputKind.AUDIO and vm is None)
            or (p.text is None and p.kind in (InputKind.TEXT, InputKind.EMAIL))
            for p in env.parts
        )
        # A money or bank-detail request that only a recording supports: code can't check
        # what was said, so it stays unconfirmed until someone calls back a number on file.
        heard_request = vm is not None and (
            vm.asks_for_payment or vm.claims_bank_change or vm.bank is not None
        )
        math = status_of("invoice.math")
        key_fields = {
            c.status
            for c in checks
            if c.name
            in (
                "invoice.in_source:total",
                "invoice.in_source:iban",
                "invoice.in_source:account_number",
            )
        }

        verdict = result.risk.verdict if result.risk else None
        if verdict in (None, "benign") and result.triage and result.triage.risk_signals:
            # A benign second read doesn't erase triage's signals; verification below
            # decides whether they count (verified -> MEDIUM, otherwise silent).
            verdict = "suspicious"
        if result.skeptic and {verdict, result.skeptic.counter_verdict} == {
            "likely_fraud",
            "benign",
        }:
            verdict = "suspicious"  # strong disagreement: neither side wins outright
        verified = sum(
            1
            for c in checks
            if c.name.startswith(("triage.", "risk.")) and c.status is CheckStatus.PASS
        )

        result.facts = Facts(
            category=category,
            screening_complete=not result.errors and not result.budget_stopped,
            vendor_known=(vendor is not None) if category in MONEY_CATEGORIES else None,
            bank_status=bank_status,
            amount_status=amount_status,
            iban_valid=iban_valid,
            math_ok=(
                False if CheckStatus.FAIL in math else True if CheckStatus.PASS in math else None
            ),
            key_fields_unconfirmed=CheckStatus.FAIL in key_fields,
            evidence_unverifiable=CheckStatus.UNVERIFIABLE in status_of("invoice.") or blind_media,
            claims_bank_change=bool(ext and ext.claims_bank_change)
            or bool(vm and vm.claims_bank_change)
            or triage_category == "bank_change_request",
            lookalike_domain=code.lookalike_of is not None,
            executive_impersonation=code.impersonated_executive is not None,
            reply_to_mismatch=code.reply_to_mismatch,
            attachment_mime_mismatch=code.attachment_mime_mismatch,
            # raise-only: a transcript can add an injection finding, never remove one
            injection_attempt=code.injection_attempt or bool(vm and injection_text(vm.transcript)),
            voicemail=vm is not None,
            heard_request=heard_request,
            executive_voice_request=bool(
                vm
                and (vm.asks_for_payment or category in MONEY_CATEGORIES)
                and self.baselines.executive_named(vm.caller_name)
            ),
            media_fallback=result.media_fallback,
            ember_verdict=verdict,
            verified_signals=verified,
        )
        result.decision = decide(result.facts)
