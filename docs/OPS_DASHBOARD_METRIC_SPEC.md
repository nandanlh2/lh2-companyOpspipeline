# Company Ops dashboard — metric spec

**What this is.** The definition of every number on the Company Ops dashboard. If a count on
the board looks wrong, the answer is here or it is a bug. Change a meaning here *first*, then
in `dashboard/build_ops_dashboard.py`.

**Portal** `246897735`, pipeline **Company Ops Data** (`2464812771`).
**Built by** `dashboard/build_ops_dashboard.py` → `ops_dashboard_data.json` → `dashboard/index.html`.
**Deployed by** `.github/workflows/deploy_ops_dashboard.yml` to GitHub Pages.

---

## 1. The counting rule

The board answers three different questions and keeps them apart on purpose. Conflating them is
exactly why this dashboard once disagreed with HubSpot.

| On the page | Function | What it counts | How a range narrows it |
|---|---|---|---|
| **The big number on every row, the bar, the KPI value, every badge and rate** | `countEver` | **Distinct deals that have ever entered** a qualifying stage | By the day of the **event** |
| The muted "*N* now" beside each stage row | `countNow` | Deals whose **current stage** is in the set | **Not at all** — a snapshot has no window |
| The 8-week trend charts only | `countEvents` | **Every entry event**, so a deal can count twice | By the day of the **event** |

> **The headline is ever-reached: a deal that has moved further down the funnel still counts at
> every stage it passed through.**

That is what makes a funnel a funnel. `One Pager Shared` holds 0 deals right now, but 4 have been
through it — under a snapshot that row reads `0` and says "we have never sent a one-pager", which
is false. Under ever-reached it reads `4`.

**The snapshot has not gone away**, it rides alongside as the muted `N now`. That figure is the
HubSpot board's column count, so the two can always be reconciled: if the board says
`Cold LinkedIn Sent 879`, the LinkedIn row says `879 now` whatever range is selected. It ignores
the picker deliberately — "how many are sitting here" is only ever true *now*.

**Why every rate is built on ever-reached.** A deal that replied has left the outreach stage. A
snapshot-based reply rate would shrink its own denominator every time the numerator grew, and a
snapshot-based show rate would put attendance *above* bookings and read over 100%.

**What a range means.** `Total` is all of history. `Today`/`WTD`/`MTD`/`Custom` count the stages
**entered** in that window, on the IST day they were entered — so `Today` is what actually happened
today. The 8-week trend charts always show eight weeks and ignore the picker: a sparkline that
collapses to a single point when somebody clicks Today is not a trend.

Consequences worth knowing before someone reports a bug:

- **The funnel is not monotonic, and should not be forced to be.** `Discovery Call Set Up` can
  exceed `1st Interest Email Sent` because the latter is note-derived and only counts deals
  somebody wrote a note on. Read each row as its own volume.
- **A funnel is not a cohort.** The samples received this month are not a subset of the outreach
  sent this month — they are the samples that arrived this month, from outreach sent whenever.
- **Totals only ever grow going back in time.** History is re-read from scratch on every build, so
  a correction made in HubSpot today fixes last week's number on the next build too.
- **`countEvents` can count one deal twice, deliberately.** Under the follow-up loops a deal
  legitimately re-enters a stage and both entries are real. Only an identical (stage, person, day)
  triple is de-duplicated. This is why the trend charts use it and nothing else does.

### Every source counts

There is **no `sourceType` filter.** An earlier version kept only `CRM_UI` moves for "activity"
metrics, so that the 10:30 OutFlo sync would not look like a record outreach day. Measured cost of
that rule on the live pipeline:

| Stage | `CRM_UI` moves | `INTEGRATION` moves |
|---|---|---|
| Cold LinkedIn Sent | 5 | 17 |
| Email Campaign Sent | 0 | 7 |
| LinkedIn Connected | 0 | 8 |
| Replied | 6 | 28 |

The sync and the Gmail import make most of the moves on both entry branches, so the filter threw
away the top of the pipeline: the board read **5** where HubSpot read **879**. A move the sync made
is still a move that happened.

The trade-off is real and accepted — the outreach trend chart is now dominated by automated
promotion, which is what actually drives this pipeline. `updatedByUserId` is absent on an
integration move, so those events carry a blank actor.

### Days

Every day is an **IST calendar day**. The portal's timezone is US/Eastern and LH2 reporting runs to
an 18:30 IST cutoff, so boundaries are computed in code and the portal's own dates are never
trusted. The front end compares day strings rather than `Date` objects, so a browser in another
timezone cannot shift a deal across a boundary.

