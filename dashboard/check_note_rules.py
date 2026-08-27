#!/usr/bin/env python3
"""Classify every note in the Company Ops portal and report what still lands in "Other".

WHY THIS EXISTS. Two dashboard metrics — `1st Interest Email Sent` and `One-Pager Received` —
are read out of note text rather than off a stage (see ops_note_rules.py for why they must be).
That makes the regexes in `ops_note_rules.py` load-bearing, and a regex that silently stops
matching is indistinguishable from a quiet week.

The full build prints a 10-line sample of unmatched bodies at the end, which is enough to notice
a problem and not enough to fix one. This reads the whole corpus instead, and it does NOT pull
stage history — so it finishes in under a minute where a full build takes tens of them.

Run it after any edit to ops_note_rules.py:

    python dashboard/check_note_rules.py           # bucket counts + everything in "Other"
    python dashboard/check_note_rules.py --all     # every note under its bucket

Exit code is 1 if more than --max-other (default 5) bodies are unclassified, so this can gate a
change. A handful of genuinely one-off notes is fine; a cluster is a missing rule.

Read-only. Token from HUBSPOT_API_KEY env or .env, exactly as the builder resolves it.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_ops_dashboard as B                      # noqa: E402
from ops_note_rules import classify, plain           # noqa: E402


def fetch_bodies():
    """-> [note body, ...] for every note attached to a deal on the Company Ops pipeline.

    Batched the same way the builder batches: one association call per 100 deals, one note read
    per 100 notes. Doing it per-deal would make this slower than the thing it is meant to make
    unnecessary.
    """
    B.load_stage_index()
    ids = [str(d["id"]) for d in B.scan()]
    print(f"{len(ids)} deals on the pipeline", file=sys.stderr)

    note_ids = set()
    for i in range(0, len(ids), 100):
        _, a = B.call("/crm/v4/associations/deals/notes/batch/read", "POST",
                      {"inputs": [{"id": x} for x in ids[i:i + 100]]})
        for r in (a.get("results") or []):
            # toObjectId comes back as an INT here while every other object id in the API is a
            # string. Comparing without casting silently matches nothing.
            note_ids.update(str(t.get("toObjectId")) for t in (r.get("to") or []))
    print(f"{len(note_ids)} notes attached", file=sys.stderr)

    bodies = []
    ordered = sorted(note_ids)
    for i in range(0, len(ordered), 100):
        _, nb = B.call("/crm/v3/objects/notes/batch/read", "POST",
                       {"properties": ["hs_note_body"],
                        "inputs": [{"id": n} for n in ordered[i:i + 100]]})
        for n in (nb.get("results") or []):
            body = plain(n.get("properties", {}).get("hs_note_body") or "")
            if body.strip():
                bodies.append(body)
    return bodies


def main(argv):
    show_all = "--all" in argv
    max_other = int(argv[argv.index("--max-other") + 1]) if "--max-other" in argv else 5

    bodies = fetch_bodies()
    if not bodies:
        print("no notes found — check the token and the pipeline id", file=sys.stderr)
        return 1

    buckets = collections.defaultdict(list)
    for b in bodies:
        buckets[classify(b)].append(b)

    print(f"\n{len(bodies)} notes\n")
    for name, got in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        # The two that are actual dashboard numbers are marked, so a drop in either is read as
        # the metric regression it is rather than as a tidy-up opportunity.
        from ops_note_rules import METRIC_BUCKETS
        tag = "  <- METRIC " + METRIC_BUCKETS[name] if name in METRIC_BUCKETS else ""
        print(f"  {name:<26} {len(got):>4}{tag}")
        if show_all:
            for body in sorted(set(got)):
                print(f"       · {body[:120]}")

    other = buckets.get("Other", [])
    if other and not show_all:
        print(f"\nunclassified ({len(other)}):")
        for body in sorted(set(other)):
            print(f"  · {body[:140]}")

    if len(other) > max_other:
        print(f"\nFAIL: {len(other)} unclassified, limit is {max_other}. "
              f"Look for a cluster — that is a missing rule, not noise.", file=sys.stderr)
        return 1
    print(f"\nOK — {len(other)} unclassified (limit {max_other})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
