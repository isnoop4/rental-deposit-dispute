# Rental Deposit Dispute

An **Intelligent Contract on [GenLayer](https://www.genlayer.com/)** that resolves rental security deposit disputes between a tenant and a landlord. Instead of relying on a human arbitrator, a set of AI validators reads both sides' claims and evidence, then reaches consensus on how the deposit should be split.

- **Contract:** [`rental_deposit_dispute.py`](./rental_deposit_dispute.py)
- **Deployed (GenLayer Studio):** [`0x8d78439196096A9DC3bB7b4AB5aF32C266c314b6`](https://explorer-studio.genlayer.com/address/0x8d78439196096A9DC3bB7b4AB5aF32C266c314b6)
- **Currency:** USD, stored as integer cents (`150000` = `$1,500.00`)

---

## Why

Security deposit disputes are common, slow, and subjective: "normal wear and tear" versus "damage" is a judgment call, and the evidence is usually scattered across photos, inspection reports, and quotes. This contract turns that judgment call into a transparent, on-chain process:

1. The tenant opens a dispute, which starts an enforceable landlord response window.
2. The landlord responds with a claim and evidence, or explicitly waives the right to respond.
3. GenLayer validators fetch the evidence and each run an LLM judgment.
4. Validators must agree on the exact settlement amounts before a verdict is finalized.
5. The verdict is stored on-chain and can be used to release funds.

---

## How it works

```
Tenant                         Landlord                      Validators (AI)
  |                               |                               |
  | open_dispute(...)             |                               |
  |------------------------------>|                               |
  |   status: OPEN                |                               |
  |   response_deadline = now + 72h                               |
  |                               |                               |
  |            landlord_respond(...)  OR  landlord_waive()        |
  |                               |                               |
  |   status: RESPONDED  /  WAIVED  (or deadline expires)         |
  |                                                               |
  | resolve(dispute_id)  (tenant, landlord, or owner)             |
  |-------------------------------------------------------------->|
  |                  fetch evidence + LLM judgment                |
  |                  consensus (strict_eq on settlement)          |
  |<--------------------------------------------------------------|
  |                      status: RESOLVED + verdict               |
```

### Landlord response window

When a dispute is opened, the contract stores a `response_deadline` (**72 hours** after opening, set by `RESPONSE_WINDOW_SECONDS`). `resolve` is **rejected** for an `OPEN` dispute until one of the following is true:

- the landlord has responded (`RESPONDED`),
- the landlord has explicitly waived the response (`WAIVED`), or
- the response deadline has expired.

A tenant therefore can never open a dispute and immediately resolve it as though the landlord failed to respond. After the deadline passes, `landlord_respond` is also rejected, so a late response cannot change a result the tenant is already entitled to ask for.

### Dispute lifecycle

| Status      | Meaning                                                                               |
|-------------|---------------------------------------------------------------------------------------|
| `OPEN`      | Dispute opened; landlord response window is running. Cannot be resolved until expiry. |
| `RESPONDED` | Landlord submitted a deduction claim and evidence. Can be resolved immediately.       |
| `WAIVED`    | Landlord explicitly waived the response (no deduction). Can be resolved immediately.  |
| `RESOLVED`  | Validators reached consensus and a verdict is stored.                                 |

If the landlord never responds, the dispute can be resolved **after the deadline** with the landlord's deduction capped at `0`.

---

## Contract API

### Write methods

| Method | Caller | Description |
|--------|--------|-------------|
| `open_dispute(landlord, property_desc, deposit_cents, tenancy_summary, tenant_claim, tenant_evidence_urls)` | Tenant | Opens a dispute, stores `response_deadline`, and returns its `dispute_id`. Up to 3 evidence URLs are used. |
| `landlord_respond(dispute_id, landlord_claim, deduction_cents, landlord_evidence_urls)` | Landlord only | Submits the landlord's claim before the deadline. `deduction_cents` must be between `0` and the deposit. |
| `landlord_waive(dispute_id)` | Landlord only | Explicitly waives the right to respond. Sets the deduction to `0` and status to `WAIVED`. |
| `resolve(dispute_id)` | Tenant, landlord, or contract owner | Allowed only when `RESPONDED`, `WAIVED`, or `OPEN` past the deadline. Runs AI judgment and stores the verdict. |

### Read methods

| Method | Returns |
|--------|---------|
| `get_dispute(dispute_id)` | Full dispute record as JSON |
| `get_status(dispute_id)` | `OPEN`, `RESPONDED`, `WAIVED`, or `RESOLVED` |
| `get_response_deadline(dispute_id)` | Response deadline as a Unix timestamp (seconds) |
| `get_verdict(dispute_id)` | Verdict JSON, or `{}` if not resolved yet |
| `get_dispute_count()` | Total number of disputes |

### Verdict format

```json
{
  "tenant_refund_percent": 86,
  "tenant_refund_cents": 130000,
  "landlord_deduction_cents": 20000,
  "tenant_refund_usd": "$1,300.00",
  "landlord_deduction_usd": "$200.00",
  "winner": "split",
  "reason_code": "DAMAGE_PARTIAL"
}
```

`winner` is one of `tenant`, `landlord`, or `split`. `tenant_refund_percent` is informational (rounded down); the binding values are the amounts in cents.

---

## Design notes

- **Evidence-based judgment.** Validators fetch up to 3 URLs per party (first 2,500 characters each) using `gl.nondet.web.render`. Claims without supporting evidence are weighed cautiously.
- **Settlement bound by consensus.** Each validator chooses a single bucketed value, `justified_percent` (one of `0, 25, 50, 75, 100`): the share of the landlord's requested deduction that the evidence justifies. The settlement is then computed **deterministically inside the non-deterministic block**:
  - `deduction = requested_deduction * justified_percent // 100`
  - `refund = deposit - deduction`
  The result is validated with `gl.eq_principle.strict_eq`, so every validator must agree on the **exact** refund and deduction in cents, not an approximate percentage.
- **Derived reason code.** `reason_code` is derived from the bucket and the landlord's status (`NO_LANDLORD_RESPONSE`, `UNSUPPORTED_CLAIM`, `DAMAGE_PARTIAL`, `DAMAGE_PROVEN`), not generated as free text, so validators cannot diverge on it.
- **Deduction cap by construction.** The deduction is a share of the landlord's own request, so it can never exceed the requested amount, and it is `0` if the landlord never responded or waived.
- **Integer math only.** All amounts are integer cents.
- **Deterministic time.** The deadline uses the transaction timestamp (`gl.message_raw["datetime"]`), so all validators see the same time.
- **Access control.** Only the landlord can respond or waive; only the tenant, landlord, or owner can resolve; a dispute can only be resolved once.
- **Settlement is off-contract.** The contract records the verdict (refund and deduction amounts). It does not hold or transfer funds. Use the on-chain verdict to drive payouts in an escrow or application layer.

---

## Quick start (GenLayer Studio)

1. Open [GenLayer Studio](https://studio.genlayer.com/) and create a new file.
2. Paste the contents of `rental_deposit_dispute_en.py`. The first line must stay as is:
   ```python
   # { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
   ```
3. Click **Deploy new instance**.
4. Use two different accounts, one for the tenant and one for the landlord.

### Example walkthrough

**1. Tenant opens the dispute** (`open_dispute`)

| Parameter | Example |
|-----------|---------|
| `landlord` | `0x...` (landlord's address) |
| `property_desc` | `2BR apartment, Jakarta` |
| `deposit_cents` | `150000` |
| `tenancy_summary` | `12-month lease, tenant moved out leaving the unit clean.` |
| `tenant_claim` | `Unit was left clean; only normal wear and tear. Requesting full deposit refund.` |
| `tenant_evidence_urls` | `["https://gist.githubusercontent.com/.../tenant_evidence.txt"]` |

Check `get_status(1)` (`OPEN`) and `get_response_deadline(1)`. Calling `resolve(1)` now reverts with *"Landlord response window is still open; cannot resolve yet"*.

**2a. Landlord responds** (`landlord_respond`, from the landlord's account)

| Parameter | Example |
|-----------|---------|
| `dispute_id` | `1` |
| `landlord_claim` | `Nail holes in walls and carpet stain, repair cost $400.` |
| `deduction_cents` | `40000` |
| `landlord_evidence_urls` | `["https://gist.githubusercontent.com/.../landlord_evidence.txt"]` |

**2b. Or the landlord waives** (`landlord_waive` with `dispute_id` = `1`, from the landlord's account). The result is a full refund to the tenant.

**3. Resolve** (`resolve` with `dispute_id` = `1`), then read the result with `get_verdict`.

> Evidence URLs must be passed as a JSON array of strings, and should point to **raw, publicly readable text** (for example a raw GitHub Gist link), so validators can read the content.

---

## Tested scenarios

All paths were tested in GenLayer Studio:

- `resolve` right after `open_dispute`: reverts (response window still open)
- `landlord_respond` from a non-landlord account: reverts (`Only the landlord can respond`)
- Landlord responds, then `resolve`: consensus reached, split verdict (landlord requested `$400`, verdict `$200` deduction and `$1,300` refund, total `$1,500`)
- Landlord waives, then `resolve`: full refund to the tenant
- Response deadline expired with no response, then `resolve`: resolves with `NO_LANDLORD_RESPONSE` (tested on a temporary instance with a 60-second window; the main deployment uses 72 hours)

---

## Limitations

- No native token custody or transfer; this contract is the decision layer.
- The response window is fixed at deployment (72 hours); it is not configurable per dispute.
- Evidence is limited to 3 URLs per party and the first 2,500 characters of each page.
- Because validators must agree exactly, a transaction can occasionally need leader rotations or a retry before it is accepted.
- Verdicts come from LLM judgment and are not legal advice.

---

## Ideas for next steps

- Escrow integration that locks the deposit and releases it automatically from the verdict
- Configurable response window per dispute
- Appeal round with a larger validator set
- Support for multiple currencies and image evidence

---

## License

MIT
