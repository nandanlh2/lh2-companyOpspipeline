"""Create the stage-gate deal properties for the ops-data flow, and group them.

Dry-run by default: prints the plan and writes nothing. --apply to execute.
Writing an enum value that is not an option fails with INVALID_OPTION, so every
enum here ships its full option list up front; extend options via PATCH before
ever writing a new value to a deal.

Property meanings are documented in docs/OPSDATA_PIPELINE.md — that doc is the
contract; keep it in sync with this file.
"""
import json, os, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUP = "opsdata_procurement"

def _key():
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("HUBSPOT_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("HUBSPOT_API_KEY missing from .env")

KEY = _key()

def hs(path, payload=None, method=None):
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

def opts(*labels):
    return [{"label": l, "value": l, "displayOrder": i} for i, l in enumerate(labels)]

# name -> spec. Names match the main LH2 portal where an equivalent exists
# (gmeet1_*, data_delivery_timeline, number_of_datasets ...) so tooling ports over.
NEW = {
    "li_msg1_date":       dict(label="LinkedIn Msg 1 Sent (date)", type="date", fieldType="date"),
    "li_msg2_date":       dict(label="Msg 2 / Cold Email Sent (date)", type="date", fieldType="date"),
    "one_pager_sent_date": dict(label="One Pager Sent (date)", type="date", fieldType="date"),
    "gmeet1_date":        dict(label="GMeet 1 Date", type="datetime", fieldType="date"),
    "gmeet1_link":        dict(label="GMeet 1 Link", type="string", fieldType="text"),
    "gmeet1_outcome":     dict(label="GMeet 1 Outcome", type="enumeration", fieldType="select",
                               options=opts("Proceeds", "Wrong Fit", "No Show", "Privacy Concerns")),
    "sample_requested_date": dict(label="Sample Requested (date)", type="date", fieldType="date"),
    "sample_pii_flags":   dict(label="Sample PII Flags", type="enumeration", fieldType="checkbox",
                               options=opts("None", "Emails", "Phone numbers", "Addresses",
                                            "Financial", "Govt IDs (PAN / Aadhaar)", "Health", "Other")),
    "token_paid_date":    dict(label="Token Paid (date)", type="date", fieldType="date"),
    "migration_volume":   dict(label="Migration Volume (human-readable)", type="string", fieldType="text"),
    "migration_record_count": dict(label="Migration Record Count", type="number", fieldType="number"),
    "number_of_datasets": dict(label="Number of Datasets Delivered", type="number", fieldType="number"),
    "dataset_customization_required": dict(label="Dataset Customization Required", type="enumeration",
                                           fieldType="select", options=opts("Yes", "No")),
    "data_delivery_timeline": dict(label="Data Delivery Deadline", type="date", fieldType="date"),
    # OutFlo import provenance — frozen at import time; the deal's stage moves on,
    # these record how the lead arrived
    "outflo_status":       dict(label="OutFlo Status (at import)", type="enumeration", fieldType="select",
                                options=opts("Connected", "Replied")),
    "outflo_campaign":     dict(label="OutFlo Campaign", type="string", fieldType="text"),
    "outflo_lead_id":      dict(label="OutFlo Lead ID", type="string", fieldType="text"),
    "outflo_assigned_account": dict(label="OutFlo Sender Account", type="string", fieldType="text"),
    "outflo_last_action_at": dict(label="OutFlo Last Action At", type="datetime", fieldType="date"),
    # Gmail cold-email campaign provenance (Kartik's mailbox, Calendly-link mails)
    "email_status":        dict(label="Email Campaign Status", type="enumeration", fieldType="select",
                                options=opts("Awaiting Reply", "Replied", "Bounced")),
    "email_campaign":      dict(label="Email Campaign (subject)", type="string", fieldType="text"),
    "email_sent_at":       dict(label="Email First Sent At", type="datetime", fieldType="date"),
}

# contact-side: the join key must live on the contact too (this portal was born
# without it, unlike the main portal)
CONTACT_NEW = {
    "linkedin_url": dict(label="LinkedIn URL (LH2)", type="string", fieldType="text",
                         groupName="contactinformation"),
}

# already created by the earlier build attempt; pull them into the same UI group
# so the analyst sees one coherent card at the stage gates
REGROUP = ["ops_data_types", "internal_eval_result", "sample_format",
           "sample_quality_score", "systems_of_record", "token_amount", "replied_at"]

def main():
    apply = "--apply" in sys.argv
    plan, audit = [], {"group": None, "created": [], "regrouped": []}

    s, d = hs("/crm/v3/properties/deals/groups")
    have_group = any(g["name"] == GROUP for g in d["results"])
    plan.append(("group", GROUP, "ok" if have_group else "CREATE"))

    s, d = hs("/crm/v3/properties/deals")
    have = {p["name"]: p for p in d["results"]}

    for name, spec in NEW.items():
        if name not in have:
            plan.append(("property", name, f"CREATE ({spec['type']}/{spec['fieldType']})"))
        elif have[name].get("groupName") != GROUP:
            plan.append(("property", name, f"exists — move group {have[name].get('groupName')} -> {GROUP}"))
        else:
            plan.append(("property", name, "ok"))
    for name in REGROUP:
        if name not in have:
            sys.exit(f"ABORT: expected pre-existing property {name!r} is missing — "
                     "portal state has drifted from what this script assumes.")
        cur = have[name].get("groupName")
        plan.append(("regroup", name, "ok" if cur == GROUP else f"move {cur} -> {GROUP}"))

    s, d = hs("/crm/v3/properties/contacts")
    have_contact = {p["name"]: p for p in d["results"]}
    for name, spec in CONTACT_NEW.items():
        plan.append(("contact", name,
                     "ok" if name in have_contact else f"CREATE ({spec['type']}/{spec['fieldType']})"))

    print(f"{'kind':<9} {'name':<34} action")
    print(f"{'-'*9} {'-'*34} {'-'*40}")
    for kind, name, action in plan:
        print(f"{kind:<9} {name:<34} {action}")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to execute.")
        return

    if not have_group:
        s, g = hs("/crm/v3/properties/deals/groups",
                  {"name": GROUP, "label": "Ops Data Procurement", "displayOrder": 0},
                  method="POST")
        audit["group"] = g

    for name, spec in NEW.items():
        if name not in have:
            body = {"name": name, "groupName": GROUP, **spec}
            s, p = hs("/crm/v3/properties/deals", body, method="POST")
            audit["created"].append(p)
        elif have[name].get("groupName") != GROUP:
            s, p = hs(f"/crm/v3/properties/deals/{name}", {"groupName": GROUP}, method="PATCH")
            audit["regrouped"].append(p)
    for name in REGROUP:
        if have[name].get("groupName") != GROUP:
            s, p = hs(f"/crm/v3/properties/deals/{name}", {"groupName": GROUP}, method="PATCH")
            audit["regrouped"].append(p)
    for name, spec in CONTACT_NEW.items():
        if name not in have_contact:
            s, p = hs("/crm/v3/properties/contacts", {"name": name, **spec}, method="POST")
            audit["created"].append(p)

    os.makedirs(os.path.join(ROOT, "audit"), exist_ok=True)
    out = os.path.join(ROOT, "audit",
                       f"properties_sync_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    print(f"\nApplied: {len(audit['created'])} created, "
          f"{len(audit['regrouped'])} regrouped. Audit: {out}")

if __name__ == "__main__":
    main()
