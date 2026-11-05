#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前20会员主力合约净持仓 —— 每日滚动采集（追加最新交易日 -> 曲线滚动）

数据口径：前20会员「主力合约（持买合计最大）」净持仓 = 该品种持买合计最大的真实月份
合约的前20会员持买 - 持卖合计。与快照 parse_neodata 选主力合约口径完全一致。
来源（纯 HTTP 可跑，不依赖 neodata 连接器、不访问各交易所官网）：
  - SHFE（上期所）12 品种：AKShare 直连 get_rank_sum_daily（上期所官网 shfe.com.cn 可用）。
  - CZCE（郑商所）11 品种：东方财富龙虎榜 RPT_FUTU_DAILYPOSITION（市场代码 069001008），
    替代 AKShare（AKShare 抓郑商所需访问 czce.com.cn，本环境网络不可达）。
  - DCE（大商所）13 品种：东方财富龙虎榜（市场代码 069001007）；
    NeoData futures_rank 作为兜底保留（仅当连接器可用）。

幂等：已存在的交易日自动跳过，故同一天重复运行无害；每当出现新的已收盘交易日，
本脚本会把该日数据追加进 collect/raw_hist/agg_<CODE>.json，曲线随之向前滚动。

调用：
  python collect/refresh_t20_daily.py                  # 抓 WB_SNAP_DATE(或最新已收盘交易日)
  python collect/refresh_t20_daily.py --date 20261030  # 指定日期（调试）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t20_collect_lib import (upsert, parse_rank_result, rec_from_neodata,  # noqa: E402
                             parse_akshare_dominant, em_aggregate_dominant,
                             source_cached)
from wb_common import snap_date  # noqa: E402
import akshare as ak  # noqa: E402
import requests  # noqa: E402
import urllib.parse  # noqa: E402

# AKShare 直连可覆盖的交易所：仅 SHFE（上期所官网 shfe.com.cn 在本环境可用）
SHFE = ["RB", "HC", "RU", "CU", "AL", "AU", "NI", "ZN", "AG", "PB", "BU", "SN"]
# CZCE（郑商所）改用东方财富龙虎榜：AKShare 需访问 czce.com.cn，本环境网络不可达
CZCE = ["FG", "SA", "SR", "CF", "RM", "OI", "AP", "CJ", "TA", "MA", "UR"]
AK_VARS = SHFE  # 仅 SHFE 走 AKShare 直连
AK_VARIETY = {c: f"{c}.SHF" for c in SHFE}
CZCE_VARIETY = {c: f"{c}.ZCE" for c in CZCE}

# 东方财富龙虎榜市场代码
EM_MARKET = {
    "DCE": "069001007",   # 大商所
    "CZCE": "069001008",  # 郑商所
}

# DCE 13 品种（AKShare 不可达，需 NeoData）
DCE_LIST = ["I", "J", "JM", "M", "C", "A", "Y", "P", "JD", "LH", "L", "V", "PP"]
DCE_VARIETY = {c: f"{c}.DCE" for c in DCE_LIST}

RANK_FIELDS = ("contract_code,variety_code,end_date,indicator_code,member_code,"
              "indicator_volume,change_volume")


def collect_ak(date):
    """AKShare 直连（SHFE）：取某交易日各品种主力合约（持买合计最大）前20净持仓。"""
    df = ak.get_rank_sum_daily(start_day=date, end_day=date, vars_list=AK_VARS)
    # 主力合约口径：逐品种在真实月份合约中选 持买合计(top20) 最大者
    return parse_akshare_dominant(df, date, AK_VARIETY)


