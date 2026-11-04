#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
温和补数：针对仍未采集到的日期（排除已知永久空数据 20260925），
逐个单发调用 futures_rank，调用间隔较长（~25s），每日期最多重试 8 次（间隔 30s）。
目的：在并发采集进程限流的间隙“插空”成功率更高，避免突发连发触发 429。
幂等：已存在日期跳过；落盘仍用 t20_collect_lib.upsert。
"""
import os, sys, json, time, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from t20_collect_lib import load_agg, upsert, parse_rank_result, DATES  # noqa: E402

CFG = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
cfg = json.loads(CFG)
nd = cfg["mcpServers"]["neodata"]
URL = nd["url"]
HEADERS = nd["headers"]

GROUP = [
    ("SA", "SA.ZCE"), ("M", "M.DCE"), ("C", "C.DCE"),
    ("SR", "SR.ZCE"), ("CF", "CF.ZCE"), ("A", "A.DCE"),
]
PERM_GAP_DATE = "20260925"   # 已确认该日各品种均无数据（success 但空结果）

CALL_GAP = 25      # 每日期之间 sleep 25s（温和）
RETRY_GAP = 30     # 单日期重试之间 sleep 30s
MAX_TRIES = 8      # 每日期最多 8 次尝试
TOOL_NAME = "futures_rank"

_logf = open(os.path.join(BASE, "raw_hist", "_t20_backfill.log"), "a", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True)
    _logf.write(s + "\n"); _logf.flush()

def _post(session, method, params, msgid, notification=False):
    body = {"jsonrpc": "2.0", "method": method}
    if not notification: body["id"] = msgid
    if params is not None: body["params"] = params
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in HEADERS.items(): req.add_header(k, v)
    if session: req.add_header("Mcp-Session-Id", session)
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        return session, e.code, e.read().decode("utf-8", "replace")
    sid = resp.headers.get("Mcp-Session-Id")
    raw = resp.read().decode("utf-8", "replace")
    ct = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ct:
        objs = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                c = line[5:].strip()
                if c:
                    try: objs.append(json.loads(c))
                    except Exception: pass
        return sid, 200, (objs[-1] if objs else None)
    try:
        return sid, 200, (json.loads(raw) if raw.strip() else None)
    except Exception:
        return sid, 200, raw

_session = [None]
def mcp_init():
    if _session[0] is not None: return _session[0]
    s, _, _ = _post(None, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "t20_backfill", "version": "1.0"}}, 1)
    _post(s, "notifications/initialized", {}, None, notification=True)
    _session[0] = s; return s

def call_once(sess, variety, date):
    params = {"name": TOOL_NAME, "arguments": {
        "variety_codes": variety, "top_n": 20, "trading_date": date,
        "limit": 1000,
        "output_fields": "variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date"}}
    sess, code, resp = _post(sess, "tools/call", params, int(time.time()*1000) % 1000000)
    if code == 429: return None, "429"
    if code != 200: return None, "http_%d" % code
    try:
        content = resp.get("result", {}).get("content", [])
        txt = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        p = json.loads(txt)
    except Exception as e:
        return None, "parse:%s" % e
    blob = json.dumps(p, ensure_ascii=False)
    if "429" in blob or "rate" in blob.lower() or "频率" in blob or "限流" in blob:
        return None, "429"
    if p.get("status") is not None and p.get("status") != "success":
        return None, "status:%s" % p.get("status")
    rows = p.get("result")
    if not isinstance(rows, list):
        # fallback walk
        cands = []
        def walk(o):
            if isinstance(o, dict):
                if "indicator_volume" in o and "indicator_code" in o: cands.append(o)
                for v in o.values(): walk(v)
            elif isinstance(o, list):
                for it in o: walk(it)
        walk(p); rows = cands
    if not rows: return None, "empty"
    return rows, "success"

def main():
    sess = mcp_init()
    log("=== BACKFILL START %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    still_missing = []
    for code, variety in GROUP:
        p = load_agg(code); data = p.get("data", {})
        for date in DATES:
            if date in data:
                continue
            if date == PERM_GAP_DATE:
                continue  # 已知空数据，跳过
            ok = False
            for t in range(1, MAX_TRIES + 1):
                rows, reason = call_once(sess, variety, date)
                if reason == "success":
                    rec = parse_rank_result(rows, date)
                    rec["net"] = int(round(rec["net"]))
                    upsert(code, variety, date, rec)
                    log("OK %s %s net=%d (try=%d)" % (code, date, rec["net"], t))
                    ok = True
                    break
                else:
                    log("  %s %s try=%d reason=%s, sleep %ds" % (code, date, t, reason, RETRY_GAP))
                    time.sleep(RETRY_GAP)
            if not ok:
                still_missing.append((code, date))
            time.sleep(CALL_GAP)
    log("=== BACKFILL END ===")
    log("still_missing(final): " + json.dumps(still_missing, ensure_ascii=False))

if __name__ == "__main__":
    main()
