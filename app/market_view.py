#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场资金结构清单 —— 后端 /api/market_divergence 与离线单文件共用同一份口径。

两层判定：
  结构层：机构 / 散户净持仓反向（分歧）、同向且双方显著（一致偏多 / 一致偏空）、
          或至少一方不明显（结构不明）。
  价仓层：近 5 日「价格 × 持仓」四象限，判断行情是增量资金推动还是平仓 / 回补驱动。

关键口径：净持仓是当日榜单存量，不是当日资金流向；无分歧 ≠ 预期一致 ≠ 趋势行情。
一致偏多也可能是多头拥挤（增量买盘枯竭），必须用价格与持仓确认。
"""


def _i(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# 净持仓占该组双边（毛）持仓比例低于此值，视为「方向不明显」，不能算进共识
MIN_NET_RATIO = 0.03
# 近 5 日价格 / 持仓变化的噪音阈值（%）
PX_EPS = 0.8
OI_EPS = 1.0


def price_oi(snap):
    """近 5 日价、量、仓读数（只用连续主连 K 线序列，换月前后不与分层历史混用）。"""
    rows = ((snap or {}).get("kline") or {}).get("rows") or []
    if not rows:
        return {}
    last = rows[-1]
    prev = rows[-6] if len(rows) >= 6 else rows[0]
    close, close5 = _f(last.get("close")), _f(prev.get("close"))
    oi, oi5 = _f(last.get("oi")), _f(prev.get("oi"))
    doi5 = oi - oi5
    vol = _f(last.get("volume"))
    hist = [_f(r.get("volume")) for r in rows[-21:-1]]
    hist = [v for v in hist if v > 0]
    avg_vol = sum(hist) / len(hist) if hist else 0.0
    return {
        "close": close,
        "chg_pct": round(_f(last.get("chg_pct")), 2),
        "px_5d": round((close / close5 - 1) * 100 if close5 else 0.0, 2),
        "oi": round(oi),
        "oi_5d": round(doi5),
        "oi_5d_pct": round(doi5 / oi5 * 100 if oi5 else 0.0, 2),
        "volume": round(vol),
        "vol_ratio": round(vol / avg_vol, 2) if avg_vol else 1.0,
    }


def _sig(group):
    """该组净持仓方向：+1 净多、-1 净空、0 方向不明显。"""
    gross = _f(group.get("long")) + _f(group.get("short"))
    net = _f(group.get("net"))
    if not gross or abs(net) / gross < MIN_NET_RATIO:
        return 0
    return 1 if net > 0 else -1


def _part(group, label, s):
    return label + ("净多" if s > 0 else "净空" if s < 0 else "方向不明")


def structure(inst, ret, available=True):
    """结构层：分歧 / 一致 / 结构不明 / 无数据。"""
    if not available:
        return "nodata", "暂无分层数据", 0, 0
    inst_net, ret_net = _f(inst.get("net")), _f(ret.get("net"))
    si, sr = _sig(inst), _sig(ret)
    inst_gross = _f(inst.get("long")) + _f(inst.get("short"))
    ret_gross = _f(ret.get("long")) + _f(ret.get("short"))
    inst_ratio = round(inst_net / inst_gross * 100 if inst_gross else 0.0, 1)
    ret_ratio = round(ret_net / ret_gross * 100 if ret_gross else 0.0, 1)
    # 沿用既有口径：只要两侧净持仓符号相反即为分歧（不做显著性过滤，保证与 ⑥/⑦ 预警一致）
    if (inst_net > 0 and ret_net < 0) or (inst_net < 0 and ret_net > 0):
        direction = "机构净多 / 散户净空" if inst_net > 0 else "机构净空 / 散户净多"
        return "divergence", direction, inst_ratio, ret_ratio
    if si and sr and si == sr:
        return ("aligned_long" if si > 0 else "aligned_short"), \
               ("一致净多" if si > 0 else "一致净空"), inst_ratio, ret_ratio
    direction = _part(inst, "机构", si) + " / " + _part(ret, "散户", sr)
    return "neutral", direction, inst_ratio, ret_ratio


def price_oi_signal(po, stance):
    """价仓层：四象限 + 结合结构层给出风险提示与一句话解读。"""
    px, doi = po.get("px_5d", 0.0), po.get("oi_5d_pct", 0.0)
    up_px, dn_px = px >= PX_EPS, px <= -PX_EPS
    up_oi, dn_oi = doi >= OI_EPS, doi <= -OI_EPS
    if up_px and up_oi:
        pq = "价涨仓增"
    elif up_px and dn_oi:
        pq = "价涨仓减"
    elif up_px:
        pq = "价涨仓平"
    elif dn_px and up_oi:
        pq = "价跌仓增"
    elif dn_px and dn_oi:
        pq = "价跌仓减"
    elif dn_px:
        pq = "价跌仓平"
    elif up_oi:
        pq = "价平仓增"
    elif dn_oi:
        pq = "价平仓减"
    else:
        pq = "价仓平稳"

    px_txt = f"近 5 日价格 {px:+.2f}%、持仓 {doi:+.2f}%"
    if pq == "价涨仓增":
        if stance == "aligned_long" and px >= 6:
            result = (pq, "趋势确认·防过热", "trend",
                      f"{px_txt}：增量资金进场推动，趋势延续性较强；但一致净多且涨幅已大，"
                      "多头趋于拥挤，需防获利回吐。")
        else:
            result = (pq, "趋势确认", "trend",
                      f"{px_txt}：增量资金进场推动，趋势由新资金确认，延续性相对较强。")
    elif pq == "价涨仓减":
        if stance == "aligned_short":
            result = (pq, "空头回补", "fade",
                      f"{px_txt}：一致净空却减仓上涨，多为空头集中回补（轧空）；"
                      "反转需增仓放量确认，不宜追高。")
        elif stance == "aligned_long":
            result = (pq, "警惕回落", "caution",
                      f"{px_txt}：一致净多却减仓上涨，涨势由空头回补 / 存量离场推动，"
                      "不是新多进场；增量买盘不足，涨幅易回吐。")
        else:
            result = (pq, "警惕回落", "caution",
                      f"{px_txt}：价涨但持仓下降，属平仓 / 回补驱动而非增量资金，"
                      "趋势动能偏弱，警惕回落。")
    elif pq == "价涨仓平":
        result = (pq, "增量不足", "fade",
                  f"{px_txt}：价格上行但持仓未增，缺少增量资金确认，涨势持续性存疑。")
    elif pq == "价跌仓增":
        result = (pq, "跌势延续", "bear",
                  f"{px_txt}：新空主动增仓，跌势有增量资金支撑，暂不宜抄底。")
    elif pq == "价跌仓减":
        result = (pq, "动能衰减", "fade",
                  f"{px_txt}：多头离场推动，跌势动能衰减，不宜追空，留意企稳。")
    elif pq == "价跌仓平":
        result = (pq, "跌势待确认", "none",
                  f"{px_txt}：价格下行但持仓未明显变化，缺少新空进场，跌势待确认。")
    elif pq == "价平仓增":
        result = (pq, "对峙增仓", "none",
                  f"{px_txt}：价格走平而持仓增加，多空同时进场对峙，方向待放量突破选择。")
    elif pq == "价平仓减":
        result = (pq, "减仓观望", "none",
                  f"{px_txt}：价格走平且持仓下降，资金离场观望，等待新驱动。")
    elif stance == "aligned_long":
        result = (pq, "多头拥挤", "caution",
                  f"{px_txt}：机构与散户同为净多，但价格未跟涨——多头持仓拥挤、"
                  "增量买盘不足，警惕获利回吐。")
    elif stance == "aligned_short":
        result = (pq, "空头拥挤", "fade",
                  f"{px_txt}：机构与散户同为净空，但价格未跟跌——空头拥挤，"
                  "警惕利多触发的集中回补反弹。")
    else:
        result = (pq, "观望", "none", f"{px_txt}：价格与持仓均无明显变化，资金面中性。")

    pq, flag, risk, note = result
    # 大幅增减仓常见于移仓换月，必须先排除再解读资金进出
    if abs(doi) >= 15:
        note += " 注意：5 日持仓变动超 15%，须先排除移仓换月。"
    return pq, flag, risk, note


def compute_market_view(items, default_as_of=""):
    """
    items: 可迭代的 {"code", "name", "category", "snap"}；snap 可为 None。
    返回全市场清单：divergences 仅分歧品种（兼容旧调用），all 为全部品种。
    """
    as_of_set, cf_as_of_set = set(), set()
    out = []
    for it in items:
        snap = it.get("snap") or {}
        if snap.get("as_of"):
            as_of_set.add(snap["as_of"])
        cf = snap.get("capital_flow") or {}
        available = bool(cf.get("available"))
        if cf.get("as_of"):
            cf_as_of_set.add(cf["as_of"])
        iv = cf.get("institutional_vs_retail") or {}
        inst, ret = iv.get("institutional") or {}, iv.get("retail") or {}
        inst_net, inst_chg = _i(inst.get("net")), _i(inst.get("net_chg"))
        ret_net, ret_chg = _i(ret.get("net")), _i(ret.get("net_chg"))
        stance, direction, inst_ratio, ret_ratio = structure(inst, ret, available)
        gap_chg = inst_chg - ret_chg
        widening = stance == "divergence" and (
            (inst_net > 0 and gap_chg > 0) or (inst_net < 0 and gap_chg < 0))
        magnitude = abs(inst_net) + abs(ret_net)
        po = price_oi(snap)
        pq, flag, risk, note = price_oi_signal(po, stance)
        tp = cf.get("total_position") or {}
        row = {
            "code": it["code"], "name": it.get("name", ""), "category": it.get("category", ""),
            "main_contract": cf.get("main_contract") or cf.get("contract") or "",
            "available": available, "stance": stance, "direction": direction,
            "level": ("danger" if widening else "warn") if stance == "divergence" else risk,
            "widening": widening, "risk": risk,   # risk 为价仓层结论，全品种统一，供「警惕回落」筛选
            "inst_net": inst_net, "inst_chg": inst_chg, "inst_ratio": inst_ratio,
            "ret_net": ret_net, "ret_chg": ret_chg, "ret_ratio": ret_ratio,
            "magnitude": magnitude,
            "total_position": _i(tp.get("latest")) or po.get("oi", 0),
            "chg_5d": _i(tp.get("chg_5d")) or po.get("oi_5d", 0),
            "pq": pq, "flag": flag, "note": note,
            **{k: po.get(k, 0) for k in ("close", "chg_pct", "px_5d", "oi_5d", "oi_5d_pct", "vol_ratio")},
        }
        if not available:
            row.update({"pq": pq, "flag": "—", "risk": "none", "level": "none",
                        "note": "暂无前 20 会员持仓排名，无法判断机构 / 散户结构；价仓象限仍可参考。"})
        out.append(row)

    # 分组排序：分歧（加大优先）→ 无分歧（需警惕回落优先）→ 无数据
    rank = {"caution": 0, "trend": 1, "bear": 2, "fade": 3, "none": 4}
    def key(r):
        if r["stance"] == "divergence":
            return (0, 0 if r["widening"] else 1, -r["magnitude"])
        if r["stance"] == "nodata":
            return (2, 0, 0)
        return (1, rank.get(r["risk"], 9), -abs(r.get("px_5d") or 0))
    out.sort(key=key)

    divergences = [r for r in out if r["stance"] == "divergence"]
    as_of = (sorted(cf_as_of_set)[-1] if cf_as_of_set else
             (sorted(as_of_set)[-1] if as_of_set else default_as_of))
    return {
        "as_of": as_of,
        "total": len(out),
        "covered": sum(1 for r in out if r["available"]),
        "count": len(divergences),                                   # 兼容旧字段
        "danger_count": sum(1 for r in divergences if r["widening"]),
        "aligned_count": sum(1 for r in out if r["stance"] in ("aligned_long", "aligned_short")),
        "neutral_count": sum(1 for r in out if r["stance"] == "neutral"),
        "caution_count": sum(1 for r in out if r["risk"] == "caution"),
        "nodata_count": sum(1 for r in out if r["stance"] == "nodata"),
        "divergences": divergences,                                   # 兼容旧字段
        "all": out,
    }
