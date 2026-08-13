# Company Ops Data — pipeline & property contract

**Portal:** `246897735` (build portal — **not** the main LH2 portal `246754894` that
`context.md` describes). Pipeline **Company Ops Data**, id `2464812771`.
Source of truth for the flow: **`Company_Ops_SOP_flowchart_v3.png`**
(v1 `OPSDATA_SOP_GoogleDocs_flowchart1.png` and v2
`Company Ops_SOP_flowchart_v2.png` are history; v1→v2 was applied by
`pipeline_v2_update.py` + `deals_v2_migrate.py`, v2→v3 by
`pipeline_v3_update.py`, with the deal move in `deals_v3_migrate.py`,
applied 2026-08-13 as part of the OutFlo bucket fix).
Synced by `opsdata/pipeline_sync.py` and `opsdata/properties_sync.py` (dry-run by
default, `--apply` to write, audit JSON lands in `audit/`).

**Never hardcode a stage ID.** Resolve from the deal's own `pipeline` value:

```python
s, d = hs("/crm/v3/pipelines/deals")
STAGE = {p["id"]: {st["label"]: st["id"] for st in p["stages"]} for p in d["results"]}
```

## Design decisions (deliberate — don't "fix" these)

- **The outcome IS the stage.** `Begin Here` and `Profile PreScreen` existed as
  stages from an earlier build and were removed (0 deals held them). The start
  marker and the decision diamond are not states; a stage that means "we are
  judging" is the same mistake as the retired `Call Attempted`.
