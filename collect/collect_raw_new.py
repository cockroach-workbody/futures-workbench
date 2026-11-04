#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过本地 MCP HTTP 代理调用 neodata futures_rank / futures_warehouse，
采集「前20会员持仓龙虎榜 + 标准仓单」原始数据，落盘 collect/raw/<CODE>_rank.json / _wh.json。
幂等：目标文件已存在且非空则跳过。
"""
import json, os, sys, time, urllib.request, urllib.error

CFG = json.loads(os.environ["CODEBUDDY_MCP_CONFIG"])
nd = CFG["mcpServers"]["neodata"]
URL = nd["url"]
HEADERS = nd["headers"]

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "raw")
os.makedirs(RAW, exist_ok=True)

VARIETIES = [
    ("EG", "EG.DCE", "乙二醇"),
    ("EB", "EB.DCE", "苯乙烯"),
    ("PG", "PG.DCE", "液化石油气"),
    ("BZ", "BZ.DCE", "纯苯"),
    ("SH", "SH.ZCE", "烧碱"),
    ("PX", "PX.ZCE", "对二甲苯"),
    ("PR", "PR.ZCE", "瓶片"),
]

RANK_FIELDS = "contract_code,contract_inner_code,variety_code,variety_inner_code,end_date,indicator_code,member_code,member_abbr,indicator_volume,change_volume"
WH_FIELDS = "trading_day,variety_code,wr_stat_type_name,wrq,wrq_change,wr_unit_name"

GAP = 8
MAX_RETRY = 4

_session = [None]


def _post(method, params, msgid, notification=False):
    body = {"jsonrpc": "2.0", "method": method}
    if not notification:
        body["id"] = msgid
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    if _session[0]:
        req.add_header("Mcp-Session-Id", _session[0])
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    sid = resp.headers.get("Mcp-Session-Id")
    if sid:
        _session[0] = sid
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
        return 200, objs[-1] if objs else None
    try:
        return 200, json.loads(raw) if raw.strip() else None
    except Exception:
        return 200, raw


def init_session():
    code, resp = _post("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "collect_raw_new", "version": "1.0"},
    }, 1)
    if code != 200:
        raise RuntimeError("init failed %s %s" % (code, resp))
    _post("notifications/initialized", {}, None, notification=True)


def call_tool(name, arguments):
    """返回 (ok, payload_or_None, errstr)。带 429/频率限制重试。"""
    for attempt in range(1, MAX_RETRY + 1):
        code, resp = _post("tools/call", {"name": name, "arguments": arguments},
                           int(time.time() * 1000) % 1000000)
        if code != 200:
            err = str(resp)
            if "429" in err or "rate" in err.lower() or "频率" in err or code == 429:
                print("    [429] attempt %d/%d -> sleep 90" % (attempt, MAX_RETRY), flush=True)
                time.sleep(90)
                continue
            print("    [http %s] attempt %d/%d -> sleep 90" % (code, attempt, MAX_RETRY), flush=True)
            time.sleep(90)
            continue
        # 解析
        try:
            content = resp.get("result", {}).get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            payload = json.loads("\n".join(texts))
        except Exception as e:
            return False, None, "parse-fail:%s" % e
        if payload.get("status") != "success":
            msg = str(payload.get("message") or payload.get("error") or payload.get("status"))
            if "429" in msg or "频率" in msg or "限流" in msg:
                print("    [rate] attempt %d/%d -> sleep 90" % (attempt, MAX_RETRY), flush=True)
                time.sleep(90)
                continue
            return False, None, "status=%s" % msg
        return True, payload, ""
    return False, None, "exhausted-retries"


def dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def non_empty(path):
    return os.path.exists(path) and os.path.getsize(path) > 2


def main():
    init_session()
    failures = []
    report = []

    for code, vc, cn in VARIETIES:
        print("== %s (%s %s) ==" % (code, vc, cn), flush=True)
        rank_path = os.path.join(RAW, "%s_rank.json" % code)
        wh_path = os.path.join(RAW, "%s_wh.json" % code)
        rank_rows = None
        used_date = None
        main_contract = None

        if non_empty(rank_path):
            print("  rank: 已存在，跳过", flush=True)
            try:
                rank_rows = json.load(open(rank_path, "r", encoding="utf-8"))
            except Exception:
                rank_rows = []
        else:
            for date in ("20261028", "20261027"):
                merged = []
                ok_all = True
                for ind in ("long", "short"):
                    ok, payload, err = call_tool("futures_rank", {
                        "variety_codes": vc, "indicator_type": ind, "top_n": 20,
                        "trading_date": date, "limit": 1000, "output_fields": RANK_FIELDS,
                    })
                    if not ok:
                        print("  rank %s %s FAIL: %s" % (date, ind, err), flush=True)
                        ok_all = False
                        break
                    rows = payload.get("result") or []
                    print("  rank %s %s: %d rows" % (date, ind, len(rows)), flush=True)
                    if isinstance(rows, list):
                        merged.extend(rows)
                    time.sleep(GAP)
                if ok_all and merged:
                    rank_rows = merged
                    used_date = date
                    break
                if ok_all and not merged:
                    print("  rank %s: 空，切换前一交易日" % date, flush=True)
                    time.sleep(GAP)
                    continue
                break
            if rank_rows is None:
                failures.append("%s rank" % code)
                rank_rows = []
            else:
                dump(rank_path, rank_rows)

        # 主力合约：持买+持卖合计最大的合约
        try:
            tot = {}
            for r in rank_rows:
                cc = r.get("contract_code") or ""
                if not any(ch.isdigit() for ch in cc):
                    continue
                tot[cc] = tot.get(cc, 0) + (r.get("indicator_volume") or 0)
            if tot:
                main_contract = max(tot.items(), key=lambda kv: kv[1])[0]
        except Exception:
            pass

        wh_rows = None
        if non_empty(wh_path):
            print("  wh: 已存在，跳过", flush=True)
            try:
                wh_rows = json.load(open(wh_path, "r", encoding="utf-8"))
            except Exception:
                wh_rows = []
        else:
            ok, payload, err = call_tool("futures_warehouse", {
                "variety_codes": vc, "view": "warehouse", "limit": 100,
                "output_fields": WH_FIELDS,
            })
            if not ok:
                print("  wh FAIL: %s" % err, flush=True)
                failures.append("%s wh" % code)
                wh_rows = []
            else:
                wh_rows = payload.get("result") or []
                print("  wh: %d rows" % len(wh_rows), flush=True)
                dump(wh_path, wh_rows)
            time.sleep(GAP)

        has_variety_row = any(
            (r.get("wr_stat_type_name") or "").find("品种") >= 0 for r in (wh_rows or [])
        )
        report.append({
            "code": code, "variety": vc, "cn": cn,
            "rank_rows": len(rank_rows or []),
            "rank_date": used_date,
            "main_contract": main_contract,
            "wh_rows": len(wh_rows or []),
            "wh_variety_row": has_variety_row,
        })

    print("\n==== REPORT ====", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    print("FAILURES: %s" % (failures or "none"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
