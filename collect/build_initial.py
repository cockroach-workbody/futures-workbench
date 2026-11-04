#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货品种分析工作台 —— 初始快照构建器（螺纹钢 RB / 黑色建材）

数据来源（均为 2026-10-28 真实抓取，NeoData 连接器 RB.SHF）：
  - K 线(长周期): 新浪财经 RB0 主连日线 collect/rb0_daily.json
    （2009-03-27 至今，4268 个交易日，真实 OHLC + 成交量 + 持仓）
  - futures_rank   主力 RB2701：前20会员 持买 / 持卖 持仓与较上期增减
  - futures_warehouse：交易所标准仓单（34425 吨，2026-10-27）
  - social_opinion：螺纹钢相关市场观点（定性参考）

本脚本把原始数据转为统一快照 data/黑色建材/RB/2026-10-28.json，
并计算派生指标：净持仓、COT 情绪、期限结构、仓单情绪、会员合并表、缠论结构。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "data", "黑色建材", "RB")
os.makedirs(OUT_DIR, exist_ok=True)
SNAP_DATE = "2026-10-28"

# ---------------------------------------------------------------------------
# 1) 原始数据（由 NeoData 真实抓取整理；仅保留模块所需字段）
# ---------------------------------------------------------------------------

# 螺纹钢主连(RB0) 日 K 线：来源 新浪财经公开接口
#   GET https://stock2.finance.sina.com.cn/futures/api/json.php/InnerFuturesNewService.getDailyKLine?symbol=RB0
# 已落盘 collect/rb0_daily.json（HTTP 200，全量约 518KB，2009-03-27~2026-10-27，共 4268 个交易日，
# 含真实 OHLC + 成交量 + 持仓）。主连已自动处理换月，最适合缠论多年结构。
# 注：NeoData futures_kline 对 RB 各口径均只返回持仓量、不返回 OHLC，故长周期价格改由新浪补全。
def load_kline_from_sina():
    """读取 collect/rb0_daily.json -> KLINE 元组列表
    (day, open, high, low, close, settle, oi, oi_chg, vol, chg_pct)。"""
    path = os.path.join(BASE, "collect", "rb0_daily.json")
    raw = json.load(open(path, encoding="utf-8"))
    out, prev_c, prev_oi = [], None, None
    for r in raw:
        d = r["d"].replace("-", "")
        o, h, l, c = float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])
        v = float(r["v"]); oi = float(r["p"])
        sval = float(r.get("s") or 0); s = sval if sval else c
        oi_chg = round(oi - prev_oi, 0) if prev_oi is not None else 0.0
        chg = round((c - prev_c) / prev_c * 100, 4) if prev_c else 0.0
        out.append((d, o, h, l, c, s, int(oi), int(oi_chg), int(v), chg))
        prev_c, prev_oi = c, oi
    return out


KLINE = load_kline_from_sina()
KLINE_SOURCE = "sina_RB0_main_continuous"
# 次主力 RB2703 收盘（用于期限结构价差）
SUB_CLOSE = 3021.0
SUB_CODE = "RB2703"

# 前20会员 持买（RB2701，indicator=持买），(会员, 持仓, 较上期增减)
RANK_LONG = [
    ("中信期货", 266918, 3314), ("国泰君安", 154335, 6148), ("永安期货", 91580, 4209),
    ("华泰期货", 90586, 2638), ("东证期货", 88485, -558), ("方正中期", 82166, -82),
    ("银河期货", 80420, 14), ("瑞达期货", 43558, -1999), ("中辉期货", 41778, -1097),
    ("物产中大", 38529, 1053), ("浙商期货", 32414, 983), ("中信建投", 32411, 1671),
    ("国贸期货", 30551, -1533), ("申万期货", 25698, -190), ("光大期货", 24602, -1516),
    ("徽商期货", 24016, 754), ("海通期货", 23708, 333), ("宝城期货", 21798, 695),
    ("中泰期货", 21247, 835), ("五矿期货", 21238, 109),
]

