# Company Ops dashboard — metric spec

**What this is.** The definition of every number on the Company Ops dashboard. If a count on
the board looks wrong, the answer is here or it is a bug. Change a meaning here *first*, then
in `dashboard/build_ops_dashboard.py`.

**Portal** `246897735`, pipeline **Company Ops Data** (`2464812771`).
**Built by** `dashboard/build_ops_dashboard.py` → `ops_dashboard_data.json` → `dashboard/index.html`.
**Deployed by** `.github/workflows/deploy-ops-dashboard.yml` to GitHub Pages.

---

## 1. The counting rule

> **Every metric is a count of times a deal ENTERED a qualifying stage, on the IST day it
> entered**, read from that deal's stage-change history.

It is **not** "deals currently at or past stage X". That distinction is the whole reason Today
and WTD mean anything: a deal that entered `Replied` last week and `Discovery Call` this week
contributes to a different week for each, and a stale deal sitting at `Samples Requested` for a
month contributes to exactly one day — the day it got there.

Consequences worth knowing before someone reports a bug:

- **A deal can count more than once for the same metric.** The follow-up loops are real: a lead
  replies, goes quiet, gets a `Ghost Follow-Up`, replies again. Both replies are events. Only an
  identical (stage, person, day) triple is de-duplicated.
- **A funnel is not a cohort.** The 12 samples received this month are not a subset of the 40
  outreaches sent this month — they are the samples that arrived this month, from outreach sent
  whenever. Read each row as a volume, not a survival rate.
- **Totals only ever grow going back in time.** History is re-read from scratch on every build,
  so a correction made in HubSpot today fixes last week's number on the next build too.

### Activity vs asset facts

| | Counts | `sourceType` filter |
|---|---|---|
| **Activity** — everything a person did | LinkedIn sent, replied, calls, one-pagers, samples, negotiation, contract | **`CRM_UI` only** |
| **Asset facts** — things true of the data | `Closed/Won`, `Data Migration Done` | **any source** |

Activity has to filter, because `outflo-sync.yml` promotes deals up the LinkedIn ladder every
morning at 10:30 and the Gmail import promotes the email branch. Counting those as human
activity would render the daily sync as the biggest outreach day the company has ever had.

Asset facts must *not* filter: a dataset we received is ours whether a person clicked the stage
or a script set it.

### Who gets credit

Activity is credited to the **actor** — the person who made the move — not the current owner.
The analyst books the Discovery Call and hands the deal to the Pod Lead in the same action, so
by build time it belongs to someone else. Crediting the current owner would take that call off
the analyst who booked it, and the better the handover discipline, the more work gets
misattributed. Assignment stays owner-based; it really is about who received the deal.

Note that stage history names the actor by **user id** while deals name people by **owner id** —
separate HubSpot namespaces that coincide only by luck. `USER2OWNER` maps them explicitly.

### Days

Every day is an **IST calendar day**. The portal's timezone is US/Eastern and LH2 reporting runs
to an 18:30 IST cutoff, so boundaries are computed in code and the portal's own dates are never
trusted. The front end compares day strings rather than `Date` objects, so a browser in another
timezone cannot shift a deal across a boundary.

---

## 2. Metrics

Each is a **set of stages**, never a flag or a counter. Membership is explicit rather than a
depth threshold, because stages at the same depth can mean opposite things —
`Dead/Cold/No Reply` and `Dead/Cold/Not Interested` are equally deep, but one means nobody ever
answered and the other means a human replied and said no.

### Outreach & Response (Input Matrix)

| Key | Stage(s) that satisfy it |
|---|---|
| `outreach` | `Cold LinkedIn Sent`, `Email Campaign Sent` |
| `outreachLi` / `outreachEmail` | the branch split, used for the channel chart |
| `liConnected` | `LinkedIn Connected` |
| `outreachFollowUp` | `LinkedIn Follow-Up`, `Email Follow-Up` |
| `interestSent` | **note-derived** — see §3 |
| `outreachReplied` | `Replied` |
| `ghostFollowUp` | `Ghost Follow-Up` |
| `vcSetup` | `Discovery Call` |
| `vcAttended` | **derived** — see below |

The LinkedIn/Email split falls out of *which entry stage* was entered. No channel property is
read or stored — the branch already is the channel. A company that arrived on both tracks (the
Workline case: an OutFlo lead that was also mailed) counts to both, exactly as both outreach
events counted.

**`vcAttended` has no stage and never will.** A stage is a state the deal is *in*, and "they
showed up" is not one — the deal sits at `Discovery Call` either way until an outcome moves it.
So attendance is the **absence of a later `Dead/Interested/No Show`**, credited to the day of
the **call**, not the day somebody marked the no-show. A call booked and attended on Monday
counts to Monday even if the stage was tidied on Thursday. This means a very recent day's show
rate can only fall as no-shows get recorded — the honest direction for it to move.

`Dead/Discovery Call/Privacy Concerns` is deliberately **not** a no-show: they turned up and
then balked, so the call happened and the deal died for a different reason.

### Materials & Samples (Outcome Matrix)

| Key | Stage(s) |
|---|---|
| `onePagerRequested` | `One Pager Requested` |
| `onePagerFollowUp` | `One Pager Follow-Up` |
| `onePagerSent` | `One Pager Shared` |
| `onePagerReceived` | **note-derived** — see §3 |
| `internalEval` | `Internal Evaluation (Sample)` |
| `samplesRequested` | `Samples Requested` |
| `sampleFollowUp` | `Sample Follow-Up` |
| `samplesReceived` | `Sample Received + Quality Check` |

