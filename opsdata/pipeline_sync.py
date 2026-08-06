"""Sync the 'Company Ops Data' deal pipeline to the OPSDATA SOP flowchart.

Dry-run by default: prints the current->desired plan and writes nothing.
Run with --apply to PUT the pipeline. The PUT is a single atomic replace of the
whole stage list — sequential per-stage PATCHes make HubSpot re-sequence between
calls and the order comes out wrong (learned the hard way on the main portal).
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_ID = "2464812771"

def _key():
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("HUBSPOT_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("HUBSPOT_API_KEY missing from .env")

KEY = _key()

def hs(path, payload=None, method=None):
    # retry only transient statuses; everything else fails loudly
    for attempt in range(5):
        req = urllib.request.Request(
            "https://api.hubapi.com" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} on {path}: {e.read().decode(errors='replace')}")

# The flowchart's blue boxes, in funnel order. 'Begin Here' and 'Profile
# PreScreen' are deliberately NOT stages: one is the diagram's start marker, the
# other is a decision — an activity, not an outcome. Their state ("lead loaded,
# not yet screened/contacted") is the single entry stage Cold Lead; the
# prescreen's outcomes are the next stages (Cold LinkedIn Sent / Dead/Cold/WrongFit).
LIVE = [
    ("Cold Lead",                        "0.02"),
    ("Cold LinkedIn Sent",               "0.04"),
    ("Message Back (Email + 2nd Msg)",   "0.06"),
    ("Replied",                          "0.10"),
    ("Ghost Follow-Up",                  "0.08"),
    ("GMeet Fixed",                      "0.20"),
    ("One Pager + Deck Shared",          "0.30"),
    ("Internal Evaluation (Sample)",     "0.35"),
    ("Samples Requested",                "0.40"),
    ("Sample Follow-Up",                 "0.38"),
    ("Sample Received + Interest Gauge", "0.50"),
    ("Token Amount Paid",                "0.65"),
    ("Commercial Negotiations",          "0.70"),
    ("Deal Contract Signed",             "0.85"),
    ("Data Migration Done",              "0.90"),
    ("Payment Initiation",               "0.95"),
    ("Closed/Won",                       "1.0"),
]

# Dead/<where it died>/<why> — middle segment makes per-stage survival computable.
# Ordered by where in the funnel the death happens.
DEAD = [
    "Dead/Cold/WrongFit",
    "Dead/Cold/Not Interested",
    "Dead/Cold/No Reply",
    "Dead/Interested/No Show",
    "Dead/GMeet/Privacy Concerns",
    "Dead/Sample Not Collected/Wrong Fit-Rejected",
    "Dead/Sample Not Received/Company No Show",
    "Dead/No Interest from Demand/Wrong Fit-Rejected",
    "Dead/Negotiations/Pricing",
    "Dead/Negotiations/Contractual",
    "Dead/Migration/Failed",
]

def desired_stages():
    stages = [{"label": l, "displayOrder": i, "metadata": {"probability": p}}
              for i, (l, p) in enumerate(LIVE)]
    stages += [{"label": l, "displayOrder": len(LIVE) + i, "metadata": {"probability": "0.0"}}
               for i, l in enumerate(DEAD)]
    return stages

def deal_count_in_stage(stage_id):
    s, d = hs("/crm/v3/objects/deals/search",
              {"filterGroups": [{"filters": [{"propertyName": "dealstage",
                                              "operator": "EQ", "value": stage_id}]}],
               "limit": 1}, method="POST")
    n = d.get("total", 0)
    # search never returns archived deals, and archived stage history is still real
    after = None
    while True:
        q = f"/crm/v3/objects/deals?limit=100&archived=true&properties=dealstage"
        if after:
            q += f"&after={after}"
        s, d = hs(q)
        n += sum(1 for r in d.get("results", [])
                 if r["properties"].get("dealstage") == stage_id)
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return n

def main():
    apply = "--apply" in sys.argv

    s, cur = hs(f"/crm/v3/pipelines/deals/{PIPELINE_ID}")
    by_label = {st["label"]: st for st in cur["stages"]}
    want = desired_stages()
    want_labels = {st["label"] for st in want}

    # keep existing stage ids where the label survives, so stage identity is stable
    for st in want:
        if st["label"] in by_label:
            st["id"] = by_label[st["label"]]["id"]

    drops = [st for st in cur["stages"] if st["label"] not in want_labels]
    for st in drops:
        n = deal_count_in_stage(st["id"])
        if n:
            sys.exit(f"ABORT: stage {st['label']!r} still holds {n} deal(s) — "
                     "dropping it would orphan them. Move them first.")

    print(f"Pipeline {cur['label']!r} ({PIPELINE_ID}) — plan:\n")
    print(f"  {'#':>2}  {'stage':<48} {'prob':>5}  action")
    print(f"  {'-'*2}  {'-'*48} {'-'*5}  {'-'*30}")
    for st in want:
        old = by_label.get(st["label"])
        if not old:
            action = "CREATE"
        else:
            changes = []
            if old["displayOrder"] != st["displayOrder"]:
                changes.append(f"move {old['displayOrder']}->{st['displayOrder']}")
            if str(old["metadata"].get("probability")) != st["metadata"]["probability"]:
                changes.append(f"prob {old['metadata'].get('probability')}->{st['metadata']['probability']}")
            action = ", ".join(changes) or "keep"
        print(f"  {st['displayOrder']:>2}  {st['label']:<48} {st['metadata']['probability']:>5}  {action}")
    for st in drops:
        print(f"  {'--':>2}  {st['label']:<48} {'':>5}  DROP (0 deals, incl. archived)")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to PUT the pipeline.")
        return

    body = {"label": cur["label"], "displayOrder": cur.get("displayOrder", 0), "stages": want}
    s, after = hs(f"/crm/v3/pipelines/deals/{PIPELINE_ID}", body, method="PUT")

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"pipeline_sync_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"before": cur, "after": after}, f, indent=2)
    print(f"\nApplied (HTTP {s}). {len(after['stages'])} stages live. Audit: {out}")

if __name__ == "__main__":
    main()