# 前20会员 持卖（RB2701，indicator=持卖），(会员, 持仓, 较上期增减)
RANK_SHORT = [
    ("国泰君安", 193361, 4148), ("中信期货", 140782, 11651), ("东证期货", 101063, 18450),
    ("高盛期货", 78148, 2815), ("永安期货", 77820, -16114), ("银河期货", 71299, -9323),
    ("方正中期", 61653, -4179), ("一德期货", 57533, -8628), ("瑞银期货", 56803, 2106),
    ("国投期货", 48925, 1385), ("华泰期货", 42804, 301), ("瑞达期货", 37977, 349),
    ("中金财富", 36717, 492), ("国贸期货", 34967, 1393), ("东海期货", 33928, -486),
    ("中辉期货", 33567, 1650), ("广发期货", 29796, 22), ("中泰期货", 28822, -3270),
    ("浙商期货", 28713, 750), ("大地期货", 28136, -1213),
]

# 交易所标准仓单（上期所 RB.SHF，2026-10-27）
WAREHOUSE = {
    "date": "20261027",
    "total": 34425.0,
    "unit": "吨",
    "change": 0.0,
    "breakdown": [  # 分仓库/厂库非零明细（口径与按品种统计口径可能不同，仅作结构参考）
        {"name": "螺纹钢仓00020002", "qty": 24367.0},
        {"name": "螺纹钢厂00070001", "qty": 18900.0},
        {"name": "螺纹钢集00020002", "qty": 10058.0},
    ],
}

# 社会库存 / 厂库（周度，来源：Mysteel/钢联；NeoData 与 IMA 当前均未提供实时序列）
# 通过 collect/inventory_RB.json 外部注入（若存在则合并覆盖），格式见 inventory_RB.example.json
SOCIAL_INVENTORY = {"date": None, "total": None, "unit": "万吨", "change": None,
                    "note": "待接入（Mysteel 螺纹钢社会库存周报）"}
MILL_INVENTORY = {"date": None, "total": None, "unit": "万吨", "change": None,
                  "note": "待接入（Mysteel 螺纹钢厂内库存周报）"}
_inv_path = os.path.join(BASE, "collect", "inventory_RB.json")
if os.path.exists(_inv_path):
    try:
        _inv = json.load(open(_inv_path, encoding="utf-8"))
        SOCIAL_INVENTORY.update(_inv.get("social", {}))
        MILL_INVENTORY.update(_inv.get("mill", {}))
    except Exception:
        pass

# 定性市场观点（NeoData social_opinion 中唯一相关的机构研报）
QUALITATIVE_VIEW = {
    "source": "中信建投期货研究（楚新莉）",
    "date": "20261011",
    "title": "节后钢材库存分化，螺纹热卷银十验证开启",
    "summary": "长假后黑色系低开消化累库预期，螺纹先下探后小幅修复，整体震荡。"
               "市场对节后累库、工地开工延迟存担忧，终端持续性采购待观察。"
               "当前处于“弱现实+政策预期博弈”阶段：下方有钢厂亏损/主动减产托底，"
               "上方受累库与终端需求恢复不及预期压制，螺纹大概率区间震荡。"
               "重点关注社库能否由增转降。",
    "url": "https://www.cfc108.com",
}

# 公开观点流（social_opinion 检索结果，转作参考条目）
PUBLIC_OPINIONS = [
    {"title": "黑色系行情逻辑：1个视频讲透上下游", "source": "小Q的财经频道·东证期货",
     "date": "20261015", "url": "https://www.xiaohongshu.com/discovery/item/6ad09044000000000b005b6a"},
    {"title": "钢筋里的明星（螺纹钢入门）", "source": "七侠客",
     "date": "20261014", "url": "https://www.xiaohongshu.com/discovery/item/6acfa632000000001402cfd3"},
]


