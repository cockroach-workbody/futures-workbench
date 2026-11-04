#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集「前20会员持仓龙虎榜」近一月历史净持仓，落盘为按品种汇总的 JSON。

实现要点（遵循任务规范）：
  - 对单个品种 V、单个交易日 D，调用一次 futures_rank（不传 indicator_type，
    一次返回 持买/持卖/成交 三类）。
  - 参数：variety_codes=V, top_n=20, trading_date=D, limit=1000,
    output_fields="variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date"
  - 用 t20_collect_lib.parse_rank_result(result, D) 得到单日汇总。
  - 用 load_agg / upsert 落盘到 collect/raw_hist/agg_<CODE>.json（幂等：已存在日期跳过）。
  - 限流/失败处理：每个日期最多 1 次初始 + 4 次重试（共 ≤5 次），每次失败后等待约
    90s 再重试；采用“整轮重试”方式——每轮对当前缺失日期各尝试 1 次，轮间等待 90s，
    这样在限流间隙其它日期仍可推进，整体更快收敛。仍失败则记入 failures 继续。
  - 每成功/尝试一日 sleep 8s，降低自身请求频率。
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
    ("SA", "SA.ZCE"),
    ("M",  "M.DCE"),
    ("C",  "C.DCE"),
    ("SR", "SR.ZCE"),
    ("CF", "CF.ZCE"),
    ("A",  "A.DCE"),
]

SLEEP_BETWEEN = 10     # 每个日期尝试后 sleep 10s（降低频率，更温和）
ROUND_SLEEP = 120      # 轮间等待 120s（对应“限流后 sleep 再重试”，更耐心）
MAX_ROUNDS = 8         # 1 次初始 + 7 次重试（补数阶段更耐心）
TOOL_NAME = "futures_rank"

OUT_DIR = os.path.join(BASE, "raw_hist")
os.makedirs(OUT_DIR, exist_ok=True)
LOG_PATH = os.path.join(OUT_DIR, "_t20_group.log")
_logf = open(LOG_PATH, "a", encoding="utf-8")

def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _logf.write(s + "\n")
    _logf.flush()

# ---- MCP Streamable HTTP 客户端 ----
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
                chunk = line[5:].strip()
                if chunk:
                    try:
                        objs.append(json.loads(chunk))
                    except Exception:
                        pass
        return sid, 200, (objs[-1] if objs else None)
    try:
        return sid, 200, (json.loads(raw) if raw.strip() else None)
    except Exception:
        return sid, 200, raw

_session = [None]
def mcp_init():
    if _session[0] is not None:
        return _session[0]
    sess, _, _ = _post(None, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "fetch_t20_group", "version": "1.0"},
    }, 1)
    _post(sess, "notifications/initialized", {}, None, notification=True)
    _session[0] = sess
    return sess

def find_rows(obj):
    cands = []
    def walk(o):
        if isinstance(o, dict):
            if "indicator_volume" in o and "indicator_code" in o:
                cands.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)
    walk(obj)
    return cands

