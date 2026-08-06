# LH2 — HubSpot operating context

**For:** an agent building a new procurement flow (company ops-data) in a separate project.
**Written:** 2026-08-06, from the live portal, not from memory.
**Portal:** `246754894`. Key is `hubspot_key` in `.env` (same key, same portal — a new folder does
not mean a new CRM).

Read this before writing a single API call. Most of it is the record of something that already
went wrong once.

---

## 1. What LH2 actually buys

LH2 acquires **assets from dying or dead Indian companies**, on two tracks:

| Track | What we buy | Who has it |
|---|---|---|
| **Codebase** | A real pre-2024 product codebase, still coherent | Small eng team, own product, not an agency |
| **Ops data** | The reusable *operating blueprint* — SOPs, playbooks, process, tooling, org design | Companies that ran at real scale |

**The ops-data track is the one your project serves.** Two things about it are easy to get wrong,
and both were corrected explicitly by the user:

- **Ops data is NOT consumer or product data.** It is not the user table. It is how the company
  *ran itself*. Do not build a flow that scores companies on how many users they had.
- **Operating scale is king, funding is irrelevant.** The `opsdata_fit` score weights peak
  headcount highest (0–35), then years operating (0–12), then real revenue (0–8), then a
  qualitative read of the know-how (0–30), plus a sector bonus for ops-intensive businesses
  (+9). **Funding is deliberately excluded** — money raised is not operations run.

Note the two tracks want *opposite* companies. `codebase_fit` favours a small clean eng team with
a real product. `opsdata_fit` favours a big operationally complex business. A company scoring high
on one usually scores low on the other. Scoring logic lives in `crm_mirror/rules/asset_fit_score.md`,
implemented in `crm_mirror/enrich/asset_fit.py`.

---

## 2. The design principle: the outcome IS the stage

This is the single most important rule in the CRM and the thing most likely to be violated by
someone designing a new pipeline.

**There is no stage that means "we tried".** Every stage is an *outcome*. A stage named
`Call Attempted` used to exist and was deliberately retired, because "attempted" is an activity,
not a result — it tells you nothing about what to do next and it double-counts against real
outcomes.

So when a call happens, the deal moves to exactly one of: `No Pickup`, `Interested`,
`Dead/ColdCall/Not Interested`, `Dead/ColdCall/WrongNumber`, or `Dead/ColdCall/WrongFit`. The
count of "calls attempted" is then *derived* by summing those outcomes — it is never stored.

Corollary for metrics: **a metric is a set of stages, not a flag.** See §7.

The retired stage is still on both pipelines as `Call Attempted (retired)` at displayOrder 99.
Do not write to it. Do not delete it either — deals' stage history references it.

---

## 3. The two pipelines

Identical stage *labels*, **completely different stage IDs**.

| Pipeline | ID |
|---|---|
| Scraped | `default` |
| Campaign | `2425754306` |

> **This has already caused a production incident.** A script wrote Scraped stage IDs to Campaign
> deals. All 13 writes failed with `INVALID_OPTION`, but the contacts had already been created, so
> the portal was left with 13 orphan contacts and no deals. **Always resolve the stage ID from the
> deal's own `pipeline` value.** Never hardcode a stage ID without the pipeline beside it.

Build the map once and index by pipeline:

```python
s, d = hs("/crm/v3/pipelines/deals")
STAGE = {p["id"]: {st["label"]: st["id"] for st in p["stages"]} for p in d["results"]}
LABEL = {st["id"]: st["label"] for p in d["results"] for st in p["stages"]}
# STAGE[deal["properties"]["pipeline"]]["Interested"]  <- correct
```

Which pipeline a lead belongs in is decided by **source**, not by segment:

- **Scraped** — anything our own scrapers or sheets produced (Scraping Algo, Tracxn, Private Codebase Tracker)
- **Campaign** — anything from an outbound or inbound campaign (OutFlo, LinkedIn)

### Live stage order (both pipelines, same order)

| # | Stage | Meaning |
|---|---|---|
| 0 | Cold Call | Never contacted |
| 1 | No Pickup | Rang out — see the warning in §8 |
| 2 | Interested | Said yes on the call |
| 3 | GMeet Fixed | Meeting booked |
| 4 | Script Shared | Our script sent, awaiting their run |
| 5 | Script Results Received | Output back from them |
| 6 | Commercial Negotiation | Talking price |
| 7 | Deal Contract Signed | Signed |
| 8 | Data Migration Done | Assets transferred |
| 9 | Metadata Matched | Verified against what was promised |
| 10 | Payment Initiation | Paying |
| 11 | Closed/Won | Done |

