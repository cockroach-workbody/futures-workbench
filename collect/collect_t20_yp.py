#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本组「前20会员持仓龙虎榜」近一月历史净持仓采集（Y/P/RM/OI/AP/JD 六个品种）。
落盘：collect/raw_hist/agg_<CODE>.json（按品种汇总，幂等，已存在日期跳过）。

严格遵循任务调用方法：
  - 对单个品种 V、单个交易日 D，调用一次 futures_rank（不传 indicator_type，
    一次返回 持买/持卖/成交 三类）。
  - 参数：variety_codes=V, top_n=20, trading_date=D, limit=1000,
          output_fields="variety_code,contract_code,indicator_code,
                         indicator_volume,change_volume,end_date"
  - 返回的 result 数组即行数据，用 t20_collect_lib.parse_rank_result(result, D)
    得到单日汇总 {date,net,long_total,short_total,long_chg,short_chg}。

其余约定：
  - 幂等：load_agg 检查日期已存在则跳过（不覆盖）。
  - 落盘：upsert(code, variety, date, rec)。
  - 每次采集后 sleep 8s。
  - 非 success 或 429/频率限制：sleep 90s 后重试，最多 4 次；仍失败记 failures 继续。

本脚本独立命名（collect_t20_yp.py），避免与 collect_t20_hist.py（RB 组）冲突。
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)
from t20_collect_lib import load_agg, upsert, parse_rank_result, DATES

# ---- NeoData MCP 连接（来自环境变量，避免硬编码）----
cfg = json.loads(os.environ["CODEBUDDY_MCP_CONFIG"])
nd = cfg["mcpServers"]["neodata"]
BASE = nd["url"]
AUTH = nd["headers"]["Authorization"]
CTX = nd["headers"]["X-WorkBuddy-MCP-Context"]

# 本组品种（code -> variety_codes 带交易所后缀）
VARIETIES = [
    ("Y",  "Y.DCE"),
    ("P",  "P.DCE"),
    ("RM", "RM.ZCE"),
    ("OI", "OI.ZCE"),
    ("AP", "AP.ZCE"),
    ("JD", "JD.DCE"),
]

OUTPUT_FIELDS = "variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date"
TOP_N = 20
LIMIT = 1000

SLEEP_BETWEEN = 8       # 每个(品种,日期)采集后休眠
SLEEP_RETRY = 90        # 限流/失败重试休眠
MAX_RETRY = 4           # 单次调用最多重试次数（初始 + 4 次重试）
CALL_TIMEOUT = 60       # 单次 HTTP 超时，避免服务端 hang 时无限阻塞

LOG_PATH = os.path.join(WORK, "raw_hist", "_collect_t20_yp.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
_logf = open(LOG_PATH, "a", encoding="utf-8")

_sid = [None]

def log(*a):
    s = " ".join(str(x) for x in a)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, s)
    print(line, flush=True)
    _logf.write(line + "\n")
    _logf.flush()

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
    try:
        r = urllib.request.urlopen(req, timeout=CALL_TIMEOUT)
        raw = r.read().decode("utf-8")
        return r.headers.get("mcp-session-id"), raw, None
    except urllib.error.HTTPError as e:
        # 429 等 HTTP 错误
        return None, e.read().decode("utf-8", "replace"), "http_%s" % e.code
    except urllib.error.URLError as e:
        # 连接/超时：作为可重试失败返回
        return None, "", "urlerror:%s" % e
    except Exception as e:
        return None, "", "reqerror:%s" % e

def ensure_session():
    if _sid[0] is not None:
        return
    s, _, _ = _post("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "collect_t20_yp", "version": "1.0"},
    })
    _sid[0] = s
    _post("notifications/initialized", {}, sid=s)

def call_rank_once(vcode, date):
    """单次调用 futures_rank（不传 indicator_type）。成功返回 (rows, None)，
    失败返回 (None, reason_str)。"""
    ensure_session()
    args = {
        "variety_codes": vcode,
        "trading_date": date,
        "top_n": TOP_N,
        "limit": LIMIT,
        "output_fields": OUTPUT_FIELDS,
    }
    sid, raw, err = _post("tools/call", {"name": "futures_rank", "arguments": args},
                         sid=_sid[0])
    if err is not None:
        return None, err
    try:
        obj = json.loads(raw)
    except Exception as e:
        return None, "json_parse:%s" % e
    if "error" in obj:
        return None, "mcp_error:%s" % str(obj["error"])[:160]
    if "result" not in obj or "content" not in obj.get("result", {}):
        return None, "unexpected_resp:%s" % raw[:160]
    content = obj["result"]["content"]
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    try:
        inner = json.loads("\n".join(texts))
    except Exception as e:
        return None, "inner_parse:%s" % e
    status = inner.get("status")
    if status != "success":
        return None, "status_%s:%s" % (status, str(inner.get("message", ""))[:160])
    return inner.get("result", []), None

def collect_one(code, vcode, date):
    """带重试地采集单日；成功返回 rec，失败返回 (None, rate_limited_flag)。"""
    rate_limited = False
    for attempt in range(1 + MAX_RETRY):
        rows, err = call_rank_once(vcode, date)
        if err is None:
            rec = parse_rank_result(rows, date)
            upsert(code, vcode, date, rec)
            if rec["long_total"] == 0 and rec["short_total"] == 0:
                log("  [warn] %s %s 汇总为 0（可能无数据）" % (code, date))
            return rec, False
        if ("429" in err or "频率" in err or "rate" in err.lower()
                or "limit" in err.lower()):
            rate_limited = True
        if attempt < MAX_RETRY:
            log("  [retry %d/%d] %s %s: %s -> sleep %ds" %
                (attempt + 1, MAX_RETRY, code, date, err[:120], SLEEP_RETRY))
            time.sleep(SLEEP_RETRY)
        else:
            log("  [giveup] %s %s: %s" % (code, date, err[:120]))
    return None, rate_limited

def main():
    log("=== T20-YP 历史采集 START ===")
    summary = {}
    rate_limited_any = False
    for code, vcode in VARIETIES:
        prev = load_agg(code)
        existing = prev.get("data", {}) or {}
        before = len(existing)
        skipped = 0
        new = 0
        failed = []
        log("--- %s (%s) 已有 %d 个日期 ---" % (code, vcode, before))
        for date in DATES:
            if date in existing:
                skipped += 1
                log("  [skip] %s %s 已存在" % (code, date))
                continue
            rec, rl = collect_one(code, vcode, date)
            if rl:
                rate_limited_any = True
            if rec is None:
                failed.append(date)
                log("  [fail] %s %s" % (code, date))
            else:
                new += 1
                log("  OK %s %s net=%d long=%d short=%d long_chg=%d short_chg=%d" %
                    (code, date, rec["net"], rec["long_total"], rec["short_total"],
                     rec["long_chg"], rec["short_chg"]))
            time.sleep(SLEEP_BETWEEN)
        after = len(load_agg(code).get("data", {}))
        summary[code] = {"before": before, "new": new, "skipped": skipped,
                         "after": after, "failed": failed}
        log("DONE %s before=%d new=%d skipped=%d after=%d failed=%s" %
            (code, before, new, skipped, after, failed))
    log("=== T20-YP 历史采集 FINISH ===")
    log("遇到限流(429/频率限制): %s" % rate_limited_any)
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))
    log("FAILURES: " + json.dumps(
        {c: s["failed"] for c, s in summary.items() if s["failed"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
