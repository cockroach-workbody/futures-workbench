#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 collect/raw_hist/agg_<CODE>.json 中采集到的「前20会员全合约合计净持仓」历史，
注入到两套快照的 top20.history 字段：
  - data/<板块>/<CODE>/2026-10-28.json   （源）
  - web/data/<板块>/<CODE>/2026-10-28.json （线上服务用）
history = [{date, net, long_total, short_total, long_chg, short_chg}, ...] 按日期升序
"""
import json, os, sys, glob, datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAWHIST = os.path.join(BASE, "collect", "raw_hist")
HERE = os.path.join(BASE, "collect")
sys.path.insert(0, HERE)
from wb_common import snap_date  # 动态快照日期（与 build_all/build_web 一致）

# 优先用刷新脚本传入的 WB_SNAP_DATE，否则自动取最新已收盘交易日
SNAP_DATE = os.environ.get("WB_SNAP_DATE") or snap_date()

def load_agg(code):
    fp = os.path.join(RAWHIST, f"agg_{code}.json")
    if not os.path.exists(fp):
        return None
    try:
        return json.load(open(fp, "r", encoding="utf-8"))
    except Exception:
        return None

def _norm(d):
    """规整日期为 8 位 YYYYMMDD（去掉可能存在的横线），便于正确字符串比对/排序。"""
    return (d or "").replace("-", "")


def build_history(payload, snap):
    """取近一月滚动窗口（date<=snap 且 date>=snap-40天，最多约22个交易日），按日期升序。
    点过少时兜底取全部 date<=snap。
    注意：agg 内部日期可能带横线（2026-11-03）也可能无横线（20261103），本函数对
    键与 snap/cutoff 统一规整为 8 位后再比对，并按规整后的日期去重（避免同一天被
    两种格式各记一次），输出统一为 8 位日期。"""
    snap = _norm(snap)
    data = payload.get("data", {})
    if not data:
        return None
    try:
        sd = datetime.datetime.strptime(snap, "%Y%m%d")
    except Exception:
        sd = None
    cutoff = (sd - datetime.timedelta(days=40)).strftime("%Y%m%d") if sd else None

    def _mk(date, rec):
        d = _norm(date) or date
        return {
            "date": d,
            "net": int(rec["net"]),
            "long_total": int(rec.get("long_total", 0)),
            "short_total": int(rec.get("short_total", 0)),
            "long_chg": int(rec.get("long_chg", 0)),
            "short_chg": int(rec.get("short_chg", 0)),
        }

    seen = set()
    pts = []
    for date in sorted(data.keys()):
        rec = data[date]
        dk = _norm(date)
        if not dk or not isinstance(rec, dict) or "net" not in rec:
            continue
        if dk > snap:
            continue
        if cutoff and dk < cutoff:
            continue
        if dk in seen:
            continue
        seen.add(dk)
        pts.append(_mk(date, rec))
    # 兜底：窗口内点过少（如首次运行），放宽到全部 <= snap（仍按规整日期去重）
    if len(pts) < 5:
        pts = []
        seen = set()
        for date in sorted(data.keys()):
            rec = data[date]
            dk = _norm(date)
            if dk and isinstance(rec, dict) and "net" in rec and dk <= snap and dk not in seen:
                seen.add(dk)
                pts.append(_mk(date, rec))
    return pts if pts else None

def inject_into(path, history):
    if not os.path.exists(path):
        return False
    try:
        d = json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        return False
    t = d.get("top20")
    if not isinstance(t, dict):
        t = {}
        d["top20"] = t
    t["history"] = history
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return True

def main():
    codes = [f.split("agg_")[1].split(".json")[0] for f in glob.glob(os.path.join(RAWHIST, "agg_*.json"))]
    print(f"Found agg files for {len(codes)} varieties")
    done = 0
    for code in sorted(codes):
        agg = load_agg(code)
        if not agg:
            continue
        history = build_history(agg, SNAP_DATE)
        if not history:
            continue
        targets = []
        # 源快照
        for p in glob.glob(os.path.join(BASE, "data", "*", code, SNAP_DATE + ".json")):
            targets.append(p)
        # 线上快照
        for p in glob.glob(os.path.join(BASE, "web", "data", "*", code, SNAP_DATE + ".json")):
            targets.append(p)
        ok = 0
        for t in targets:
            if inject_into(t, history):
                ok += 1
        print(f"  {code}: {len(history)} pts -> injected into {ok} snapshot file(s) "
              f"({history[0]['date']}..{history[-1]['date']}, last net={history[-1]['net']})")
        done += 1
    print(f"Done. {done} varieties injected.")

if __name__ == "__main__":
    main()
