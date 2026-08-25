# Company Ops dashboard

Read-only board over the **Company Ops Data** pipeline (portal `246897735`). HubSpot is the
source of truth — there is no add/edit anywhere in the UI, deliberately: a second place to type
a milestone is a second version of it.

```
build_ops_dashboard.py  ->  ops_dashboard_data.json  ->  index.html   (GitHub Pages)
```

| File | What it is |
|---|---|
| `build_ops_dashboard.py` | Pulls the pipeline, derives every metric from stage history |
| `ops_note_rules.py` | Note classification — the two metrics that have no stage |
| `index.html` | The board. Fetches the JSON, renders it, filters by range and owner |
| `../docs/OPS_DASHBOARD_METRIC_SPEC.md` | **What every number means.** Read this first |
| `../.github/workflows/deploy-ops-dashboard.yml` | Build + deploy to Pages on a schedule |

## Local run

```bash
python dashboard/build_ops_dashboard.py        # writes dashboard/ops_dashboard_data.json
cd dashboard && python -m http.server 8000     # open http://localhost:8000
```

Needs `HUBSPOT_API_KEY` in env or `hubspot_key=` in `.env`. Standard library only.

Opening `index.html` as a `file://` URL will not work — the fetch of the JSON is blocked by
CORS. Serve it.

## Ranges

**Today · WTD · MTD · Total · Custom.** Every count is the number of times a deal *entered* a
stage on that IST day, so each range is a genuine volume for that window rather than a snapshot
of who is sitting where. The growth badge on the first KPI of each strip compares against the
immediately preceding window of the same length (Today vs yesterday, a 7-day week vs the 7 days
before it).

The 8-week trend charts deliberately ignore the range picker — a sparkline that collapses to a
single point when somebody clicks Today is not a trend.

## Adding a metric

1. Write the definition in `docs/OPS_DASHBOARD_METRIC_SPEC.md`.
2. Add the stage set to `METRICS` in `build_ops_dashboard.py`.
3. Add the key to the matching `*_STAGES` list in `index.html`.

The keys in step 2 and 3 must match; nothing validates that at runtime, so there is a check in
the metric spec's §2 table worth keeping current.