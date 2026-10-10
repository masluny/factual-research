"""Minimal MCP server (stdio, JSON-RPC 2.0) so factual-research works as tools in
the Claude desktop app or any MCP client. Each tool maps onto a CLI
command and returns its text output.

Claude desktop config (claude_desktop_config.json):
  "factual-research": {"command": "factual-research", "args": ["--project", "/path/to/project", "mcp"]}
"""
from __future__ import annotations

import contextlib
import io
import json
import shlex
import sys

from . import __version__

S = {"type": "string"}
I = {"type": "integer"}

TOOLS = [
    ("status", "Project overview and the recommended next step.", {}, [], lambda a: ["status"]),
    ("search", "Run the plan's searches across the domain pack's sources (or an ad-hoc query).",
     {"query": S, "connector": S, "sq": S, "limit": I}, [],
     lambda a: ["search"] + (["--query", a["query"]] if a.get("query") else []) +
     (["--connector", a["connector"]] if a.get("connector") else []) + (["--sq", a["sq"]] if a.get("sq") else []) +
     (["--limit", str(a["limit"])] if a.get("limit") else [])),
    ("screen_list", "List records awaiting screening (title, design, abstract), most relevant first.",
     {"limit": I, "offset": I, "status": S}, [],
     lambda a: ["screen", "list", "--limit", str(a.get("limit", 20)), "--offset", str(a.get("offset", 0))] +
     (["--status", a["status"]] if a.get("status") else [])),
    ("screen", "Include / exclude / maybe records. Exclusions need a reason.",
     {"keys": {"type": "array", "items": S}, "decision": {"type": "string", "enum": ["include", "exclude", "maybe"]},
      "reason": S, "stage": {"type": "string", "enum": ["ta", "ft"]}}, ["keys", "decision"],
     lambda a: ["screen", a["decision"], *a["keys"]] + (["--reason", a["reason"]] if a.get("reason") else []) +
     ["--stage", a.get("stage", "ta")]),
    ("enrich", "Retraction notices, published versions of preprints, study designs, venue signals.", {}, [],
     lambda a: ["enrich"]),
    ("fetch", "Download open-access full texts for included records.", {}, [], lambda a: ["fetch"]),
    ("show", "Show a record; with text=true print its full text with page/section locations.",
     {"key": S, "text": {"type": "boolean"}, "loc": S, "grep": S}, ["key"],
     lambda a: ["show", a["key"]] + (["--text"] if a.get("text") else []) + (["--loc", a["loc"]] if a.get("loc") else [])
     + (["--grep", a["grep"]] if a.get("grep") else [])),
    ("find", "Find passages (with location) in included sources to quote verbatim.",
     {"query": S, "keys": S, "limit": I}, ["query"],
     lambda a: ["find", a["query"]] + (["--keys", a["keys"]] if a.get("keys") else []) + ["--limit", str(a.get("limit", 8))]),
    ("claim_add", "Add a claim to the evidence matrix.", {"text": S, "sq": S, "id": S}, ["text"],
     lambda a: ["claim", "add", a["text"]] + (["--sq", a["sq"]] if a.get("sq") else []) + (["--id", a["id"]] if a.get("id") else [])),
    ("evidence_add", "Attach a source to a claim with a verbatim quote (verified immediately).",
     {"claim": S, "key": S, "stance": {"type": "string", "enum": ["for", "against", "mixed", "neutral"]}, "quote": S,
      "loc": S, "effect": S}, ["claim", "key", "stance", "quote"],
     lambda a: ["evidence", "add", a["claim"], a["key"], "--stance", a["stance"], "--quote", a["quote"]] +
     (["--loc", a["loc"]] if a.get("loc") else []) + (["--effect", a["effect"]] if a.get("effect") else [])),
    ("matrix", "Consensus and GRADE-style certainty per claim.", {}, [], lambda a: ["matrix"]),
    ("extract_set", "Set one evidence-table cell (value + verbatim quote + location); verified on write.",
     {"key": S, "field": S, "value": S, "quote": S, "loc": S}, ["key", "field", "value"],
     lambda a: ["extract", "set", a["key"], a["field"], a["value"]] + (["--quote", a["quote"]] if a.get("quote") else [])
     + (["--loc", a["loc"]] if a.get("loc") else [])),
    ("table", "Render a table (sources|extraction|sof|matrix|custom) as markdown.",
     {"kind": S, "spec": S, "format": S}, ["kind"],
     lambda a: ["table", a["kind"], "--format", a.get("format", "md")] + (["--spec", a["spec"]] if a.get("spec") else [])),
    ("verify", "Score a markdown draft 0-100 with ranked, actionable issues.", {"draft": S, "target": I}, ["draft"],
     lambda a: ["verify", a["draft"], "--target", str(a.get("target", 80))]),
    ("render", "Produce the final text with in-text citations and reference list.", {"draft": S, "style": S}, ["draft"],
     lambda a: ["render", a["draft"]] + (["--style", a["style"]] if a.get("style") else [])),
    ("report", "Write the HTML research report.", {"draft": S}, [],
     lambda a: ["report"] + (["--draft", a["draft"]] if a.get("draft") else [])),
]


def _run(argv: list[str], project: str | None) -> tuple[str, bool]:
    from .cli import main
    buf = io.StringIO()
    err = False
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            main((["--project", project] if project else []) + argv)
        except SystemExit as e:
            if e.code not in (0, None):
                err = True
                if isinstance(e.code, str):
                    print(e.code)
        except Exception as e:  # noqa: BLE001
            err = True
            print(f"{type(e).__name__}: {e}")
    return buf.getvalue()[-60000:], err


def serve(project: str | None = None):
    tools = {t[0]: t for t in TOOLS}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue  # notification
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "factual-research", "version": __version__}}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props,
                                                                          "required": req}}
                             for n, d, props, req, _ in TOOLS]}
        elif method == "tools/call":
            t = tools.get(params.get("name"))
            if not t:
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}})
                continue
            argv = t[4](params.get("arguments") or {})
            text, err = _run(argv, project)
            res = {"content": [{"type": "text", "text": text or "(no output)"}], "isError": err}
        else:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})
            continue
        _send({"jsonrpc": "2.0", "id": mid, "result": res})


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cli_line(tool: str, args: dict) -> str:
    t = {x[0]: x for x in TOOLS}[tool]
    return "factual-research " + " ".join(shlex.quote(x) for x in t[4](args))
