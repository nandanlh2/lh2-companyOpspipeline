"""Pull Connected/Replied leads from the OutFlo 'Company Ops' campaign, push to HubSpot.

Dry-run by default: prints the full plan table and writes nothing. --apply to push.

Shape of the push:
- one DEAL per company (deal name = company, never the person), in the
  'Company Ops Data' pipeline; one CONTACT per person, associated to the deal
- Connected -> stage 'Cold LinkedIn Sent'; Replied -> stage 'Replied'
  (reply beats connect when a company has both)
- provenance frozen at import: lead_source, outflo_status, outflo_campaign,
  outflo_lead_id, outflo_assigned_account, outflo_last_action_at
- all deals and contacts owned by Kartik Pillai (only owner in this portal);
  the real OutFlo sender is kept in outflo_assigned_account for later re-split
- replied_at comes from the LinkedIn conversation when the last message is from
  the lead; otherwise falls back to OutFlo's Last Action At
- idempotent: existing deals (matched by outflo_lead_id, then by company name in
  this pipeline) are skipped; stage is only ever promoted Cold LinkedIn Sent ->
  Replied, never demoted, and never touched once a human moved the deal further
- no phone numbers are pushed at all: OutFlo has none, and pushing an unverified
  number would break the no-non-Indian-number rule
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER_KARTIK = "96316911"
PIPELINE_LABEL = "Company Ops Data"
LEAD_SOURCE = "Outflo Outreach ( Startups )"
STAGE_CONNECTED, STAGE_REPLIED = "Cold LinkedIn Sent", "Replied"

def _env():
    d = {}
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                d[k] = v.strip()
    return d

ENV = _env()

def _req(url, headers, payload=None, method=None):
    for attempt in range(5):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} on {url}: {e.read().decode(errors='replace')[:500]}")

def hs(path, payload=None, method=None):
    return _req("https://api.hubapi.com" + path,
                {"Authorization": f"Bearer {ENV['HUBSPOT_API_KEY']}",
                 "Content-Type": "application/json"}, payload, method)

def outflo(path):
    s, d = _req("https://live.outflo.in" + path,
                {"x-api-key": ENV["OUTFLO_API_KEY"], "Accept": "application/json"})
    return d["data"]

def norm_url(u):
    u = (u or "").strip().lower().split("?")[0]
    return u.rstrip("/")

def to_ms(iso):
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp() * 1000)

def mailer_guard():
    # documented incident: an armed mailer once fanned out 9 mails over a bulk
    # owner write; the guard lives in the script, not in anyone's head
    q = subprocess.run(["schtasks", "/query", "/tn", "LH2 VCF lead notifier",
                        "/fo", "LIST", "/v"], capture_output=True, text=True)
    if q.returncode == 0 and "Disabled" not in q.stdout:
        sys.exit("ABORT: 'LH2 VCF lead notifier' task is armed — it would mail "
                 "everyone on a bulk owner write. Disable it first.")

def pull_outflo():
    camps = outflo("/api/public/campaigns")["campaigns"]
    hits = [c for c in camps if "company ops" in c["name"].lower() and c["status"] == "ACTIVE"]
    if len(hits) != 1:
        sys.exit(f"ABORT: expected exactly one ACTIVE 'company ops' campaign, found "
                 f"{[(c['name'], c['status']) for c in hits]}")
    camp = hits[0]

    d = outflo(f"/api/public/campaigns/{camp['id']}/leads")
    rows = d.get("rows") or d.get("leads") or []
    push = [r for r in rows
            if r.get("Connection Status") in ("Connected", "Previously Connected")
            or r.get("Reply Status") == "Replied"]

    # conversation lastMessage.sentAt is a real reply time only when the lead sent
    # it; the campaignId filter on this endpoint is unreliable, so match by URL
    replied_at = {}
    try:
        convs = outflo(f"/api/public/conversations?campaignId={camp['id']}")["conversations"]
        for c in convs:
            att, lm = c.get("attendee") or {}, c.get("lastMessage") or {}
            if lm.get("senderUrn") and lm.get("senderUrn") == att.get("urn"):
                replied_at[norm_url(att.get("profileUrl"))] = lm.get("sentAt")
    except SystemExit:
        raise
    except Exception as e:
        print(f"note: conversation enrichment unavailable ({e!r}); "
              "falling back to Last Action At for replied_at")
    return camp, push, replied_at

def build_units(camp, push, replied_at_by_url):
    # one deal per company; a replied member sets the whole company to Replied
    units = {}
    for r in push:
        company = (r.get("Company") or "").strip()
        if not company:
            print(f"  SKIP (no company, deal would be unnameable): {r.get('Lead')!r}")
            continue
        u = units.setdefault(company.casefold(), {"company": company, "leads": []})
        u["leads"].append(r)
    for u in units.values():
        replied = [r for r in u["leads"] if r.get("Reply Status") == "Replied"]
        u["status"] = "Replied" if replied else "Connected"
        u["stage"] = STAGE_REPLIED if replied else STAGE_CONNECTED
        rep = replied[0] if replied else u["leads"][0]
        u["rep"] = rep
        u["replied_at"] = (replied_at_by_url.get(norm_url(rep.get("linkedinUrl")))
                           or rep.get("Last Action At")) if replied else None
    return list(units.values())

def hubspot_state():
    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    stage_id = {st["label"]: st["id"] for st in pipe["stages"]}
    stage_label = {v: k for k, v in stage_id.items()}

    deals, after = {}, None
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": "pipeline",
                                               "operator": "EQ", "value": pipe["id"]}]}],
                "properties": ["dealname", "dealstage", "outflo_lead_id"], "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        for r in d.get("results", []):
            deals[r["id"]] = r["properties"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    contacts, after = {}, None
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": "linkedin_url",
                                               "operator": "HAS_PROPERTY"}]}],
                "properties": ["linkedin_url"], "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/contacts/search", body, method="POST")
        for r in d.get("results", []):
            contacts[norm_url(r["properties"].get("linkedin_url"))] = r["id"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return pipe, stage_id, stage_label, deals, contacts

def main():
    apply = "--apply" in sys.argv
    camp, push, replied_at_by_url = pull_outflo()
    print(f"OutFlo campaign: {camp['name']!r} ({camp['id']}) — {len(push)} pushable leads")
    units = build_units(camp, push, replied_at_by_url)

    pipe, stage_id, stage_label, existing_deals, existing_contacts = hubspot_state()
    for lbl in (STAGE_CONNECTED, STAGE_REPLIED):
        if lbl not in stage_id:
            sys.exit(f"ABORT: stage {lbl!r} missing from pipeline {PIPELINE_LABEL!r}")

    by_lead_id = {p.get("outflo_lead_id"): (i, p) for i, p in existing_deals.items()
                  if p.get("outflo_lead_id")}
    by_name = {p.get("dealname", "").casefold(): (i, p) for i, p in existing_deals.items()}

    plan = []
    for u in units:
        rep_lead_id = u["rep"].get("leadId")
        hit = by_lead_id.get(rep_lead_id) or by_name.get(u["company"].casefold())
        if not hit:
            action = "CREATE"
        else:
            deal_id, props = hit
            cur = stage_label.get(props.get("dealstage"), props.get("dealstage"))
            if cur == STAGE_CONNECTED and u["stage"] == STAGE_REPLIED:
                action, u["deal_id"] = "PROMOTE -> Replied", deal_id
            else:
                # a human may have moved it further along — never touch that
                action, u["deal_id"] = f"SKIP (exists at {cur!r})", deal_id
        u["action"] = action
        n_new = sum(1 for r in u["leads"]
                    if norm_url(r.get("linkedinUrl")) not in existing_contacts)
        plan.append((u["company"], u["status"], u["stage"],
                     f"{len(u['leads'])} ({n_new} new)", u["rep"]["Assigned Account"], action))

    print(f"\nPlan — pipeline {PIPELINE_LABEL!r}, owner Kartik Pillai ({OWNER_KARTIK}), "
          f"lead_source {LEAD_SOURCE!r}:\n")
    print(f"  {'company':<34} {'status':<10} {'stage':<20} {'contacts':<12} {'sender':<14} action")
    print(f"  {'-'*34} {'-'*10} {'-'*20} {'-'*12} {'-'*14} {'-'*20}")
    for row in sorted(plan, key=lambda x: (x[5] != 'CREATE', x[1] != 'Replied', x[0].lower())):
        print(f"  {row[0][:34]:<34} {row[1]:<10} {row[2]:<20} {row[3]:<12} {row[4]:<14} {row[5]}")
    creates = [u for u in units if u["action"] == "CREATE"]
    promotes = [u for u in units if u["action"].startswith("PROMOTE")]
    print(f"\n  totals: {len(creates)} deals to create, {len(promotes)} to promote, "
          f"{len(units) - len(creates) - len(promotes)} skipped; "
          f"{sum(len(u['leads']) for u in units)} contacts in scope")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to push.")
        return

    mailer_guard()
    audit = {"campaign": {"id": camp["id"], "name": camp["name"]},
             "contacts_created": [], "deals_created": [], "deals_promoted": []}

    # contacts first: deal creation needs their ids for inline association
    to_create = {}
    for u in units:
        if u["action"].startswith("SKIP"):
            continue
        for r in u["leads"]:
            url = norm_url(r.get("linkedinUrl"))
            if url and url not in existing_contacts and url not in to_create:
                to_create[url] = {"properties": {
                    "firstname": r.get("First Name") or "",
                    "lastname": r.get("Last Name") or "",
                    "jobtitle": r.get("Title") or "",
                    "company": r.get("Company") or "",
                    "linkedin_url": r.get("linkedinUrl"),
                    "hubspot_owner_id": OWNER_KARTIK,
                }}
    batch = list(to_create.values())
    for i in range(0, len(batch), 100):
        s, d = hs("/crm/v3/objects/contacts/batch/create",
                  {"inputs": batch[i:i+100]}, method="POST")
        if s == 207:
            print(f"WARNING: partial contact batch (207): {json.dumps(d.get('errors'))[:400]}")
        for r in d.get("results", []):
            existing_contacts[norm_url(r["properties"].get("linkedin_url"))] = r["id"]
            audit["contacts_created"].append({"id": r["id"],
                                              "linkedin_url": r["properties"].get("linkedin_url")})

    deal_inputs = []
    for u in creates:
        rep = u["rep"]
        props = {
            "dealname": u["company"],
            "pipeline": pipe["id"],
            "dealstage": stage_id[u["stage"]],
            "hubspot_owner_id": OWNER_KARTIK,
            "lead_source": LEAD_SOURCE,
            "outflo_status": u["status"],
            "outflo_campaign": camp["name"],
            "outflo_lead_id": rep.get("leadId"),
            "outflo_assigned_account": rep.get("Assigned Account") or "",
            "linkedin_url": rep.get("linkedinUrl"),
        }
        ms = to_ms(rep.get("Last Action At"))
        if ms:
            props["outflo_last_action_at"] = ms
        if u["status"] == "Replied":
            ms = to_ms(u["replied_at"])
            if ms:
                props["replied_at"] = ms
        assoc = [{"to": {"id": existing_contacts[norm_url(r.get("linkedinUrl"))]},
                  "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]}
                 for r in u["leads"] if norm_url(r.get("linkedinUrl")) in existing_contacts]
        deal_inputs.append({"properties": props, "associations": assoc})
    for i in range(0, len(deal_inputs), 100):
        s, d = hs("/crm/v3/objects/deals/batch/create",
                  {"inputs": deal_inputs[i:i+100]}, method="POST")
        if s == 207:
            print(f"WARNING: partial deal batch (207): {json.dumps(d.get('errors'))[:400]}")
        for r in d.get("results", []):
            audit["deals_created"].append({"id": r["id"],
                                           "dealname": r["properties"].get("dealname"),
                                           "dealstage": r["properties"].get("dealstage")})

    for u in promotes:
        body = {"properties": {"dealstage": stage_id[STAGE_REPLIED],
                               "outflo_status": "Replied"}}
        ms = to_ms(u["replied_at"])
        if ms:
            body["properties"]["replied_at"] = ms
        s, d = hs(f"/crm/v3/objects/deals/{u['deal_id']}", body, method="PATCH")
        audit["deals_promoted"].append({"id": u["deal_id"], "dealname": u["company"]})

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"outflo_pull_push_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    print(f"\nApplied: {len(audit['contacts_created'])} contacts created, "
          f"{len(audit['deals_created'])} deals created, "
          f"{len(audit['deals_promoted'])} promoted. Audit: {out}")

if __name__ == "__main__":
    main()
