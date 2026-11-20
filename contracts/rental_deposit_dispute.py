# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json
import datetime


REASON_CODES = (
    "NORMAL_WEAR",
    "DAMAGE_PROVEN",
    "DAMAGE_PARTIAL",
    "CLEANING_PROVEN",
    "UNSUPPORTED_CLAIM",
    "NO_LANDLORD_RESPONSE",
    "MIXED_EVIDENCE",
)

# Allowed refund percentages. Validators must agree on the exact same bucket,
# so the consequential settlement amount is bound by consensus.
PERCENT_BUCKETS = (0, 25, 50, 75, 100)

# Landlord response window: 72 hours from the moment the dispute is opened.
RESPONSE_WINDOW_SECONDS = 72 * 60 * 60


def _usd(cents: int) -> str:
    return "$" + format(cents // 100, ",") + "." + str(cents % 100).zfill(2)


def _now_ts() -> int:
    # Transaction timestamp (deterministic across validators).
    raw = gl.message_raw["datetime"]
    dt = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return int(dt.timestamp())


def _fetch_evidence(urls: list) -> str:
    chunks = []
    for url in urls[:3]:
        try:
            text = gl.nondet.web.render(url, mode="text")
            chunks.append("SOURCE " + url + ":\n" + text[:2500])
        except Exception:
            chunks.append("SOURCE " + url + ": (could not be fetched)")
    return "\n\n".join(chunks) if chunks else "(no evidence URLs provided)"


def _nearest_bucket(pct: int) -> int:
    best = PERCENT_BUCKETS[0]
    for b in PERCENT_BUCKETS:
        if abs(b - pct) < abs(best - pct):
            best = b
    return best


class RentalDepositDispute(gl.Contract):
    """
    Intelligent Contract for resolving rental security deposit disputes (USD).

    Flow:
      1. Tenant opens a dispute (open_dispute). This starts an enforceable
         landlord response window (RESPONSE_WINDOW_SECONDS) with a stored
         response_deadline.
      2. During the window the landlord either:
           - responds with a deduction claim + evidence (landlord_respond), or
           - explicitly waives the right to respond (landlord_waive).
      3. resolve() is only allowed when ONE of these is true:
           - status is RESPONDED (landlord responded),
           - status is WAIVED (landlord explicitly waived),
           - status is OPEN and the response deadline has expired.
         A tenant can therefore never open and immediately resolve a dispute
         as if the landlord failed to respond.
      4. AI validators weigh the evidence and decide the split.

    All amounts are in USD cents (e.g. 150000 = $1,500.00).
    The settlement (refund and deduction in cents) is computed inside the
    non-deterministic block and validated with strict equality, so every
    validator must agree on the exact amounts, not just an approximate percent.
    Actual fund transfers happen off-contract based on the on-chain verdict.
    """

    owner: Address
    next_id: u256
    disputes: TreeMap[u256, str]  # id -> JSON string

    def __init__(self):
        self.owner = gl.message.sender_address
        self.next_id = u256(1)

    # ---------- helpers ----------

    def _sender(self) -> str:
        return str(gl.message.sender_address)

    def _load(self, dispute_id: int) -> dict:
        key = u256(dispute_id)
        if key not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        return json.loads(self.disputes[key])

    def _save(self, dispute_id: int, data: dict) -> None:
        self.disputes[u256(dispute_id)] = json.dumps(data)

    # ---------- write methods ----------

    @gl.public.write
    def open_dispute(
        self,
        landlord: str,
        property_desc: str,
        deposit_cents: int,
        tenancy_summary: str,
        tenant_claim: str,
        tenant_evidence_urls: list,
    ) -> int:
        if deposit_cents <= 0:
            raise gl.vm.UserError("Deposit must be greater than 0")
        if len(tenant_claim.strip()) == 0:
            raise gl.vm.UserError("Tenant claim is empty")

        now = _now_ts()
        dispute_id = int(self.next_id)
        data = {
            "id": dispute_id,
            "tenant": self._sender(),
            "landlord": str(Address(landlord)),
            "property": property_desc[:300],
            "deposit_cents": deposit_cents,
            "currency": "USD",
            "tenancy_summary": tenancy_summary[:1000],
            "tenant_claim": tenant_claim[:2000],
            "tenant_evidence": tenant_evidence_urls[:3],
            "landlord_claim": "",
            "landlord_deduction_cents": 0,
            "landlord_evidence": [],
            "landlord_waived": False,
            "opened_at": now,
            "response_deadline": now + RESPONSE_WINDOW_SECONDS,
            "status": "OPEN",
            "verdict": None,
        }
        self._save(dispute_id, data)
        self.next_id = u256(dispute_id + 1)
        return dispute_id

    @gl.public.write
    def landlord_respond(
        self,
        dispute_id: int,
        landlord_claim: str,
        deduction_cents: int,
        landlord_evidence_urls: list,
    ) -> None:
        data = self._load(dispute_id)
        if self._sender() != data["landlord"]:
            raise gl.vm.UserError("Only the landlord can respond")
        if data["status"] != "OPEN":
            raise gl.vm.UserError("Dispute is not OPEN")
        if _now_ts() >= int(data["response_deadline"]):
            raise gl.vm.UserError("Landlord response window has expired")
        if deduction_cents < 0 or deduction_cents > data["deposit_cents"]:
            raise gl.vm.UserError("Deduction must be between 0 and the deposit amount")

        data["landlord_claim"] = landlord_claim[:2000]
        data["landlord_deduction_cents"] = deduction_cents
        data["landlord_evidence"] = landlord_evidence_urls[:3]
        data["status"] = "RESPONDED"
        self._save(dispute_id, data)

    @gl.public.write
    def landlord_waive(self, dispute_id: int) -> None:
        """Landlord explicitly waives the right to respond (claims no deduction)."""
        data = self._load(dispute_id)
        if self._sender() != data["landlord"]:
            raise gl.vm.UserError("Only the landlord can waive the response")
        if data["status"] != "OPEN":
            raise gl.vm.UserError("Dispute is not OPEN")

        data["landlord_waived"] = True
        data["landlord_deduction_cents"] = 0
        data["status"] = "WAIVED"
        self._save(dispute_id, data)

    @gl.public.write
    def resolve(self, dispute_id: int) -> str:
        data = self._load(dispute_id)
        sender = self._sender()
        if sender not in (data["tenant"], data["landlord"], str(self.owner)):
            raise gl.vm.UserError("Not a party to this dispute")
        if data["status"] == "RESOLVED":
            raise gl.vm.UserError("Dispute already resolved")

        # Enforce the landlord response opportunity: an OPEN dispute can only
        # be resolved after the response deadline has expired. RESPONDED and
        # WAIVED disputes may be resolved right away.
        if data["status"] == "OPEN":
            if _now_ts() < int(data["response_deadline"]):
                raise gl.vm.UserError(
                    "Landlord response window is still open; cannot resolve yet"
                )

        snapshot = dict(data)

        def run_judgement() -> str:
            tenant_ev = _fetch_evidence(snapshot["tenant_evidence"])
            landlord_ev = _fetch_evidence(snapshot["landlord_evidence"])
            responded = snapshot["status"] == "RESPONDED"
            waived = snapshot["status"] == "WAIVED"
            deposit = int(snapshot["deposit_cents"])
            max_deduction = int(snapshot["landlord_deduction_cents"])
            deposit_str = _usd(deposit)
            deduction_str = _usd(max_deduction)

            if responded:
                landlord_status = "The landlord responded within the response window."
            elif waived:
                landlord_status = "The landlord explicitly waived the right to respond and claims no deduction."
            else:
                landlord_status = "The landlord did not respond before the response deadline expired."

            prompt = f"""
You are a neutral mediator for a rental security deposit dispute. Judge based on evidence, not emotion.
Principles: damage beyond normal wear and tear may be deducted at reasonable repair cost;
normal wear and tear, adequately clean condition, and unsupported claims must not be deducted.
If the landlord did not respond or waived, judge on the tenant's claim and evidence alone, with caution.
The landlord's deduction can never exceed the amount they requested.

PROPERTY: {snapshot["property"]}
DEPOSIT AMOUNT: {deposit_str}
TENANCY SUMMARY: {snapshot["tenancy_summary"]}

TENANT CLAIM: {snapshot["tenant_claim"]}
TENANT EVIDENCE:
{tenant_ev}

LANDLORD STATUS: {landlord_status}
LANDLORD CLAIM: {snapshot["landlord_claim"]}
DEDUCTION REQUESTED BY LANDLORD: {deduction_str}
LANDLORD EVIDENCE:
{landlord_ev}

Decide what share of the landlord's requested deduction ({deduction_str}) is justified by the evidence.
Use exactly one of these values for justified_percent:
0 = no part of the claimed deduction is justified (normal wear, unsupported, or no landlord claim)
50 = some claimed items are justified and others are not
100 = the whole claimed deduction is justified
(25 and 75 only if clearly between those anchors.)
If the landlord did not respond or waived, justified_percent must be 0.

Reply ONLY with valid JSON in exactly this format:
{{"justified_percent": <0|25|50|75|100>}}
"""
            result = gl.nondet.exec_prompt(prompt, response_format="json")

            try:
                raw_pct = int(result.get("justified_percent", 0))
            except Exception:
                raw_pct = 0
            pct = _nearest_bucket(max(0, min(100, raw_pct)))
            if not responded:
                pct = 0

            # Deterministic settlement math, done inside the consensus block.
            # The deduction is a bucketed share of the landlord's request, so
            # it can never exceed the requested amount.
            deduction = (max_deduction * pct) // 100
            refund = deposit - deduction

            # Reason code is derived from the bucket, not free-form LLM output,
            # so validators cannot diverge on it.
            if not responded:
                code = "NO_LANDLORD_RESPONSE"
            elif pct == 0:
                code = "UNSUPPORTED_CLAIM"
            elif pct == 100:
                code = "DAMAGE_PROVEN"
            else:
                code = "DAMAGE_PARTIAL"

            if refund == deposit:
                winner = "tenant"
            elif deduction >= max_deduction and max_deduction > 0:
                winner = "landlord"
            else:
                winner = "split"

            return json.dumps(
                {
                    "tenant_refund_percent": (refund * 100) // deposit,
                    "tenant_refund_cents": refund,
                    "landlord_deduction_cents": deduction,
                    "winner": winner,
                    "reason_code": code,
                },
                sort_keys=True,
            )

        # Strict equality: validators must produce the exact same settlement
        # amounts (refund and deduction in cents), winner and reason code.
        raw = gl.eq_principle.strict_eq(run_judgement)

        verdict = json.loads(raw)
        refund = int(verdict["tenant_refund_cents"])
        deduction = int(verdict["landlord_deduction_cents"])

        data["verdict"] = {
            "tenant_refund_percent": int(verdict["tenant_refund_percent"]),
            "tenant_refund_cents": refund,
            "landlord_deduction_cents": deduction,
            "tenant_refund_usd": _usd(refund),
            "landlord_deduction_usd": _usd(deduction),
            "winner": verdict["winner"],
            "reason_code": verdict["reason_code"],
        }
        data["status"] = "RESOLVED"
        self._save(dispute_id, data)
        return json.dumps(data["verdict"])

    # ---------- view methods ----------

    @gl.public.view
    def get_dispute(self, dispute_id: int) -> str:
        return json.dumps(self._load(dispute_id))

    @gl.public.view
    def get_dispute_count(self) -> int:
        return int(self.next_id) - 1

    @gl.public.view
    def get_status(self, dispute_id: int) -> str:
        return self._load(dispute_id)["status"]

    @gl.public.view
    def get_response_deadline(self, dispute_id: int) -> int:
        return int(self._load(dispute_id)["response_deadline"])

    @gl.public.view
    def get_verdict(self, dispute_id: int) -> str:
        data = self._load(dispute_id)
        if data["verdict"] is None:
            return "{}"
        return json.dumps(data["verdict"])
