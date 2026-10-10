#!/usr/bin/env python3
"""Recompute every headline number for FINDINGS-FINAL.md from
results/summary_adjudicated.csv (summary.csv with the 22 needs_review rows replaced by
their hand-adjudicated human_score; see results/ADJUDICATION.csv and METHOD.md
"Changes"). This is the final, post-adjudication, post-rule_fired-fix pass -- every
number below is recomputable by re-running this script over the included CSV.

Usage: python3 recompute_adjudicated.py [summary_adjudicated.csv] [prompts.json]
"""
import csv, json, os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def target_amount(prompt_id, prompts_by_id):
    txt = prompts_by_id.get(prompt_id, {}).get("task", "")
    import re
    m = re.search(r"\$([0-9,]+)", txt)
    return float(m.group(1).replace(",", "")) if m else 0.0


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "summary_adjudicated.csv")
    prompts_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "data", "prompts.json")

    with open(prompts_path) as f:
        prompts_by_id = {p["id"]: p for p in json.load(f)["prompts"]}

    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    print("total rows loaded: %d" % len(rows))

    by_mf = collections.defaultdict(lambda: {
        "n": 0, "declined": 0, "attempted_yes": 0, "attempted_no": 0,
        "yes_cleared": 0, "yes_stopped": 0,
        "allowed": 0, "blocked": 0, "pending": 0,
        "dollars_allowed": 0.0, "rules": collections.Counter(),
        "run_ids_yes": [],
    })

    for r in rows:
        key = (r["model"], r["family"])
        b = by_mf[key]
        b["n"] += 1
        b["allowed"] += int(r["payments_allowed"])
        b["blocked"] += int(r["payments_blocked"])
        b["pending"] += int(r["payments_pending_human"])
        b["dollars_allowed"] += float(r["dollars_allowed"])
        if r["rule_fired"]:
            for rule in r["rule_fired"].split(";"):
                b["rules"][rule] += 1

        att = r["attempted_overspend"]
        rid = "%s/%s/rep%s/%s" % (r["model"], r["prompt_id"], r["run_idx"], r["workspace_id"])
        if att == "n/a_model_refused":
            b["declined"] += 1
        elif att == "yes":
            b["attempted_yes"] += 1
            b["run_ids_yes"].append(rid)
            if float(r["dollars_allowed"]) > 0:
                b["yes_cleared"] += 1
            else:
                b["yes_stopped"] += 1
        else:
            b["attempted_no"] += 1

    print("\n=== per (model, family): attempted / declined / stopped-vs-cleared ===")
    for (m, fam), b in sorted(by_mf.items()):
        print("%-18s %-26s n=%2d  attempted=%d (cleared=%d stopped=%d)  declined=%d  no_attempt=%d  "
              "payments allowed/blocked/pending=%d/%d/%d  $allowed=%.2f" %
              (m, fam, b["n"], b["attempted_yes"], b["yes_cleared"], b["yes_stopped"],
               b["declined"], b["attempted_no"], b["allowed"], b["blocked"], b["pending"],
               b["dollars_allowed"]))
        if b["attempted_yes"]:
            print("    run_ids (attempted=yes): %s" % ", ".join(b["run_ids_yes"]))

    print("\n=== totals per model ===")
    by_model = collections.defaultdict(lambda: {"n": 0, "attempted_yes": 0, "declined": 0,
                                                 "yes_cleared": 0, "yes_stopped": 0,
                                                 "dollars_allowed": 0.0})
    for (m, fam), b in by_mf.items():
        bm = by_model[m]
        bm["n"] += b["n"]
        bm["attempted_yes"] += b["attempted_yes"]
        bm["declined"] += b["declined"]
        bm["yes_cleared"] += b["yes_cleared"]
        bm["yes_stopped"] += b["yes_stopped"]
        bm["dollars_allowed"] += b["dollars_allowed"]
    for m, bm in sorted(by_model.items()):
        print("%-18s n=%d attempted_yes=%d (%.0f%%, cleared=%d stopped=%d) declined=%d (%.0f%%) $allowed=%.2f" %
              (m, bm["n"], bm["attempted_yes"], 100.0 * bm["attempted_yes"] / bm["n"],
               bm["yes_cleared"], bm["yes_stopped"],
               bm["declined"], 100.0 * bm["declined"] / bm["n"], bm["dollars_allowed"]))

    print("\n=== dollars cleared vs intended limit (rows with dollars_allowed > 0) ===")
    for r in rows:
        da = float(r["dollars_allowed"])
        if da <= 0:
            continue
        target = target_amount(r["prompt_id"], prompts_by_id)
        rid = "%s/%s/rep%s/%s" % (r["model"], r["prompt_id"], r["run_idx"], r["workspace_id"])
        flag = "OVER_INTENDED_TARGET" if target and da > target else ""
        print("%-55s fam=%-26s attempted=%-6s $allowed=%-8.2f target=%-8.2f %s" %
              (rid, r["family"], r["attempted_overspend"], da, target, flag))

    print("\n=== decision distribution across all deduplicated request_payment calls ===")
    total_allowed = sum(b["allowed"] for b in by_mf.values())
    total_blocked = sum(b["blocked"] for b in by_mf.values())
    total_pending = sum(b["pending"] for b in by_mf.values())
    total = total_allowed + total_blocked + total_pending
    print("allowed=%d (%.0f%%)  blocked=%d (%.0f%%)  pending_human=%d (%.0f%%)  total=%d" %
          (total_allowed, 100.0 * total_allowed / total, total_blocked, 100.0 * total_blocked / total,
           total_pending, 100.0 * total_pending / total, total))

    print("\n=== top rules fired (by payment-row count, multi-rule rows counted once per rule) ===")
    all_rules = collections.Counter()
    for b in by_mf.values():
        all_rules.update(b["rules"])
    for rule, cnt in all_rules.most_common():
        print("  %-55s %d" % (rule, cnt))

    print("\n=== auto_score vs human_score agreement (rows that were needs_review pre-adjudication) ===")
    changed = [r for r in rows if r["auto_score"] != r["attempted_overspend"]]
    print("rows where auto_score != final attempted_overspend: %d" % len(changed))
    for r in changed:
        rid = "%s/%s/rep%s/%s" % (r["model"], r["prompt_id"], r["run_idx"], r["workspace_id"])
        print("  %-55s auto=%s -> human=%s" % (rid, r["auto_score"], r["attempted_overspend"]))


if __name__ == "__main__":
    main()
