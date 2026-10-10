#!/usr/bin/env python3
"""Build results/summary_adjudicated.csv = results/summary.csv with attempted_overspend
replaced by the hand-adjudicated human_score for the runs in results/ADJUDICATION.csv
(all of them originally scored needs_review by summarize.py's automatic heuristics).
Adds an `auto_score` column preserving the original automatic score for every row (equal
to attempted_overspend for the 58 rows that were never adjudicated).

Usage: python3 make_adjudicated_summary.py [summary.csv] [ADJUDICATION.csv] [out.csv]
"""
import csv, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def run_id(row):
    return "%s/%s/rep%s/%s" % (row["model"], row["prompt_id"], row["run_idx"], row["workspace_id"])


def main():
    summary_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "summary.csv")
    adj_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "data", "ADJUDICATION.csv")
    out_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(ROOT, "data", "summary_adjudicated.csv")

    with open(adj_path) as f:
        adj_by_run = {r["run_id"]: r["human_score"] for r in csv.DictReader(f)}

    with open(summary_path) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    applied = 0
    for row in rows:
        auto = row["attempted_overspend"]
        row["auto_score"] = auto
        rid = run_id(row)
        if rid in adj_by_run:
            row["attempted_overspend"] = adj_by_run[rid]
            applied += 1

    out_fields = fieldnames + ["auto_score"]
    # keep attempted_overspend and auto_score next to each other for readability
    out_fields = [f for f in fieldnames if f != "attempted_overspend"] + ["attempted_overspend", "auto_score"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    print("wrote %d rows to %s (%d rows adjudicated by hand)" % (len(rows), out_path, applied))
    if applied != len(adj_by_run):
        print("WARNING: %d adjudication rows did not match any summary.csv row" %
              (len(adj_by_run) - applied))


if __name__ == "__main__":
    main()
