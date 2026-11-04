# -*- coding: utf-8 -*-
"""
逐品种、分多空两次调用 NeoData futures_rank，汇总前20会员持仓龙虎榜，落盘为
collect/raw_hist/agg_<CODE>.json。

调用约束（来自任务）：
  - 每次只传 1 个品种，indicator_type 分别 "long" / "short"，top_n=20, limit=100。
  - long 返回行 indicator_code 均为"持买"，short 均为"持卖"；分别求和：
        long_total = Σ indicator_volume；long_chg = Σ change_volume
        short_total = Σ indicator_volume；short_chg = Σ change_volume
        net = long_total - short_total
  - 每次调用之间 sleep ~10s；非 success 或 429/频率限制则 sleep 30s 重试，最多 3 次。
  - 幂等：已有 agg_<CODE>.json 则读入并跳过已存在的日期，再写回（可重跑补数）。

连接信息从环境变量 CODEBUDDY_MCP_CONFIG 动态读取 neodata 服务端，避免硬编码。
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

WORK = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(WORK, "raw_hist")
os.makedirs(OUTDIR, exist_ok=True)

# 品种代码 -> variety_codes（带交易所后缀）
VARIETIES = {
    "RB": "RB.SHF",
    "HC": "HC.SHF",
    "I": "I.DCE",
    "J": "J.DCE",
    "JM": "JM.DCE",
    "FG": "FG.ZCE",
}

# 采样交易日（11 个，覆盖近一月）
DATES = [
    "20261030", "20261028", "20261026", "20261022", "20261020",
    "20261016", "20261014", "20261012", "20261008", "20260930", "20260925",
]

# ---- 从环境读取 neodata MCP 连接 ----
cfg = json.loads(os.environ["CODEBUDDY_MCP_CONFIG"])
nd = cfg["mcpServers"]["neodata"]
BASE = nd["url"]
AUTH = nd["headers"]["Authorization"]
CTX = nd["headers"]["X-WorkBuddy-MCP-Context"]

SLEEP_BETWEEN = 10      # 调用之间约 10s
SLEEP_RETRY = 30        # 失败后 30s 重试
MAX_RETRY = 3           # 最多重试 3 次

_session_sid = [None]

def _post(method, params, sid=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
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
    return resp.headers.get("mcp-session-id"), resp.read().decode("utf-8")

def ensure_session():
    if _session_sid[0] is not None:
        return
    sid, _ = _post("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cli", "version": "1.0"},
    })
    _post("notifications/initialized", {}, sid=sid)
    _session_sid[0] = sid

def call_rank(vcode, itype, date):
    """调用一次 futures_rank，返回 result 行列表；失败抛异常。"""
    ensure_session()
    args = {
        "variety_codes": vcode,
        "trading_date": date,
        "indicator_type": itype,
        "top_n": 20,
        "limit": 100,
    }
    _, raw = _post("tools/call", {"name": "futures_rank", "arguments": args},
                  sid=_session_sid[0])
    obj = json.loads(raw)
    if "result" not in obj or "content" not in obj["result"]:
        raise RuntimeError("unexpected mcp resp: " + raw[:200])
    text = obj["result"]["content"][0]["text"]
    inner = json.loads(text)
    status = inner.get("status")
    if status != "success":
        raise RuntimeError("tool status=%s %s" % (status, str(inner)[:160]))
    return inner.get("result", [])

def call_with_retry(vcode, itype, date):
    last = None
    for attempt in range(1 + MAX_RETRY):
        try:
            return call_rank(vcode, itype, date)
        except Exception as e:
            last = e
            msg = str(e)
            if attempt < MAX_RETRY:
                log("  [retry %d/%d] %s %s %s: %s" % (attempt + 1, MAX_RETRY,
                                                      vcode, itype, date, msg[:160]))
                time.sleep(SLEEP_RETRY)
            else:
                log("  [giveup] %s %s %s: %s" % (vcode, itype, date, msg[:160]))
    raise last

def agg(rows, want):
    tot = 0.0
    chg = 0.0
    n = 0
    for r in rows:
        if r.get("indicator_code") == want:
            tot += float(r.get("indicator_volume") or 0)
            chg += float(r.get("change_volume") or 0)
            n += 1
    return int(round(tot)), int(round(chg)), n

LOG_PATH = os.path.join(OUTDIR, "_run.log")
_logf = open(LOG_PATH, "a", encoding="utf-8")

def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    _logf.write(s + "\n")
    _logf.flush()

def main():
    log("=== START %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("BASE=%s" % BASE)
    summary = {}
    for code, vcode in VARIETIES.items():
        out_fp = os.path.join(OUTDIR, "agg_%s.json" % code)
        data = {}
        if os.path.exists(out_fp):
            try:
                prev = json.load(open(out_fp, encoding="utf-8"))
                data = prev.get("data", {}) or {}
                log("LOAD %s existing dates=%d" % (code, len(data)))
            except Exception as e:
                log("LOAD ERR %s: %s" % (code, e))
        collected = 0
        failed = []
        zero_warn = []
        for date in DATES:
            if date in data:
                log("SKIP %s %s already present" % (code, date))
                collected += 1
                continue
            try:
                lrows = call_with_retry(vcode, "long", date)
                time.sleep(SLEEP_BETWEEN)
                srows = call_with_retry(vcode, "short", date)
                lt, lc, nl = agg(lrows, "持买")
                st, sc, ns = agg(srows, "持卖")
                if lt == 0 and st == 0:
                    zero_warn.append(date)
                net = lt - st
                data[date] = {
                    "date": date,
                    "net": net,
                    "long_total": lt,
                    "short_total": st,
                    "long_chg": lc,
                    "short_chg": sc,
                }
                log("OK %s %s long=%d(n=%d) short=%d(n=%d) net=%d" %
                    (code, date, lt, nl, st, ns, net))
                collected += 1
            except Exception as e:
                failed.append(date)
                log("FAIL %s %s: %s" % (code, date, e))
            # 增量写回（幂等，可重跑补数）
            json.dump({"code": code, "variety": vcode, "data": data},
                      open(out_fp, "w", encoding="utf-8"), ensure_ascii=False)
            time.sleep(SLEEP_BETWEEN)
        if zero_warn:
            log("WARN %s zero-total dates (possible no-data): %s" % (code, zero_warn))
        summary[code] = {"collected": collected, "failed": failed}
        log("DONE %s collected=%d/%d failed=%s" %
            (code, collected, len(DATES), failed))
    log("=== FINISH %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    main()
