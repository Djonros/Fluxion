"""A tiny MCP server over stdio for the tests of assistant/mcp.py: two tools, one read-only."""
import json
import sys

TOOLS = [
    {"name": "echo", "description": "повторить текст", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "write_note", "description": "записать заметку",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
]
notes = []
print("this server logs to stdout too", flush=True)  # not JSON: the client must skip it
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        if name == "echo":
            result = {"content": [{"type": "text", "text": "эхо: " + args.get("text", "")}]}
        elif name == "write_note":
            notes.append(args.get("text", ""))
            result = {"content": [{"type": "text", "text": f"заметок: {len(notes)}"}]}
        else:
            result = {"content": [{"type": "text", "text": "нет такого"}], "isError": True}
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                                     "error": {"code": -32601, "message": "unknown"}}) + "\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
