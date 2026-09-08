"""Move existing deals OUT of stages that pipeline_v4_update.py is about to delete.

ORDER: run pipeline_v4_update.py --apply FIRST (it is safe to run before deals are
migrated -- it creates 'LOI signed', 'Dead: Email Campaign / Branch Retired' and every
other new v4 stage immediately, but HOLDS BACK any stage it would otherwise delete while
that stage still has deals on it, rather than orphaning them). Only then do those target
stages exist for this script to move deals into. Once this script has run, re-run
pipeline_v4_update.py --apply a second time to actually drop the now-empty stages. See
docs/OPSDATA_PIPELINE.md for the full sequence.

Two migrations, at very different confidence levels:

1. Internal Evaluation (Sample) + Samples Requested + Sample Received + Quality Check
   -> LOI signed. 4 deals total (measured 2026-09-08). This always runs when --apply is
   given: the mapping is a reasonably direct "collapse the sample/negotiation stretch
   into one stage" and there is no live equivalent left for these deals to sit at
   otherwise.

2. Email Campaign Sent + Email Follow-Up -> Dead: Email Campaign / Branch Retired. 124
   deals (measured 2026-09-08). This does NOT run unless --close-email is also passed.
   The v4 flowchart drops the email branch entirely and these deals never replied while
   the channel was live, so they are CLOSED rather than folded into an active LinkedIn
   stage (which would misrepresent them as live LinkedIn outreach and imply someone is
   about to work them). The lead_source property still says 'Cold Email ( Company Ops )'
   on every one of them, so the original channel is not lost. Sign-off recorded
   2026-09-08 (user confirmed via chat) -- get the same confirmation again if this script
   is ever re-run against a portal where it has not already applied.

Every deal moved by either migration is written to BOTH an audit JSON (matches the shape
of every other opsdata/ script) and a CSV (deal id, name, lead source, from stage, to
stage) -- the CSV is the human-reviewable form the team can actually open and check
without parsing JSON.

Dry-run by default; --apply to write. Audit files (JSON + CSV) to audit/.
"""
import csv, json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"

SAMPLE_STAGES = ["Internal Evaluation (Sample)", "Samples Requested",
                 "Sample Follow-Up", "Sample Received + Quality Check",
                 "Commercial Negotiations"]
SAMPLE_TARGET = "LOI signed"

EMAIL_STAGES = ["Email Campaign Sent", "Email Follow-Up"]
EMAIL_TARGET = "Dead: Email Campaign / Branch Retired"

def _env():
    d = {}
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                d[k] = v.strip()
    return d

ENV = _env()

def hs(path, payload=None, method=None):
    for attempt in range(5):
        req = urllib.request.Request("https://api.hubapi.com" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {ENV['HUBSPOT_API_KEY']}",
                     "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} on {path}: {e.read().decode(errors='replace')[:600]}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise

def deals_at(pipeline_id, stage_id):
    deals, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [
                    {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
                    {"propertyName": "dealstage", "operator": "EQ", "value": stage_id}]}],
                "properties": ["dealname", "lead_source"], "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        deals += d["results"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return deals

def main():
    apply = "--apply" in sys.argv
    close_email = "--close-email" in sys.argv

    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    stage_id = {st["label"]: st["id"] for st in pipe["stages"]}

    groups = [("sample/negotiation -> LOI signed", SAMPLE_STAGES, SAMPLE_TARGET, True),
              ("email -> Dead: Email Campaign / Branch Retired", EMAIL_STAGES,
               EMAIL_TARGET, close_email)]

    plan = []  # (group_name, from_stage, deal, active)
    for name, from_stages, target, active in groups:
        if target not in stage_id:
            sys.exit(f"ABORT: target stage {target!r} missing — run "
                     "'python opsdata/pipeline_v4_update.py --apply' first (it creates "
                     "new v4 stages immediately and holds back only the ones still "
                     "holding deals). See docs/OPSDATA_PIPELINE.md for the full sequence.")
        for lbl in from_stages:
            if lbl not in stage_id:
                continue  # already migrated/removed on a previous run
            for deal in deals_at(pipe["id"], stage_id[lbl]):
                plan.append((name, lbl, deal, active))

    print(f"Deals to move out of stages v4 is about to delete:\n")
    by_group = {}
    for name, lbl, deal, active in plan:
        by_group.setdefault(name, []).append((lbl, deal, active))
    for name, rows in by_group.items():
        active = rows[0][2] if rows else True
        tag = "" if active else "  (SKIPPED — pass --close-email to include)"
        print(f"  {name} ({len(rows)} deal(s)){tag}")
        for lbl, deal, _ in rows:
            print(f"     {deal['properties'].get('dealname')!r} "
                  f"(source={deal['properties'].get('lead_source')!r}) at {lbl!r}")

    move = [(lbl, deal, target) for name, lbl, deal, active in plan for _, sts, target, _
            in [next(g for g in groups if g[0] == name)] if active]
    total_active = sum(1 for _, _, _, active in plan if active)
    total_skipped = sum(1 for _, _, _, active in plan if not active)
    print(f"\n  totals: {total_active} to move, {total_skipped} skipped "
          f"(need --close-email)")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    # dealname/lead_source per moved deal, keyed by id, from the deals_at() lookup done
    # above -- NOT from the batch/update response below, which only echoes back the
    # properties actually sent in the request (dealstage) and returns everything else,
    # including dealname, as null. Learned the hard way: the first --close-email run
    # produced an audit CSV with every dealname blank.
    detail_by_id = {deal["id"]: (deal["properties"].get("dealname") or "", lbl,
                                 deal["properties"].get("lead_source") or "", target)
                    for lbl, deal, target in move}

    audit = []
    inputs = [{"id": deal["id"], "properties": {"dealstage": stage_id[target]}}
              for lbl, deal, target in move]
    for i in range(0, len(inputs), 100):
        s, d = hs("/crm/v3/objects/deals/batch/update",
                  {"inputs": inputs[i:i+100]}, method="POST")
        if s == 207:
            print(f"WARNING: partial batch (207): {json.dumps(d.get('errors'))[:400]}")
        audit += [{"id": r["id"], "dealname": detail_by_id.get(r["id"], ("",))[0]}
                  for r in d.get("results", [])]

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(ROOT, "audit", f"deals_v4_migrate_{stamp}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)

    out_csv = os.path.join(ROOT, "audit", f"deals_v4_migrate_{stamp}.csv")
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["deal_id", "dealname", "lead_source", "from_stage", "to_stage"])
        for row in audit:
            name, lbl, source, target = detail_by_id.get(row["id"], ("", "", "", ""))
            w.writerow([row["id"], name, source, lbl, target])

    print(f"\nApplied: {len(audit)} deals moved. Audit: {out_json}")
    print(f"CSV (human-reviewable): {out_csv}")
    if not close_email:
        print("Email Campaign Sent / Email Follow-Up NOT touched — "
              "pipeline_v4_update.py will keep holding those two stages in place "
              "(not dropping them) until you re-run this script with --close-email "
              "(see docs/OPSDATA_PIPELINE.md).")

if __name__ == "__main__":
    main()
