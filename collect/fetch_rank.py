#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过本地 MCP HTTP 代理调用 neodata futures_rank，逐品种/逐交易日/分多空两次调用，
汇总前20会员持仓并落盘为 collect/raw_hist/agg_<CODE>.json。
幂等：已存在的日期跳过，可重跑补数。
"""
import json, os, sys, time, urllib.request, urllib.error

CFG = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
cfg = json.loads(CFG)
nd = cfg["mcpServers"]["neodata"]
URL = nd["url"]
HEADERS = nd["headers"]  # Authorization + X-WorkBuddy-MCP-Context

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raw_hist")
# 当脚本在 collect/ 下时，raw_hist 是其子目录；确保存在
os.makedirs(OUT_DIR, exist_ok=True)

VARIETIES = [
    ("Y", "Y.DCE"),
    ("P", "P.DCE"),
    ("RM", "RM.ZCE"),
    ("OI", "OI.ZCE"),
    ("AP", "AP.ZCE"),
    ("JD", "JD.DCE"),
]
DATES = ["20261030","20261028","20261026","20261022","20261020",
         "20261016","20261014","20261012","20261008","20260930","20260925"]

# ---- 极简 MCP Streamable HTTP 客户端 ----
def _post(session, method, params, msgid, notification=False):
    body = {"jsonrpc": "2.0", "method": method}
    if not notification:
        body["id"] = msgid
    if params is not None:
        body["params"] = params
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    if session:
        req.add_header("Mcp-Session-Id", session)
    try:
        resp = urllib.request.urlopen(req, timeout=90)
    except urllib.error.HTTPError as e:
        # 429 等可能在响应体里
        return session, e.code, e.read().decode("utf-8", "replace")
    sid = resp.headers.get("Mcp-Session-Id")
    raw = resp.read().decode("utf-8", "replace")
    ct = resp.headers.get("Content-Type", "")
    # 解析 SSE 或 JSON
    if "text/event-stream" in ct:
        objs = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        objs.append(json.loads(chunk))
                    except Exception:
                        pass
        return sid, 200, objs[-1] if objs else None
    try:
        return sid, 200, json.loads(raw) if raw.strip() else None
    except Exception:
        return sid, 200, raw

def mcp_init():
    sess, code, resp = _post(None, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "fetch_rank", "version": "1.0"}
    }, 1)
    # 发送 initialized 通知
    _post(sess, "notifications/initialized", {}, None, notification=True)
    return sess

def find_rows(obj):
    """从工具返回的文本/对象中找到含 indicator_volume 的行列表。"""
    candidates = []
    def walk(o):
        if isinstance(o, dict):
            # 直接是行？
            if "indicator_volume" in o and "indicator_code" in o:
                candidates.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)
    walk(obj)
    return candidates

def call_rank(sess, variety, date, indicator):
    msgid = int(time.time() * 1000) % 1000000
    params = {
        "name": TOOL_NAME,
        "arguments": {
            "variety_codes": variety,
            "indicator_type": indicator,
            "top_n": 20,
            "trading_date": date,
            "limit": 100,
        },
    }
    sess, code, resp = _post(sess, "tools/call", params, msgid)
    if code != 200:
        return None, code, resp
    # resp 形如 {"result":{"content":[{"type":"text","text":"...json..."}],...}}
    try:
        content = resp.get("result", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        blob = "\n".join(texts)
        parsed = json.loads(blob)
    except Exception as e:
        return None, 500, "parse-fail:%s" % e
    rows = find_rows(parsed)
    if not rows:
        return [], 200, "empty"
    return rows, 200, "ok"

def aggregate(rows):
    long_total = long_chg = short_total = short_chg = 0
    for r in rows:
        ic = r.get("indicator_code")
        vol = r.get("indicator_volume") or 0
        chg = r.get("change_volume") or 0
        if ic == "持买":
            long_total += vol; long_chg += chg
        elif ic == "持卖":
            short_total += vol; short_chg += chg
    net = long_total - short_total
    return {
        "long_total": int(round(long_total)),
        "short_total": int(round(short_total)),
        "long_chg": int(round(long_chg)),
        "short_chg": int(round(short_chg)),
        "net": int(round(net)),
    }

def load_existing(code):
    path = os.path.join(OUT_DIR, "agg_%s.json" % code)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("data", {})
        except Exception:
            return {}
    return {}

def save(code, variety, data):
    path = os.path.join(OUT_DIR, "agg_%s.json" % code)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "variety": variety, "data": data}, f,
                  ensure_ascii=False, indent=2)

TOOL_NAME = None

def main():
    global TOOL_NAME
    test_mode = "--test" in sys.argv
    sess = mcp_init()
    # 发现工具名
    _, _, tlist = _post(sess, "tools/list", {}, 2)
    tools = []
    if isinstance(tlist, dict):
        tools = tlist.get("result", {}).get("tools", [])
    for t in tools:
        nm = t.get("name", "")
        if "futures_rank" in nm:
            TOOL_NAME = nm
            break
    if not TOOL_NAME:
        # 兜底
        TOOL_NAME = "futures_rank"
    print("[info] tool name =", TOOL_NAME, flush=True)

    if test_mode:
        vlist = [("Y", "Y.DCE")]
        dlist = ["20261030"]
    else:
        vlist = VARIETIES
        dlist = DATES

    rate_limited = False
    failures = []  # (code, date)

    for code, variety in vlist:
        existing = load_existing(code)
        for date in dlist:
            if date in existing:
                # 已有则跳过（幂等）
                # 但仅当该日期字段完整时跳过
                rec = existing[date]
                if all(k in rec for k in ("net","long_total","short_total","long_chg","short_chg")):
                    continue
            ok = False
            for attempt in range(4):  # 初始 + 3 次重试
                lrows, lcode, lmsg = call_rank(sess, variety, date, "long")
                if lcode == 429 or (isinstance(lmsg, str) and "429" in lmsg):
                    rate_limited = True
                    print("[429] %s %s long attempt %d, sleep 30" % (code, date, attempt), flush=True)
                    time.sleep(30); continue
                if lrows is None:
                    print("[err] %s %s long code=%s msg=%s" % (code, date, lcode, lmsg), flush=True)
                    if attempt < 3:
                        time.sleep(30); continue
                    break
                srows, scode, smsg = call_rank(sess, variety, date, "short")
                if scode == 429 or (isinstance(smsg, str) and "429" in smsg):
                    rate_limited = True
                    print("[429] %s %s short attempt %d, sleep 30" % (code, date, attempt), flush=True)
                    time.sleep(30); continue
                if srows is None:
                    print("[err] %s %s short code=%s msg=%s" % (code, date, scode, smsg), flush=True)
                    if attempt < 3:
                        time.sleep(30); continue
                    break
                la = aggregate(lrows) if isinstance(lrows, list) else None
                sa = aggregate(srows) if isinstance(srows, list) else None
                if la is None or sa is None:
                    if attempt < 3:
                        time.sleep(30); continue
                    break
                rec = {
                    "date": date,
                    "net": la["net"] - sa["net"],  # net 已分别是 long/short 各自 vol 差，这里改为总额差
                    "long_total": la["long_total"],
                    "short_total": sa["short_total"],
                    "long_chg": la["long_chg"],
                    "short_chg": sa["short_chg"],
                }
                # 修正 net：应为 long_total - short_total
                rec["net"] = rec["long_total"] - rec["short_total"]
                existing[date] = rec
                ok = True
                break
            if not ok:
                failures.append((code, date))
            else:
                # 每个品种每采集若干日期即落盘一次，避免中途丢失
                save(code, variety, existing)
                print("[ok] %s %s net=%d long=%d short=%d" % (
                    code, date, rec["net"], rec["long_total"], rec["short_total"]), flush=True)
            # 调用之间 sleep ~10s（已在每次调用内控制；这里对日期间再保证间隔）
            time.sleep(10)

        # 品种结束落盘
        save(code, variety, existing)
        print("[done-variety] %s collected=%d" % (code, len(existing)), flush=True)

    print("=== SUMMARY ===", flush=True)
    print("rate_limited_encountered:", rate_limited, flush=True)
    print("failures:", failures, flush=True)

if __name__ == "__main__":
    main()
