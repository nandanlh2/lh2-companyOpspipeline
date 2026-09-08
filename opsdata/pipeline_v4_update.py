"""Restructure the Company Ops Data pipeline to SOP flowchart v4
(lh2_deal_stage_flowchart_v3.svg — the team's new diagram; despite the filename it
supersedes Company_Ops_SOP_flowchart_v3.png).

v4 changes — see docs/OPSDATA_PIPELINE.md for the full rationale:
- new:      Cold called assigned, 1st interest sent, Call rescheduled, LOI signed,
            Dead: 1st Interest, Dead: Discovery Call / Rejected by LH2,
            Dead: Discovery Call / Not Interested, Dead: One Pager / Less Data,
            Dead: LOI / Terms Not Agreed, Dead: Email Campaign / Branch Retired
- renames:  Cold LinkedIn Sent -> LinkedIn sent, LinkedIn Connected -> LinkedIn connected,
            Ghost Follow-Up -> 1st interest follow up, Discovery Call -> Discovery call,
            One Pager Requested -> One pager requested,
            One Pager Follow-Up -> One pager follow up, One Pager Shared -> One pager received,
            Deal Contract Signed -> Contract signed (becomes the closed-won stage),
            Dead/Cold/Not Interested -> Dead: Replied / Not Interested,
            Dead/Interested/No Show -> Dead: Discovery Call / No Show,
            Dead/One Pager Not Shared -> Dead: One Pager / Not Received
- removed:  Email Campaign Sent, Email Follow-Up (Gmail branch retired),
            Internal Evaluation (Sample), Samples Requested, Sample Follow-Up,
            Sample Received + Quality Check, Commercial Negotiations,
            Token Amount Paid, Data Migration Done, Payment Initiation, Closed/Won,
            Dead/Cold/No Reply, Dead/Discovery Call/Privacy Concerns,
            Dead/Sample Not Collected/Wrong Fit-Rejected,
            Dead/Sample Not Received/Company No Show, Dead/Sample/Bad Quality,
            Dead/Negotiations/Pricing, Dead/Negotiations/Contractual, Dead/Migration/Failed

Renames go through per-stage PATCH first: the pipeline PUT matches by label, so a rename
inside the PUT silently deletes + recreates the stage with a new id (v2 lesson).

SAFETY / ORDERING: a stage can only be DROPPED from the PUT if it holds ZERO live deals
-- dropping it otherwise either orphans those deals or gets rejected by the API, and
either way is a production incident on real CRM data (see the main-portal incident in
context.md SS3). But deals_v4_migrate.py needs the NEW stage 'LOI signed' to already
exist before it can move deals into it -- so this script is idempotent and safe to run
before deals are migrated: any TO_REMOVE stage that still has deals on it is kept in
place (not dropped) rather than aborting the whole run. Renames and brand-new stages
(LOI signed, Cold called assigned, etc.) apply immediately; a stage only actually
disappears from the pipeline once a later run finds it empty.

Normal sequence:
  1. python opsdata/pipeline_v4_update.py --apply       (creates LOI signed etc.,
     keeps Email Campaign Sent / sample stages in place since they still hold deals)
  2. python opsdata/deals_v4_migrate.py --apply          (moves deals out of the
     doomed stages, now that their targets exist)
  3. python opsdata/pipeline_v4_update.py --apply again  (drops the now-empty stages)

Dry-run by default; --apply to write. Audit JSON to audit/.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"

RENAME = {  # old label -> new label; PATCHed in place, id preserved
    "Cold LinkedIn Sent": "LinkedIn sent",
    "LinkedIn Connected": "LinkedIn connected",
    "Ghost Follow-Up": "1st interest follow up",
    "Discovery Call": "Discovery call",
    "One Pager Requested": "One pager requested",
    "One Pager Follow-Up": "One pager follow up",
    "One Pager Shared": "One pager received",
    "Deal Contract Signed": "Contract signed",
    "Dead/Cold/Not Interested": "Dead: Replied / Not Interested",
    "Dead/Interested/No Show": "Dead: Discovery Call / No Show",
    "Dead/One Pager Not Shared": "Dead: One Pager / Not Received",
}

# Stages that must be EMPTY before the PUT runs, because they are not in V4_LIVE/V4_DEAD
# below and therefore fall out of the pipeline entirely. Email Campaign Sent / Email
# Follow-Up are listed here too -- run deals_v4_migrate.py --close-email to move their
# deals to the new 'Dead: Email Campaign / Branch Retired' stage first (see
# docs/OPSDATA_PIPELINE.md "Gmail cold-email import"), or this run just holds them back.
TO_REMOVE = [
    "LinkedIn Follow-Up", "Email Campaign Sent", "Email Follow-Up",
    "Internal Evaluation (Sample)", "Samples Requested", "Sample Follow-Up",
    "Sample Received + Quality Check", "Commercial Negotiations",
    "Token Amount Paid", "Data Migration Done", "Payment Initiation", "Closed/Won",
    "Dead/Cold/No Reply", "Dead/Discovery Call/Privacy Concerns",
    "Dead/Sample Not Collected/Wrong Fit-Rejected",
    "Dead/Sample Not Received/Company No Show", "Dead/Sample/Bad Quality",
    "Dead/Negotiations/Pricing", "Dead/Negotiations/Contractual", "Dead/Migration/Failed",
]

V4_LIVE = [
    ("LinkedIn sent", 0.03), ("Cold called assigned", 0.03),
    ("LinkedIn connected", 0.05), ("Replied", 0.12), ("1st interest sent", 0.14),
    ("1st interest follow up", 0.16), ("Discovery call", 0.25),
    ("Call rescheduled", 0.25), ("One pager requested", 0.30),
    ("One pager follow up", 0.30), ("One pager received", 0.35),
    ("LOI signed", 0.55),
]
V4_WON = "Contract signed"  # was a plain stage in v3; becomes closed-won here
V4_DEAD = [
    "Dead: Replied / Not Interested", "Dead: 1st Interest",
    "Dead: Discovery Call / No Show", "Dead: Discovery Call / Rejected by LH2",
    "Dead: Discovery Call / Not Interested", "Dead: One Pager / Not Received",
    "Dead: One Pager / Less Data", "Dead: LOI / Terms Not Agreed",
    "Dead: Email Campaign / Branch Retired",
]

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

def deal_count(pipeline_id, stage_id):
    s, d = hs("/crm/v3/objects/deals/search",
              {"filterGroups": [{"filters": [
                  {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
                  {"propertyName": "dealstage", "operator": "EQ", "value": stage_id}]}],
               "limit": 1}, method="POST")
    return d.get("total", 0)

def main():
    apply = "--apply" in sys.argv
    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    cur = {st["label"]: st for st in pipe["stages"]}

    for old in RENAME:
        if old not in cur and RENAME[old] not in cur:
            sys.exit(f"ABORT: expected stage {old!r} (or already-renamed "
                     f"{RENAME[old]!r}) not found")

    # Every stage about to fall out of the target list gets checked against LIVE deal
    # counts right now. Empty ones are dropped for real; nonzero ones are KEPT IN PLACE
    # (appended after the dead stages, at probability 0) so this run never orphans a
    # deal -- it just doesn't finish the removal yet. Re-run after deals_v4_migrate.py.
    print("Checking live deal counts on stages slated for removal...")
    still_present, dropped = [], []
    for lbl in TO_REMOVE:
        if lbl not in cur:
            continue  # already removed on a previous run
        n = deal_count(pipe["id"], cur[lbl]["id"])
        if n:
            print(f"  {lbl:<45} {n} deal(s)  <-- kept in place, not dropped yet")
            still_present.append(lbl)
        else:
            print(f"  {lbl:<45} 0 deals  -- will be dropped")
            dropped.append(lbl)

    plan_labels = [lbl for lbl, _ in V4_LIVE] + [V4_WON] + V4_DEAD
    effective = {RENAME.get(lbl, lbl) for lbl in cur}   # labels after renames
    news = [lbl for lbl in plan_labels if lbl not in effective]
    print(f"\nPipeline {PIPELINE_LABEL!r} -> v4: {len(plan_labels)} target stages "
          f"+ {len(still_present)} held back ({len(cur)} now)")
    for old, new in RENAME.items():
        if old in cur:
            st = cur[old]
            tag = " [becomes closed-won]" if new == V4_WON else ""
            print(f"  ~ PATCH-rename {old!r} -> {new!r} (id {st['id']} kept){tag}")
    for lbl in news:
        print(f"  + NEW {lbl!r}")
    for lbl in dropped:
        print(f"  - REMOVE {lbl!r} (id {cur[lbl]['id']}, 0 deals confirmed above)")
    for lbl in still_present:
        print(f"  = HOLD {lbl!r} (id {cur[lbl]['id']}) -- still has deals, not removed this run")
    print("  = order + probabilities set atomically via one PUT")
    if still_present:
        print(f"\nNOTE: {len(still_present)} stage(s) not removed this run — run "
              f"deals_v4_migrate.py (with --close-email if Email Campaign Sent / "
              f"Email Follow-Up are in this list) then re-run this script.")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    # 1) renames in place, ids untouched
    for old, new in RENAME.items():
        if old not in cur:
            continue                      # already renamed on a previous run
        st = cur[old]
        is_won = new == V4_WON
        meta = dict(st.get("metadata", {}))
        if is_won:
            meta["isClosed"] = "true"
            meta["probability"] = "1.0"
        s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}/stages/{st['id']}",
                  {"label": new, "displayOrder": st["displayOrder"], "metadata": meta},
                  method="PATCH")
        print(f"renamed: {old!r} -> {new!r} (id {d.get('id')})")

    # 2) refetch, then one atomic PUT for inserts + removals + order + probabilities
    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next(p for p in d["results"] if p["label"] == PIPELINE_LABEL)
    cur = {st["label"]: st for st in pipe["stages"]}

    stages = []
    def entry(label, prob, closed_won=False):
        st = {"label": label, "displayOrder": len(stages),
              "metadata": {"isClosed": "true" if (closed_won or prob == 0.0) else "false",
                           "probability": str(prob)}}
        if label in cur:
            st["id"] = cur[label]["id"]
        stages.append(st)
    for lbl, prob in V4_LIVE:
        entry(lbl, prob)
    entry(V4_WON, 1.0, closed_won=True)
    for lbl in V4_DEAD:
        entry(lbl, 0.0)
    # Stages still holding deals are appended as-is (own current label, id, metadata) --
    # not part of the v4 shape, just not orphaned. A re-run after deals_v4_migrate.py
    # will find them empty and drop them for real.
    for lbl in still_present:
        if lbl in cur:
            st = cur[lbl]
            stages.append({"label": lbl, "displayOrder": len(stages),
                           "metadata": st.get("metadata", {}), "id": st["id"]})

    s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}",
              {"label": pipe["label"], "displayOrder": pipe.get("displayOrder", 0),
               "stages": stages}, method="PUT")

    # loud check: every previously-existing stage we KEPT must have kept its id
    after = {st["label"]: st["id"] for st in d["stages"]}
    drift = [(lbl, cur[lbl]["id"], after.get(lbl))
             for lbl in after if lbl in cur and after[lbl] != cur[lbl]["id"]]
    for lbl, old_id, new_id in drift:
        print(f"WARNING: stage {lbl!r} id changed {old_id} -> {new_id}")

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"pipeline_v4_update_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"before": cur, "after": d}, f, indent=2, default=str)
    print(f"\nApplied: {len(d['stages'])} stages. Audit: {out}")
    for lbl in plan_labels:
        print(f"  {lbl:<40} id={after.get(lbl, 'MISSING!')}")

if __name__ == "__main__":
    main()
