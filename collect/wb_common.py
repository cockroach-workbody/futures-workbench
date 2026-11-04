#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期货工作台 —— 公共工具：动态快照日期（SNAP_DATE）解析。

每日自动刷新时，快照日期应等于「最新已收盘交易日」，即所有品种
新浪主连日线 collect/<CODE>_daily.json 中最大的最后一根 K 线日期。
这样保证：开盘前刷新 -> 取到上一交易日完整收盘数据。

优先级：
  1) 环境变量 WB_SNAP_DATE（显式覆盖，如 2026-10-30）
  2) 自动扫描 collect/*_daily.json 的最大末日期
  3) 兜底常量
"""
import os
import json
import glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECT_DIR = os.path.join(BASE, "collect")
FALLBACK = "2026-10-28"


def latest_kline_date():
    """扫描 collect/*_daily.json，返回最新末日期（YYYY-MM-DD），无则 None。"""
    mx = None
    for p in glob.glob(os.path.join(COLLECT_DIR, "*_daily.json")):
        base = os.path.basename(p)
        if base in ("rb0_daily.json",):  # 早期 RB 专用副本，已并入 RB_daily.json
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        last = data[-1].get("d") if isinstance(data[-1], dict) else None
        if not last:
            continue
        d8 = str(last).replace("-", "")
        if len(d8) == 8 and (mx is None or d8 > mx):
            mx = d8
    if not mx:
        return None
    return f"{mx[0:4]}-{mx[4:6]}-{mx[6:8]}"


def snap_date():
    """返回带分隔符的快照日期 YYYY-MM-DD。"""
    env = os.environ.get("WB_SNAP_DATE")
    if env and len(env.replace("-", "")) == 8:
        d8 = env.replace("-", "")
        return f"{d8[0:4]}-{d8[4:6]}-{d8[6:8]}"
    auto = latest_kline_date()
    return auto or FALLBACK


def snap_date_compact():
    """返回 YYYYMMDD（无分隔符）。"""
    return snap_date().replace("-", "")


if __name__ == "__main__":
    print("snap_date =", snap_date())
