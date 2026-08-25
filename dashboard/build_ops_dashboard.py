#!/usr/bin/env python3
"""CI-ready: pull the Company Ops Data pipeline from HubSpot -> ops_dashboard_data.json.

Portal `246897735` (the build portal — NOT the main LH2 portal `246754894` that context.md
describes). Pipeline "Company Ops Data".

Every metric is a count of TIMES A DEAL ENTERED a qualifying stage, on the IST day it entered,
read from each deal's stage-change history. Not "deals currently at or past stage X": a deal
that entered `Replied` in week 1 and `Discovery Call` in week 2 contributes to a different
week for each, which is the whole point of a WTD/Today view.

Activity metrics count only human moves (`sourceType == "CRM_UI"`). Procurement facts
(Closed/Won, Data Migration Done) ignore sourceType — a dataset we received is ours whether a
person clicked the stage or the OutFlo sync set it.

Two metrics have no stage and come from notes; see ops_note_rules.py for why.

Full spec: docs/OPS_DASHBOARD_METRIC_SPEC.md

Token: HUBSPOT_API_KEY env (CI) or local .env (hubspot_key=...).

Usage:
  python dashboard/build_ops_dashboard.py                  full rebuild -> ops_dashboard_data.json
  python dashboard/build_ops_dashboard.py --deals 1,2      refresh those deals only -> stdout
  python dashboard/build_ops_dashboard.py --deals-since <ISO>   everything modified since then

The executable part lives in main(); everything above it is import-safe, so the daily report
and any later refresh script can import these definitions rather than hand-copying them. TOK is
resolved lazily for the same reason — importing this module must not exit on a missing token.
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

# Portal timezone is US/Eastern and LH2 reporting days are IST with an 18:30 cutoff, so every
# day boundary is computed here and the portal's own dates are never trusted.
IST = timezone(timedelta(hours=5, minutes=30))

# The Company Ops Data pipeline. Overridable because this same script has to be runnable
# against a sandbox pipeline without editing code, but there is exactly ONE by default.
OPS_PIPELINE = os.environ.get("OPS_PIPELINE_ID", "2464812771")


def ist_day(iso):
    """UTC ISO timestamp -> YYYY-MM-DD in IST."""
    if not iso:
        return None
    try:
        if isinstance(iso, (int, float)) or str(iso).isdigit():
            dt = datetime.fromtimestamp(int(iso) / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%Y-%m-%d")
    except Exception:
        return str(iso)[:10]


def token():
    t = os.environ.get("HUBSPOT_API_KEY")
    if t:
        return t.strip()
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                if line.lower().startswith("hubspot") and "=" in line:
                    return line.split("=", 1)[1].strip()
    sys.exit("HUBSPOT_API_KEY not set (env or .env)")


_TOK = None


def _token():
    global _TOK
    if _TOK is None:
        _TOK = token()
    return _TOK


def call(path, method="GET", payload=None):
    url = path if path.startswith("http") else "https://api.hubapi.com" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _token(),
                                          "Content-Type": "application/json"})
    # A full build is hundreds of sequential calls over minutes, so a single dropped connection
    # anywhere in that window must not abort the run. Transport errors are retried like any
    # other transient and 5xx is treated alongside 429 (lesson from the sales build: six local
    # builds died to uncaught URLError/SSLError while short one-off calls to the same host
    # succeeded).
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read().decode()
                return r.status, (json.loads(b) if b else {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 * (attempt + 1)); continue
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            if attempt == 5:
                raise
            time.sleep(3 * (attempt + 1))
    return 429, {}


# ---------------------------------------------------------------------------------------
# METRICS — a metric is a SET OF STAGES, never a flag or a counter.
#
# Not a level threshold: `Dead/Cold/No Reply` and `Dead/Cold/Not Interested` sit at the same
# depth in the funnel, yet one means nobody ever answered and the other means a human replied
# and said no. Any metric that needs to separate exactly those two has to list membership.
#
# The keys match the stage keys the front end renders, so the funnel order in index.html and
# the definition here cannot drift.
# ---------------------------------------------------------------------------------------
METRICS = {
    # --- outreach & response (Input Matrix) ---
    # Both branch entries are one metric: "we put outreach out". The LinkedIn/Email split is
    # derived from WHICH stage was entered, so no channel field is needed or stored.
    "outreach":          {"Cold LinkedIn Sent", "Email Campaign Sent"},
    "outreachLi":        {"Cold LinkedIn Sent"},
    "outreachEmail":     {"Email Campaign Sent"},
    "liConnected":       {"LinkedIn Connected"},
    "outreachFollowUp":  {"LinkedIn Follow-Up", "Email Follow-Up"},
    "outreachReplied":   {"Replied"},
    "ghostFollowUp":     {"Ghost Follow-Up"},
    "vcSetup":           {"Discovery Call"},
    # vcAttended has no stage of its own and is NOT in this dict — it is derived in
    # derive_attendance() below, because "attended" is the absence of a later no-show.

    # --- materials & samples (Outcome Matrix) ---
    "onePagerRequested": {"One Pager Requested"},
    "onePagerFollowUp":  {"One Pager Follow-Up"},
    "onePagerSent":      {"One Pager Shared"},
    "internalEval":      {"Internal Evaluation (Sample)"},
    "samplesRequested":  {"Samples Requested"},
    "sampleFollowUp":    {"Sample Follow-Up"},
    "samplesReceived":   {"Sample Received + Quality Check"},

    # --- commercials & close ---
    "negotiationDone":   {"Commercial Negotiations"},
    "contractSigned":    {"Deal Contract Signed"},
    "tokenPaid":         {"Token Amount Paid"},
    "migrationDone":     {"Data Migration Done"},
    "paymentInitiation": {"Payment Initiation"},
    "dealWon":           {"Closed/Won"},
}

# Procurement facts about the ASSET, not about who moved the deal — counted regardless of
# sourceType. Everything else is activity and requires a human CRM_UI move.
ASSET_METRICS = {"dealWon", "migrationDone"}

# Stage renames are history. `pipeline_v3_update.py` renamed Message Back -> LinkedIn Follow-Up
# and One Pager + Deck Shared -> One Pager Shared; those PATCHes preserved stage ids, but the
# v2 restructure's PUT-based renames did NOT (GMeet Fixed -> Discovery Call got a fresh id), so
# old labels survive in the history of migrated deals. Map them forward or the series before
# 2026-08-13 falls off a cliff.
ALIAS = {
    "Message Back (Email + 2nd Msg)": "LinkedIn Follow-Up",
    "Message Back": "LinkedIn Follow-Up",
    "One Pager + Deck Shared": "One Pager Shared",
    "GMeet Fixed": "Discovery Call",
    "Cold Lead": "Cold LinkedIn Sent",
}

# Funnel depth. How far a deal got, used to rank the Hot Pipeline and to decide what is "hot".
_SEQ = ["Cold LinkedIn Sent", "LinkedIn Connected", "LinkedIn Follow-Up",
        "Email Campaign Sent", "Email Follow-Up", "Replied", "Ghost Follow-Up",
        "Discovery Call", "One Pager Requested", "One Pager Follow-Up", "One Pager Shared",
        "Internal Evaluation (Sample)", "Samples Requested", "Sample Follow-Up",
        "Sample Received + Quality Check", "Commercial Negotiations", "Deal Contract Signed",
        "Token Amount Paid", "Data Migration Done", "Payment Initiation", "Closed/Won"]

# A dead stage is not "off the end of the funnel" — it marks the point the deal REACHED, so
# Dead/Sample/Bad Quality means it got a sample in hand. Without this every dead deal sorts
# last and the drop-off panel cannot say where the funnel actually leaks.
_DEAD_SEQ = {
    "Dead/Cold/Not Interested": 5,
    "Dead/Cold/No Reply": 2,
    "Dead/Interested/No Show": 7,
    "Dead/Discovery Call/Privacy Concerns": 7,
    "Dead/One Pager Not Shared": 9,
    "Dead/Sample Not Collected/Wrong Fit-Rejected": 11,
    "Dead/Sample Not Received/Company No Show": 13,
    "Dead/Sample/Bad Quality": 14,
    "Dead/Negotiations/Pricing": 15,
    "Dead/Negotiations/Contractual": 15,
    "Dead/Migration/Failed": 18,
}

# Where the funnel leaked, for the drop-off panel. The middle segment of the stage name IS the
# answer — no separate lost-reason property exists or should exist.
DEAD_BUCKET = {
    "Dead/Cold/Not Interested": "Outreach",
    "Dead/Cold/No Reply": "Outreach",
    "Dead/Interested/No Show": "Discovery call",
    "Dead/Discovery Call/Privacy Concerns": "Discovery call",
    "Dead/One Pager Not Shared": "One pager",
    "Dead/Sample Not Collected/Wrong Fit-Rejected": "Evaluation",
    "Dead/Sample Not Received/Company No Show": "Sample",
    "Dead/Sample/Bad Quality": "Sample",
    "Dead/Negotiations/Pricing": "Negotiation",
    "Dead/Negotiations/Contractual": "Negotiation",
    "Dead/Migration/Failed": "Migration",
}

# The one stage that turns a booked call into a no-show. Named once here; derive_attendance
# reads it rather than matching on a substring, so renaming the stage breaks loudly in the
# unknown-label report instead of silently zeroing the show rate.
NO_SHOW_STAGE = "Dead/Interested/No Show"

STAGE_LABEL = {}
WON_IDS, ENTRY_IDS, DEAD = set(), set(), set()
ORDER = {}
USER2OWNER = {}
UNKNOWN_LABELS = set()


def load_stage_index():
    """Populate STAGE_LABEL / WON_IDS / ENTRY_IDS / DEAD / ORDER from the live pipeline.

    Stage ids are ALWAYS resolved from the live pipeline, never hardcoded. On the main portal a
    script that hardcoded Scraped ids and wrote them to Campaign deals failed all 13 writes with
    INVALID_OPTION after already creating the contacts, leaving 13 orphan contacts behind. The
    pipeline can also drift via the HubSpot UI, so this re-reads on every run.

    Idempotent — a refresh run calls this exactly as a full build does. That is the point: one
    resolution, one meaning.
    """
    s, pp = call(f"/crm/v3/pipelines/deals/{OPS_PIPELINE}")
    if s != 200:
        sys.exit(f"cannot read pipeline {OPS_PIPELINE}: {s} {str(pp)[:200]}")
    for st in pp.get("stages", []):
        sid = st["id"]
        lab = ALIAS.get(st["label"], st["label"])
        STAGE_LABEL[sid] = lab
        meta = st.get("metadata") or {}
        if str(meta.get("isClosed")).lower() == "true":
            DEAD.add(sid)
        if lab == "Closed/Won":
            WON_IDS.add(sid); DEAD.discard(sid)
        if lab in ("Cold LinkedIn Sent", "Email Campaign Sent"):
            ENTRY_IDS.add(sid)
        if lab in _SEQ:
            ORDER[sid] = _SEQ.index(lab)
        elif lab in _DEAD_SEQ:
            ORDER[sid] = _DEAD_SEQ[lab]
        else:
            # A stage the dashboard has never heard of. Rank it at the entry and SAY SO at the
            # end of the build — silently ranking it 0 is how a new stage stays invisible for
            # a month.
            ORDER[sid] = 0
            UNKNOWN_LABELS.add(lab)


def metrics_for(label):
    """Which metric keys does ENTERING this stage satisfy."""
    return [k for k, s in METRICS.items() if label in s]


# Stage history identifies the actor by USER id; deals identify people by OWNER id. Separate
# namespaces in HubSpot that coincide only by luck, so map them explicitly.
#
# Why the actor matters: the analyst books the Discovery Call and hands the deal to the Pod
# Lead in the same action (v3 §7 handover), so by build time the deal belongs to someone else.
# Crediting activity to the CURRENT owner would take that call off the analyst who booked it —
# and the better the handoff discipline, the more work gets misattributed. Assignment stays
# owner-based (it really is about who received the deal); activity follows the actor.
def load_owners():
    """-> {owner_id: display name}; fills USER2OWNER as a side effect."""
    s, ow = call("/crm/v3/owners?limit=200")
    results = ow.get("results") or []
    USER2OWNER.update({str(o["userId"]): o["id"] for o in results if o.get("userId")})
    return {o["id"]: (f"{o.get('firstName','')} {o.get('lastName','')}".strip()
                      or o.get("email", "")) for o in results}


def actor_owner(uid):
    if uid is None:
        return ""
    return USER2OWNER.get(str(uid), str(uid))


PROPS = ["hubspot_owner_id", "pipeline", "dealstage", "createdate", "dealname",
         "lead_source", "email_campaign", "lh2_domain",
         # Price lives in `cost`, NOT `amount` — the flowchart's Payment Initiation gate is
         # literally "Deal Cost ($)" and this flow never writes `amount`. Keyed `cost` all the
         # way to the front end so nothing downstream can quietly read the wrong field.
         "cost", "token_amount", "token_paid_date", "deal_value_range",
         "sample_quality_score", "sample_format", "internal_eval_result",
         "ops_data_types", "systems_of_record",
         "migration_record_count", "number_of_datasets", "migration_volume",
         "data_delivery_timeline"]


def num(p, k):
    v = p.get(k)
    try:
        return float(v) if v not in (None, "") else 0
    except Exception:
        return 0


def deal_row(deal):
    """A HubSpot deal object -> the dashboard row, or None if it does not belong on the board.

    THE single definition of a row. The full build and the incremental refresh both come through
    here, so a deal touched by one path is indistinguishable from the same deal touched by the
    other. Metric event lists start empty — apply_history() fills them.
    """
    p = deal["properties"]
    st = p.get("dealstage")
    if st not in STAGE_LABEL:
        return None
    lab = STAGE_LABEL[st]
    return {
        # Carried so a delta can be spliced into a loaded page by identity rather than position.
        "id": str(deal["id"]),
        "o": p.get("hubspot_owner_id") or "",
        "nm": p.get("dealname") or "",
        "c": ist_day(p.get("createdate")),
        "r": ORDER.get(st, 0),
        "sl": lab,
        "won": st in WON_IDS,
        "dead": st in DEAD,
        "db": DEAD_BUCKET.get(lab, "") if st in DEAD else "",
        "src": p.get("lead_source") or "",
        "dvr": p.get("deal_value_range") or "",
        "cost": num(p, "cost"),
        "tok": num(p, "token_amount"),
        "sq": num(p, "sample_quality_score"),
        "sf": p.get("sample_format") or "",
        "ier": p.get("internal_eval_result") or "",
        "recs": num(p, "migration_record_count"),
        "ds": num(p, "number_of_datasets"),
        # per-metric list of [IST day, actor owner id] on which this deal ENTERED a qualifying
        # stage. A LIST, not a single date: under the follow-up loops a deal legitimately
        # re-enters a stage (Replied, went quiet, Ghost Follow-Up, Replied again) and both are
        # real events. Keeping only the first would make the whole chase loop invisible.
        "m": {k: [] for k in METRICS},
        "asg": [],       # IST days this deal was assigned to its current owner
    }


def apply_history(d, hist):
    """Fill a row's m / asg from propertiesWithHistory. Mutates and returns `d`.

    Deriving these from history rather than from a webhook payload is deliberate. A webhook
    carries `changeSource: "CRM"` where history carries `sourceType: "CRM_UI"` — different
    vocabularies for the same intent, and trusting the payload would be a SECOND definition of
    "a human did this". Re-reading history also makes this idempotent, so a duplicated or
    out-of-order delivery cannot double-count.
    """
    for e in hist.get("dealstage", []):
        if not e.get("timestamp"):
            continue
        lab = STAGE_LABEL.get(e.get("value"))
        if not lab:
            continue          # a stage since deleted — skip, never guess
        day = ist_day(e["timestamp"])
        keys = metrics_for(lab)

        # Asset facts ignore sourceType; activity requires a human. The OutFlo sync promotes
        # deals up the LinkedIn ladder every morning and the Gmail import promotes the email
        # branch — counting those as CRM activity would render the daily sync as the biggest
        # outreach day the company has ever had.
        for k in keys:
            if k not in ASSET_METRICS and e.get("sourceType") != "CRM_UI":
                continue
            ev = [day, actor_owner(e.get("updatedByUserId")) if k not in ASSET_METRICS else ""]
            if ev not in d["m"][k]:   # same stage, same person, same day is ONE event
                d["m"][k].append(ev)

        # A no-show is what un-attends a booked call. Recorded on the row rather than counted,
        # because attendance is credited to the day of the CALL, not the day somebody got
        # around to marking the no-show.
        if lab == NO_SHOW_STAGE:
            d["ns"] = True

    # Assignment is an OWNER change, not a stage change. createdate was the obvious proxy and
    # it is wrong the moment a lead is reassigned.
    for e in hist.get("hubspot_owner_id", []):
        if e.get("value") == d["o"] and e.get("timestamp"):
            day = ist_day(e["timestamp"])
            if day not in d["asg"]:
                d["asg"].append(day)
    if not d["asg"] and d["c"]:
        d["asg"].append(d["c"])       # never reassigned — creation is when they got it
    return d


def derive_attendance(d):
    """vcAttended: a Discovery Call that did not later turn into a no-show.

    "Attended" is deliberately NOT a stage — per the pipeline's own design rule, a stage is a
    state the deal is IN, and "they showed up" is not one; the deal is at Discovery Call either
    way until an outcome moves it. So attendance is the ABSENCE of `Dead/Interested/No Show`.

    Credited on the day of the CALL, not the day the no-show was recorded, so a call booked and
    attended on Monday counts to Monday even if somebody tidied the stage on Thursday. That
    also means the show rate for a very recent day can only fall, never rise, as no-shows get
    marked — which is the honest direction for it to move.

    `Dead/Discovery Call/Privacy Concerns` is NOT a no-show on purpose: they turned up and then
    balked, so the call was attended and the deal died for a different reason.
    """
    d["m"]["vcAttended"] = [] if d.get("ns") else list(d["m"]["vcSetup"])
    return d


def fetch_history(did):
    s, h = call(f"/crm/v3/objects/deals/{did}"
                "?propertiesWithHistory=dealstage,hubspot_owner_id")
    return h.get("propertiesWithHistory", {}) if s == 200 else {}


# ---- NOTE-DERIVED METRICS -----------------------------------------------------------------
# Rules are IMPORTED, never copied — see ops_note_rules.py. A plain import with no try/except is
# deliberate: if this file cannot be found, the build SHOULD die loudly rather than publish a
# dashboard that quietly lost two KPIs with only a stderr line to show for it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_note_rules import METRIC_BUCKETS, classify as _classify, plain as _plain  # noqa: E402

NOTE_DROP = {"System note"}
UNMATCHED_SAMPLE = []      # a few "Other" bodies, reported so the rules can be tuned


def note_events(ids):
    """-> {deal_id: [[day, owner_id, bucket], ...]} via batch reads.

    Batched deliberately: one association call per deal would double a build that already makes
    one history call per deal. The v4 batch endpoint does 100 deals a call.
    """
    d2n, allnotes = {}, set()
    for i in range(0, len(ids), 100):
        s, a = call("/crm/v4/associations/deals/notes/batch/read", "POST",
                    {"inputs": [{"id": x} for x in ids[i:i + 100]]})
        for r in (a.get("results") or []):
            fid = str((r.get("from") or {}).get("id") or "")
            # toObjectId comes back as an INT here while every other object id in the API is a
            # string. Comparing without casting silently matches nothing.
            ns = [str(t.get("toObjectId")) for t in (r.get("to") or [])]
            if fid and ns:
                d2n[fid] = ns
                allnotes.update(ns)

    meta = {}
    nl = sorted(allnotes)
    for i in range(0, len(nl), 100):
        s, nb = call("/crm/v3/objects/notes/batch/read", "POST",
                     {"properties": ["hs_note_body", "hs_timestamp", "hubspot_owner_id"],
                      "inputs": [{"id": n} for n in nl[i:i + 100]]})
        for n in (nb.get("results") or []):
            meta[n["id"]] = n.get("properties", {})

    out = {}
    for did, ns in d2n.items():
        got = []
        for n in ns:
            p = meta.get(n)
            if not p:
                continue
            body = _plain(p.get("hs_note_body") or "")
            if not body.strip():
                continue
            b = _classify(body)
            if b in NOTE_DROP:
                continue
            if b == "Other" and len(UNMATCHED_SAMPLE) < 25:
                UNMATCHED_SAMPLE.append(body[:140])
            got.append([ist_day(p.get("hs_timestamp")), p.get("hubspot_owner_id") or "", b])
        if got:
            out[did] = got
    return out


def apply_note_metrics(d):
    """Fold the two note-derived buckets into the same `m` shape as the stage metrics.

    Same shape on purpose: the front end must not need to know that interestSent came from a
    note and onePagerSent came from a stage. One counting path, one range filter, one owner
    filter.
    """
    for key in METRIC_BUCKETS.values():
        d["m"].setdefault(key, [])
    for day, owner, bucket in d.get("nb", []):
        key = METRIC_BUCKETS.get(bucket)
        if not key:
            continue
        ev = [day, owner]
        if ev not in d["m"][key]:
            d["m"][key].append(ev)
    return d


def is_hot(d):
    """Does this deal earn a row in the Hot Pipeline — live, and past first reply."""
    return (not d["dead"]) and (not d["won"]) and d["r"] >= _SEQ.index("Replied")


def scan(extra_filters=None):
    out, after = [], None
    while True:
        filters = [{"propertyName": "pipeline", "operator": "EQ", "value": OPS_PIPELINE}]
        filters += (extra_filters or [])
        b = {"limit": 100, "properties": PROPS, "filterGroups": [{"filters": filters}]}
        if after:
            b["after"] = after
        s, d = call("/crm/v3/objects/deals/search", "POST", b)
        if s != 200:
            sys.exit(f"HubSpot error {s}: {str(d)[:200]}")
        out += d.get("results", [])
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            return out


def fetch_deals(ids):
    """Read specific deals by id, keeping the same property set the search returns."""
    out = []
    for i in range(0, len(ids), 100):
        s, d = call("/crm/v3/objects/deals/batch/read", "POST",
                    {"properties": PROPS, "inputs": [{"id": str(x)} for x in ids[i:i + 100]]})
        out += (d.get("results") or []) if s == 200 else []
    return out


def build_rows(deals, progress=False):
    """deals -> rows, with history, attendance and note metrics applied. The one code path."""
    rows, by_id = [], {}
    for r in deals:
        d = deal_row(r)
        if d is None:
            continue
        by_id[d["id"]] = d
        rows.append(d)

    if progress:
        print(f"{len(rows)} deals | fetching stage history...", file=sys.stderr)
    for i, d in enumerate(rows):
        apply_history(d, fetch_history(d["id"]))
        if progress and (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(rows)}", file=sys.stderr)

    if progress:
        print("fetching notes (batched)...", file=sys.stderr)
    for did, evs in note_events([d["id"] for d in rows]).items():
        if did in by_id:
            by_id[did]["nb"] = evs

    for d in rows:
        derive_attendance(d)
        apply_note_metrics(d)
        d.pop("ns", None)
    return rows


def team(owners, rows):
    """Owner list for the member dropdown, pulled LIVE from HubSpot rather than hardcoded.

    On the sales side a fixed list silently omitted two people for their whole first week, so
    every per-member view was blind to them. Only owners who actually appear on this pipeline
    are listed — the portal has owners who have never touched a Company Ops deal and a dropdown
    full of them is noise.
    """
    seen = set()
    for d in rows:
        if d["o"]:
            seen.add(d["o"])
        for evs in d["m"].values():
            for _day, who in evs:
                if who:
                    seen.add(who)
    return sorted([[i, owners.get(i, i)] for i in seen], key=lambda x: x[1])


def main(argv):
    load_stage_index()
    owners = load_owners()

    def arg(name):
        return argv[argv.index(name) + 1] if name in argv else None

    ids, since = arg("--deals"), arg("--deals-since")
    if ids or since:
        # Incremental refresh. Two inputs, deliberately: --deals is what a webhook knows,
        # --deals-since is the sweep that catches everything moved while nothing was listening.
        # hs_lastmodifieddate rather than hs_v2_date_entered_current_stage, which a later move
        # overwrites and which therefore under-counts any window ending in the past.
        wanted = [x for x in (ids or "").replace(",", " ").split() if x]
        deals = fetch_deals(wanted) if wanted else []
        if since:
            deals += scan([{"propertyName": "hs_lastmodifieddate", "operator": "GT",
                            "value": since}])
        seen, uniq = set(), []
        for r in deals:
            if r["id"] not in seen:
                seen.add(r["id"]); uniq.append(r)
        rows = build_rows(uniq)
        json.dump({"rows": rows, "team": team(owners, rows),
                   "high_water": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                  sys.stdout, separators=(",", ":"))
        print(f"\nrefreshed {len(rows)} deals", file=sys.stderr)
        return

    rows = build_rows(scan(), progress=True)
    out = {"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
           "pipeline": OPS_PIPELINE,
           "team": team(owners, rows),
           "rows": rows}
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ops_dashboard_data.json")
    with open(dest, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    won = sum(1 for d in rows if d["won"])
    dead = sum(1 for d in rows if d["dead"])
    print(f"wrote ops_dashboard_data.json — {len(rows)} deals ({won} won, {dead} dead)")
    if UNKNOWN_LABELS:
        # Loud on purpose. A stage added in the HubSpot UI that nothing here knows about is
        # ranked at the entry and counted toward no metric; that must never be a silent state.
        print(f"WARNING: {len(UNKNOWN_LABELS)} stage(s) not in _SEQ/_DEAD_SEQ — add them: "
              f"{sorted(UNKNOWN_LABELS)}", file=sys.stderr)
    if UNMATCHED_SAMPLE:
        print(f"note rules missed {len(UNMATCHED_SAMPLE)}+ bodies, sample for tuning:",
              file=sys.stderr)
        for b in UNMATCHED_SAMPLE[:10]:
            print(f"   · {b}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])