---

## 2. Metrics

Each is a **set of stages**, never a flag or a counter. Membership is explicit rather than a depth
threshold, because stages at the same depth can mean opposite things — `Dead/Cold/No Reply` and
`Dead/Cold/Not Interested` are equally deep, but one means nobody ever answered and the other means
a human replied and said no.

The board charts **eleven stage-backed metrics and three derived ones**, matching the agreed layout
in `resources/lh2-pipeline-overview.html`. The keys below are the whole of `METRICS` in
`build_ops_dashboard.py` and the whole of `INPUT_STAGES` / `OUTCOME_STAGES` in `index.html`; the two
lists must agree, and nothing validates that at runtime.

### Outreach & Response (Input Matrix)

| Key | Stage(s) that satisfy it |
|---|---|
| `outreach` | `Cold LinkedIn Sent`, `Email Campaign Sent` |
| `outreachLi` / `outreachEmail` | the branch split, for the channel chart and the KPI split line |
| `outreachReplied` | `Replied`, `Ghost Follow-Up` |
| `interestSent` | **note-derived** — see §3 |
| `vcSetup` | `Discovery Call` |
| `vcAttended` | **derived** — see below |

**`Ghost Follow-Up` counts as a reply.** A deal only reaches it *because* somebody answered and
then went quiet, so filing the chase as a different kind of event from the reply that caused it
would split one fact in two — and would drop 9 live deals that have plainly replied out of the
reply row entirely.

The LinkedIn/Email split falls out of *which entry stage* the deal is at. No channel property is
read or stored — the branch already is the channel. A company that arrived on both tracks (the
Workline case: an OutFlo lead that was also mailed) counts to both, exactly as both outreach events
counted.

**`vcAttended` has no stage and never will.** A stage is a state the deal is *in*, and "they showed
up" is not one — the deal sits at `Discovery Call` either way until an outcome moves it. So
attendance is the **absence of a later `Dead/Interested/No Show`**, credited to the day of the
**call**, not the day somebody marked the no-show. A call booked and attended on Monday counts to
Monday even if the stage was tidied on Thursday. This means a very recent day's show rate can only
fall as no-shows get recorded — the honest direction for it to move.

`Dead/Discovery Call/Privacy Concerns` is deliberately **not** a no-show: they turned up and then
balked, so the call happened and the deal died for a different reason.

### Materials & Samples (Outcome Matrix)

| Key | Stage(s) |
|---|---|
| `onePagerSent` | `One Pager Shared` |
| `onePagerReceived` | **note-derived** — see §3 |
| `samplesRequested` | `Samples Requested` |
| `samplesReceived` | `Sample Received + Quality Check` |

### Commercials & Close

| Key | Stage(s) |
|---|---|
| `negotiationDone` | `Commercial Negotiations` |
| `contractSigned` | `Deal Contract Signed` |
| `dealWon` | `Closed/Won` |

**Money comes from `cost`** ("Deal Cost (USD)"), never `amount`. The flowchart's Payment Initiation
gate is literally "Deal Cost ($)" and this flow does not write `amount` at all. (The main portal has
an unreconciled `amount`-vs-`cost` conflict; here we pick one and stay with it.)

### Stages the pipeline has but the board does not chart

`LinkedIn Connected`, `LinkedIn Follow-Up`, `Email Follow-Up`, `One Pager Requested`, `One Pager Follow-Up`, `Internal Evaluation (Sample)`, `Sample Follow-Up`,
`Token Amount Paid`, `Data Migration Done`, `Payment Initiation`.

These are real stages and deals do sit at them — 388 at `LinkedIn Connected` at the time of writing.
They are simply not on the board, by agreement. A deal at one of them gets an **empty `occ`** and
appears in no funnel row, but it still ranks in the Hot Pipeline through `_SEQ`. To put one back:
add it to `METRICS` here and in the builder, then add the key to the matching `*_STAGES` list in
`index.html`.

---

## 3. The two note-derived metrics

`1st Interest Email Sent` and `One-Pager Received` have no stage, and should not get one. Both are
*events on a deal* rather than states it enters, and the pipeline's founding rule is that
[the outcome IS the stage](OPSDATA_PIPELINE.md) — a stage meaning "we sent something" is the same
mistake as the retired `Call Attempted`.