def collect_dce(date):
    """DCE 13 品种：用 NeoData futures_rank（仅当 neodata 连接器可用）。最佳努力。"""
    cfg_env = os.environ.get("CODEBUDDY_MCP_CONFIG")
    if not cfg_env:
        print("  [DCE] 未连接 neodata 连接器，跳过 DCE 前20滚动（保持上次数据）", flush=True)
        return {}
    try:
        import collect_raw_new as crn
        crn.init_session()
    except Exception as e:  # noqa
        print(f"  [DCE] neodata 初始化失败: {e}", flush=True)
        return {}
    out = {}
    for code in DCE_LIST:
        vc = DCE_VARIETY[code]
        merged = []
        ok_all = True
        for ind in ("long", "short"):
            ok, payload, err = crn.call_tool("futures_rank", {
                "variety_codes": vc, "indicator_type": ind, "top_n": 20,
                "trading_date": date, "limit": 1000, "output_fields": RANK_FIELDS,
            })
            if not ok:
                ok_all = False
                print(f"  [DCE] {code} {ind} FAIL: {err}", flush=True)
                break
            rows = payload.get("result") or []
            merged.extend(rows)
            time.sleep(4)
        if ok_all and merged:
            rec = rec_from_neodata(merged, date)
            if rec:
                out[code] = rec
        time.sleep(4)
    return out


def _fetch_em_rows(date, market_code):
    """东方财富龙虎榜 RPT_FUTU_DAILYPOSITION：拉取某交易日某市场全天「逐会员·逐合约」明细。
    纯 HTTP，不依赖 neodata 连接器，云端沙箱可直接跑。返回原始行列表。"""
    d8 = (date or "").replace("-", "")
    if len(d8) != 8:
        print(f"  [EM] 日期格式异常 {date}，跳过", flush=True)
        return []
    ymd = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
    H = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/",
         "Accept": "application/json"}
    base = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    rows = []
    for pn in range(1, 60):
        fstr = f"(TRADE_DATE='{ymd}')(TRADE_MARKET_CODE=\"{market_code}\")"
        url = (f'{base}?reportName=RPT_FUTU_DAILYPOSITION&columns=ALL'
               f'&filter={urllib.parse.quote(fstr)}'
               f'&pageSize=500&pageNumber={pn}&source=WEB&client=WEB')
        try:
            r = requests.get(url, timeout=30, headers=H)
            d = (r.json().get("result") or {}).get("data") or []
        except Exception as e:  # noqa
            print(f"  [EM] 第{pn}页请求失败: {e}", flush=True)
            break
        if not d:
            break
        rows.extend(d)
        if len(d) < 500:
            break
    return rows


def _aggregate_em_top20(rows, codes, exch_map=None):
    """按品种代码前缀聚合前20会员「主力合约（持买合计最大）」合计净持仓。"""
    return em_aggregate_dominant(rows, codes, exch_map or {})


def collect_dce_em(date):
    """DCE 13 品种：东方财富龙虎榜（市场代码 069001007）。主力合约口径。"""
    rows = _fetch_em_rows(date, EM_MARKET["DCE"])
    if not rows:
        print("  [DCE-EM] 未获取到 DCE 明细，跳过", flush=True)
        return {}
    out = _aggregate_em_top20(rows, DCE_LIST, DCE_VARIETY)
    print(f"  [DCE-EM] 全天 DCE 明细 {len(rows)} 行, 聚合 {len(out)} 个品种", flush=True)
    return out


def collect_czce_em(date):
    """CZCE（郑商所）11 品种：东方财富龙虎榜（市场代码 069001008）。
    替代 AKShare（AKShare 抓郑商所需访问 czce.com.cn，本环境网络不可达）。主力合约口径。"""
    rows = _fetch_em_rows(date, EM_MARKET["CZCE"])
    if not rows:
        print("  [CZCE-EM] 未获取到郑商所明细，跳过", flush=True)
        return {}
    out = _aggregate_em_top20(rows, CZCE, CZCE_VARIETY)
    print(f"  [CZCE-EM] 全天郑商所明细 {len(rows)} 行, 聚合 {len(out)} 个品种", flush=True)
    return out


