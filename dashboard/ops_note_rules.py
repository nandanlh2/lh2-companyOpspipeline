# -*- coding: utf-8 -*-
"""Note classification for the Company Ops dashboard — the single source of truth.

WHY NOTES AT ALL. Two dashboard metrics have no stage behind them, because neither is a
state the deal is IN:

  * "1st Interest Email Sent" — we mailed them something (deck, intro, calendly) while the
    deal legitimately stays wherever it is. There is no v3 stage for it and there should not
    be one: `docs/OPSDATA_PIPELINE.md` §"the outcome IS the stage" forbids a stage that means
    "we tried". So it is read off the note somebody wrote.
  * "One-Pager Received" — them confirming receipt / coming back on the one-pager. `One Pager
    Shared` is us sending; the reply is an event on the deal, not a new state.

ORDER MATTERS — first match wins, same as the sales-side note_rules.py. This module lives NEXT
TO build_ops_dashboard.py and inside the repo on purpose: the sales dashboard once imported its
rules from a local-only sibling directory, which resolved on the laptop and raised ImportError
in Actions, leaving the note KPIs silently empty on every deployed build.

These rules are IMPORTED, never copied. An earlier hand-copy of KPI definitions on the sales
side drifted and inflated dial counts 2.7x.

The regexes below are a STARTING SET written against the flow, not against a scrape of real
Company Ops notes — that portal's notes have not been read yet. Expect a tuning pass once the
first build reports its unmatched sample (see UNMATCHED_SAMPLE in the builder).
"""
import re, html

NOTE_RULES = [
    # Machine-written first so our own scripts' notes can never be counted as somebody's work.
    ("System note", r"^(\[outflo|\[opsdata|\[pipeline|imported from outflo|gmail import|"
                    r"auto-created|migration note)"),

    # --- the two that feed dashboard metrics ---
    # "Received" must be tested BEFORE "sent", or "sent the one pager, they received it"
    # lands in the wrong bucket.
    ("One pager received",
        r"((one[- ]?pager|onepager|1[- ]?pager|deck)[^.]{0,40}?"
        r"(received|acknowledg|confirmed receipt|went through|reviewed|got it|came back|"
        r"responded|replied)"
        r"|(received|got|reviewed|went through)[^.]{0,25}?(one[- ]?pager|onepager|deck))"),

    ("Interest email sent",
        r"((interest|intro|introduct|first|1st|follow[- ]?up)[^.]{0,30}?(email|mail|e-mail)"
        r"[^.]{0,30}?(sent|shared|out|fired|dropped)"
        r"|(sent|shared|mailed|dropped|fired)[^.]{0,40}?"
        r"(interest (email|mail)|intro (email|mail)|introduction (email|mail)|"
        r"details over (mail|email)|deck|calendly|profile|company profile|proposal)"
        r"|shared (the )?(details|collateral|material)s? over (mail|email))"),

    # --- context buckets: not metrics, but they explain a quiet week ---
    ("Not interested", r"(not interested|was ?n'?t interested|were ?n'?t interested|"
                       r"no bandwidth|no interest|not looking|declined|passed on)"),
    ("Privacy concern", r"(privacy|confidential|nda|legal (said|team)|data protection|"
                        r"can'?t share (the )?data|cannot share (the )?data|gdpr|dpa concern)"),
    ("No reply", r"(no (reply|response|resposne)|did ?n'?o?t? (reply|respond|revert)|"
                 r"went (quiet|silent|cold)|ghosted|radio silence|no revert)"),
    ("Callback booked", r"(call ?back|asked to call|get(ting)? back|will revert|will lmk|"
                        r"in a meeting|busy|reschedul|connect back|check back)"),
    ("Meeting fixed", r"(call fixed|meeting fixed|gmeet fixed|discovery call (fixed|set)|"
                      r"scheduled for|slot(s)? (shared|confirmed)|invite sent)"),
    ("Sample discussed", r"(sample[^.]{0,30}?(requested|asked|promised|shared|sent|received)|"
                         r"(asked|requested)[^.]{0,20}?sample)"),
    ("Chase sent", r"(followed up|follow[- ]?up (sent|done)|sent (a )?(message|reminder|nudge)|"
                   r"whats ?app|\bwa\b|pinged|nudged|left a message)"),
]

# Buckets that merely restate a stage KPI are dropped by the builder so the same act is never
# counted once as a stage metric and again as note activity.
BUCKETS = [b for b, _ in NOTE_RULES] + ["Other"]

# The two buckets that ARE dashboard metrics. Named here rather than in the builder so the
# metric and the rule that produces it cannot drift apart.
METRIC_BUCKETS = {"Interest email sent": "interestSent",
                  "One pager received": "onePagerReceived"}


def plain(s):
    """HubSpot note bodies are HTML. Strip tags and collapse whitespace before matching."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def classify(t):
    s = (t or "").lower()
    for name, rx in NOTE_RULES:
        if re.search(rx, s):
            return name
    return "Other"