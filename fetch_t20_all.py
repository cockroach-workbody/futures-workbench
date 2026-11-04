# -*- coding: utf-8 -*-
"""
fetch_t20_all.py
一次性拉取 5 个目标交易日 × 36 个品种 × (long/short) 的前20会员持仓龙虎榜，
把每个 (date, code, side) 的 result 数组落盘到 collect/raw/<date>_<code>_<side>.json，
最后调用 agg_t20.py 聚合写出 collect/_t20_part_D.json。

依赖：本地 MCP 代理可访问（fetch_rank.py 同款 BASE/AUTH/CTX）。
若代理当前 502，请确认 WorkBuddy 已连接 NeoData MCP 后重跑本脚本。

规则（与任务一致）：
- 品种用 variety_codes（带交易所后缀），indicator_type 分别取 long / short；
- top_n=20, limit=10000, output_fields 仅取需要的字段以减小返回；
- 单批次失败重试一次，仍失败则跳过该 (date, code, side)；
- 聚合：long_total/short_total = 该品种所有合约 indicator_volume 之和；net = long - short。

注意：NeoData futures_rank 单次调用存在约 100 行全局上限（与 limit 参数无关）。
对单一 (variety, side, date) 调用，每合约至多返回 top_n(20) 行；
因此活跃合约数 >5 的高流动性品种（如 M/CU/AG 等）会被截断，仅取前 5 个合约，
其 long_total/short_total 为近似值（缺失远月合约的前20会员持仓）。
如需精确，应改为按合约逐合约拉取后再按会员汇总，代价是调用量大幅增加。
"""
import json, os, sys, time, urllib.request

BASE_MCP = "http://127.0.0.1:49920/01fe583d8ba57fea4c4e11f8fe01311e/mcp"
AUTH = "Bearer NjTTPuGHMTX_qpYtw7D6bdWWSGQvuTenKNcu4XEbkuQ"
CTX = "eyJzZXNzaW9uSWQiOiI1ODE0ZDRlMS00YzI4LTQ2N2ItYWRjMi1iOTFlODhiNWEzMWIiLCJ3ZWxjb21lTW9kZSI6IndvcmtpbmciLCJ3b3Jrc3BhY2UiOiJFOi93b3JrYnVkZHkvMjAyNi0xMC0yOC0xMS0yMi0yNiIsImFsbG93ZWRCdWlsdGluVG9vbHMiOlsiYWdlbnRfbWFpbF9kb3dubG9hZF9hdHRhY2htZW50IiwiYWdlbnRfbWFpbF91cGxvYWRfYXR0YWNobWVudCIsImF1dG9tYXRpb25fdXBkYXRlIiwiY29ubmVjdF9jbG91ZF9zZXJ2aWNlIiwiY29ubmVjdF9vcGVuX3BsYXRmb3JtIiwiY29udmVyc2F0aW9uX3NlYXJjaCIsImRvbWFpbl9zZWFyY2giLCJpbV9nZXRfbWVzc2FnZXMiLCJpbV9saXN0X2NvbnZlcnNhdGlvbnMiLCJrbm93bGVkZ2Vfc2VhcmNoIiwibGlzdF9pbnN0YWxsZWRfcGx1Z2lucyIsInByZXNlbnRfZmlsZXMiLCJyZWNvbm5lY3RfcGx1Z2luIiwic2VhcmNoX2FuZF9pbnN0YWxsX3NraWxscyIsInNlYXJjaF9wbHVnaW5zIiwic2hvd193aWRnZXQiLCJza2lsbF9tYW5hZ2VtZW50Iiwic3VnZ2VzdF9wbHVnaW5faW5zdGFsbCIsInVuaW5zdGFsbF9wbHVnaW4iLCJ3aWRnZXRfZ3VpZGVsaW5lcyIsIndvcmtidWRkeV9hcHBfc2V0X2NvbnZlcnNhdGlvbl90aXRsZSIsIndvcmtidWRkeV9jbG91ZF9zZXJ2aWNlIiwid29ya2J1ZGR5X21pbmlwcm9ncmFtX3NjYWZmb2xkIiwid29ya2J1ZGR5X3JlcXVlc3RfbWNwX2Nvbm5lY3Rpb24iLCJ3b3JrYnVkZHlfc2l0ZXNfZGVwbG95Iiwid29ya2J1ZGR5X3NpdGVzX3VucHVibGlzaCJdfQ.c_jI4NTj8HHY3gbzTOVbtyoQY_2RKv3RJBMK6VguydE"