- **Two entry branches, no pre-contact stage (v2).** Leads enter at
  `Cold LinkedIn Sent` **or** `Email Campaign Sent` — a lead exists in the CRM
  only once outreach is out. v1's `Cold Lead` entry stage and the prescreen
  outcome `Dead/Cold/WrongFit` were deleted in the v2 restructure (verified
  unused across every deal's stage history first).
- **Renaming a stage: use per-stage PATCH, never the pipeline PUT.** The v3
  pipeline PUT matches stages **by label** — a rename via PUT silently becomes
  delete + create with a NEW stage id (observed in the v2 restructure:
  `GMeet Fixed` → `Discovery Call` got a fresh id). Harmless only because the
  renamed stages held zero deals and zero history references. A stage that
  holds deals must be renamed with
  `PATCH /crm/v3/pipelines/deals/{pipelineId}/stages/{stageId}`.
  Reordering, however, must still be one atomic PUT (sequential PATCHes
  re-sequence wrongly — main-portal lesson).
- **Price lives in `cost`** ("Deal Cost (USD)") because the flowchart's Payment
  Initiation gate is literally "Deal Cost ($)". This flow never writes `amount`.
  (The main portal has an unreconciled `amount`-vs-`cost` conflict; here we pick
  one and stick to it.)
- **Closed/Won emails (client + internal) are manual.** Hard rule: no automated
  outbound mail except the sanctioned 18:30 IST daily report.
- **Metrics are sets of stages, computed from stage history**
  (`propertiesWithHistory=dealstage`), never counters. Activity metrics filter
  `sourceType == "CRM_UI"`; procurement metrics ignore `sourceType`.

## Live stages (v3, live as of 2026-08-13)

v3 (applied by `pipeline_v3_update.py`) added LinkedIn Connected and the One
Pager sub-flow and renamed Message Back → LinkedIn Follow-Up, One Pager + Deck
Shared → One Pager Shared. The pipeline can also drift via the HubSpot UI, so
**before any pipeline write, re-pull the live pipeline and diff** — a shape
script is only truth after its dry-run shows zero changes against live.

| # | Stage | Prob | Meaning (state, not activity) | Gate — set when entering |
|---|---|---|---|---|
| 0 | Cold LinkedIn Sent | 0.03 | LinkedIn-branch entry: connect request / msg 1 out (OutFlo "leads processed") | `li_msg1_date` |
| 1 | LinkedIn Connected | 0.05 | They accepted the connect (OutFlo "Connected") | — |
| 2 | LinkedIn Follow-Up | 0.08 | Connected, no reply +1 d; follow-up out (ex "Message Back (Email + 2nd Msg)") | `li_msg2_date` |
| 3 | Email Campaign Sent | 0.05 | Email-branch entry: campaign mail out | `email_sent_at`, `email_campaign` |
| 4 | Email Follow-Up | 0.08 | Email branch, no reply +1 d; follow-up mail out | — |
| 5 | Replied | 0.12 | They answered (either branch) | `replied_at` |
| 6 | Ghost Follow-Up | 0.12 | Agreed, then went quiet; follow-up out | — |
| 7 | Discovery Call | 0.25 | Meeting booked. **Handover: analyst → Pod Lead** | `gmeet1_link`, `gmeet1_date` |
| 8 | One Pager Requested | 0.30 | They asked for the one-pager | — |
| 9 | One Pager Follow-Up | 0.30 | One-pager promised, chasing | — |
| 10 | One Pager Shared | 0.35 | Collateral sent (ex "One Pager + Deck Shared") | `one_pager_sent_date`, `gmeet1_outcome` |
| 11 | Internal Evaluation (Sample) | 0.40 | We are judging their ops data on paper | `ops_data_types`, `systems_of_record`, `internal_eval_result` |
| 12 | Samples Requested | 0.45 | Good fit; asked for a sample | `sample_requested_date` |
| 13 | Sample Follow-Up | 0.45 | Nothing yet; email + call-back, 1–2 d | — |
| 14 | Sample Received + Quality Check | 0.55 | Sample in hand; judged on quality. **Handover: Pod Lead → Pod Head** | `sample_quality_score`, `sample_format`, `sample_pii_flags` |
| 15 | Commercial Negotiations | 0.65 | Quality good; talking price | `deal_value_range` |
| 16 | Deal Contract Signed | 0.80 | Signed, incl. DPA / scrub plan | `data_delivery_timeline` |
| 17 | Token Amount Paid | 0.85 | Token paid **after signing** (v2 moved this from pre-negotiation) | `token_amount`, `token_paid_date` |
| 18 | Data Migration Done | 0.90 | Assets transferred | `migration_volume`, `migration_record_count`, `number_of_datasets` |
| 19 | Payment Initiation | 0.95 | Paying the balance | `cost` |
| 20 | Closed/Won | 1.0 | Done. Client + internal email — **sent manually** | — |

(One extra dead stage arrived with the One Pager sub-flow:
`Dead/One Pager Not Shared`.)

Roles by LH2 names: **Lead Manager** = Pod Lead, **Lead Closer** = Pod Head.
Never put an individual's name in this document — people rotate.

## Dead stages — `Dead/<where it died>/<why>`, probability 0.0

| # | Stage | Use when |
|---|---|---|
| 18 | Dead/Cold/Not Interested | Replied, said no |
| 19 | Dead/Cold/No Reply | All outreach in the branch went silent (either branch) |
| 20 | Dead/Interested/No Show | Discovery Call outcome: wrong fit or they never showed |
| 21 | Dead/Discovery Call/Privacy Concerns | Call happened; they balked at sharing ops data |
| 22 | Dead/Sample Not Collected/Wrong Fit-Rejected | Internal evaluation said wrong fit — sample never requested |
| 23 | Dead/Sample Not Received/Company No Show | Sample requested + follow-up; nothing arrived |
| 24 | Dead/Sample/Bad Quality | Sample received but quality check failed |
| 25 | Dead/Negotiations/Pricing | Price gap |
| 26 | Dead/Negotiations/Contractual | Contract terms failed |
| 27 | Dead/Migration/Failed | Contract signed but delivery failed |

Removed in v2 (deleted after verifying zero deals and zero history references):
`Dead/Cold/WrongFit` (no prescreen stage anymore) and
`Dead/No Interest from Demand/Wrong Fit-Rejected` (the demand gauge became a
quality check; its failure outcome is `Dead/Sample/Bad Quality`).

The middle segment is the stage it died *at* — per-stage survival is computed
from these, no separate lost-reason field exists or should exist.

## Property contract (group `opsdata_procurement`)

Greenfield properties get their meaning here; if you change a meaning, change it
here first.

| Property | Type | Definition |
|---|---|---|
| `li_msg1_date` | date | Date LinkedIn msg 1 sent (LinkedIn branch) |
| `li_msg2_date` | date | Date msg 2 + email sent (LinkedIn branch only — email-branch deals use `email_sent_at`) |
| `replied_at` | datetime | First substantive reply from the lead |
| `gmeet1_date` / `gmeet1_link` | datetime / text | First meeting slot + link |
| `gmeet1_outcome` | enum: Proceeds, Wrong Fit, No Show, Privacy Concerns | Maps 1:1 to the post-GMeet branches |
| `one_pager_sent_date` | date | Date one-pager + deck shared |
| `ops_data_types` | multi-checkbox (SOPs & playbooks, Process docs, Internal tooling / workflows, CRM records, Support tickets, Transactions, Logistics / delivery, Inventory, HR / org structure, Vendor / supplier, Financial ops) | What kinds of ops data they hold |
| `systems_of_record` | text | Where it lives (tools/systems, comma-separated) |
| `internal_eval_result` | enum: Good Fit, Wrong Fit - Rejected, Pending | Paper-evaluation verdict |
| `sample_requested_date` | date | Date sample was requested |
| `sample_quality_score` | number | 0–10, judged on the received sample |
| `sample_format` | enum: CSV, SQL dump, API export, Excel, PDF / Docs, Mixed | Dominant sample format |
| `sample_pii_flags` | multi-checkbox (None, Emails, Phone numbers, Addresses, Financial, Govt IDs (PAN / Aadhaar), Health, Other) | PII present in sample — drives the scrub plan in the DPA |
| `token_amount` / `token_paid_date` | number / date | Token payment (USD) + date paid |
| `deal_value_range` | text | Indicative range before a price exists |
| `data_delivery_timeline` | date | Delivery deadline agreed at contract signing |
| `migration_volume` | text | Human-readable size of transferred assets, e.g. "4.2 GB" |
| `migration_record_count` | number | Total records across all delivered datasets |
| `number_of_datasets` | number | Count of distinct datasets delivered |
| `dataset_customization_required` | enum: Yes, No | Whether data needs custom scrub/transform before reuse |
| `cost` | number | Final deal cost in USD — the price this flow writes |

Enum discipline: writing a value that is not an option fails with
`INVALID_OPTION`. Read the property, PATCH the new option in, then write.

## OutFlo import (opsdata/outflo_pull_push.py)

Leads come from **every ACTIVE OutFlo campaign whose name contains "company
ops"** (`live.outflo.in` API, `x-api-key` auth; key `OUTFLO_API_KEY` in `.env`).
Campaigns rotate — Pilot became Company Ops_India, then geo campaigns
(UAE/Singapore), then Company Ops_India _All Industries; inactive campaigns
drop out of sync scope, so stage fixes for their already-imported deals need
one-off migrations (`deals_v3_migrate.py` was one — 77 deals, 2026-08-13).

Bucket mapping (fixed 2026-08-13): OutFlo **Request Sent** ("leads processed")
→ `Cold LinkedIn Sent`; **Connected** → `LinkedIn Connected`; **Replied** →
`Replied`. Stage climbs that ladder only, never down. "Checking" / "Failed"
leads stay in OutFlo.

- One **deal per company** (deal name = company), one contact per person,
  associated; multi-founder companies share one deal.
- All deals + contacts owned by **Kartik Pillai** (owner id `96316911`, the
  only seat in this portal). The actual OutFlo sender is kept in
  `outflo_assigned_account` (Anu Meena / Kartik Pillai) so deals can be
  re-split if Anu gets a seat.
- Provenance frozen at import: `lead_source` = `Outflo Outreach ( Startups )`,
  `outflo_status` (Request Sent/Connected/Replied), `outflo_campaign`,
  `outflo_lead_id`, `outflo_last_action_at`.
- `replied_at` = timestamp of the last conversation message *when the lead sent
  it*, else OutFlo's Last Action At (approximation, documented).
- Idempotent: matched by `outflo_lead_id` then normalized deal name; stage only
  ever climbs `Cold LinkedIn Sent` → `LinkedIn Connected` → `Replied`; deals a
  human moved anywhere else are never touched. Re-run any time.
- No phones are pushed: OutFlo carries none, and unverified numbers are banned.

## Gmail cold-email import (opsdata/gmail_pull_push.py)

Kartik's mailbox (kartik.pillai@lh2.ai) runs a cold-email campaign; OAuth is
read-only (`gmail.readonly`, token in `.gmail_token.json`, consent via
`opsdata/gmail_auth.py`). **A campaign mail is any SENT message whose body
contains a Calendly link** — subjects drifted, the link didn't.

- One deal per recipient **domain**; company name derived from the domain
  (renaming deals in HubSpot is safe — dedupe runs on `lh2_domain`, not name).
- Everyone → `Email Campaign Sent` (v2 email-branch entry) with `email_sent_at`;
  a real human reply → `Replied` with `replied_at` from the thread. Auto-replies
  (OOO) and postmaster mail never count as replies; bounces get `email_status`
  = Bounced but stay live — the SOP has no bounce outcome, a human decides.
  (v1 pushed these to `Message Back (Email + 2nd Msg)` with `li_msg2_date`;
  `deals_v2_migrate.py` moved all 125 and cleared that date — under v2 both
  belong to the LinkedIn branch only.)
- Provenance: `lead_source` = `Cold Email ( Company Ops )`, `email_status`
  (Awaiting Reply / Replied / Bounced), `email_campaign` (subject),
  `email_sent_at`, `lh2_domain`.
- A company already present from the OutFlo track gets the email contact
  associated + email provenance stamped; its stage is untouched (Workline case).
- Idempotent; stage only promotes `Email Campaign Sent` / `Email Follow-Up`
  → `Replied`. Safe to re-run daily; each full run re-scans the mailbox
  (~5–10 min).

## Known gaps / notes

- **Deal merge produces a NEW object id.** `POST /crm/v3/objects/deals/merge`
  absorbs both records into a fresh id; the old primary id soft-redirects on
  v3 `GET`/`PATCH` but NOT on every v4 endpoint (association listing on the
  old id silently returns empty). After a merge, re-resolve the deal by search
  before reading associations. (Learned merging the DoubleTick /
  DoubleTick.io duplicate — same company, two founders, one deal.)

- `lead_source` in this portal is a plain **text** property, not the 5-option
  enum the main portal uses. Convert before any cross-portal reporting.
- Portal timezone is **US/Eastern**; LH2 reporting days are IST with an
  18:30 IST cutoff — compute day boundaries in code, don't trust portal-local dates.
- Any lead-loading script must gate phones through the shared Indian-number
  validator (`indian_number.py` in the main repo): no Indian mobile, no push.
