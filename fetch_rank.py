import json, urllib.request, sys

BASE = "http://127.0.0.1:49920/01fe583d8ba57fea4c4e11f8fe01311e/mcp"
AUTH = "Bearer NjTTPuGHMTX_qpYtw7D6bdWWSGQvuTenKNcu4XEbkuQ"
CTX = "eyJzZXNzaW9uSWQiOiI1ODE0ZDRlMS00YzI4LTQ2N2ItYWRjMi1iOTFlODhiNWEzMWIiLCJ3ZWxjb21lTW9kZSI6IndvcmtpbmciLCJ3b3Jrc3BhY2UiOiJFOi93b3JrYnVkZHkvMjAyNi0xMC0yOC0xMS0yMi0yNiIsImFsbG93ZWRCdWlsdGluVG9vbHMiOlsiYWdlbnRfbWFpbF9kb3dubG9hZF9hdHRhY2htZW50IiwiYWdlbnRfbWFpbF91cGxvYWRfYXR0YWNobWVudCIsImF1dG9tYXRpb25fdXBkYXRlIiwiY29ubmVjdF9jbG91ZF9zZXJ2aWNlIiwiY29ubmVjdF9vcGVuX3BsYXRmb3JtIiwiY29udmVyc2F0aW9uX3NlYXJjaCIsImRvbWFpbl9zZWFyY2giLCJpbV9nZXRfbWVzc2FnZXMiLCJpbV9saXN0X2NvbnZlcnNhdGlvbnMiLCJrbm93bGVkZ2Vfc2VhcmNoIiwibGlzdF9pbnN0YWxsZWRfcGx1Z2lucyIsInByZXNlbnRfZmlsZXMiLCJyZWNvbm5lY3RfcGx1Z2luIiwic2VhcmNoX2FuZF9pbnN0YWxsX3NraWxscyIsInNlYXJjaF9wbHVnaW5zIiwic2hvd193aWRnZXQiLCJza2lsbF9tYW5hZ2VtZW50Iiwic3VnZ2VzdF9wbHVnaW5faW5zdGFsbCIsInVuaW5zdGFsbF9wbHVnaW4iLCJ3aWRnZXRfZ3VpZGVsaW5lcyIsIndvcmtidWRkeV9hcHBfc2V0X2NvbnZlcnNhdGlvbl90aXRsZSIsIndvcmtidWRkeV9jbG91ZF9zZXJ2aWNlIiwid29ya2J1ZGR5X21pbmlwcm9ncmFtX3NjYWZmb2xkIiwid29ya2J1ZGR5X3JlcXVlc3RfbWNwX2Nvbm5lY3Rpb24iLCJ3b3JrYnVkZHlfc2l0ZXNfZGVwbG95Iiwid29ya2J1ZGR5X3NpdGVzX3VucHVibGlzaCJdfQ.c_jI4NTj8HHY3gbzTOVbtyoQY_2RKv3RJBMK6VguydE"

def post(method, params, sid=None, notif=False):
    body = {"jsonrpc": "2.0"}
    if not notif:
        body["id"] = 1
    body["method"] = method
    if params is not None:
        body["params"] = params
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Authorization", AUTH)
    req.add_header("X-WorkBuddy-MCP-Context", CTX)
    if sid:
        req.add_header("Mcp-Session-Id", sid)
    resp = urllib.request.urlopen(req, timeout=120)
    new_sid = resp.headers.get("mcp-session-id")
    raw = resp.read().decode("utf-8")
    return new_sid, raw

def parse_text(raw):
    # handle SSE or plain json
    if "data:" in raw and ("event:" in raw or raw.lstrip().startswith("data:")):
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                out.append(line[len("data:"):].strip())
        text = "\n".join(out)
    else:
        text = raw
    return text

def call_futures_rank(variety_codes, indicator_type):
    # initialize
    sid, raw = post("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cli", "version": "1.0"}
    })
    if sid is None:
        # try to find in body
        pass
    # initialized notification
    post("notifications/initialized", {}, sid=sid, notif=True)
    args = {
        "variety_codes": variety_codes,
        "limit": 100,
        "indicator_type": indicator_type,
        "output_fields": "contract_code,indicator_code,member_code,member_abbr,indicator_volume,change_volume,end_date"
    }
    sid2, raw = post("tools/call", {"name": "futures_rank", "arguments": args}, sid=sid)
    text = parse_text(raw)
    obj = json.loads(text)
    # obj may be wrapped: {"status":"success","result":[...],"output":"..."}
    if "result" in obj and isinstance(obj["result"], list):
        return obj["result"]
    # or MCP standard: {"content":[{"type":"text","text":"..."}]}
    if "content" in obj:
        txt = obj["content"][0]["text"]
        inner = json.loads(txt)
        if "result" in inner and isinstance(inner["result"], list):
            return inner["result"]
        return inner
    return obj

if __name__ == "__main__":
    variety = sys.argv[1]
    itype = sys.argv[2]
    out = sys.argv[3]
    res = call_futures_rank(variety, itype)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print("WROTE", out, "rows=", len(res) if isinstance(res, list) else "n/a")
