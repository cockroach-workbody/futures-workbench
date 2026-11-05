# -*- coding: utf-8 -*-
"""
前20会员持仓龙虎榜 —— 近一月历史净持仓采集（本组 6 品种，主力合约口径）。

落盘：collect/raw_hist/agg_<CODE>.json（按品种汇总，幂等，已存在日期跳过）。
口径：复用 parse_neodata.parse_rank 选「持买合计最大」主力合约，与快照完全一致。

调用方式：NeoData MCP 工具 futures_rank。
重要修正：工具对返回行数有 100 行硬上限（limit=1000 也会被截断）。
若单次"不传 indicator_type"把 持买/持卖/成交 三类混在一起返回，多合约品种
会被截断（例如 RB 在某日有 4 个活跃合约，三类混排需 240 行 > 100，导致
持卖/持买 丢合约，net 计算错误，且与已有 9 个日期的口径不一致）。
因此这里对每个 (品种, 日期) 分别按 indicator_type="long" 和 "short" 各调一次，
再把两次结果合并后交给 t20_collect_lib.parse_rank_result 汇总，输出字段与
任务要求完全一致 {date,net,long_total,short_total,long_chg,short_chg}。

其余严格遵循任务要求：
  - 幂等：load_agg 检查日期已存在则跳过。
  - 落盘：upsert(code, variety, date, rec)。
  - 每次采集后 sleep 8s。
  - 非 success 或 429/频率限制：sleep 90s 后重试，最多 4 次；仍失败记 failures 继续。
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)
from t20_collect_lib import load_agg, upsert, rec_from_neodata, DATES

# ---- NeoData MCP 连接（来自环境变量，避免硬编码）----
cfg = json.loads(os.environ["CODEBUDDY_MCP_CONFIG"])
nd = cfg["mcpServers"]["neodata"]
BASE = nd["url"]
AUTH = nd["headers"]["Authorization"]
CTX = nd["headers"]["X-WorkBuddy-MCP-Context"]

# 本组品种（code -> variety_codes 带交易所后缀）
VARIETIES = {
    "RB": "RB.SHF",
    "HC": "HC.SHF",
    "I": "I.DCE",
    "J": "J.DCE",
    "JM": "JM.DCE",
    "FG": "FG.ZCE",
}

OUTPUT_FIELDS = "variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date"
TOP_N = 20
LIMIT = 1000            # 工具仍会硬截断到 100 行，但分别按类型调用即可拿到完整数据

SLEEP_BETWEEN = 8       # 每个(品种,日期)采集后休眠
SLEEP_BETWEEN_CALLS = 3 # 同日期 long/short 两次调用之间的小间隔，降低 429 概率
SLEEP_RETRY = 90        # 限流/失败重试休眠
MAX_RETRY = 4           # 单次调用最多重试次数

LOG_PATH = os.path.join(WORK, "raw_hist", "_collect_t20_hist.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
_logf = open(LOG_PATH, "a", encoding="utf-8")

_sid = [None]

def log(*a):
    s = " ".join(str(x) for x in a)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, s)
    print(line)
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
    r = urllib.request.urlopen(req, timeout=120)
    return r.headers.get("mcp-session-id"), r.read().decode("utf-8")

def ensure_session():
    if _sid[0] is not None:
        return
    s, _ = _post("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cli", "version": "1.0"},
    })
    _sid[0] = s
    _post("notifications/initialized", {}, sid=s)

def call_rank(vcode, itype, date):
    """调用一次 futures_rank（指定 indicator_type）。返回 result 行列表。失败抛异常。"""
    ensure_session()
    args = {
        "variety_codes": vcode,
        "trading_date": date,
        "indicator_type": itype,
        "top_n": TOP_N,
        "limit": LIMIT,
        "output_fields": OUTPUT_FIELDS,
    }
    _, raw = _post("tools/call", {"name": "futures_rank", "arguments": args},
                  sid=_sid[0])
    obj = json.loads(raw)
    if "error" in obj:
        raise RuntimeError("mcp_error: %s" % str(obj["error"])[:200])
    if "result" not in obj or "content" not in obj.get("result", {}):
        raise RuntimeError("unexpected_resp: %s" % raw[:200])
    inner = json.loads(obj["result"]["content"][0]["text"])
    status = inner.get("status")
    if status != "success":
        raise RuntimeError("status=%s %s" % (status, str(inner.get("message", ""))[:160]))
    return inner.get("result", [])

def call_with_retry(vcode, itype, date):
    last = None
    for attempt in range(1 + MAX_RETRY):
        try:
            return call_rank(vcode, itype, date)
        except Exception as e:
            last = e
            if attempt < MAX_RETRY:
                log("  [retry %d/%d] %s %s %s: %s" %
                    (attempt + 1, MAX_RETRY, vcode, itype, date, str(e)[:160]))
                time.sleep(SLEEP_RETRY)
            else:
                log("  [giveup] %s %s %s: %s" %
                    (vcode, itype, date, str(e)[:160]))
    raise last

def collect_one(code, vcode, date):
    """采集单日：分别取 long/short 合并后按「持买合计最大」主力合约汇总 -> upsert。
    复用 parse_neodata.parse_rank，与快照口径完全一致。成功返回 rec，失败抛异常。"""
    long_rows = call_with_retry(vcode, "long", date)
    time.sleep(SLEEP_BETWEEN_CALLS)
    short_rows = call_with_retry(vcode, "short", date)
    rows = long_rows + short_rows
    rec = rec_from_neodata(rows, date)
    if not rec:
        raise RuntimeError("rec_from_neodata 返回空（可能无数据或无主力合约）")
    upsert(code, vcode, date, rec)
    if rec["long_total"] == 0 and rec["short_total"] == 0:
        log("  [warn] %s %s 汇总为 0（可能无数据）: %s" % (code, date, rec))
    return rec

def main():
    log("=== T20 历史采集 START ===")
    summary = {}
    rate_limited = False
    for code, vcode in VARIETIES.items():
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
                continue
            try:
                rec = collect_one(code, vcode, date)
                new += 1
                log("OK %s %s net=%d long=%d short=%d" %
                    (code, date, rec["net"], rec["long_total"], rec["short_total"]))
            except Exception as e:
                failed.append(date)
                msg = str(e)
                if "429" in msg or "频率" in msg or "rate" in msg.lower():
                    rate_limited = True
                log("FAIL %s %s: %s" % (code, date, msg[:160]))
            time.sleep(SLEEP_BETWEEN)
        after = len(load_agg(code).get("data", {}))
        summary[code] = {
            "before": before, "new": new, "skipped": skipped,
            "after": after, "failed": failed,
        }
        log("DONE %s before=%d new=%d skipped=%d after=%d failed=%s" %
            (code, before, new, skipped, after, failed))
    log("=== T20 历史采集 FINISH ===")
    log("遇到限流(429/频率限制): %s" % rate_limited)
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))
    log("FAILURES: " + json.dumps(
        {c: s["failed"] for c, s in summary.items() if s["failed"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
