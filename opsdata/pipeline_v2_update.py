"""Restructure the Company Ops Data pipeline to SOP flowchart v2.

v2 changes (Company Ops_SOP_flowchart_v2.png):
- renames: GMeet Fixed -> Discovery Call;
           Sample Received + Interest Gauge -> Sample Received + Quality Check;
           Dead/GMeet/Privacy Concerns -> Dead/Discovery Call/Privacy Concerns
- new:     Email Campaign Sent (2nd entry branch), Email Follow-Up,
           Dead/Sample/Bad Quality
- reorder: Token Amount Paid now AFTER Deal Contract Signed
- removed: Cold Lead, Dead/Cold/WrongFit,
           Dead/No Interest from Demand/Wrong Fit-Rejected

Removal policy: a stage is deleted only if no live or archived deal is at it
NOW and none ever passed through it (dealstage history). Otherwise it is kept,
relabelled '(retired)', and parked at the end — same convention as the main
portal's 'Call Attempted (retired)'.

The whole pipeline is written in ONE PUT: sequential per-stage PATCHes make
HubSpot re-sequence between calls and the order comes out wrong.

Dry-run by default; --apply to write. Audit JSON to audit/.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"

RENAME = {
    "GMeet Fixed": "Discovery Call",
    "Sample Received + Interest Gauge": "Sample Received + Quality Check",
    "Dead/GMeet/Privacy Concerns": "Dead/Discovery Call/Privacy Concerns",
}
REMOVE = {"Cold Lead", "Dead/Cold/WrongFit",
          "Dead/No Interest from Demand/Wrong Fit-Rejected"}

# label -> probability, in v2 display order; None = create new
V2_LIVE = [
    ("Cold LinkedIn Sent", 0.05), ("Email Campaign Sent", 0.05),
    ("Message Back (Email + 2nd Msg)", 0.08), ("Email Follow-Up", 0.08),
    ("Replied", 0.12), ("Ghost Follow-Up", 0.12), ("Discovery Call", 0.25),
    ("One Pager + Deck Shared", 0.35), ("Internal Evaluation (Sample)", 0.40),
    ("Samples Requested", 0.45), ("Sample Follow-Up", 0.45),
    ("Sample Received + Quality Check", 0.55), ("Commercial Negotiations", 0.65),
    ("Deal Contract Signed", 0.80), ("Token Amount Paid", 0.85),
    ("Data Migration Done", 0.90), ("Payment Initiation", 0.95),
]
V2_WON = "Closed/Won"
V2_DEAD = [
    "Dead/Cold/Not Interested", "Dead/Cold/No Reply", "Dead/Interested/No Show",
    "Dead/Discovery Call/Privacy Concerns",
    "Dead/Sample Not Collected/Wrong Fit-Rejected",
    "Dead/Sample Not Received/Company No Show", "Dead/Sample/Bad Quality",
    "Dead/Negotiations/Pricing", "Dead/Negotiations/Contractual",
    "Dead/Migration/Failed",
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

def all_deal_ids(pipe_id):
    ids, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": "pipeline",
                                               "operator": "EQ", "value": pipe_id}]}],
                "properties": ["dealname"], "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        ids += [r["id"] for r in d.get("results", [])]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return ids

def stages_ever_used(deal_ids):
    used = set()
    for i in range(0, len(deal_ids), 50):   # history reads cap at 50 inputs
        s, d = hs("/crm/v3/objects/deals/batch/read",
                  {"propertiesWithHistory": ["dealstage"], "properties": ["dealstage"],
                   "inputs": [{"id": x} for x in deal_ids[i:i+50]]}, method="POST")
        for r in d.get("results", []):
            hist = r.get("propertiesWithHistory", {}).get("dealstage", [])
            used |= {h["value"] for h in hist}
            used.add(r["properties"].get("dealstage"))
    return used

def main():
    apply = "--apply" in sys.argv
    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    cur = {st["label"]: st for st in pipe["stages"]}

    deal_ids = all_deal_ids(pipe["id"])
    # archived deals hold history too — account for them, not ignore them
    s, d = hs("/crm/v3/objects/deals?archived=true&limit=100")
    archived = [r["id"] for r in d.get("results", [])
                if r.get("properties", {}).get("pipeline") in (None, pipe["id"])]
    used_ids = stages_ever_used(deal_ids + archived)

    removable, retiring = [], []
    for lbl in REMOVE:
        if lbl not in cur:
            continue
        (removable if cur[lbl]["id"] not in used_ids else retiring).append(lbl)

    stages = []

    def entry(label, prob, closed_won=False, old_label=None):
        src = cur.get(old_label or label)
        st = {"label": label, "displayOrder": len(stages),
              "metadata": {"isClosed": "true" if (closed_won or prob == 0.0) else "false",
                           "probability": str(prob)}}
        if src:
            st["id"] = src["id"]
        stages.append((st, "RENAME" if (old_label and old_label != label) else
                       ("KEEP" if src else "NEW")))

    old_of = {v: k for k, v in RENAME.items()}
    for lbl, prob in V2_LIVE:
        entry(lbl, prob, old_label=old_of.get(lbl))
    entry(V2_WON, 1.0, closed_won=True)
    for lbl in V2_DEAD:
        entry(lbl, 0.0, old_label=old_of.get(lbl))
    for lbl in retiring:
        entry(f"{lbl} (retired)", 0.0, old_label=lbl)

    print(f"Pipeline {PIPELINE_LABEL!r} -> v2 ({len(stages)} stages), "
          f"{len(deal_ids)} live deals checked, {len(archived)} archived:\n")
    for st, action in stages:
        mark = {"NEW": "+", "RENAME": "~", "KEEP": " "}[action]
        print(f"  {mark} {st['displayOrder']:>2}  {st['label']:<48} p={st['metadata']['probability']}")
    for lbl in removable:
        print(f"  - DELETE {lbl!r} (never used by any deal, live or archived)")
    for lbl in retiring:
        print(f"  ! RETIRE {lbl!r} (referenced in deal history — kept at end)")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    payload = {"label": pipe["label"], "displayOrder": pipe.get("displayOrder", 0),
               "stages": [st for st, _ in stages]}
    s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}", payload, method="PUT")

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"pipeline_v2_update_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"before": pipe, "after": d}, f, indent=2)
    final = {st["label"]: st["id"] for st in d["stages"]}
    print(f"\nApplied: pipeline now has {len(final)} stages. Audit: {out}")
    for lbl in ("Email Campaign Sent", "Email Follow-Up", "Discovery Call",
                "Sample Received + Quality Check", "Dead/Sample/Bad Quality"):
        print(f"  {lbl:<36} id={final.get(lbl, 'MISSING!')}")

if __name__ == "__main__":
    main()
