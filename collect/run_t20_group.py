#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本组（CJ/LH/TA/MA/RU/L）前20会员历史净持仓采集驱动。
- 单品种单交易日，单次调用 futures_rank（不传 indicator_type，一次返回 持买/持卖/成交 三类）。
- 用 t20_collect_lib 的 load_agg / upsert / parse_rank_result / DATES。
- 幂等：load_agg 已存在日期则跳过；upsert 二次兜底。
- 节奏：每个日期调用后 sleep 8s；状态码非 success 或 429/频率限制 -> sleep 90s 重试，最多 4 次。
- 若单日返回 0 行（无真实数据），不写入伪造的 0 记录，记入 no_data 并继续。
"""
import os, sys, json, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from t20_collect_lib import load_agg, upsert, parse_rank_result, DATES

cfg = json.loads(os.environ["CODEBUDDY_MCP_CONFIG"])
nd = cfg["mcpServers"]["neodata"]
URL = nd["url"]
HEADERS = nd["headers"]

# 本组品种 (code, variety_codes)
GROUP = [
    ("CJ", "CJ.ZCE"),
    ("LH", "LH.DCE"),
    ("TA", "TA.ZCE"),
    ("MA", "MA.ZCE"),
    ("RU", "RU.SHF"),
    ("L",  "L.DCE"),
]

SLEEP_BETWEEN = 180  # 真实 API 调用之间的间隔（保守稳态节奏，低于触发限流的阈值）
SLEEP_RETRY = 240    # 遇 429/失败 的退避
MAX_RETRY = 4        # 初始 + 4 次重试 = 最多 5 次尝试

LOG_PATH = os.path.join(HERE, "raw_hist", "_run_t20_group.log")
_logf = open(LOG_PATH, "a", encoding="utf-8")

def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _logf.write(s + "\n"); _logf.flush()

class RateLimit(Exception):
    pass

def _post(session, method, params, msgid, notif=False):
    body = {"jsonrpc": "2.0", "method": method}
    if not notif:
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
                c = line[5:].strip()
                if c:
                    try:
                        objs.append(json.loads(c))
                    except Exception:
                        pass
        return sid, 200, (objs[-1] if objs else None)
    try:
        return sid, 200, (json.loads(raw) if raw.strip() else None)
    except Exception:
        return sid, 200, raw

def ensure_session():
    sess, _, _ = _post(None, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "t20group", "version": "1.0"},
    }, 1)
    _post(sess, "notifications/initialized", {}, None, notif=True)
    return sess

def call_rank(sess, variety, date):
    """单次调用（复用会话，降低 HTTP 请求数），返回 (status, result_list)。429 抛 RateLimit。"""
    params = {
        "name": "futures_rank",
        "arguments": {
            "variety_codes": variety,
            "top_n": 20,
            "trading_date": date,
            "limit": 1000,
            "output_fields": "variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date",
        },
    }
    sid, code, resp = _post(sess, "tools/call", params, int(time.time() * 1000) % 1000000)
    if code == 429:
        raise RateLimit("http429")
    if code != 200:
        raise RuntimeError("http%d %s" % (code, str(resp)[:160]))
    txt = resp.get("result", {}).get("content", [{}])[0].get("text", "")
    obj = json.loads(txt)
    status = obj.get("status")
    blob = json.dumps(obj, ensure_ascii=False)
    if status != "success" or "429" in blob or "频率限制" in blob:
        if "429" in blob or "频率限制" in blob:
            raise RateLimit(blob[:160])
        raise RuntimeError("status=%s %s" % (status, blob[:160]))
    return status, obj.get("result", [])

def collect_one(sess, code, variety, date, rate_seen):
    # 幂等：已存在的日期跳过调用
    agg = load_agg(code)
    if date in agg.get("data", {}):
        return "skip"
    last_err = None
    for attempt in range(MAX_RETRY + 1):
        try:
            status, res = call_rank(sess, variety, date)
            rec = parse_rank_result(res, date)
            if rec["long_total"] == 0 and rec["short_total"] == 0:
                # 无真实数据：不写伪造 0 记录
                return "empty"
            upsert(code, variety, date, rec)
            return "ok"
        except RateLimit as e:
            rate_seen[0] = True
            last_err = "429 %s" % e
            if attempt < MAX_RETRY:
                log("  [429 retry %d/%d] %s %s sleep %ds" % (attempt + 1, MAX_RETRY, code, date, SLEEP_RETRY))
                time.sleep(SLEEP_RETRY)
                continue
            return "ratelimit_fail"
        except Exception as e:
            last_err = str(e)
            if attempt < MAX_RETRY:
                log("  [err retry %d/%d] %s %s: %s sleep %ds" % (attempt + 1, MAX_RETRY, code, date, last_err[:160], SLEEP_RETRY))
                time.sleep(SLEEP_RETRY)
                continue
            return "fail"

def main():
    log("=== T20 GROUP START %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    sess = ensure_session()
    # 启动冷却：等前期累计的 HTTP 请求额度窗口（滚动1小时）完全回落后再开始，
    # 避免开局即被限流。落到 ~21:46 之后，旧请求已移出滚动窗口。
    log("cooldown 1500s before first call ...")
    time.sleep(1500)
    rate_seen = [False]
    summary = {}
    for code, variety in GROUP:
        existing = load_agg(code).get("data", {})
        n_exist = len(existing)
        log("VARIETY %s (%s) already-have=%d" % (code, variety, n_exist))
        ok_dates, skipped, empties, failed = [], [], [], []
        for date in DATES:
            if date in existing:
                skipped.append(date)
                continue  # 缓存命中，无 API 调用，不消耗额度，也不 sleep
            r = collect_one(sess, code, variety, date, rate_seen)
            if r == "skip":
                skipped.append(date)
            elif r == "ok":
                ok_dates.append(date)
                log("  [ok] %s %s" % (code, date))
            elif r == "empty":
                empties.append(date)
                log("  [empty/no-data] %s %s (skip write)" % (code, date))
            else:  # fail / ratelimit_fail
                failed.append(date)
                log("  [FAIL] %s %s: %s" % (code, date, r))
            # 仅在发生真实 API 调用后保持节奏（skip/缓存命中不 sleep）
            if r != "skip":
                time.sleep(SLEEP_BETWEEN)
        summary[code] = {
            "already": n_exist,
            "collected_new": len(ok_dates),
            "skipped_existing": len(skipped),
            "empty_no_data": empties,
            "failed": failed,
        }
        log("DONE %s: %s" % (code, json.dumps(summary[code], ensure_ascii=False)))
    log("=== T20 GROUP FINISH %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("rate_limited_encountered: %s" % rate_seen[0])
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    main()