### Commercials & Close

| Key | Stage(s) |
|---|---|
| `negotiationDone` | `Commercial Negotiations` |
| `contractSigned` | `Deal Contract Signed` |
| `tokenPaid` | `Token Amount Paid` |
| `migrationDone` | `Data Migration Done` |
| `paymentInitiation` | `Payment Initiation` |
| `dealWon` | `Closed/Won` |

**Money comes from `cost`** ("Deal Cost (USD)"), never `amount`. The flowchart's Payment
Initiation gate is literally "Deal Cost ($)" and this flow does not write `amount` at all. (The
main portal has an unreconciled `amount`-vs-`cost` conflict; here we pick one and stay with it.)

**Token is shown separately and never added to Value Won.** It is paid *after* signing against
the same deal, so summing the two would count part of the same price twice.

---

## 3. The two note-derived metrics

`1st Interest Email Sent` and `One-Pager Received` have no stage, and should not get one. Both
are *events on a deal* rather than states it enters, and the pipeline's founding rule is that
[the outcome IS the stage](OPSDATA_PIPELINE.md) — a stage meaning "we sent something" is the
same mistake as the retired `Call Attempted`.

So both are read from note text, classified by `dashboard/ops_note_rules.py`, first match wins.
They carry a small **`note`** flag on the dashboard so nobody mistakes them for stage-backed
numbers.

Two things to know:

1. **The rules are a starting set.** They were written against the flow, not against a scrape of
   real Company Ops notes. Every build prints a sample of note bodies that matched nothing
   (`note rules missed N+ bodies`) — that output is the tuning list. Expect one pass after the
   first real build, and treat these two numbers as indicative until it has happened.
2. **"Received" is tested before "sent"**, or a note reading *"sent the one pager, they received
   it"* lands in the wrong bucket. Rule order is load-bearing.

Rules are **imported, never copied**. A hand-copy of KPI definitions on the sales side drifted
and inflated dial counts 2.7x, and `ops_note_rules.py` sits next to the builder inside the repo
because the sales dashboard once imported its rules from a local-only sibling directory —
resolving on the laptop, raising `ImportError` in Actions, and leaving the note KPIs silently
empty on every deployed build.

---

## 4. Drop-off

Dead deals are grouped by **where they died** — the middle segment of the stage name. That is
the only lost-reason record that exists, deliberately: no separate lost-reason property exists
or should exist, because it would immediately disagree with the stage.

| Bucket | Stages |
|---|---|
| Outreach | `Dead/Cold/Not Interested`, `Dead/Cold/No Reply` |
| Discovery call | `Dead/Interested/No Show`, `Dead/Discovery Call/Privacy Concerns` |
| One pager | `Dead/One Pager Not Shared` |
| Evaluation | `Dead/Sample Not Collected/Wrong Fit-Rejected` |
| Sample | `Dead/Sample Not Received/Company No Show`, `Dead/Sample/Bad Quality` |
| Negotiation | `Dead/Negotiations/Pricing`, `Dead/Negotiations/Contractual` |
| Migration | `Dead/Migration/Failed` |

A dead deal is placed in the range by the **last dated event on it**. HubSpot does not record a
"died on" date, and the last thing that happened to it is the closest honest proxy.

---

## 5. Stage resolution and drift

Stage ids are **always resolved live** from the pipeline, never hardcoded — on the main portal a
script that hardcoded one pipeline's ids and wrote them to another failed all 13 writes with
`INVALID_OPTION` *after* creating the contacts, leaving 13 orphan contacts and no deals.

The pipeline can also drift through the HubSpot UI, so the index is rebuilt on every run and a
stage the dashboard has never heard of is reported loudly at the end of the build:

```
WARNING: 1 stage(s) not in _SEQ/_DEAD_SEQ — add them: ['Some New Stage']
```

An unknown stage is ranked at the funnel entry and counts toward **no metric**. That must never
be a silent state — a new stage that nobody adds here is a new stage nobody can see.

Renames are handled two ways. `pipeline_v3_update.py` renamed `Message Back` → `LinkedIn
Follow-Up` and `One Pager + Deck Shared` → `One Pager Shared` via per-stage `PATCH`, which
preserves the stage id. The v2 restructure's PUT-based renames did **not** (`GMeet Fixed` →
`Discovery Call` got a fresh id). Old labels therefore survive in the history of migrated deals,
and `ALIAS` maps them forward — without it the series before 2026-08-13 falls off a cliff.

---

## 6. Running it

```bash
python dashboard/build_ops_dashboard.py                    # full rebuild
python dashboard/build_ops_dashboard.py --deals 123,456     # those deals only -> stdout
python dashboard/build_ops_dashboard.py --deals-since 2026-08-20T00:00:00Z
```

Token from `HUBSPOT_API_KEY` (CI) or `.env` (`hubspot_key=...`). Standard library only — no
install step to break on a runner image change.

`--deals-since` filters on `hs_lastmodifieddate`, not
`hs_v2_date_entered_current_stage`: a later move overwrites the latter, so it under-counts any
window ending in the past.

The build is read-only. It never writes to HubSpot, which is why the workflow cancels
in-progress runs rather than queueing them.