So both are read from note text, classified by `dashboard/ops_note_rules.py`, **first match wins**.
They carry a small **`note`** flag on the dashboard so nobody mistakes them for stage-backed
numbers.

### The rules are tuned against the real corpus

The whole portal holds **68 notes**, and all 68 have been read. Every rule below was written
against them, not against the flow diagram. Before that pass 39 of 68 fell into `Other` — almost
all of them our own size/fit disqualifications, which no rule had ever existed for.

| Bucket | Notes | Is it a metric? |
|---|---|---|
| Disqualified — size/fit | 23 | no — context |
| Not interested | 16 | no — context |
| **Interest email sent** | **10** | **yes → `interestSent`** |
| Wrong contact | 9 | no — context |
| No reply | 4 | no — context |
| Meeting fixed | 3 | no — context |
| Privacy concern | 1 | no — context |
| Prospect shared contact | 1 | no — context |
| Other | 1 | — |

Three orderings are load-bearing and must not be tidied:

1. **"Received" before "sent"**, or *"sent the one pager, they received it"* lands in the wrong
   bucket.
2. **"Interest email sent" before every disqualification bucket.** Losing a metric event is worse
   than mis-filing a context note, and a real send is often written in the same breath as the
   reason the lead later died: *"He was interested and sent his email immediately. We have sent him
   an email with the company deck."*
3. **"Wrong contact" before "Disqualified"**, so *"asked to connect with digital marketing team,
   not interesting"* is filed under the actionable half.

### Why `interestSent` does not match a bare "sent" or "shared"

Every branch of that rule **names what was sent** — a deck, a profile, details, an email of ours.
That is deliberate. Two notes in this portal record the *prospect* doing the sending:

> *"He has sent his email but is is not a ceo or founder. so not a potential lead."*
> *"Asked him for his email. He sent a contact number will also reach out on the number."*

A rule keyed on bare "sent"/"shared" counts both as our outreach. The object is what makes it ours.
The `Prospect shared contact` bucket catches the leftovers, and sits *after* `Interest email sent`
so a note recording both still counts as a send.

**`not interesting` is not `not interested`.** One word apart, opposite meanings: *they* declined
versus *we* disqualified them, almost always on company size. They are separate buckets.

### Checking them

```bash
python dashboard/check_note_rules.py         # bucket counts + anything still in "Other"
python dashboard/check_note_rules.py --all   # every note under its bucket
```

Reads the whole corpus without touching stage history, so it finishes in under a minute against the
full build's tens. Exits 1 if more than five bodies are unclassified.

**The ceiling on both metrics is note-writing discipline, not the regexes.** 68 notes across 1,499
deals means `interestSent` can only ever describe the deals somebody wrote a note on. It is 10
because ten notes record a send — not because the rules are missing nine hundred.

Rules are **imported, never copied**. A hand-copy of KPI definitions on the sales side drifted and
inflated dial counts 2.7x, and `ops_note_rules.py` sits next to the builder inside the repo because
the sales dashboard once imported its rules from a local-only sibling directory — resolving on the
laptop, raising `ImportError` in Actions, and leaving the note KPIs silently empty on every deployed
build.

---

## 4. Stage resolution and drift

Stage ids are **always resolved live** from the pipeline, never hardcoded — on the main portal a
script that hardcoded one pipeline's ids and wrote them to another failed all 13 writes with
`INVALID_OPTION` *after* creating the contacts, leaving 13 orphan contacts and no deals.

The pipeline can also drift through the HubSpot UI, so the index is rebuilt on every run and a
stage the dashboard has never heard of is reported loudly at the end of the build:

```
WARNING: 1 stage(s) not in _SEQ/_DEAD_SEQ — add them: ['Some New Stage']
```

An unknown stage is ranked at the funnel entry and counts toward **no metric** — the
same state a deliberately un-charted stage is in, which is why the warning matters. That must never
be a silent state — a new stage that nobody adds here is a new stage nobody can see.

Renames are handled two ways. `pipeline_v3_update.py` renamed `Message Back` → `LinkedIn
Follow-Up` and `One Pager + Deck Shared` → `One Pager Shared` via per-stage `PATCH`, which
preserves the stage id. The v2 restructure's PUT-based renames did **not** (`GMeet Fixed` →
`Discovery Call` got a fresh id). Old labels therefore survive in the history of migrated deals,
and `ALIAS` maps them forward — without it the series before 2026-08-13 falls off a cliff.

---

## 5. Running it

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