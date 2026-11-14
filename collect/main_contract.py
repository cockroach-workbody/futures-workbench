#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主力合约解析（换月标注）——期货工作台数据层组件。

问题背景
--------
原实现中：
  * 合约代码取自 collect/neodata_<CODE>.json 的 dominant_contract（一次性生成，日更不回写）；
  * 行情价格取自新浪主连 XX0（每日更新，跨月自动换月）。
两者不同源且无一致性校验，品种换月后即出现「价格是新合约、标注是旧合约」的错配，
并导致换月日涨跌幅含跨月跳空（例：JD 2026-11-12 显示 -7.86%，实际 +3.39%）。

本模块的判定口径
----------------
1) 客观主力：当日真实月合约中持仓量最大者（交易所口径）；
2) 价格归属：当日主连收盘价落在哪个真实月合约上（即行情实际取价合约）；
3) 展示主力：优先取「价格归属」，保证「标注 = 价格」；两者不一致时显式标注分歧；
4) 换月事件：由状态文件的历史序列判定「最近一次切换」，避免换月后隔天重建时误标换月日；
5) 无本地全月份数据时回退静态值，并置 stale 标记，避免静默沿用过期代码。

数据来源全部为本地文件（collect/raw_capflow/allmonths_<CODE>.json + collect/main_state.json），
不新增任何网络依赖。
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPFLOW_DIR = os.path.join(BASE, "collect", "raw_capflow")
STATE_PATH = os.path.join(BASE, "collect", "main_state.json")
PRICE_TOL = 1e-6
KEEP_DAYS = 60

_STATE = None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm_sym(sym, suffix):
    """合约代码归一化到展示口径。

    郑商所合约采用「品种+1位年份+2位月份」写法（FG701 = 2027年01月），
    而逐月数据源（新浪）用 4 位年份（FG2701）。不归一化会把同一合约
    误判为换月，因此统一转回交易所口径（与前端既有展示、NeoData 一致）。
    """
    if not sym or suffix != "ZCE":
        return sym
    head, _, tail = str(sym).partition(".")
    letters = "".join(ch for ch in head if ch.isalpha())
    digits = "".join(ch for ch in head if ch.isdigit())
    if len(digits) == 4:
        head = letters + digits[-3:]
    return head + (("." + tail) if tail else "")


def normalize_allmonths(am, suffix):
    """就地把全月份数据中的合约代码归一到展示口径。"""
    for m in am.get("months") or []:
        m["contract"] = norm_sym(m.get("contract"), suffix)
    for r in am.get("daily") or []:
        for key in ("months_hold", "months_volume"):
            if r.get(key):
                r[key] = {norm_sym(k, suffix): v for k, v in r[key].items()}
        if r.get("main_contract"):
            r["main_contract"] = norm_sym(r["main_contract"], suffix)
    if am.get("main_contract"):
        am["main_contract"] = norm_sym(am["main_contract"], suffix)
    return am


def load_allmonths(code):
    fp = os.path.join(CAPFLOW_DIR, "allmonths_%s.json" % code)
    if not os.path.exists(fp):
        return None
    try:
        am = json.load(open(fp, encoding="utf-8"))
    except Exception:
        return None
    return am if am.get("months") else None


def load_state(force=False):
    """加载换月状态（进程内缓存，避免逐品种重复读盘）。"""
    global _STATE
    if _STATE is not None and not force:
        return _STATE
    if os.path.exists(STATE_PATH):
        try:
            _STATE = json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            _STATE = {}
    else:
        _STATE = {}
    return _STATE


def save_state(state=None):
    state = _STATE if state is None else state
    if state is None:
        return
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def _pick_price_owner(months, close):
    """在当日各月合约中反查主连收盘价归属；同价多解时按成交量、持仓量降序消歧。"""
    if close is None:
        return None, []
    hits = [m for m in months if _f(m.get("close")) is not None
            and abs(_f(m["close"]) - float(close)) < PRICE_TOL]
    if not hits:
        return None, []
    hits.sort(key=lambda m: ((m.get("volume") or 0), (m.get("hold") or 0)), reverse=True)
    return hits[0]["contract"], [m["contract"] for m in hits]