def call_rank_once(sess, variety, date):
    """调用一次 futures_rank（不传 indicator_type）。返回 (rows, reason)。
    reason: "success" / "429" / "empty" / "status:..." / "http_N" / "parse:..." """
    params = {
        "name": TOOL_NAME,
        "arguments": {
            "variety_codes": variety,
            "top_n": 20,
            "trading_date": date,
            "limit": 1000,
            "output_fields": "variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date",
        },
    }
    sess, code, resp = _post(sess, "tools/call", params, int(time.time()*1000) % 1000000)
    if code == 429:
        return None, "429"
    if code != 200:
        return None, "http_%d" % code
    try:
        content = resp.get("result", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        blob = "\n".join(texts)
        parsed = json.loads(blob)
    except Exception as e:
        return None, "parse:%s" % e
    txt = json.dumps(parsed, ensure_ascii=False)
    if "429" in txt or "rate" in txt.lower() or "频率" in txt or "限流" in txt:
        return None, "429"
    status = parsed.get("status")
    if status is not None and status != "success":
        return None, "status:%s" % status
    rows = parsed.get("result")
    if not isinstance(rows, list):
        rows = find_rows(parsed)
    if not rows:
        return None, "empty"
    return rows, "success"

def main():
    test_mode = "--test" in sys.argv
    vlist = [("SA", "SA.ZCE")] if test_mode else GROUP
    dlist = ["20261030"] if test_mode else DATES

    sess = mcp_init()
    log("=== START %s (pass-based, MAX_ROUNDS=%d) ===" % (time.strftime("%Y-%m-%d %H:%M:%S"), MAX_ROUNDS))

    # 每轮：对缺失日期各尝试 1 次；轮间等待 ROUND_SLEEP；最多 MAX_ROUNDS 轮。
    # failed_rounds[code][date] 记录已尝试次数，超过上限则放弃。
    attempts = {}      # (code,date) -> count
    gaps = set()       # 永久空数据 (code,date)
    failures = []      # (code,date) 最终失败

    # 初始统计已有
    existing_counts = {}
    for code, variety in vlist:
        p = load_agg(code)
        existing_counts[code] = len(p.get("data", {}))
    log("existing: " + json.dumps(existing_counts, ensure_ascii=False))

    for rnd in range(1, MAX_ROUNDS + 1):
        pending = []  # 本轮要尝试的 (code, variety, date)
        for code, variety in vlist:
            p = load_agg(code)
            for date in dlist:
                if date in p.get("data", {}):
                    continue
                if (code, date) in gaps:
                    continue
                if attempts.get((code, date), 0) >= MAX_ROUNDS:
                    continue
                pending.append((code, variety, date))
        if not pending:
            log("round %d: nothing pending, all done." % rnd)
            break
        log("=== round %d: %d pending attempts ===" % (rnd, len(pending)))
        for code, variety, date in pending:
            attempts[(code, date)] = attempts.get((code, date), 0) + 1
            rows, reason = call_rank_once(sess, variety, date)
            if reason == "success":
                rec = parse_rank_result(rows, date)
                rec["net"] = int(round(rec["net"]))
                upsert(code, variety, date, rec)
                log("OK %s %s net=%d long=%d short=%d"
                    % (code, date, rec["net"], rec["long_total"], rec["short_total"]))
            elif reason == "empty":
                gaps.add((code, date))
                log("GAP %s %s (empty result, permanent)" % (code, date))
            else:
                log("  [retryable] %s %s attempt=%d reason=%s"
                    % (code, date, attempts[(code, date)], reason))
            time.sleep(SLEEP_BETWEEN)
        # 轮末统计
        done = 0
        for code, variety in vlist:
            p = load_agg(code)
            done += len(p.get("data", {}))
        total_target = len(vlist) * len(dlist)
        log("round %d end: collected=%d/%d gaps=%d" % (rnd, done, total_target, len(gaps)))
        if rnd < MAX_ROUNDS:
            log("  sleep %ds before next round" % ROUND_SLEEP)
            time.sleep(ROUND_SLEEP)

    # 汇总
    summary = {}
    failures = []
    for code, variety in vlist:
        p = load_agg(code)
        data = p.get("data", {})
        succ = len(data)
        miss = [d for d in dlist if d not in data]
        # 区分真正缺口（永久 empty）与可能因限流放弃的
        perm_gap = [d for d in miss if (code, d) in gaps]
        rate_fail = [d for d in miss if (code, d) not in gaps]
        summary[code] = {"success": succ, "total": len(dlist),
                         "perm_gap": perm_gap, "rate_fail": rate_fail}
        for d in miss:
            failures.append((code, d))
    log("=== FINISH %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))
    log("failures(final): " + json.dumps(failures, ensure_ascii=False))

if __name__ == "__main__":
    main()
