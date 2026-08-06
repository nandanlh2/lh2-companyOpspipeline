# Company Ops Data — pipeline & property contract

**Portal:** `246897735` (build portal — **not** the main LH2 portal `246754894` that
`context.md` describes). Pipeline **Company Ops Data**, id `2464812771`.
Source of truth for the flow: `OPSDATA_SOP_GoogleDocs_flowchart1.png`.
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
  judging" is the same mistake as the retired `Call Attempted`. The entry state
  is `Cold Lead`; the prescreen's *outcomes* are `Cold LinkedIn Sent` (good fit)
  or `Dead/Cold/WrongFit` (screened out, never contacted).
- **Price lives in `cost`** ("Deal Cost (USD)") because the flowchart's Payment
  Initiation gate is literally "Deal Cost ($)". This flow never writes `amount`.
  (The main portal has an unreconciled `amount`-vs-`cost` conflict; here we pick
  one and stick to it.)
- **Closed/Won emails (client + internal) are manual.** Hard rule: no automated
  outbound mail except the sanctioned 18:30 IST daily report.
- **Metrics are sets of stages, computed from stage history**
  (`propertiesWithHistory=dealstage`), never counters. Activity metrics filter
  `sourceType == "CRM_UI"`; procurement metrics ignore `sourceType`.

## Live stages

| # | Stage | Prob | Meaning (state, not activity) | Gate — set when entering |
|---|---|---|---|---|
| 0 | Cold Lead | 0.02 | Lead loaded, PoC = analyst; not yet screened | — |
| 1 | Cold LinkedIn Sent | 0.04 | Prescreen passed, LinkedIn msg 1 out | `li_msg1_date` |
| 2 | Message Back (Email + 2nd Msg) | 0.06 | No reply in 1–2 d; msg 2 + cold email out | `li_msg2_date` |
| 3 | Replied | 0.10 | They answered | `replied_at` |
| 4 | Ghost Follow-Up | 0.08 | Agreed, then went quiet; follow-up out | — |
| 5 | GMeet Fixed | 0.20 | Meeting booked. **Handover: analyst → Pod Lead** | `gmeet1_link`, `gmeet1_date` |
| 6 | One Pager + Deck Shared | 0.30 | GMeet happened, proceeds; collateral sent | `one_pager_sent_date`, `gmeet1_outcome` |
| 7 | Internal Evaluation (Sample) | 0.35 | We are judging their ops data on paper | `ops_data_types`, `systems_of_record`, `internal_eval_result` |
| 8 | Samples Requested | 0.40 | Good fit; asked for a sample | `sample_requested_date` |
| 9 | Sample Follow-Up | 0.38 | Nothing yet; email + call-back, 1–2 d | — |
| 10 | Sample Received + Interest Gauge | 0.50 | Sample in hand; gauge vs DEMAND. **Handover: Pod Lead → Pod Head** | `sample_quality_score`, `sample_format`, `sample_pii_flags` |
| 11 | Token Amount Paid | 0.65 | We paid a token — skin in the game | `token_amount`, `token_paid_date` |
| 12 | Commercial Negotiations | 0.70 | Talking price | `deal_value_range` |
| 13 | Deal Contract Signed | 0.85 | Signed, incl. DPA / scrub plan | `data_delivery_timeline` |
| 14 | Data Migration Done | 0.90 | Assets transferred | `migration_volume`, `migration_record_count`, `number_of_datasets` |
| 15 | Payment Initiation | 0.95 | Paying the balance | `cost` |
| 16 | Closed/Won | 1.0 | Done. Client + internal email — **sent manually** | — |

Roles by LH2 names: **Lead Manager** = Pod Lead, **Lead Closer** = Pod Head.
Never put an individual's name in this document — people rotate.

## Dead stages — `Dead/<where it died>/<why>`, probability 0.0