def _oi_leader(months_hold):
    if not months_hold:
        return None, None
    sym = max(months_hold.items(), key=lambda kv: kv[1] or 0)[0]
    return sym, months_hold[sym]


def _last_roll(hist, as_of):
    """取「不晚于 as_of」的最近一次主力切换。

    返回 (上一交易日主力, 换月事件或 None, 该换月是否发生在 as_of 当天)。
    """
    dates = sorted(d for d in hist if d <= as_of)
    prev_day, last, cur = None, None, None
    for d in dates:
        c = (hist[d] or {}).get("contract")
        if not c:
            continue
        if cur is not None and c != cur:
            last = {"date": d, "from": cur, "to": c, "gap_pct": None, "reason": None}
        if d < as_of:
            prev_day = c
        cur = c
    return prev_day, last, bool(last and last["date"] == as_of)


def resolve(code, variety, kline_last, as_of, static_main=None, am=None, state=None):
    """解析当日主力合约、换月事件与标注文案。

    参数：code 品种代码；variety 品种全码（补交易所后缀）；kline_last 主连日线末行；
          as_of 快照日 YYYY-MM-DD；static_main 静态 dominant_contract（回退用）；
          am / state 可传入已加载对象，缺省自动加载。
    """
    as_of8 = str(as_of).replace("-", "")[:8]
    suffix = (variety or "").split(".")[-1]
    am = am if am is not None else load_allmonths(code)
    if am:
        am = normalize_allmonths(am, suffix)
    state = state if state is not None else load_state()
    hist = state.setdefault(code, {}) if state is not None else {}

    close = _f((kline_last or {}).get("c"))
    out = {"contract": None, "contract_full": None, "price_owner": None,
           "price_owner_candidates": [], "oi_leader": None, "oi_leader_hold": None,
           "source": None, "roll_event": None, "note": None, "price_basis": None,
           "stale": False, "prev_contract": None, "chg_pct_note": None}

    if not am:
        if static_main:
            sym = str(static_main).split(".")[0]
            out.update({"contract": sym, "contract_full": static_main,
                        "source": "static_fallback", "stale": True,
                        "note": "无可用的全月份持仓数据，主力合约为初始化静态值，换月后可能过期"})
        return out

    months = [m for m in am.get("months") or [] if m.get("hold")]
    oi_sym, oi_hold = _oi_leader({m["contract"]: m["hold"] for m in months})
    owner, cands = _pick_price_owner(months, close)

    if owner:
        sym, source = owner, "allmonths_price_match"
    elif oi_sym:
        sym, source = oi_sym, "allmonths_oi_leader"
    else:
        sym, source = None, "no_data"

    if not sym:
        return out

    full = sym if "." in sym else "%s.%s" % (sym, suffix)

    hist[as_of] = {"contract": sym, "price_owner": owner, "oi_leader": oi_sym,
                   "closes": {m["contract"]: _f(m.get("close")) for m in months}}
    for d in sorted(hist)[:-KEEP_DAYS]:
        hist.pop(d, None)

    prev, roll, is_today = _last_roll(hist, as_of)
    if roll:
        if is_today:
            closes = {m["contract"]: _f(m.get("close")) for m in months}
            old_c, new_c = closes.get(roll["from"]), closes.get(roll["to"])
            gap = round((new_c - old_c) / old_c * 100, 2) if (old_c and new_c) else None
            roll["gap_pct"] = gap
            roll["reason"] = "持仓量首次超越" if oi_sym == roll["to"] else "主连切换"
            out["chg_pct_note"] = (
                "主力换月首日：当前涨跌幅按主连跨月计算，含 %+.2f%% 跨月价差，"
                "不可与前一交易日直接比较（接续合约自身涨跌幅见分时/日线）" % gap
                if gap is not None else
                "主力换月首日：涨跌幅按主连跨月计算，含跨月价差，不可与前一交易日直接比较")
        else:
            # 换月发生在历史某日：仅标注路径，价差数值需当日留存，历史不回溯
            roll["reason"] = roll.get("reason") or None
        out["note"] = "%s 主力由 %s 换月至 %s%s" % (
            roll["date"], roll["from"], roll["to"],
            "（%s）" % roll["reason"] if roll.get("reason") else "")

    if owner and oi_sym and owner != oi_sym:
        div = "价格仍取 %s（主连尚未切换），持仓最大已为 %s" % (owner, oi_sym)
        out["note"] = (out["note"] + "；" + div) if out["note"] else div
    elif not owner and oi_sym:
        tail = "主连价格未能在当日合约中匹配，已按持仓最大合约标注"
        out["note"] = (out["note"] + "；" + tail) if out["note"] else tail

    out.update({
        "contract": sym, "contract_full": full, "price_owner": owner,
        "price_owner_candidates": cands, "oi_leader": oi_sym, "oi_leader_hold": oi_hold,
        "source": source, "roll_event": roll, "prev_contract": prev,
        "price_basis": "主连 %s0，当期对应 %s" % (code, sym),
    })
    return out