WORK = os.path.dirname(os.path.abspath(__file__))
VAR_FILE = os.path.join(WORK, "collect", "_t20_variety.json")
RAW_DIR = os.path.join(WORK, "collect", "raw")
OUT_DIR = os.path.join(WORK, "collect")

# 任务要求的 5 个负责交易日（必须全查）
DATES = ["20260924", "20260925", "20260928", "20260929", "20260930"]
OUTPUT_FIELDS = "contract_code,variety_code,indicator_code,member_code,indicator_volume,change_volume"

def load_variety():
    arr = json.load(open(VAR_FILE, encoding="utf-8"))
    return [(v["code"], v["variety"]) for v in arr]

def post(method, params, sid=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE_MCP, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Authorization", AUTH)
    req.add_header("X-WorkBuddy-MCP-Context", CTX)
    if sid:
        req.add_header("Mcp-Session-Id", sid)
    resp = urllib.request.urlopen(req, timeout=120)
    return resp.headers.get("mcp-session-id"), resp.read().decode("utf-8")

def parse_text(raw):
    if "data:" in raw and raw.lstrip().startswith("data:"):
        out = [l.strip()[len("data:"):].strip() for l in raw.splitlines() if l.strip().startswith("data:")]
        return "\n".join(out)
    return raw

def call_rank(variety_codes, indicator_type, trading_date):
    sid, raw = post("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "cli", "version": "1.0"}})
    post("notifications/initialized", {}, sid=sid, notif=True)
    args = {
        "variety_codes": variety_codes,
        "limit": 10000,
        "top_n": 20,
        "indicator_type": indicator_type,
        "trading_date": trading_date,
        "output_fields": OUTPUT_FIELDS,
    }
    sid2, raw = post("tools/call", {"name": "futures_rank", "arguments": args}, sid=sid)
    text = parse_text(raw)
    obj = json.loads(text)
    if "result" in obj and isinstance(obj["result"], list):
        return obj["result"]
    if "content" in obj:
        inner = json.loads(obj["content"][0]["text"])
        if "result" in inner and isinstance(inner["result"], list):
            return inner["result"]
    return None

def save_raw(date, code, side, rows):
    os.makedirs(RAW_DIR, exist_ok=True)
    p = os.path.join(RAW_DIR, f"{date}_{code}_{side}.json")
    json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)

def main():
    vlist = load_variety()
    os.makedirs(RAW_DIR, exist_ok=True)
    done = skip = 0
    for date in DATES:
        for code, variety in vlist:
            for side, itype in (("long", "long"), ("short", "short")):
                rows = None
                for attempt in (1, 2):
                    try:
                        rows = call_rank(variety, itype, date)
                    except Exception as e:
                        print(f"  [retry {attempt}] {date} {code} {side}: {e}")
                        time.sleep(1)
                        continue
                    if rows:
                        break
                if not rows:
                    print(f"  [SKIP] {date} {code} {side}: 无数据")
                    skip += 1
                    continue
                # 仅保留与品种匹配的行（防御性）
                rows = [r for r in rows if r.get("variety_code") in (variety, None)]
                save_raw(date, code, side, rows)
                done += 1
        print(f"[date {date}] 完成，累计 done={done} skip={skip}")
    print(f"拉取结束 done={done} skip={skip}")
    # 聚合
    print("开始聚合 -> collect/_t20_part_D.json")
    import subprocess
    subprocess.run([sys.executable, os.path.join(WORK, "agg_t20.py")], check=True)

if __name__ == "__main__":
    main()
