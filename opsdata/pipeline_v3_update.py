"""Restructure the Company Ops Data pipeline to SOP flowchart v3.

v3 changes (Company_Ops_SOP_flowchart_v3.png):
- new:     LinkedIn Connected (request accepted; splits v2's overloaded
           Cold LinkedIn Sent), One Pager Requested, One Pager Follow-Up,
           Dead/One Pager Not Shared
- renames: Message Back (Email + 2nd Msg) -> LinkedIn Follow-Up
           One Pager + Deck Shared -> One Pager Shared
- removed: nothing

Renames go through per-stage PATCH first: the pipeline PUT matches by label,
so a rename inside the PUT silently deletes + recreates the stage with a new
id (v2 lesson). One Pager holds live deals and Message Back is referenced in
125 deals' stage history — both must keep their ids. The PUT then only
inserts new stages and fixes order, with every label matching an existing
stage or being genuinely new.

Dry-run by default; --apply to write. Audit JSON to audit/.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"

RENAME = {  # old label -> new label; PATCHed in place, id preserved
    "Message Back (Email + 2nd Msg)": "LinkedIn Follow-Up",
    "One Pager + Deck Shared": "One Pager Shared",
}

V3_LIVE = [
    ("Cold LinkedIn Sent", 0.03), ("LinkedIn Connected", 0.05),
    ("LinkedIn Follow-Up", 0.08), ("Email Campaign Sent", 0.05),
    ("Email Follow-Up", 0.08), ("Replied", 0.12), ("Ghost Follow-Up", 0.12),
    ("Discovery Call", 0.25), ("One Pager Requested", 0.30),
    ("One Pager Follow-Up", 0.30), ("One Pager Shared", 0.35),
    ("Internal Evaluation (Sample)", 0.40), ("Samples Requested", 0.45),
    ("Sample Follow-Up", 0.45), ("Sample Received + Quality Check", 0.55),
    ("Commercial Negotiations", 0.65), ("Deal Contract Signed", 0.80),
    ("Token Amount Paid", 0.85), ("Data Migration Done", 0.90),
    ("Payment Initiation", 0.95),
]
V3_WON = "Closed/Won"
V3_DEAD = [
    "Dead/Cold/Not Interested", "Dead/Cold/No Reply", "Dead/Interested/No Show",
    "Dead/Discovery Call/Privacy Concerns", "Dead/One Pager Not Shared",
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

    plan_labels = [lbl for lbl, _ in V3_LIVE] + [V3_WON] + V3_DEAD
    effective = {RENAME.get(lbl, lbl) for lbl in cur}   # labels after renames
    news = [lbl for lbl in plan_labels if lbl not in effective]
    print(f"Pipeline {PIPELINE_LABEL!r} -> v3: {len(plan_labels)} stages "
          f"({len(cur)} now)")
    for old, new in RENAME.items():
        if old in cur:
            st = cur[old]
            print(f"  ~ PATCH-rename {old!r} -> {new!r} (id {st['id']} kept)")
    for lbl in news:
        print(f"  + NEW {lbl!r}")
    print("  = order + probabilities set atomically via one PUT")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    # 1) renames in place, ids untouched
    for old, new in RENAME.items():
        if old not in cur:
            continue                      # already renamed on a previous run
        st = cur[old]
        s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}/stages/{st['id']}",
                  {"label": new, "displayOrder": st["displayOrder"],
                   "metadata": st.get("metadata", {})}, method="PATCH")
        print(f"renamed: {old!r} -> {new!r} (id {d.get('id')})")

    # 2) refetch, then one atomic PUT for inserts + order + probabilities
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
    for lbl, prob in V3_LIVE:
        entry(lbl, prob)
    entry(V3_WON, 1.0, closed_won=True)
    for lbl in V3_DEAD:
        entry(lbl, 0.0)

    s, d = hs(f"/crm/v3/pipelines/deals/{pipe['id']}",
              {"label": pipe["label"], "displayOrder": pipe.get("displayOrder", 0),
               "stages": stages}, method="PUT")

    # loud check: every previously-existing stage must have kept its id
    after = {st["label"]: st["id"] for st in d["stages"]}
    drift = [(lbl, cur[lbl]["id"], after.get(lbl))
             for lbl in after if lbl in cur and after[lbl] != cur[lbl]["id"]]
    for lbl, old_id, new_id in drift:
        print(f"WARNING: stage {lbl!r} id changed {old_id} -> {new_id}")

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"pipeline_v3_update_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"before": cur, "after": d}, f, indent=2, default=str)
    print(f"\nApplied: {len(d['stages'])} stages. Audit: {out}")
    for lbl in ("LinkedIn Connected", "LinkedIn Follow-Up", "One Pager Requested",
                "One Pager Follow-Up", "One Pager Shared", "Dead/One Pager Not Shared"):
        print(f"  {lbl:<28} id={after.get(lbl, 'MISSING!')}")

if __name__ == "__main__":
    main()