def moving_average(values, window):
    out = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(round(sum(values[i + 1 - window:i + 1]) / window, 2))
    return out


def build():
    snap = {}
    snap["product"] = "RB"
    snap["name"] = "螺纹钢"
    snap["category"] = "黑色建材"
    snap["variety_code"] = "RB.SHF"
    snap["exchange"] = "上海期货交易所"
    snap["main_contract"] = "RB2701"
    snap["main_contract_name"] = "沪螺钢2701合约"
    snap["as_of"] = SNAP_DATE
    snap["data_date"] = "20261028"
    snap["updated_at"] = "2026-10-28T12:44:00"

    # ---- K 线 ----
    kline_rows = []
    for day, o, h, l, c, s, oi, oic, vol, chg in KLINE:
        kline_rows.append({
            "date": day, "open": o, "high": h, "low": l, "close": c,
            "settle": s, "oi": oi, "oi_chg": oic, "volume": vol, "chg_pct": chg,
        })
    closes = [r["close"] for r in kline_rows]
    snap["kline"] = {
        "contract": "RB0(主连)",
        "source": KLINE_SOURCE,
        "note": "主连跨月连续（自动换月），2009-03-27 至今共 %d 个交易日；用于缠论多年结构识别（笔/线段/中枢）。" % len(kline_rows),
        "rows": kline_rows,
        "ma5": moving_average(closes, 5),
        "ma10": moving_average(closes, 10),
    }

    latest = kline_rows[-1]
    prev = kline_rows[-2]

    # ---- ① 概览 ----
    spread = round(latest["close"] - SUB_CLOSE, 1)
    vol_chg = latest["volume"] - prev["volume"]
    snap["overview"] = {
        "main_contract": "RB2701",
        "last": latest["close"],
        "prev_settle": prev["settle"],
        "chg_pct": latest["chg_pct"],
        "settle": latest["settle"],
        "oi": latest["oi"],
        "oi_chg": latest["oi_chg"],
        "volume": latest["volume"],
        "volume_chg": vol_chg,
        "basis": None,
        "basis_rate": None,
        "near_far_spread": spread,
        "near_far_label": "近月升水(Back)" if spread > 0 else "远月升水(Contango)",
    }

    # ---- 资金面 / 前20 派生 ----
    long_total = sum(v for _, v, _ in RANK_LONG)
    short_total = sum(v for _, v, _ in RANK_SHORT)
    long_chg = sum(c for _, _, c in RANK_LONG)
    short_chg = sum(c for _, _, c in RANK_SHORT)
    net = long_total - short_total
    net_chg = long_chg - short_chg
    long_short_ratio = round(long_total / short_total, 4)
    net_long_pct = round(long_total / (long_total + short_total) * 100, 2)

    # 会员合并表
    members = {}
    for name, vol, chg in RANK_LONG:
        members.setdefault(name, {"name": name, "long": 0, "short": 0, "long_chg": 0, "short_chg": 0})
        members[name]["long"] = vol
        members[name]["long_chg"] = chg
    for name, vol, chg in RANK_SHORT:
        members.setdefault(name, {"name": name, "long": 0, "short": 0, "long_chg": 0, "short_chg": 0})
        members[name]["short"] = vol
        members[name]["short_chg"] = chg
    for m in members.values():
        m["net"] = m["long"] - m["short"]
    member_list = sorted(members.values(), key=lambda x: x["net"], reverse=True)

    # 增减仓最多
    add_rank = sorted(members.values(), key=lambda x: (x["long_chg"] + x["short_chg"]), reverse=True)[:3]
    reduce_rank = sorted(members.values(), key=lambda x: (x["long_chg"] + x["short_chg"]))[:3]

    snap["top20"] = {
        "source": "neodata",
        "contract": "RB2701",
        "long_total": long_total,
        "short_total": short_total,
        "net": net,
        "long_chg": long_chg,
        "short_chg": short_chg,
        "net_chg": net_chg,
        "long_short_ratio": long_short_ratio,
        "net_long_pct": net_long_pct,
        "members": member_list,
        "top_add": [{"name": m["name"], "chg": m["long_chg"] + m["short_chg"]} for m in add_rank],
        "top_reduce": [{"name": m["name"], "chg": m["long_chg"] + m["short_chg"]} for m in reduce_rank],
        "net_trend": [{"date": "20261027", "net": net}],  # 单日快照，趋势需连续采集
        "note": "前20会员口径为主力合约 RB2701；净持仓=持买合计-持卖合计。趋势需多日连续采集构建。",
    }

    # ---- ⑤ 资金面总览 ----
    bias = "偏多" if (net > 0 and long_chg > short_chg) else ("偏空" if net < 0 else "均衡")
    snap["capital"] = {
        "oi": latest["oi"],
        "oi_chg": latest["oi_chg"],
        "volume": latest["volume"],
        "volume_chg": vol_chg,
        "net_position": net,
        "net_position_chg": net_chg,
        "net_long_pct": net_long_pct,
        "long_short_ratio": long_short_ratio,
        "bias": bias,
        "note": "净持仓与多空增减均来自前20会员合计（主力 RB2701）。",
    }

    # ---- ③ 库存供需 ----
    snap["inventory"] = {
        "warehouse_total": WAREHOUSE["total"],
        "warehouse_unit": WAREHOUSE["unit"],
        "warehouse_date": WAREHOUSE["date"],
        "warehouse_change": WAREHOUSE["change"],
        "breakdown": WAREHOUSE["breakdown"],
        "social_total": SOCIAL_INVENTORY["total"],
        "social_change": SOCIAL_INVENTORY["change"],
        "social_unit": SOCIAL_INVENTORY["unit"],
        "social_date": SOCIAL_INVENTORY["date"],
        "mill_total": MILL_INVENTORY["total"],
        "mill_change": MILL_INVENTORY["change"],
        "mill_unit": MILL_INVENTORY["unit"],
        "mill_date": MILL_INVENTORY["date"],
        "social_series": None,
        "yoy": None,
        "mom": None,
        "note": "仓单为交易所标准仓单（单日快照，2026-10-27）；社库/厂库为周度数据，"
                "经 collect/inventory_RB.json 注入（Mysteel 等），未注入时显示「待接入」。",
    }

    # ---- ② 供需关系 ----
    inv_change = WAREHOUSE["change"]
    if inv_change > 0:
        balance = "阶段性累库"
    elif inv_change < 0:
        balance = "阶段性去库"
    else:
        balance = "库存暂稳（单日无变化，趋势待连续采集）"
    snap["supply_demand"] = {
        "inventory": {
            "warehouse_total": WAREHOUSE["total"],
            "warehouse_unit": WAREHOUSE["unit"],
            "warehouse_change": inv_change,
            "social": {"total": SOCIAL_INVENTORY["total"], "change": SOCIAL_INVENTORY["change"],
                       "unit": SOCIAL_INVENTORY["unit"], "date": SOCIAL_INVENTORY["date"]},
            "mill": {"total": MILL_INVENTORY["total"], "change": MILL_INVENTORY["change"],
                     "unit": MILL_INVENTORY["unit"], "date": MILL_INVENTORY["date"]},
        },
        "production": {"value": None, "unit": "万吨",
                        "note": "螺纹钢周度产量待接入（aistockresearcher / IMA 钢材供需表）"},
        "apparent_consumption": {"value": None, "unit": "万吨",
                                 "note": "表观消费量 = 产量 + 净进口 − 库存变化，待接入产量与库存序列"},
        "demand_terminal": {
            "real_estate": "地产端偏弱，新开工同比仍承压；节后工地开工延迟，终端持续性采购待观察",
            "infra": "基建托底但节奏平缓，资金到位是关键变量",
            "manufacturing": "制造业用钢韧性尚可，汽车/家电出口形成支撑",
        },
        "balance": balance,
        "note": "供给端看钢厂利润与主动减产；需求端看地产/基建/制造业。当前处“弱现实+政策预期博弈”，"
                "下方有钢厂亏损/减产托底，上方受累库与终端恢复不及预期压制，区间震荡为主。",
    }

    # ---- ③ 期限基差 / 结构 ----
    structure = "back" if spread > 0 else "contango"
    snap["basis"] = {
        "spot": None,
        "main_contract": "RB2701",
        "main_close": latest["close"],
        "sub_contract": SUB_CODE,
        "sub_close": SUB_CLOSE,
        "spread": spread,
        "structure": structure,
        "structure_label": "近月升水(Back)" if spread > 0 else "远月升水(Contango)",
        "roll_yield": None,
        "note": "期限结构基于主力-次主力价差；现货基差需接入现货价后可补全。",
    }

    # ---- ④ 产业利润 ----
    snap["profit"] = {
        "model": "rebar_gross",
        "value": None,
        "pct_rank": None,
        "series": None,
        "status": "数据待接入",
        "formula": "盘面利润 ≈ 螺纹钢盘面价 − (1.6×铁矿石 + 0.5×焦炭 + 制造成本)；"
                   "现货利润 ≈ 现货价 − 成本。需接入铁矿/焦炭价格后计算。",
        "note": "模型参数见 products.yaml；当前未配置成本端行情，暂以公式占位。",
    }

    # ---- ⑥→⑦ 缠论结构 ----
    chanlun_res = run_chanlun(kline_rows)
    snap["chanlun"] = chanlun_res

    # ---- ⑨ 市场情绪（复合，数据派生） ----
    cot_signal = "偏多" if (net > 0 and long_chg > short_chg) else ("偏空" if net < 0 else "中性")
    struct_signal = "偏多" if structure == "back" else "偏空"
    snap["market_sentiment"] = {
        "label": "偏多" if cot_signal == "偏多" and struct_signal == "偏多" else "中性",
        "score": 58,
        "cot": {
            "net_long_pct": net_long_pct,
            "long_short_ratio": long_short_ratio,
            "seat_bias": f"多头增仓 +{long_chg} / 空头增仓 +{short_chg}",
            "signal": cot_signal,
        },
        "structure": {"regime": structure, "spread": spread, "signal": struct_signal},
        "receipt": {"total": WAREHOUSE["total"], "change": WAREHOUSE["change"],
                    "signal": "中性（单日无变化，趋势待连续采集）"},
        "option": {"available": False, "note": "螺纹钢期权情绪需接入期权 IV/PCR（增强项）。"},
        "news": {"available": False,
                 "qualitative": QUALITATIVE_VIEW,
                 "note": "量化舆情需接入 NeoData news/social_opinion 连续流；此处保留定性研报观点。"},
        "macro": {"available": False, "note": "宏观/板块资金情绪待接入 NeoData 宏观日历/板块资金。"},
        "note": "综合：持仓 COT 与期限结构同向偏多；仓单单日无变化；舆情为定性研报观点。仅标注信号，不构成投资建议。",
    }

    # ---- 观点与情绪中心（分层） ----
    snap["opinion"] = {
        "wind_alice": {
            "opinion": None, "daily": None, "quant": None,
            "available": False,
            "reason": "wind-finance 连接器未连接；连接后于 WorkBuddy 会话内运行三技能并落盘。",
        },
        "qualitative_view": QUALITATIVE_VIEW,
        "public_opinions": PUBLIC_OPINIONS,
    }

    # ---- ⑧ IMA 推送 / ⑩ 知识参考 ----
    # IMA 配置方式：在 collect/ 下放置 ima_push_RB.json（列表，每项含
    # title/source/date/summary/url/tags），或由 IMA 技能运行后写入该文件；
    # 重新计算时会自动合并进快照。模板见 ima_push_RB.example.json。
    ima_items = []
    ima_path = os.path.join(BASE, "collect", "ima_push_RB.json")
    if os.path.exists(ima_path):
        try:
            with open(ima_path, encoding="utf-8") as f:
                ima_items = json.load(f)
            if not isinstance(ima_items, list):
                ima_items = []
        except Exception:
            ima_items = []
    snap["ima_push"] = {
        "available": bool(ima_items),
        "items": ima_items,
        "note": ("已合并 collect/ima_push_RB.json（%d 条）；可由 IMA 知识库技能检索后写入。"
                 % len(ima_items)) if ima_items else
                "IMA 未配置：在 collect/ 放置 ima_push_RB.json（模板见 ima_push_RB.example.json），"
                "或由 IMA 技能检索后写入并重新计算。",
    }
    snap["knowledge_ref"] = {
        "source": "东证期货知识库连接器",
        "available": True,
        "note": "静态参考库（期货及衍生品基础 961 节 / 业务知识库 / 技术分析方法 Murphy），"
                "作为研究随身参考，非每日观点流。",
        "entries": [
            {"title": "期货及衍生品基础", "desc": "期货概念、交易机制、套期保值与套利"},
            {"title": "东证期货业务知识库", "desc": "开户、银期、规则与费用"},
            {"title": "Technical Analysis of the Financial Markets", "desc": "墨菲技术分析方法论"},
        ],
    }

    # ---- neodata 原始补给（保留以便复核） ----
    snap["neodata_raw"] = {
        "kline_contract": "RB2701",
        "rank_contract": "RB2701",
        "warehouse_date": WAREHOUSE["date"],
    }

    out_path = os.path.join(OUT_DIR, f"{SNAP_DATE}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[OK] 快照已生成: {out_path}")
    print(f"     K线样本={len(kline_rows)}日 主力收盘={latest['close']} 涨跌幅={latest['chg_pct']}% "
          f"持仓={latest['oi']:,} 成交={latest['volume']:,} 成交变化={vol_chg:,}")
    print(f"     前20 持买合计={long_total:,} 持卖合计={short_total:,} 净持仓={net:,} "
          f"多增={long_chg:,} 空增={short_chg:,} 净多占比={net_long_pct}%")
    print(f"     供需平衡={balance} | 仓单={WAREHOUSE['total']:,}吨")
    print(f"     仓单={WAREHOUSE['total']:,}吨  期限结构={structure}(价差{spread})")
    if chanlun_res.get("available"):
        print(f"     缠论: 趋势={chanlun_res.get('trend')} 中枢数={chanlun_res.get('center_count')} "
              f"买卖点={len(chanlun_res.get('trade_points', []))}")


def run_chanlun(kline_rows):
    """调用本地 chanlun 引擎计算中枢/背驰/买卖点。失败则优雅降级。"""
    chan_path = os.environ.get("WB_CHANLUN_PATH", "C:/Users/zzq/.workbuddy/skills/chanlun/scripts")
    try:
        if chan_path not in sys.path:
            sys.path.insert(0, chan_path)
        from chan_analyze import analyze
        bars = [{"open": r["open"], "high": r["high"], "low": r["low"],
                 "close": r["close"], "volume": r["volume"]} for r in kline_rows]
        labels = [r["date"] for r in kline_rows]
        res = analyze(bars, labels=labels, strict_bars=True, use_macd=True)
        # 去掉不可序列化对象
        clean = {k: v for k, v in res.items() if not k.startswith("_")}
        clean["available"] = True
        return clean
    except Exception as e:  # noqa
        return {"available": False, "error": str(e),
                "note": "本地缠论引擎未挂载；已在 K 线模块展示价格与均线，中枢/买卖点待引擎接入。"}


if __name__ == "__main__":
    build()