def collect_one_date(date):
    """采集单个交易日（3 路），返回新增点数。已存在日期先做缓存检查，跳过网络请求。"""
    added = 0
    # 1) AKShare 直连 SHFE（上期所官网可用）
    if not source_cached(AK_VARS, date):
        try:
            recs = collect_ak(date)
            for code, rec in recs.items():
                if upsert(code, AK_VARIETY.get(code), date, rec):
                    added += 1
            print(f"  AKShare(SHFE) {date}: {len(recs)} 品种, 新增 {added} 个日期点", flush=True)
        except Exception as e:  # noqa
            print(f"  AKShare(SHFE) {date} 抓取失败: {e}", flush=True)
    else:
        print(f"  AKShare(SHFE) {date}: 已缓存，跳过", flush=True)

    # 2) 东方财富：CZCE（郑商所，替代 AKShare 因 czce.com.cn 不可达）
    if not source_cached(CZCE, date):
        try:
            crecs = collect_czce_em(date)
            base = added
            for code, rec in crecs.items():
                if upsert(code, CZCE_VARIETY.get(code), date, rec):
                    added += 1
            if crecs:
                print(f"  CZCE(东方财富) {date}: {len(crecs)} 品种, 新增 {added - base} 个日期点", flush=True)
        except Exception as e:  # noqa
            print(f"  CZCE(东方财富) {date} 抓取失败: {e}", flush=True)
    else:
        print(f"  CZCE(东方财富) {date}: 已缓存，跳过", flush=True)

    # 3) 东方财富 DCE（大商所，优先），失败回退 NeoData
    if not source_cached(DCE_LIST, date):
        try:
            drecs = collect_dce_em(date)
            src = "东方财富"
            if not drecs:
                drecs = collect_dce(target=date)  # 旧 NeoData 兜底（仅当连接器可用）
                src = "NeoData"
            base = added
            for code, rec in drecs.items():
                if upsert(code, DCE_VARIETY.get(code), date, rec):
                    added += 1
            if drecs:
                print(f"  DCE({src}) {date}: {len(drecs)} 品种, 新增 {added - base} 个日期点", flush=True)
        except Exception as e:  # noqa
            print(f"  DCE {date} 抓取失败: {e}", flush=True)
    else:
        print(f"  DCE(东方财富) {date}: 已缓存，跳过", flush=True)

    return added


def trading_window(target, days=22):
    """取 target 当日 + 之前约 days 个交易日的 8 位日期列表（跳过周末）。"""
    from datetime import datetime, timedelta
    td = datetime.strptime(target, "%Y%m%d")
    out, d = [], td
    while len(out) < days:
        if d.weekday() < 5:  # 跳过周六周日
            out.append(d.strftime("%Y%m%d"))
        d = d - timedelta(days=1)
    if target not in out:
        out.append(target)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定目标交易日 YYYYMMDD（默认 WB_SNAP_DATE 或最新已收盘交易日）")
    ap.add_argument("--backfill", type=int, default=0,
                    help="回填近 N 个交易日（含今日）的滚动曲线；0=仅采集目标日。幂等，已存在自动跳过。")
    args = ap.parse_args()

    target = args.date or os.environ.get("WB_SNAP_DATE") or snap_date()
    target = target.replace("-", "") if target else target  # 规整为 8 位 YYYYMMDD，避免与历史键格式混用

    if args.backfill and args.backfill > 0:
        dates = trading_window(target, args.backfill)
        print(f"== 前20回填(主力合约口径) -> {target} 共 {len(dates)} 个交易日 ==", flush=True)
        total = 0
        for i, dt in enumerate(sorted(dates)):
            total += collect_one_date(dt)
            if i < len(dates) - 1:
                time.sleep(1.5)  # 礼貌限速，避免连续请求被交易所/东方财富拦截
        print(f"DONE 前20回填. 累计新增 {total} 个日期点", flush=True)
        return 0

    print(f"== 前20每日滚动(主力合约口径) -> 目标日期 {target} ==", flush=True)
    added = collect_one_date(target)
    print(f"DONE 前20滚动. 新增 {added} 个日期点", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