### Dead stages — 13 of them, and the shape matters

`Dead/<where it died>/<why>`. The middle segment is the stage it died *at*, which is what makes
the funnel analysable — you can compute survival per stage without a separate "lost reason" field.

```
Dead/ColdCall/Not Interested      Dead/GMeet/NoShow
Dead/ColdCall/WrongFit            Dead/GMeet/Cancelled
Dead/ColdCall/WrongNumber         Dead/GMeet/wrong fit
Dead/ColdCall/NoPickup            Dead/GMeet/Privacy Concerns
Dead/Interested/NoShow            Dead/ScriptShared/NoShow
Dead/ResultsReceived/WrongFit-Rejected
Dead/Negotiation/Pricing          Dead/Negotiation/Contractual
```

Two easily-confused pairs, both real distinctions — do not "clean them up":

- `Dead/ColdCall/NoPickup` = gave up after repeated ring-outs. `No Pickup` (live) = still trying.
- `Dead/Interested/NoShow` = said interested, never converted. `Dead/GMeet/NoShow` = meeting was
  actually booked and they didn't turn up.

**Reordering stages:** you must `PUT` the whole pipeline object atomically. Sequential `PATCH`es
per stage cause HubSpot to re-sequence between calls and the order comes out wrong. This was
learned by watching `No Pickup` land after `Interested`.

---

## 4. People, roles, owner IDs

| Owner ID | Name | Email | Role |
|---|---|---|---|
| 96574824 | Lamiya Saleem | lamiya.saleem@lh2.ai | GTM Analyst |
| 96573782 | Yuktha Anand | yuktha.anand@lh2.ai | GTM Analyst |
| 166420402 | Shreyas Boosnoor | shreyas.boosnoor@lh2.ai | GTM Analyst |
| 166483631 | Yash Wani | yash.wani@lh2.ai | GTM Analyst |
| 166322228 | Ishpreet Sood | ishpreet.sood@lh2.ai | Pod Lead (a.k.a. Lead Manager) |
| 166262056 | Shobit Gupta | shobit.gupta@lh2.ai | Pod Head (a.k.a. Lead Closer) |
| 166322218 | Ashish Ranjan | ashish.ranjan@lh2.ai | — |
| 95472647 | Bhanu Enamala | bhanu.enamala@lh2.ai | Test mailbox / build account |

**Naming:** internally the roles are Pod Lead and Pod Head. In the dashboard and SoPs they are
written as **Lead Manager** and **Lead Closer**. Both names refer to the same role; the user
prefers the LH2 names in conversation. Never put an individual's name in a role document —
people rotate.

**Handover model:** GTM Analyst runs Cold Call → GMeet Fixed, then hands to the Pod Lead, who
runs the meeting through Script Shared → Script Results Received, then hands to the Pod Head for
negotiation and close.

> **Documented exception:** Lamiya and Yuktha carry deals through the *entire* funnel by design.
> Deals they own at `Script Shared` or beyond are correct, not a process failure. Do not "fix"
> them by reassigning to the Pod Lead.

---

## 5. Lead source — the 5-source universe

`lead_source` is an enumeration on the deal. It currently carries 26 options because of
historical drift, but only **five are live**, in the shape `Source ( Segment )`:

| Value | What it is |
|---|---|
| `Linkedin Campaign ( IT Services )` | Inbound — they filled our LinkedIn lead-gen form. Warmest. |
| `Outflo Outreach ( Startups )` | Accepted our LinkedIn connect, or replied to the message |
| `Scraping Algo ( IT services )` | Our own scraper, Indian IT-services firms |
| `Scraping Algo ( Startups )` | Deadpool list — dead startups, aim at the founder not the company |
| `Tracxn Sheet ( Startups )` | Tracxn database pull — mostly shut down or winding down |

The segment in brackets is load-bearing: it drives the IT-services-vs-startups conversion
comparison. Keep it truthful; do not fold a distinct origin into a generic bucket.

Call priority is fixed: **LinkedIn → OutFlo → everything cold**. Inbound goes stale fastest.

> **Writing an enumeration value that is not already an option fails with `INVALID_OPTION`.**
> Read the property, append your new options, `PATCH` the property, *then* write to deals. Adding
> a new source to your ops-data flow means doing this first.

---

## 6. Where things get written

Custom deal properties that a procurement flow will care about:

