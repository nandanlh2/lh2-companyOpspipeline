"""Move existing deals into their SOP-v3 stages.

One real move: v3 splits v2's 'Cold LinkedIn Sent' (request sent) from the new
'LinkedIn Connected' (request accepted). Every OutFlo-imported deal parked at
Cold LinkedIn Sent is there BECAUSE the connect was accepted (the importer only
pushes Connected/Replied outcomes) — so those move to LinkedIn Connected.

Guard: only deals with outflo_status = Connected move. Anything else sitting
at Cold LinkedIn Sent (e.g. hand-created deals) is reported and left alone —
acceptance can't be inferred for those.

Dry-run by default; --apply to write. Audit JSON to audit/.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_LABEL = "Company Ops Data"
FROM_STAGE = "Cold LinkedIn Sent"
TO_STAGE = "LinkedIn Connected"

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
    stage_id = {st["label"]: st["id"] for st in pipe["stages"]}
    for lbl in (FROM_STAGE, TO_STAGE):
        if lbl not in stage_id:
            sys.exit(f"ABORT: stage {lbl!r} missing — run pipeline_v3_update first")

    deals, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [
                    {"propertyName": "pipeline", "operator": "EQ", "value": pipe["id"]},
                    {"propertyName": "dealstage", "operator": "EQ",
                     "value": stage_id[FROM_STAGE]}]}],
                "properties": ["dealname", "outflo_status", "lead_source"], "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        deals += d["results"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    move = [x for x in deals if x["properties"].get("outflo_status") == "Connected"]
    stay = [x for x in deals if x["properties"].get("outflo_status") != "Connected"]

    print(f"Deals at {FROM_STAGE!r}: {len(deals)}")
    print(f"  -> move to {TO_STAGE!r} (outflo_status=Connected): {len(move)}")
    print(f"  -> stay (acceptance unknowable): {len(stay)}")
    for x in stay:
        print(f"     stays: {x['properties'].get('dealname')!r} "
              f"(source={x['properties'].get('lead_source')!r}, "
              f"outflo_status={x['properties'].get('outflo_status')!r})")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    audit = []
    inputs = [{"id": x["id"], "properties": {"dealstage": stage_id[TO_STAGE]}}
              for x in move]
    for i in range(0, len(inputs), 100):
        s, d = hs("/crm/v3/objects/deals/batch/update",
                  {"inputs": inputs[i:i+100]}, method="POST")
        if s == 207:
            print(f"WARNING: partial batch (207): {json.dumps(d.get('errors'))[:400]}")
        audit += [{"id": r["id"], "dealname": r["properties"].get("dealname"),
                   "from": FROM_STAGE, "to": TO_STAGE} for r in d.get("results", [])]

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"deals_v3_migrate_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    print(f"\nApplied: {len(audit)} deals moved to {TO_STAGE!r}. Audit: {out}")

if __name__ == "__main__":
    main()
