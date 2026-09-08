# Company Ops dashboard

Read-only board over the **Company Ops Data** pipeline (portal `246897735`). HubSpot is the
source of truth — there is no add/edit anywhere in the UI, deliberately: a second place to type
a milestone is a second version of it.

```
build_ops_dashboard.py  ->  ops_dashboard_data.json  ->  index.html   (GitHub Pages)
```

| File | What it is |
|---|---|
| `build_ops_dashboard.py` | Pulls the pipeline; reads the snapshot off each deal's current stage and the history off its stage changes |
| `ops_note_rules.py` | Note classification — the two metrics that have no stage |
| `check_note_rules.py` | Classifies every note in the portal; run after editing the rules |
| `index.html` | The board. Fetches the JSON, renders it, filters by range |
| `../docs/OPS_DASHBOARD_METRIC_SPEC.md` | **What every number means.** Read this first |
| `../.github/workflows/deploy_ops_dashboard.yml` | Build + deploy to Pages on a schedule |

## Local run

```bash
python dashboard/build_ops_dashboard.py        # writes dashboard/ops_dashboard_data.json
cd dashboard && python -m http.server 8000     # open http://localhost:8000
```

Needs `HUBSPOT_API_KEY` in env or `hubspot_key=` in `.env`. Standard library only.

Opening `index.html` as a `file://` URL will not work — the fetch of the JSON is blocked by
CORS. Serve it.

## What the numbers mean

The layout follows `../resources/lh2-pipeline-overview.html`: three sections (Outreach & Response,
Materials, Commercials & Close) of five, two and two stage rows, plus the Hot Pipeline. (Pre-v4
this was five/four/three — the sample-evaluation and negotiation rows collapsed into one `LOI
Signed` row and the separate `Deal Won` row merged into `Contract Signed` when that stage itself
became the closed-won stage; see `../docs/OPSDATA_PIPELINE.md` "v4 restructure".)

Every stage row shows **two** figures:

| | |
|---|---|
| The big number, and the bar | **Deals that have ever reached that stage.** A deal that has since moved further down still counts at every stage it passed through — that is what makes the funnel read top to bottom. Every conversion badge and both rates come from this |
| The muted `N now` beside it | **Deals sitting at that stage this moment** — the same count as the column header on the HubSpot board, so the two can always be reconciled. It ignores the range picker, because "how many are here" is only ever true now |

Rows tagged `note` or `derived` — *1st Interest Email Sent*, *One-Pager Received*, *Discovery Call
Attended* — have no pipeline stage behind them, so they show the ever figure only.

Why both: a snapshot alone hides everything that has moved on (a stage can hold few deals today
while many more have passed through it on their way further down the funnel), while a rate built
on a snapshot shrinks its own denominator every time the numerator grows.

**The funnel is not monotonic and should not be forced to be.** `Discovery Call Set Up` can exceed
`1st Interest Email Sent` because the latter is note-derived and only counts deals somebody wrote a
note on. Read each row as its own volume.

**Ranges — Today · WTD · MTD · Total · Custom.** `Total` is all of history. The others count the
stages **entered** in that window, on the IST day they were entered — so `Today` is what actually
happened today. The `N now` snapshot never moves with the picker.
The growth badge on the first KPI of each strip compares against the immediately preceding window of
the same length.

The 8-week trend charts deliberately ignore the range picker — a sparkline that collapses to a
single point when somebody clicks Today is not a trend.

**There is no `sourceType` filter.** Most moves on the LinkedIn branch are made by the OutFlo sync,
not by hand (the Gmail import made most email-branch moves historically, before that branch was
retired in v4); filtering to `CRM_UI` (as this once did) threw away the top of the pipeline and
made the board read 5 where HubSpot read 879. See the metric spec's §1.

**No owner filter.** The builder still records who made each move and still emits `team`, so the
per-member dropdown can be put back without a data change — the board just does not show one.

## Editing the note rules

`1st Interest Email Sent` and `One-Pager Received` are read out of note text by
`ops_note_rules.py`, first match wins, so rule **order** is load-bearing. After any edit:

```bash
python dashboard/check_note_rules.py        # add --all to see every note under its bucket
```

It classifies the whole corpus in under a minute (no stage history) and exits 1 if more than five
bodies land in `Other`. A handful of one-offs is fine; a cluster is a missing rule.

## Adding a metric

1. Write the definition in `docs/OPS_DASHBOARD_METRIC_SPEC.md`.
2. Add the stage set to `METRICS` in `build_ops_dashboard.py`.
3. Add the key to the matching `*_STAGES` list in `index.html`.

The keys in steps 2 and 3 must match; nothing validates that at runtime, so keep the metric spec's
§2 tables current — they are the check.

Step 2 is what gives a stage a snapshot: `occ` on each row is `metrics_for(current stage)`, so a
stage missing from `METRICS` counts toward nothing and charts nowhere, however many deals sit at it.
The stages left off the board on purpose are listed in the metric spec's §2.