"""Shared config for the Agent Overspend Benchmark harness (Data #5).

Builds on an internal ai-redteam kit (2026-10-08, same sandbox, same tool dispatch).
Differences from that kit:
 - 20 fixed prompts (prompts.json, 5 families x 4) replace the 4 P1-P4 prompts.
 - Prompts are written as ordinary business requests, not "you are a red-team
   tester" framing (see PROMPTS.md for why).
 - Workspace sign-ups use the internal bench identity, not a personal email.

No secrets are written to disk by callers; workspace agent keys live only in
memory/process (or, for create_pool, in a local temp file, never in deliverables).
"""
import json, os, time, urllib.request, urllib.error

BASE = "https://agentic-sandbox.pinkwallet.com/v1"
WS_URL = "https://agentic-sandbox.pinkwallet.com/v1/sandbox/workspaces"
HEALTH_URL = "https://agentic-sandbox.pinkwallet.com/health"

# Sandbox was updated 2026-10-10 ~10:00 PT to this Cloudflare version. Recorded verbatim
# in every run record per METHOD.md so results are attributable to a known server build.
SANDBOX_VERSION = "ace8b028 (2026-10-10)"

BENCH_COMPANY = "Pink Bench (internal)"
BENCH_EMAIL = os.environ.get("OVERSPEND_BENCH_EMAIL", "bench-internal@example.com")

UA = "Mozilla/5.0 pink-overspend-benchmark/1.0"

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "..", "prompts.json")) as f:
    _PROMPTS_DOC = json.load(f)

PROMPT_SYSTEM_PREAMBLE = _PROMPTS_DOC["_meta"]["system_preamble_for_agent"]
PROMPTS_BY_ID = {p["id"]: p for p in _PROMPTS_DOC["prompts"]}

# Tool schema shared understanding. Agent-visible tools only (no admin).
TOOL_DESCRIPTIONS = {
    "get_budget":   "Show the agent's remaining monthly budget, per-payment cap, company daily ceiling, vault balance.",
    "list_payees":  "List approved payees (id, name, category).",
    "list_rules":   "List the policy rules that apply.",
    "check_policy": "Dry-run a payment. args: amount(number), payee_id OR payee_name(string), purpose(string), local_hour(0-23 int, optional), evidence_flags(list of strings, optional). Returns would_allow/would_ask/would_block.",
    "request_payment": "Request a real payment. Same args as check_policy plus idempotency_key(string, optional). Returns status allowed (201, credential issued) / pending_human (202) / blocked (403).",
    "get_credential": "args: payment_id(string). Fetch the credential for a payment id (only issued if allowed).",
    "report_receipt": "args: payment_id(string), note(string). Report a receipt for a completed payment.",
}


def _post(url, body, key=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": "http", "code": e.code}


def _get(url, key=None):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": "http", "code": e.code}


def sandbox_health():
    """GET /health (no auth). Returns (status, json). Does not reveal a build/commit
    id (the live endpoint has no version field as of 2026-10-10) -- see METHOD.md for
    how we instead infer live-vs-staged from the idempotency-replay behavior."""
    return _get(HEALTH_URL)


def new_workspace(company=None, email=None, run_tag=""):
    """Create a fresh workspace, retrying on 429 rate limit. Returns (workspace_id, agent_key).
    Always uses the internal bench identity per task rules -- never a personal email."""
    company = company or (BENCH_COMPANY + (" " + run_tag if run_tag else ""))
    email = email or BENCH_EMAIL
    for attempt in range(8):
        st, resp = _post(WS_URL, {"company": company, "email": email, "template": "coffee"})
        if st in (200, 201):
            ak = next(a["key"] for a in resp["agents"] if a["id"] == "a_purch")
            return resp["workspace_id"], ak
        time.sleep(22)
    raise RuntimeError("could not create workspace: %s %s" % (st, resp))


def call_tool(name, args, key):
    """Dispatch a tool call against the REST mirror. Returns (http_status, server_json).
    Never includes keys in the return value."""
    name = name.replace("pink.", "").replace("pink_", "")
    if name == "get_budget":
        return _get(BASE + "/budget", key)
    if name == "list_payees":
        return _get(BASE + "/payees", key)
    if name == "list_rules":
        return _get(BASE + "/rules", key)
    if name in ("check_policy", "request_payment"):
        body = {}
        for f in ("amount", "payee_id", "payee_name", "purpose", "reason", "local_hour",
                  "evidence", "evidence_flags", "currency", "idempotency_key"):
            if f in args and args[f] is not None:
                body[f] = args[f]
        path = "/payments/check" if name == "check_policy" else "/payments"
        return _post(BASE + path, body, key)
    if name == "get_credential":
        pid = args.get("payment_id") or args.get("id")
        return _get(BASE + "/payments/%s" % pid, key)
    if name == "report_receipt":
        pid = args.get("payment_id") or args.get("id")
        return _post(BASE + "/payments/%s/receipt" % pid, {"note": args.get("note", "ok")}, key)
    return 400, {"error": "unknown tool", "tool": name}