| # | Stage | Use when |
|---|---|---|
| 17 | Dead/Cold/WrongFit | Failed profile prescreen — never contacted |
| 18 | Dead/Cold/Not Interested | Replied, said no |
| 19 | Dead/Cold/No Reply | Msg 1 + msg 2 + ghost follow-up all silent |
| 20 | Dead/Interested/No Show | GMeet outcome: wrong fit or they never showed |
| 21 | Dead/GMeet/Privacy Concerns | GMeet happened; they balked at sharing ops data |
| 22 | Dead/Sample Not Collected/Wrong Fit-Rejected | Internal evaluation said wrong fit — sample never requested |
| 23 | Dead/Sample Not Received/Company No Show | Sample requested + follow-up; nothing arrived |
| 24 | Dead/No Interest from Demand/Wrong Fit-Rejected | Sample gauged; demand side said no |
| 25 | Dead/Negotiations/Pricing | Price gap |
| 26 | Dead/Negotiations/Contractual | Contract terms failed |
| 27 | Dead/Migration/Failed | Contract signed but delivery failed |

The middle segment is the stage it died *at* — per-stage survival is computed
from these, no separate lost-reason field exists or should exist.

## Property contract (group `opsdata_procurement`)

Greenfield properties get their meaning here; if you change a meaning, change it
here first.

| Property | Type | Definition |
|---|---|---|
| `li_msg1_date` | date | Date LinkedIn msg 1 sent |
| `li_msg2_date` | date | Date msg 2 / cold email sent |
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

Leads come from the OutFlo campaign **Company Ops_Pilot** (`live.outflo.in`
API, `x-api-key` auth; key `OUTFLO_API_KEY` in `.env`). Only *outcomes* are
imported: Connected → `Cold LinkedIn Sent`, Replied → `Replied`. "Request Sent"
/ "Checking" leads stay in OutFlo — there is no stage for "we tried".

- One **deal per company** (deal name = company), one contact per person,
  associated; multi-founder companies share one deal.
- All deals + contacts owned by **Kartik Pillai** (owner id `96316911`, the
  only seat in this portal). The actual OutFlo sender is kept in
  `outflo_assigned_account` (Anu Meena / Kartik Pillai) so deals can be
  re-split if Anu gets a seat.
- Provenance frozen at import: `lead_source` = `Outflo Outreach ( Startups )`,
  `outflo_status` (Connected/Replied), `outflo_campaign`, `outflo_lead_id`,
  `outflo_last_action_at`.
- `replied_at` = timestamp of the last conversation message *when the lead sent
  it*, else OutFlo's Last Action At (approximation, documented).
- Idempotent: matched by `outflo_lead_id` then deal name; stage only ever
  promotes `Cold LinkedIn Sent` → `Replied`; deals a human moved further are
  never touched. Re-run any time to sync new connects/replies.
- No phones are pushed: OutFlo carries none, and unverified numbers are banned.

## Gmail cold-email import (opsdata/gmail_pull_push.py)

Kartik's mailbox (kartik.pillai@lh2.ai) runs a cold-email campaign; OAuth is
read-only (`gmail.readonly`, token in `.gmail_token.json`, consent via
`opsdata/gmail_auth.py`). **A campaign mail is any SENT message whose body
contains a Calendly link** — subjects drifted, the link didn't.

- One deal per recipient **domain**; company name derived from the domain
  (renaming deals in HubSpot is safe — dedupe runs on `lh2_domain`, not name).
- Everyone → `Message Back (Email + 2nd Msg)` with `li_msg2_date` = first-send
  date (IST) and `email_sent_at` exact; a real human reply → `Replied` with
  `replied_at` from the thread. Auto-replies (OOO) and postmaster mail never
  count as replies; bounces get `email_status` = Bounced but stay live — the
  SOP has no bounce outcome, a human decides.
- Provenance: `lead_source` = `Cold Email ( Company Ops )`, `email_status`
  (Awaiting Reply / Replied / Bounced), `email_campaign` (subject),
  `email_sent_at`, `lh2_domain`.
- A company already present from the OutFlo track gets the email contact
  associated + email provenance stamped; its stage is untouched (Workline case).
- Idempotent; stage only promotes `Message Back` → `Replied`. Safe to re-run
  daily; each full run re-scans the mailbox (~5–10 min).

## Known gaps / notes

- `lead_source` in this portal is a plain **text** property, not the 5-option
  enum the main portal uses. Convert before any cross-portal reporting.
- Portal timezone is **US/Eastern**; LH2 reporting days are IST with an
  18:30 IST cutoff — compute day boundaries in code, don't trust portal-local dates.
- Any lead-loading script must gate phones through the shared Indian-number
  validator (`indian_number.py` in the main repo): no Indian mobile, no push.
