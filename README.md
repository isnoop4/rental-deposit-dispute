# Rental Deposit Dispute

An **Intelligent Contract on [GenLayer](https://www.genlayer.com/)** that resolves rental security deposit disputes between a tenant and a landlord. Instead of relying on a human arbitrator, a set of AI validators reads both sides' claims and evidence, then reaches consensus on how the deposit should be split.

- **Contract:** [`rental_deposit_dispute_en.py`](./rental_deposit_dispute_en.py)
- **Deployed (GenLayer Studio):** [`0x97563633980304935BF25613e1332e3F943bfF7b`](https://explorer-studio.genlayer.com/address/0x97563633980304935BF25613e1332e3F943bfF7b)
- **Currency:** USD, stored as integer cents (`150000` = `$1,500.00`)

---

## Why

Security deposit disputes are common, slow, and subjective: "normal wear and tear" versus "damage" is a judgment call, and the evidence is usually scattered across photos, inspection reports, and quotes. This contract turns that judgment call into a transparent, on-chain process:

1. Both parties submit their claims and evidence links.
2. GenLayer validators fetch the evidence and each run an LLM judgment.
3. Validators must agree (within a tolerance) before a verdict is finalized.
4. The verdict is stored on-chain and can be used to release funds.

---

## How it works

```
Tenant                         Landlord                      Validators (AI)
  |                               |                               |
  | open_dispute(...)             |                               |
  |------------------------------>|                               |
  |        status: OPEN           |                               |
  |                               | landlord_respond(...)         |
  |                               |------------------------------>|
  |                               |      status: RESPONDED        |
  |                                                               |
  | resolve(dispute_id)  (either party, or owner)                 |
  |-------------------------------------------------------------->|
  |                  fetch evidence + LLM judgment                |
  |                  consensus (prompt_comparative)               |
  |<--------------------------------------------------------------|
  |                      status: RESOLVED + verdict               |
```

### Dispute lifecycle

| Status      | Meaning                                                        |
|-------------|----------------------------------------------------------------|
| `OPEN`      | Tenant opened the dispute; waiting for the landlord's response |
| `RESPONDED` | Landlord submitted a deduction claim and evidence              |
| `RESOLVED`  | Validators reached consensus and a verdict is stored           |

If the landlord never responds, the **tenant** can still call `resolve`. The AI then judges on the tenant's claim and evidence alone, and the landlord's deduction is capped at `0`.

---

## Contract API

### Write methods

| Method | Caller | Description |
|--------|--------|-------------|
| `open_dispute(landlord, property_desc, deposit_cents, tenancy_summary, tenant_claim, tenant_evidence_urls)` | Tenant | Opens a dispute and returns its `dispute_id`. Up to 3 evidence URLs are used. |
| `landlord_respond(dispute_id, landlord_claim, deduction_cents, landlord_evidence_urls)` | Landlord only | Submits the landlord's claim. `deduction_cents` must be between `0` and the deposit. |
| `resolve(dispute_id)` | Tenant, landlord, or contract owner | Runs AI judgment and stores the verdict. Returns the verdict as JSON. |

### Read methods

| Method | Returns |
|--------|---------|
| `get_dispute(dispute_id)` | Full dispute record as JSON |
| `get_status(dispute_id)` | `OPEN`, `RESPONDED`, or `RESOLVED` |
| `get_verdict(dispute_id)` | Verdict JSON, or `{}` if not resolved yet |
| `get_dispute_count()` | Total number of disputes |

### Verdict format

```json
{
  "tenant_refund_percent": 73,
  "tenant_refund_cents": 109500,
  "landlord_deduction_cents": 40500,
  "tenant_refund_usd": "$1,095.00",
  "landlord_deduction_usd": "$405.00",
  "winner": "split",
  "reasoning": "Short explanation of the decision."
}
```

`winner` is one of `tenant`, `landlord`, or `split`.

---

## Design notes

- **Evidence-based judgment.** Validators fetch up to 3 URLs per party (first 2,500 characters each) using `gl.nondet.web.render`. Claims without supporting evidence are weighed cautiously, and a link with no real content (for example an empty page) is not treated as proof.
- **Consensus.** Verdicts use `gl.eq_principle.prompt_comparative`: validators must agree on the same `winner`, and `tenant_refund_percent` may differ by at most **15 points**. Because LLM outputs vary, you may see leader rotations before a transaction is accepted. This is expected behavior.
- **Deduction cap.** The landlord can never receive more than the amount they requested (`deduction_cents`), and receives `0` if they never responded. The refund and percentage are recalculated after the cap is applied.
- **Integer math only.** All amounts are integer cents. The refund is `deposit * percent // 100`, and the remainder is the deduction.
- **Access control.** Only the landlord can respond; only the tenant, landlord, or owner can resolve; a dispute can only be resolved once.
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
| `tenancy_summary` | `12-month lease, security deposit $1,500` |
| `tenant_claim` | `Unit returned clean, only minor wall scuffs from normal use. Landlord is withholding the full deposit.` |
| `tenant_evidence_urls` | `["https://gist.githubusercontent.com/.../tenant_evidence.txt"]` |

**2. Landlord responds** (`landlord_respond`, from the landlord's account)

| Parameter | Example |
|-----------|---------|
| `dispute_id` | `1` |
| `landlord_claim` | `Nail holes in walls and carpet stain, repair cost $400.` |
| `deduction_cents` | `40000` |
| `landlord_evidence_urls` | `["https://gist.githubusercontent.com/.../landlord_evidence.txt"]` |

**3. Resolve** (`resolve` with `dispute_id` = `1`), then read the result with `get_verdict`.

> Evidence URLs must be passed as a JSON array of strings, and should point to **raw, publicly readable text** (for example a raw GitHub Gist link), so validators can read the content.

---

## Tested scenarios

- Dispute with no real evidence: full refund to the tenant
- Dispute with placeholder links only: full refund to the tenant
- Dispute with detailed evidence from both sides: proportional **split** verdict
- Deduction cap: landlord deduction never exceeds the requested amount
- Access control and error paths: wrong caller, over-deposit deduction, double resolve

---

## Limitations

- No native token custody or transfer; this contract is the decision layer.
- No response deadline: if a landlord stays silent, the tenant must call `resolve` explicitly.
- Evidence is limited to 3 URLs per party and the first 2,500 characters of each page.
- Verdicts come from LLM judgment and are not legal advice.

---

## Ideas for next steps

- Escrow integration that locks the deposit and releases it automatically from the verdict
- Response deadlines and automatic default judgment
- Appeal round with a larger validator set
- Support for multiple currencies and image evidence

---

## License

MIT
