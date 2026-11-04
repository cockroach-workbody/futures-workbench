#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集「前20会员持仓龙虎榜」近一月历史净持仓，按品种落盘为 collect/raw_hist/agg_<CODE>.json。

规格（来自任务）：
  - 每个品种 V、每个交易日 D，单次调用 futures_rank（不传 indicator_type，
    一次返回 持买/持卖/成交 三类），top_n=20, limit=1000。
  - output_fields = variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date
  - 用 t20_collect_lib.parse_rank_result(result, D) 得到单日汇总 {date,net,long_total,...}
  - 用 t20_collect_lib.upsert / load_agg 实现幂等（已存在日期跳过，不覆盖）。
  - 调用之间 sleep 8s；非 success 或 429/频率限制：sleep 90s 重试，最多重试 4 次；
    仍失败则把 (code, date) 记入 failures 并继续下一个（不死等）。

底层通过本地 MCP HTTP 代理调用 neodata futures_rank（沿用 fetch_raw_hist.py 的连接方式）。
"""
import os, sys, json, time, urllib.request, urllib.error

# 让脚本可从任意目录运行：确保 collect 目录在 sys.path，便于 import t20_collect_lib
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import t20_collect_lib as L

# ---- MCP 连接（与 fetch_raw_hist.py 一致）----
CFG = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
cfg = json.loads(CFG)
nd = cfg["mcpServers"]["neodata"]
URL = nd["url"]
HEADERS = nd["headers"]

# ---- 本组品种（均为 DCE，code | variety_codes）----
VARIETIES = [
    ("I", "I.DCE"),
    ("J", "J.DCE"),
    ("JM", "JM.DCE"),
    ("M", "M.DCE"),
    ("C", "C.DCE"),
    ("A", "A.DCE"),
    ("Y", "Y.DCE"),
    ("P", "P.DCE"),
    ("JD", "JD.DCE"),
    ("L", "L.DCE"),
    ("V", "V.DCE"),
    ("PP", "PP.DCE"),
    ("LH", "LH.DCE"),
]
DATES = L.DATES  # 来自辅助库，11 个采样交易日

OUT_DIR = L.RAWHIST
os.makedirs(OUT_DIR, exist_ok=True)

TOOL_NAME = "futures_rank"
OUTPUT_FIELDS = "variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date"

SLEEP_BETWEEN = 8       # 正常调用之间 8s
SLEEP_RETRY = 90        # 限流/失败重试等待 90s
MAX_RETRY = 4           # 最多重试 4 次（共 5 次尝试）

# ---- 极简 MCP Streamable HTTP 客户端 ----
_session = [None]

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
    except urllib.error.URLError as e:
        return session, 0, "urlerror:%s" % e
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

def mcp_init():
    sess, code, resp = _post(None, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "t20_collect_hist", "version": "1.0"}
    }, 1)
    _post(sess, "notifications/initialized", {}, None, notification=True)
    return sess

def extract_result(obj):
    """从 MCP tools/call 的响应中抽取 futures_rank 的 result 行数组。"""
    if not isinstance(obj, dict):
        return None, "resp-not-dict"
    res = obj.get("result")
    if not isinstance(res, dict):
        return None, "no-result: %s" % str(obj)[:160]
    content = res.get("content", [])
    blob = ""
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            blob += c.get("text", "")
    try:
        inner = json.loads(blob)
    except Exception as e:
        return None, "parse-blob-fail:%s" % e
    status = inner.get("status")
    if status is not None and status != "success":
        return None, "status=%s %s" % (status, str(inner)[:160])
    rows = inner.get("result")
    if not isinstance(rows, list):
        return None, "result-not-list: %s" % str(inner)[:160]
    return rows, "ok"

def call_rank(sess, variety, date):
    """单次调用 futures_rank（不传 indicator_type），返回 (rows, errmsg)。"""
    msgid = int(time.time() * 1000) % 1000000
    params = {
        "name": TOOL_NAME,
        "arguments": {
            "variety_codes": variety,
            "trading_date": date,
            "top_n": 20,
            "limit": 1000,
            "output_fields": OUTPUT_FIELDS,
        },
    }
    sess, code, resp = _post(sess, "tools/call", params, msgid)
    if code == 429:
        return None, "429"
    if code != 200:
        return None, "http-%s" % code
    rows, err = extract_result(resp)
    if rows is None:
        if "429" in err or "频率" in err or "限流" in err:
            return None, "429"
        return None, err
    return rows, "ok"

LOG_PATH = os.path.join(OUT_DIR, "_dce_backfill.log")
_logf = open(LOG_PATH, "a", encoding="utf-8")

def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _logf.write(s + "\n")
    _logf.flush()

def is_rate_limit(err):
    return "429" in str(err) or "频率" in str(err) or "限流" in str(err) or "rate" in str(err).lower()

def main():
    log("=== START %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    sess = mcp_init()
    _session[0] = sess
    summary = {}
    rate_limited_overall = False
    all_failures = []

    for code, variety in VARIETIES:
        collected = 0
        failed = []
        rate_limited_variety = False
        for date in DATES:
            # a. 幂等：已存在则跳过（不覆盖）
            p = L.load_agg(code)
            if date in p.get("data", {}):
                log("SKIP %s %s already present" % (code, date))
                collected += 1
                continue
            # b. 调用 futures_rank（带重试）
            rec = None
            ok = False
            last_err = ""
            for attempt in range(1 + MAX_RETRY):
                rows, err = call_rank(sess, variety, date)
                if rows is None:
                    last_err = err
                    if is_rate_limit(err):
                        rate_limited_variety = True
                        rate_limited_overall = True
                        log("  [429/limit] %s %s attempt %d/%d, sleep %ds" %
                            (code, date, attempt + 1, 1 + MAX_RETRY, SLEEP_RETRY))
                        if attempt < MAX_RETRY:
                            time.sleep(SLEEP_RETRY)
                            continue
                        else:
                            break
                    else:
                        # 非成功状态码 / 其它错误
                        log("  [err] %s %s attempt %d/%d: %s" %
                            (code, date, attempt + 1, 1 + MAX_RETRY, err[:160]))
                        if attempt < MAX_RETRY:
                            time.sleep(SLEEP_RETRY)
                            continue
                        else:
                            break
                # c. parse + upsert
                try:
                    rec = L.parse_rank_result(rows, date)
                    added = L.upsert(code, variety, date, rec)
                    if added:
                        ok = True
                        log("OK %s %s net=%d long=%d short=%d long_chg=%d short_chg=%d" %
                            (code, date, rec["net"], rec["long_total"],
                             rec["short_total"], rec["long_chg"], rec["short_chg"]))
                    else:
                        ok = True  # 被 upsert 视为已存在（理论不会到这，因前面已检查）
                        log("EXISTS %s %s (upsert skip)" % (code, date))
                    break
                except Exception as e:
                    last_err = "parse/upsert:%s" % e
                    log("  [err] %s %s parse/upsert: %s" % (code, date, e))
                    if attempt < MAX_RETRY:
                        time.sleep(SLEEP_RETRY)
                        continue
                    else:
                        break
            # d. 调用之间 sleep 8s（每次日期处理完，无论成败都间隔）
            if ok:
                collected += 1
            else:
                failed.append(date)
                all_failures.append((code, date))
                log("FAIL %s %s: %s" % (code, date, last_err[:160]))
            time.sleep(SLEEP_BETWEEN)
        if rate_limited_variety:
            log("NOTE %s 遇到限流（已重试）" % code)
        summary[code] = {"collected": collected, "failed": failed}
        log("DONE %s collected=%d/%d failed=%s" %
            (code, collected, len(DATES), failed))

    log("=== FINISH %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("rate_limited_encountered: %s" % rate_limited_overall)
    log("failures: %s" % all_failures)
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    main()