| Property | Type | Use |
|---|---|---|
| `lead_source` | enum | The 5-source universe above |
| `scraped_type` | enum | Segment marker; `Distressed startups` identifies Tracxn origin |
| `linkedin_url` | string | Founder LinkedIn — the single most reliable join key |
| `lh2_domain` | string | Domain, used as a unique key |
| `lh2_distress_key` | string | Unique key on the distress track |
| `metadata_link` | string | **Script results link.** The live one — a Drive folder or Sheet URL |
| `script_link` / `script_output_link` | string | Defined but **never populated** (0 of 930). Dead. Don't write to them expecting anyone to look. |
| `amount` | number | HubSpot's standard deal amount. 12 deals. |
| `cost` | number | Custom "Deal Cost (USD)". 5 deals. **See the warning below.** |
| `deal_value_range` | string | Indicative range before a price exists |
| `loc`, `num_projects`, `pr_count` | number | Codebase-track sizing (`loc` on 13 deals) |
| `num_repos` | number | Defined, never populated |
| `number_of_datasets`, `dataset_customization_required` | | Ops-data sizing — **2 deals each** |
| `data_delivery_timeline` | datetime | Defined, never populated |

> **Price is genuinely ambiguous right now — do not assume.** `amount` and `cost` are both in
> use and they **disagree on 3 of the 5 deals that carry both** (Nickelfox: cost 2000 / amount
> 1200; WebCodeGenie: 2100 / 7200; Serpent Consulting: 2000 / 2200). Nobody has reconciled them.
> Ask which one your flow should write before writing either.

> **The ops-data properties are effectively greenfield.** `number_of_datasets` and
> `dataset_customization_required` are populated on 2 deals; `data_delivery_timeline` on none.
> They exist but carry no established convention — **your project gets to define what they mean**,
> which is a freedom and a responsibility. Write the definition down when you do.
| `distress_score` / `distress_tier` / `distress_flags` / `distress_reasons` | | Tracxn distress rank |
| `flag_*` (deadpooled, website_dead, layoff_or_shutdown, no_revenue, tiny_headcount, funding_stale, funding_aging, negative_profit) | string | Individual distress signals |
| `gmeet1_date` / `gmeet1_link` / `gmeet1_outcome` | | First meeting |
| `callback_datetime`, `call_outcome`, `call_notes`, `call_attempt_count` | | Call logging |

Contacts carry `linkedin_url` (labelled "LinkedIn URL (LH2)"), `contact_role`, `spoc_type`,
`role`, `preferred_communication_channel`, `technical_expertise_area`.

**Deal naming: the company name, never the person's.** The company is the pointer; a deal named
after a founder is unsearchable and unjoinable. This was violated once when a name-resolution
bug silently fell back to the person, and 11 of 13 deals had to be renamed.

---

## 7. How metrics are computed — non-obvious and load-bearing

If your project reports numbers, these rules are not optional. Getting them wrong produces
plausible figures that are wrong, which is worse than an error.

**a. A metric is a set of stages.** `calls_connected` is not a counter; it is
`{Dead/ColdCall/Not Interested, Interested}` — the outcomes that prove a human answered.
`calls_attempted` adds the outcomes that prove a dial happened at all.

**b. Read stage *history*, not current stage.** A deal now at `Script Shared` passed through
`Interested` and must count on the day it did. Use
`GET /crm/v3/objects/deals/{id}?propertiesWithHistory=dealstage`.

**c. `sourceType` separates humans from scripts.** Every history entry carries `CRM_UI` (a person
moved it in the browser) or `INTEGRATION` (an API call did). About **42%** of entries are
`INTEGRATION`. Any *activity* metric — did someone do work today — must filter to `CRM_UI`, or a
bulk migration shows up as a heroic day of dialling. *Procurement* metrics (what we actually
acquired) should ignore `sourceType`, because a won deal is won however it was recorded.

**d. Actor ≠ owner.** The person who *moved* a deal is not necessarily its owner. Activity
credits the actor (`updatedByUserId` on the history entry); assignment credits
`hubspot_owner_id`. Confusing them inflated one person's booked-meeting count from 5 to 10.

**e. `updatedByUserId` is a USER id, not an OWNER id.** Conceptually separate namespaces. Map via
`GET /crm/v3/owners`, using each owner's `userId` field:
```python
USER2OWNER = {str(o["userId"]): o["id"] for o in owners if o.get("userId")}
```
In this portal the two happen to be numerically identical for all 8 owners today. **Do not rely
on that** — build the map.

**f. Day boundaries are IST**, and the reporting cutoff is **18:30 IST**.

---

## 8. Hard rules — violate these and real damage follows

