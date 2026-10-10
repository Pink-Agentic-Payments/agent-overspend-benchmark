#!/usr/bin/env python3
"""Orchestration driver for the full matrix (not a scoring/harness-logic file).
Loops (model, prompt_id, rep) combos, skips ones already present in runs.jsonl,
appends each new run immediately, retries once on error, paces workspace creation.
Also appends each new workspace_id to WORKSPACES.md incrementally.
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
RUNS = os.path.join(ROOT, "results", "runs.jsonl")
WS_MD = os.path.join(ROOT, "WORKSPACES.md")

with open(os.path.join(ROOT, "prompts.json")) as f:
    PROMPT_IDS = [p["id"] for p in json.load(f)["prompts"]]

REPS = 2
MODELS = ["gemini", "claude"]  # interleave for pacing

def load_done():
    done = set()
    if os.path.exists(RUNS):
        with open(RUNS) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                engine = d.get("engine", "")
                m = "gemini" if "gemini" in engine else "claude"
                done.add((m, d.get("prompt_id"), int(d.get("run_idx"))))
    return done


def append_run(line):
    with open(RUNS, "a") as f:
        f.write(line.rstrip("\n") + "\n")


def append_workspace(ws_id, ts, prompt_id, model_engine_label, model_name):
    with open(WS_MD, "a") as f:
        f.write("| `%s` | %s | `harness/%s` | %s | %s |\n" %
                (ws_id, ts, model_engine_label, prompt_id, model_name))


def run_one(model, pid, rep):
    if model == "gemini":
        cmd = [sys.executable, os.path.join(HERE, "gemini_run.py"), pid, str(rep), "gemini-3.8-flash"]
        env = dict(os.environ, OVERSPEND_MAX_CALLS="15")
        script_label = "gemini_run.py"
    else:
        cmd = [sys.executable, os.path.join(HERE, "claude_run.py"), pid, str(rep), "sonnet"]
        env = dict(os.environ)
        script_label = "claude_run.py"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=330, env=env, cwd=HERE)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if not out:
            return None, "no stdout. stderr_tail=%s" % err[-500:]
        line = out.splitlines()[-1]
        rec = json.loads(line)
        return line, None
    except Exception as e:
        return None, str(e)


def main():
    combos = []
    for model in MODELS:
        for pid in PROMPT_IDS:
            for rep in range(REPS):
                combos.append((model, pid, rep))

    done = load_done()
    print("already done: %d combos" % len(done), file=sys.stderr)

    todo = [c for c in combos if c not in done]
    print("remaining: %d combos" % len(todo), file=sys.stderr)

    ws_since_minute = []
    for i, (model, pid, rep) in enumerate(todo):
        print("[%d/%d] %s %s rep%d ..." % (i + 1, len(todo), model, pid, rep), file=sys.stderr, flush=True)

        # pace workspace creation: ~3 per 60s across both models
        now = time.time()
        ws_since_minute[:] = [t for t in ws_since_minute if now - t < 60]
        if len(ws_since_minute) >= 3:
            wait = 60 - (now - ws_since_minute[0]) + 1
            print("  pacing: sleeping %.0fs" % wait, file=sys.stderr)
            time.sleep(max(wait, 0))

        line, err = run_one(model, pid, rep)
        if line is None:
            print("  ERROR (attempt 1): %s -- retrying once" % err, file=sys.stderr)
            time.sleep(10)
            line2, err2 = run_one(model, pid, rep)
            if line2 is None:
                print("  ERROR (attempt 2): %s -- giving up on this combo" % err2, file=sys.stderr)
                append_run(json.dumps({"model": "sonnet" if model == "claude" else "gemini-3.8-flash",
                                       "engine": "claude-cli-mcp" if model == "claude" else "gemini-rest",
                                       "prompt_id": pid, "run_idx": rep, "error": True,
                                       "attempt1_error": err, "attempt2_error": err2,
                                       "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
                continue
            else:
                append_run(line2)
                rec = json.loads(line2)
        else:
            append_run(line)
            rec = json.loads(line)

        ws_since_minute.append(time.time())
        if rec.get("workspace_id"):
            append_workspace(rec["workspace_id"], rec.get("ts", ""),
                              rec.get("prompt_id", pid),
                              "gemini_run.py" if model == "gemini" else "claude_run.py",
                              rec.get("model", ""))

        if model == "gemini":
            time.sleep(3)

    print("DONE. total combos now in runs.jsonl: %d" % len(load_done()), file=sys.stderr)


if __name__ == "__main__":
    main()