def roll_history(state, code, as_of=None):
    """状态序列中的全部换月事件（按日期升序），用于 K 线拼接点标注。"""
    hist = (state or {}).get(code) or {}
    out, cur = [], None
    for d in sorted(hist):
        if as_of and d > as_of:
            break
        c = (hist[d] or {}).get("contract")
        if not c:
            continue
        if cur and c != cur:
            out.append({"date": d, "from": cur, "to": c})
        cur = c
    return out


def annotate_rollover_daily(daily, hist, suffix=None):
    """按日重算全月份序列的「展示主力 / 主力持仓 / 主力持仓变化」。

    原实现每日 main_contract 一律沿用静态值，且 main_close（主连价）与 main_hold
    （该静态合约持仓）常分属两个不同合约。此处保证行内价格、持仓、变化同属一个合约；
    换月日另给 main_chg_note，避免跨合约持仓变化被误读。
    """
    out, prev_mh, prev_sym = [], None, None
    for r in daily or []:
        r = dict(r)
        mh = r.get("months_hold") or {}
        if suffix:
            mh = {norm_sym(k, suffix): v for k, v in mh.items()}
        d8 = str(r.get("date"))
        d = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:]) if len(d8) == 8 else d8
        oi_sym, _ = _oi_leader(mh)
        rec = hist.get(d) or {}
        sym = rec.get("contract") or oi_sym
        r["oi_leader"] = oi_sym
        r["main_contract_basis"] = "price_match" if rec.get("contract") else "oi_leader"
        if sym:
            r["main_contract"] = sym
            r["main_hold"] = mh.get(sym)
            if prev_mh and sym in prev_mh and mh.get(sym) is not None:
                r["main_chg"] = mh[sym] - prev_mh[sym]
            elif prev_mh:
                r["main_chg"] = None
            if prev_sym and sym != prev_sym:
                r["main_chg_note"] = "%s 主力由 %s 换月至 %s，持仓变化自换月日起重新起算" % (d, prev_sym, sym)
            r["other_hold"] = sum(v for k, v in mh.items() if k != sym)
        out.append(r)
        prev_mh, prev_sym = mh or prev_mh, (sym or prev_sym)
    return out


def mark_months(months, sym, suffix=None):
    """重算 months[].is_main（原实现沿用静态主力，导致标错）。"""
    out = []
    for m in months or []:
        m = dict(m)
        m["contract"] = norm_sym(m.get("contract"), suffix) if suffix else m.get("contract")
        m["is_main"] = (m.get("contract") == sym)
        out.append(m)
    return out
