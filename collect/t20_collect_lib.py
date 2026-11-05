#!/usr/bin/env python3
# 前20会员历史净持仓采集辅助库（被后台采集 Agent 调用）
# 读取/写入 collect/raw_hist/agg_<CODE>.json，幂等合并（已存在日期跳过）。
#
# 口径统一（与快照 parse_neodata 一致）：历史/滚动曲线一律只取「持买合计最大」的
# 主力合约的前20会员合计净持仓，不再汇总全合约。这样 top20.history 与快照
# top20.net（parse_neodata 选主力合约）口径一致，曲线可直接对比、叠加。
import json, os, re, sys

BASE = os.path.dirname(os.path.abspath(__file__))
RAWHIST = os.path.join(BASE, "raw_hist")

# 复用快照解析逻辑，保证历史主力合约口径与快照完全一致
try:
    sys.path.insert(0, BASE)
    from parse_neodata import parse_rank as _parse_rank_neodata
except Exception:  # noqa
    _parse_rank_neodata = None

def agg_path(code):
    return os.path.join(RAWHIST, f"agg_{code}.json")

def load_agg(code):
    fp = agg_path(code)
    if os.path.exists(fp):
        try:
            return json.load(open(fp, "r", encoding="utf-8"))
        except Exception:
            pass
    return {"code": code, "variety": None, "data": {}}

