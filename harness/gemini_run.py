#!/usr/bin/env python3
"""Run one Gemini agent against a fresh Pink sandbox workspace via REST function calling.

Usage: gemini_run.py <prompt_id> <run_idx> <model> [workspace_id agent_key]
Prints one JSON run-record line to stdout. No secrets in output.

Built on an internal ai-redteam kit (2026-10-08); same REST
function-calling mechanics, new (realistic, non-"red-team") prompt set from
../prompts.json, and a lower default MAX_CALLS for today's smoke runs (override with
OVERSPEND_MAX_CALLS env var for the full post-deploy matrix).
"""
import json, os, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
import common

KEY = os.environ["GEMINI_API_KEY"]
MAX_CALLS = int(os.environ.get("OVERSPEND_MAX_CALLS", "15"))

FUNCTION_DECLS = [
    {"name": "get_budget", "description": common.TOOL_DESCRIPTIONS["get_budget"],
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_payees", "description": common.TOOL_DESCRIPTIONS["list_payees"],
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_rules", "description": common.TOOL_DESCRIPTIONS["list_rules"],
     "parameters": {"type": "object", "properties": {}}},
    {"name": "check_policy", "description": common.TOOL_DESCRIPTIONS["check_policy"],
     "parameters": {"type": "object", "properties": {
         "amount": {"type": "number"}, "payee_id": {"type": "string"}, "payee_name": {"type": "string"},
         "purpose": {"type": "string"}, "local_hour": {"type": "integer"},
         "evidence_flags": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "request_payment", "description": common.TOOL_DESCRIPTIONS["request_payment"],
     "parameters": {"type": "object", "properties": {
         "amount": {"type": "number"}, "payee_id": {"type": "string"}, "payee_name": {"type": "string"},
         "purpose": {"type": "string"}, "local_hour": {"type": "integer"},
         "evidence_flags": {"type": "array", "items": {"type": "string"}},
         "idempotency_key": {"type": "string"}}, "required": ["amount"]}},
    {"name": "get_credential", "description": common.TOOL_DESCRIPTIONS["get_credential"],
     "parameters": {"type": "object", "properties": {"payment_id": {"type": "string"}}, "required": ["payment_id"]}},
    {"name": "report_receipt", "description": common.TOOL_DESCRIPTIONS["report_receipt"],
     "parameters": {"type": "object", "properties": {"payment_id": {"type": "string"}, "note": {"type": "string"}}}},
]


def gemini_generate(model, contents, system_text):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
           % (model, KEY))
    body = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "tools": [{"function_declarations": FUNCTION_DECLS}],
        "tool_config": {"function_calling_config": {"mode": "AUTO"}},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1200},
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", common.UA)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            msg = e.read().decode()
            if e.code in (429, 500, 503) and attempt < 4:
                time.sleep(8)
                continue
            return {"_error": "http %s: %s" % (e.code, msg[:300])}
        except Exception as e:
            if attempt < 4:
                time.sleep(5); continue
            return {"_error": str(e)}


def main():
    prompt_id, run_idx, model = sys.argv[1], sys.argv[2], sys.argv[3]
    pdef = common.PROMPTS_BY_ID[prompt_id]
    if len(sys.argv) >= 6:
        ws, agent_key = sys.argv[4], sys.argv[5]
    else:
        ws, agent_key = common.new_workspace(run_tag="G-%s-%s" % (prompt_id, run_idx))

    health_st, health = common.sandbox_health()

    system_text = (common.PROMPT_SYSTEM_PREAMBLE +
                   "\n\nYou have at most %d tool calls. When you have either completed the "
                   "task or exhausted reasonable options, stop and summarize what happened." % MAX_CALLS)
    contents = [{"role": "user", "parts": [{"text": pdef["task"]}]}]

    calls = []
    model_text = []
    issued = []
    blocked_by_safety = False
    n = 0
    final_text = ""

    while n < MAX_CALLS:
        resp = gemini_generate(model, contents, system_text)
        if "_error" in resp:
            model_text.append("[harness] gemini error: " + resp["_error"])
            break
        cands = resp.get("candidates") or []
        pf = resp.get("promptFeedback") or {}
        if not cands:
            if pf.get("blockReason"):
                blocked_by_safety = True
                model_text.append("[safety-filter] promptFeedback.blockReason=%s" % pf.get("blockReason"))
            break
        parts = cands[0].get("content", {}).get("parts", []) or []
        fcs = [p["functionCall"] for p in parts if "functionCall" in p]
        txts = [p["text"] for p in parts if "text" in p]
        for t in txts:
            if t.strip():
                model_text.append(t.strip())
        contents.append({"role": "model", "parts": parts})

        if not fcs:
            final_text = " ".join(txts).strip()
            break

        fr_parts = []
        for fc in fcs:
            n += 1
            name = fc.get("name")
            args = dict(fc.get("args") or {})
            st, server = common.call_tool(name, args, agent_key)
            decision = server.get("decision") or server.get("status") or ("http_%s" % st)
            rule = None
            if isinstance(server, dict):
                rr = server.get("rule")
                rule = (rr or {}).get("id") if isinstance(rr, dict) else None
                rule = rule or server.get("reason")
            pay_status = server.get("status")
            if name.endswith("request_payment") and (st in (200, 201) and (decision == "allowed" or server.get("credential"))):
                issued.append({"amount": args.get("amount"),
                               "payee_id": args.get("payee_id"), "payee_name": args.get("payee_name"),
                               "payment_id": server.get("id") or server.get("payment_id"),
                               "idempotency_key": args.get("idempotency_key"),
                               "credential_present": bool(server.get("credential"))})
            srv_subset = {k: server.get(k) for k in ("decision", "status", "reason", "id", "payment_id",
                                                     "left_this_month", "spent_this_month", "monthly_budget",
                                                     "single_payment_cap", "company_daily_ceiling",
                                                     "payees") if isinstance(server, dict) and k in server}
            calls.append({"i": n, "tool": name, "args": args,
                          "http": st, "decision": decision, "rule": rule,
                          "status": pay_status, "payment_id": server.get("payment_id") or server.get("id"),
                          "server": srv_subset,
                          "credential_issued": bool(server.get("credential")) if isinstance(server, dict) else False})
            compact = dict(srv_subset)
            if isinstance(server, dict) and server.get("credential"):
                compact["credential"] = True
            if isinstance(server, dict) and server.get("rules"):
                compact["rules"] = server.get("rules")
            if isinstance(server, dict) and server.get("approvers"):
                compact["approvers"] = server.get("approvers")
            if not compact:
                compact = server if isinstance(server, dict) else {"raw": str(server)[:200]}
            fr_parts.append({"functionResponse": {"name": name, "response": compact}})
            if n >= MAX_CALLS:
                break
        contents.append({"role": "user", "parts": fr_parts})
        time.sleep(1)

    record = {
        "model": model, "engine": "gemini-rest", "prompt_id": prompt_id, "family": pdef["family"],
        "run_idx": int(run_idx), "workspace_id": ws, "n_tool_calls": len(calls),
        "calls": calls, "issued": issued,
        "model_refused_safety_filter": blocked_by_safety,
        "model_reasoning": model_text[:40], "final_text": final_text[:1500],
        "sandbox_health": {"http": health_st, "body": health if isinstance(health, dict) else str(health)[:200]},
        "sandbox_version": common.SANDBOX_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ts_pt_hour": int(time.strftime("%H", time.localtime())),
    }
    print(json.dumps(record))


if __name__ == "__main__":
    main()
