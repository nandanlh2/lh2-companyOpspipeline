"""Pull Kartik's cold-email campaign from Gmail, push recipients to HubSpot.

Campaign definition (per the user): a SENT mail whose body contains a Calendly
link. Subject lines drifted across sends, the Calendly link did not.

Dry-run by default; --apply to push. Same shape as outflo_pull_push.py:
- one deal per company; company = email domain (name derived from the domain,
  since Gmail carries no company field); contact = the recipient
- everyone -> stage 'Message Back (Email + 2nd Msg)' (the flowchart's cold-email
  state); a real human reply -> 'Replied' with replied_at from the thread
- email_status: Awaiting Reply / Replied / Bounced. Bounces stay live but
  flagged — the SOP has no bounce outcome; a human decides their fate.
  Auto-replies (OOO) count as Awaiting Reply, not Replied.
- dedupe: deal by lh2_domain then by name tokens (a company that already exists
  from the OutFlo track gets the new contact associated + email provenance,
  stage untouched); contact by email. Stage only ever promotes
  'Message Back (Email + 2nd Msg)' -> 'Replied'. Re-run daily, safely.
- Gmail scope is read-only; nothing is ever sent.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER_KARTIK = "96316911"
PIPELINE_LABEL = "Company Ops Data"
LEAD_SOURCE = "Cold Email ( Company Ops )"
STAGE_SENT, STAGE_REPLIED = "Email Campaign Sent", "Replied"
# a reply may arrive while the deal sits in either email-branch stage
PROMOTABLE = {"Email Campaign Sent", "Email Follow-Up"}
CALENDLY = re.compile(r"calendly\.com/", re.I)
ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
OWN_DOMAINS = ("lh2.ai", "lh2holdings.com")
AUTOMATON = re.compile(r"postmaster|mailer-daemon|no-?reply|donotreply", re.I)
AUTO_SUBJ = re.compile(r"automatic reply|auto-?reply|out of office|ooto|undeliverable", re.I)

def _env():
    # .env when running by hand; process env on a CI runner, where secrets
    # arrive as repo-secret env vars and must never be written to disk.
    # The file wins where both exist, so a laptop override stays possible.
    d = dict(os.environ)
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    d[k] = v.strip()
    return d

ENV = _env()

def _req(url, headers, payload=None, method=None, raw=None):
    for attempt in range(5):
        req = urllib.request.Request(
            url, data=raw if raw is not None else
            (json.dumps(payload).encode() if payload is not None else None),
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} on {url}: {e.read().decode(errors='replace')[:500]}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            # transient network/SSL drops deserve the same backoff as a 503
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise

def hs(path, payload=None, method=None):
    return _req("https://api.hubapi.com" + path,
                {"Authorization": f"Bearer {ENV['HUBSPOT_API_KEY']}",
                 "Content-Type": "application/json"}, payload, method)

def gmail_token():
    tok = json.load(open(os.path.join(ROOT, ".gmail_token.json"), encoding="utf-8"))
    body = urllib.parse.urlencode({
        "client_id": tok["client_id"], "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
    s, d = _req(tok["token_uri"],
                {"Content-Type": "application/x-www-form-urlencoded"}, raw=body, method="POST")
    return d["access_token"]

ACCESS = None
def g(path, **params):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    s, d = _req(url, {"Authorization": f"Bearer {ACCESS}"})
    return d

def list_ids(q):
    ids, page = [], None
    while True:
        params = {"q": q, "maxResults": 100}
        if page:
            params["pageToken"] = page
        d = g("messages", **params)
        ids += [m["id"] for m in d.get("messages", [])]
        page = d.get("nextPageToken")
        if not page:
            break
    return ids

def body_text(payload):
    out = []
    def walk(p):
        if p.get("body", {}).get("data"):
            import base64
            out.append(base64.urlsafe_b64decode(
                p["body"]["data"] + "==").decode(errors="replace"))
        for sub in p.get("parts", []) or []:
            walk(sub)
    walk(payload)
    return "\n".join(out)

def full(mid):
    d = g(f"messages/{mid}", format="full")
    h = {x["name"].lower(): x["value"] for x in d["payload"].get("headers", [])}
    return {"id": mid, "threadId": d.get("threadId"), "subject": h.get("subject", ""),
            "to": h.get("to", ""), "from": h.get("from", ""),
            "ts": int(d.get("internalDate", 0)), "body": body_text(d["payload"])}

def company_from_domain(domain):
    # exicom.co.in -> Exicom; workline.hr -> Workline. Crude but reviewable in
    # the plan table; a send-list sheet with real names beats this when provided.
    base = domain.split(".")[0]
    return base.capitalize() if base.islower() else base

def pull_campaign():
    sent_ids = list_ids("in:sent")
    with ThreadPoolExecutor(8) as ex:
        sent = list(ex.map(full, sent_ids))
    camp = [m for m in sent if CALENDLY.search(m["body"] or "")
            and not m["subject"].lower().startswith(("fwd:", "fw:"))]

    recipients = {}
    for m in camp:
        name = m["to"].split("<")[0].strip().strip('"') if "<" in m["to"] else ""
        for a in ADDR.findall(m["to"]):
            a = a.lower()
            if a.endswith(OWN_DOMAINS):
                continue
            r = recipients.setdefault(a, {"email": a, "name": name, "threads": set(),
                                          "subject": m["subject"], "first_ts": m["ts"]})
            r["threads"].add(m["threadId"])
            r["first_ts"] = min(r["first_ts"], m["ts"])

    by_domain = {}
    for a in recipients:
        by_domain.setdefault(a.split("@")[1], []).append(a)

    # replies: an external human voice anywhere in a campaign thread
    tids = sorted({t for r in recipients.values() for t in r["threads"]})
    def thread_msgs(tid):
        d = g(f"threads/{tid}", format="metadata", metadataHeaders=["Subject", "From"])
        return [( {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])},
                  int(m.get("internalDate", 0))) for m in d.get("messages", [])]
    with ThreadPoolExecutor(8) as ex:
        all_threads = dict(zip(tids, ex.map(thread_msgs, tids)))

    replied = {}
    for tid, msgs in all_threads.items():
        for h, ts in msgs:
            frm = h.get("from", "")
            fr = ADDR.findall(frm)
            if not fr:
                continue
            fa = fr[0].lower()
            if fa.endswith(OWN_DOMAINS) or AUTOMATON.search(frm) \
               or AUTO_SUBJ.search(h.get("subject", "")):
                continue
            hit = fa if fa in recipients else next(iter(by_domain.get(fa.split("@")[1], [])), None)
            if hit and (hit not in replied or ts < replied[hit]):
                replied[hit] = ts

    bounced = set()
    for mid in list_ids('subject:"Undeliverable" OR from:mailer-daemon'):
        blob = json.dumps(g(f"messages/{mid}", format="full"))
        for a in ADDR.findall(blob):
            if a.lower() in recipients:
                bounced.add(a.lower())
    return recipients, replied, bounced

def main():
    global ACCESS
    apply = "--apply" in sys.argv
    ACCESS = gmail_token()
    recipients, replied, bounced = pull_campaign()
    print(f"campaign (calendly-link) recipients: {len(recipients)}; "
          f"replied: {len(replied)}; bounced: {len(bounced)}")

    # one unit per domain = one company deal
    units = {}
    for a, r in recipients.items():
        dom = a.split("@")[1]
        u = units.setdefault(dom, {"domain": dom, "company": company_from_domain(dom),
                                   "members": []})
        u["members"].append(r)
    for u in units.values():
        reps = [m["email"] for m in u["members"] if m["email"] in replied]
        u["status"] = ("Replied" if reps else
                       "Bounced" if all(m["email"] in bounced for m in u["members"])
                       else "Awaiting Reply")
        u["stage"] = STAGE_REPLIED if reps else STAGE_SENT
        u["replied_ts"] = min(replied[e] for e in reps) if reps else None
        u["first_ts"] = min(m["first_ts"] for m in u["members"])
        u["subject"] = u["members"][0]["subject"]

    s, d = hs("/crm/v3/pipelines/deals")
    pipe = next((p for p in d["results"] if p["label"] == PIPELINE_LABEL), None)
    if not pipe:
        sys.exit(f"ABORT: pipeline {PIPELINE_LABEL!r} not found")
    stage_id = {st["label"]: st["id"] for st in pipe["stages"]}
    stage_label = {v: k for k, v in stage_id.items()}
    for lbl in (STAGE_SENT, STAGE_REPLIED):
        if lbl not in stage_id:
            sys.exit(f"ABORT: stage {lbl!r} missing")

    deals, after = {}, None
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": "pipeline",
                                               "operator": "EQ", "value": pipe["id"]}]}],
                "properties": ["dealname", "dealstage", "lh2_domain", "email_status"],
                "limit": 200}
        if after:
            body["after"] = after
        s, d = hs("/crm/v3/objects/deals/search", body, method="POST")
        for r in d.get("results", []):
            deals[r["id"]] = r["properties"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    by_dom = {p.get("lh2_domain"): i for i, p in deals.items() if p.get("lh2_domain")}
    def name_toks(s):
        return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - {
            "pvt", "ltd", "private", "limited", "india", "technologies",
            "solutions", "labs", "inc", "the"}
    by_toks = [(i, name_toks(p.get("dealname"))) for i, p in deals.items()]

    emails = [m["email"] for u in units.values() for m in u["members"]]
    existing_contacts = {}
    for i in range(0, len(emails), 100):
        s, d = hs("/crm/v3/objects/contacts/search",
                  {"filterGroups": [{"filters": [{"propertyName": "email",
                                                  "operator": "IN", "values": emails[i:i+100]}]}],
                   "properties": ["email"], "limit": 200}, method="POST")
        for r in d.get("results", []):
            existing_contacts[r["properties"]["email"].lower()] = r["id"]

    plan = []
    for u in sorted(units.values(), key=lambda x: (x["status"] != "Replied", x["company"].lower())):
        dom_base = u["domain"].split(".")[0]
        hit = by_dom.get(u["domain"]) or next(
            (i for i, tk in by_toks if dom_base in tk), None)
        if not hit:
            u["action"] = "CREATE"
        else:
            u["deal_id"] = hit
            cur = stage_label.get(deals[hit].get("dealstage"), "?")
            if u["stage"] == STAGE_REPLIED and cur in PROMOTABLE:
                u["action"] = "PROMOTE -> Replied"
            else:
                u["action"] = f"ATTACH to existing ({deals[hit].get('dealname')!r} at {cur!r})"
        plan.append((u["company"], u["domain"], u["status"],
                     ", ".join(m["email"] for m in u["members"])[:38], u["action"]))

    print(f"\nPlan — pipeline {PIPELINE_LABEL!r}, owner Kartik ({OWNER_KARTIK}), "
          f"lead_source {LEAD_SOURCE!r}:\n")
    print(f"  {'company':<20} {'domain':<24} {'status':<14} {'contact email(s)':<38} action")
    print(f"  {'-'*20} {'-'*24} {'-'*14} {'-'*38} {'-'*30}")
    for row in plan:
        print(f"  {row[0][:20]:<20} {row[1][:24]:<24} {row[2]:<14} {row[3]:<38} {row[4]}")
    n_new = sum(1 for u in units.values() if u["action"] == "CREATE")
    print(f"\n  totals: {len(units)} companies — {n_new} to create, "
          f"{len(units) - n_new} existing; statuses: "
          f"{sum(1 for u in units.values() if u['status'] == 'Replied')} replied, "
          f"{sum(1 for u in units.values() if u['status'] == 'Bounced')} bounced, "
          f"{sum(1 for u in units.values() if u['status'] == 'Awaiting Reply')} awaiting")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to push.")
        return

    audit = {"contacts_created": [], "deals_created": [], "deals_updated": []}
    to_create = {}
    for u in units.values():
        for m in u["members"]:
            if m["email"] not in existing_contacts and m["email"] not in to_create:
                parts = (m["name"] or "").split()
                to_create[m["email"]] = {"properties": {
                    "email": m["email"],
                    "firstname": parts[0] if parts else "",
                    "lastname": " ".join(parts[1:]) if len(parts) > 1 else "",
                    "company": u["company"],
                    "hubspot_owner_id": OWNER_KARTIK,
                }}
    batch = list(to_create.values())
    for i in range(0, len(batch), 100):
        s, d = hs("/crm/v3/objects/contacts/batch/create",
                  {"inputs": batch[i:i+100]}, method="POST")
        if s == 207:
            print(f"WARNING: partial contact batch (207): {json.dumps(d.get('errors'))[:400]}")
        for r in d.get("results", []):
            existing_contacts[r["properties"]["email"].lower()] = r["id"]
            audit["contacts_created"].append({"id": r["id"],
                                              "email": r["properties"]["email"]})

    deal_inputs = []
    for u in units.values():
        if u["action"] != "CREATE":
            continue
        props = {
            "dealname": u["company"], "pipeline": pipe["id"],
            "dealstage": stage_id[u["stage"]], "hubspot_owner_id": OWNER_KARTIK,
            "lead_source": LEAD_SOURCE, "lh2_domain": u["domain"],
            "email_status": u["status"], "email_campaign": u["subject"],
            "email_sent_at": u["first_ts"],
        }
        if u["replied_ts"]:
            props["replied_at"] = u["replied_ts"]
        assoc = [{"to": {"id": existing_contacts[m["email"]]},
                  "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]}
                 for m in u["members"] if m["email"] in existing_contacts]
        deal_inputs.append({"properties": props, "associations": assoc})
    for i in range(0, len(deal_inputs), 100):
        s, d = hs("/crm/v3/objects/deals/batch/create",
                  {"inputs": deal_inputs[i:i+100]}, method="POST")
        if s == 207:
            print(f"WARNING: partial deal batch (207): {json.dumps(d.get('errors'))[:400]}")
        for r in d.get("results", []):
            audit["deals_created"].append({"id": r["id"],
                                           "dealname": r["properties"].get("dealname")})

    for u in units.values():
        if u["action"] == "CREATE":
            continue
        did = u["deal_id"]
        props = {"email_status": u["status"], "email_campaign": u["subject"],
                 "email_sent_at": u["first_ts"]}
        if not deals[did].get("lh2_domain"):
            props["lh2_domain"] = u["domain"]
        if u["action"].startswith("PROMOTE"):
            props["dealstage"] = stage_id[STAGE_REPLIED]
            if u["replied_ts"]:
                props["replied_at"] = u["replied_ts"]
        s, d = hs(f"/crm/v3/objects/deals/{did}", {"properties": props}, method="PATCH")
        assoc = [{"from": {"id": did}, "to": {"id": existing_contacts[m["email"]]},
                  "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]}
                 for m in u["members"] if m["email"] in existing_contacts]
        if assoc:
            hs("/crm/v4/associations/deals/contacts/batch/create",
               {"inputs": assoc}, method="POST")
        audit["deals_updated"].append({"id": did, "action": u["action"]})

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"gmail_pull_push_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    print(f"\nApplied: {len(audit['contacts_created'])} contacts created, "
          f"{len(audit['deals_created'])} deals created, "
          f"{len(audit['deals_updated'])} existing deals updated. Audit: {out}")

if __name__ == "__main__":
    main()