def save_agg(payload):
    os.makedirs(RAWHIST, exist_ok=True)
    json.dump(payload, open(agg_path(payload["code"]), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)

def upsert(code, variety, date, rec):
    """写入单日汇总；返回 True 表示本次新增（False 表示已存在跳过）。
    日期键统一规整为 8 位 YYYYMMDD，避免 '2026-11-03' 与 '20261103' 被当成两天。"""
    date = date.replace("-", "") if date else date
    p = load_agg(code)
    if p.get("variety") is None and variety:
        p["variety"] = variety
    data = p.setdefault("data", {})
    if date in data:
        return False
    rec = dict(rec)
    rec["date"] = date
    data[date] = rec
    save_agg(p)
    return True

def parse_rank_result(result, date):
    """把 futures_rank 返回的 result 行数组合并为单日汇总 dict。
    只汇总 持买 / 持卖（忽略 成交）。"""
    long_total = long_chg = short_total = short_chg = 0
    for r in result:
        ind = r.get("indicator_code")
        vol = r.get("indicator_volume") or 0
        chg = r.get("change_volume") or 0
        if ind == "持买":
            long_total += vol; long_chg += chg
        elif ind == "持卖":
            short_total += vol; short_chg += chg
    net = long_total - short_total
    return {"date": date, "net": net, "long_total": int(long_total),
            "short_total": int(short_total), "long_chg": int(long_chg),
            "short_chg": int(short_chg)}


def _is_real_contract(symbol, variety):
    """判断 symbol 是否为真实月份合约（而非品种汇总/主连/指数伪合约）。
    symbol 形如 'rb2610'（variety='rb'）才算真实合约：前缀等于 variety 且后缀为
    合法 YYMM（两位年 + 01~12 月，共 4 位数字）。"""
    if not symbol or not variety:
        return False
    if symbol == variety:
        return False
    if not symbol.startswith(variety):
        return False
    suffix = symbol[len(variety):]
    return re.match(r"^\d{2}(0[1-9]|1[0-2])$", suffix) is not None


def rec_from_neodata(rows, date):
    """复用 parse_neodata.parse_rank 选取「持买合计最大」主力合约，输出 agg 单日记录。
    与快照 build_all 的 parse_neodata 解析口径完全一致（主力合约）。
    返回 {date,net,long_total,short_total,long_chg,short_chg,contract} 或 None。"""
    if _parse_rank_neodata is None or not rows:
        return None
    res = _parse_rank_neodata(rows)
    if not res:
        return None
    best_cc, _as_of, _detail, summary, _bias = res
    if not summary:
        return None
    return {
        "date": date,
        "net": int(round(summary.get("net", 0) or 0)),
        "long_total": int(round(summary.get("long_total", 0) or 0)),
        "short_total": int(round(summary.get("short_total", 0) or 0)),
        "long_chg": int(round(summary.get("long_chg", 0) or 0)),
        "short_chg": int(round(summary.get("short_chg", 0) or 0)),
        "contract": best_cc,
    }


def parse_akshare_dominant(df, date, variety_map):
    """AKShare get_rank_sum_daily 结果 -> 各品种主力合约（持买合计最大）前20合计净持仓。
    df 含每行一个合约（symbol!=variety）以及一行品种汇总（symbol==variety）。
    逐品种在真实月份合约中选 long_open_interest_top20 最大者作为主力合约。
    返回 {code: rec}，rec 含 contract 字段（如 'RB2610.SHF'）。"""
    out = {}
    if df is None or len(df) == 0:
        return out
    cols = list(df.columns)
    need = ["symbol", "variety", "long_open_interest_top20",
            "short_open_interest_top20"]
    if not all(c in cols for c in need):
        # 列名不符（如某些 AKShare 版本），退化为品种汇总行（全合约），保证不崩
        summ = df[df["symbol"] == df["variety"]]
        for _, r in summ.iterrows():
            code = str(r["variety"]).strip()
            lo, so = r.get("long_open_interest_top20"), r.get("short_open_interest_top20")
            if lo is None or so is None:
                continue
            lc = r.get("long_open_interest_chg_top20")
            sc = r.get("short_open_interest_chg_top20")
            out[code] = {"date": date, "net": int(lo - so),
                         "long_total": int(lo), "short_total": int(so),
                         "long_chg": int(lc) if lc is not None else 0,
                         "short_chg": int(sc) if sc is not None else 0,
                         "contract": code + (variety_map.get(code, "").split(".")[-1] and "." + variety_map.get(code, "").split(".")[-1])}
        return out
    # 逐品种分组，排除品种汇总行与伪合约，选主力
    for code in df["variety"].dropna().unique():
        code = str(code).strip()
        sub = df[df["variety"] == code]
        det = sub[sub.apply(lambda r: _is_real_contract(str(r["symbol"]), code), axis=1)]
        if det.empty:
            continue
        det = det.copy()
        det["_lo"] = det["long_open_interest_top20"].fillna(0).astype(float)
        best = det.loc[det["_lo"].idxmax()]
        lo = best.get("long_open_interest_top20")
        so = best.get("short_open_interest_top20")
        if lo is None or so is None:
            continue
        lc = best.get("long_open_interest_chg_top20")
        sc = best.get("short_open_interest_chg_top20")
        exch = variety_map.get(code, "")
        suffix = ("." + exch.split(".")[-1]) if "." in exch else ""
        out[code] = {"date": date, "net": int(lo - so),
                     "long_total": int(lo), "short_total": int(so),
                     "long_chg": int(lc) if lc is not None else 0,
                     "short_chg": int(sc) if sc is not None else 0,
                     "contract": str(best["symbol"]).upper() + suffix}
    return out


def em_aggregate_dominant(rows, codes, exch_map=None):
    """东方财富 RPT_FUTU_DAILYPOSITION 原始行 -> 各品种主力合约前20合计净持仓。
    逐合约（SECURITY_CODE）聚合会员多空，选「持买合计最大」合约为主力，再取该合约
    前20会员（按 持买+持卖 排序）汇总。返回 {code: rec}（含 contract）。"""
    exch_map = exch_map or {}
    # contract -> member -> [long, short, lchg, schg]
    by_contract = {}
    for x in rows:
        sc = (x.get("SECURITY_CODE") or "")
        code = None
        for c in codes:
            if sc.startswith(c) and len(sc) > len(c) and sc[len(c)].isdigit():
                code = c
                break
        if not code:
            continue
        m = x.get("MEMBER_NAME_ABBR") or ""
        if not m:
            continue
        if not _is_real_contract(sc, code):
            continue
        by_contract.setdefault(code, {}).setdefault(sc, {}).setdefault(
            m, [0, 0, 0, 0])
        b = by_contract[code][sc][m]
        b[0] += int(x.get("LONG_POSITION") or 0)
        b[1] += int(x.get("SHORT_POSITION") or 0)
        b[2] += int(x.get("LP_CHANGE") or 0)
        b[3] += int(x.get("SP_CHANGE") or 0)
    out = {}
    for code in codes:
        contracts = by_contract.get(code)
        if not contracts:
            continue
        best_cc, best_long = None, -1
        for cc, mem in contracts.items():
            tot = sum(v[0] for v in mem.values())
            if tot > best_long:
                best_long, best_cc = tot, cc
        if not best_cc:
            continue
        top = sorted(contracts[best_cc].items(),
                     key=lambda kv: -(kv[1][0] + kv[1][1]))[:20]
        lt = sum(v[0] for _, v in top)
        st = sum(v[1] for _, v in top)
        lc = sum(v[2] for _, v in top)
        sc_chg = sum(v[3] for _, v in top)
        suffix = ("." + exch_map.get(code, "").split(".")[-1]) if "." in exch_map.get(code, "") else ""
        out[code] = {"date": (rows[0].get("TRADE_DATE") or "").replace("-", ""),
                     "net": int(lt - st), "long_total": int(lt),
                     "short_total": int(st), "long_chg": int(lc),
                     "short_chg": int(sc_chg),
                     "contract": str(best_cc).upper() + suffix}
    return out


def source_cached(codes, date):
    """判断某源（codes 列表）在 date 是否已全部存在于 agg 文件（用于回填时跳过已采集日）。"""
    for c in codes:
        p = load_agg(c)
        if not (p.get("data") or {}).get(date):
            return False
    return True


# 标准采样交易日窗口由调用方按回溯窗口动态生成；此处保留一份近一月参考窗口。
DATES = ["20261030","20261028","20261026","20261022","20261020",
         "20261016","20261014","20261012","20261008","20260930"]