**1. No Indian mobile, no push. Absolute.**
> "literally anything that goes onto hubspot MUST have an indian number or else it doesn't go."

Use `crm_mirror/enrich/indian_number.py` (`to_e164`, `is_indian`, `classify`, `pick_best`) — the
single shared validator. Accepts `+91`+10 digits or a bare 10-digit Indian number; rejects any
explicit non-`+91` country code and never re-stamps one. Prefer **mobile** over landline — a
landline reaches a switchboard, not the founder.

Never trust a spreadsheet's phone column. Measured on the Tracxn sheet: ~14% wrong by the team's
own call feedback, 0/10 agreement against SignalHire on a cross-check, ~10% switchboards, and US
numbers stored with no country code (`6464800503`) that a naive 10-digit test converts into a
bogus +91. A re-verification of 154 pushed leads replaced 88 numbers.

**2. No automated outbound mail. One exception.**
The **only** sanctioned recurring mail is the **18:30 IST daily report to the Pod Head**. A
15-minute lead-assignment mailer was once left running; a routine bulk reassignment made it fan
out **9 mails covering 1,040 deals** to 7 colleagues. It is now disabled
(`schtasks` task `LH2 VCF lead notifier`).

Practical rule for your flow: **before any bulk `hubspot_owner_id` write, check no mailer is
armed**, and put the guard in the script itself rather than in your head:

```python
q = subprocess.run(["schtasks","/query","/tn","LH2 VCF lead notifier","/fo","LIST","/v"],
                   capture_output=True, text=True).stdout
if "Disabled" not in q: sys.exit("ABORT: notifier armed — it would mail everyone")
```

**3. HubSpot is the source of truth.** Not a sheet, not a local SQLite mirror. If a dashboard and
HubSpot disagree, the dashboard is wrong.

**4. Contacts follow the deal.** Reassigning a deal without reassigning its contacts hands over a
half-usable book — the new owner cannot filter to their own contacts or see the number.

---

## 9. API notes that cost time to learn

- **Pagination:** search `limit` maxes at 200. Always loop on `paging.next.after`. A truncated
  query once produced a confidently wrong answer (reported 200 live / 0 Cold Call; the truth was
  403 live / 7 Cold Call).
- **Associations must be batched.** `POST /crm/v4/associations/deals/contacts/batch/read` with
  `{"inputs":[{"id":…}]}`, 100 at a time. One call per record is thousands of round trips and the
  user *will* notice it hanging.
- **Batch reads/writes** are `/crm/v3/objects/{type}/batch/read|update`, 100 per call. Accept
  `200`, `201` and `207` as success — `207` is partial and needs inspecting.
- **Retry** on `429, 502, 503, 504` with backoff. Everything else, fail loudly.
- **Archived ≠ deleted.** Archived deals stay queryable with `archived=true` and hold real
  history. When counting "never touched", archived records must be accounted for, not ignored.

---

## 10. How to work in this codebase

Conventions the user has reinforced repeatedly:

- **Dry run first, always.** Every mutating script takes `--apply` (or `--send`); default is a
  plan printed to stdout and nothing written. Show counts and a sample before touching anything.
- **Print the plan as a table** — from/to, by stage, by source. The user reads these and catches
  real errors in them.
- **Write the audit trail.** Dump what changed to JSON next to the script, so a move is reversible.
- **Comments explain *why*, never *what*.** A comment restating the code will be deleted. A
  comment recording why a non-obvious choice was made is the point.
- **Report failures plainly.** If a run partly failed, say which part and show the output. Do not
  round a crash up to a success.
- Scripts live in `crm_mirror/enrich/`, one file per operation, self-contained, stdlib-only
  (`urllib.request`) with the same `hs()` helper. Copy the shape from
  `crm_mirror/enrich/li_campaign_to_ishpreet.py`.
- Gmail sending needs the venv Python — `lh2-pipeline/.venv/Scripts/python.exe`. System Python
  lacks `google.*`.

## Worth copying into the new project

| From | Why |
|---|---|
| `crm_mirror/enrich/indian_number.py` | The mandatory phone gate. Do not reimplement it. |
| `crm_mirror/enrich/gmail_sender.py` | Transport fallback chain, never silently drops a message |
| `crm_mirror/rules/asset_fit_score.md` | The `opsdata_fit` scoring logic your flow extends |
| `docs/sop/1_LH2_Supply_Funnel_Master.docx` | The funnel as explained to the team |
| `crm_mirror/enrich/li_campaign_to_ishpreet.py` | Reference shape: guard, dry run, batch, audit dump |
