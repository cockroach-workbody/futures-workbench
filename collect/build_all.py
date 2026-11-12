#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货品种分析工作台 —— 多品种快照构建器（数据驱动）

对每个品种读取 collect/<CODE>_daily.json（新浪财经主连日线，已落盘），
生成统一快照 data/<板块>/<CODE>/<date>.json，覆盖 9 个模块：
  ① 概览 ② 供需关系 ③ 库存 ④ 期限基差 ⑤ 产业利润
  ⑥ 资金面 ⑦ 前20多空 / AKShare 资金动向(capital_flow) ⑧ 缠论 ⑩ 观点情绪

数据策略：
  - K线(真实) + 缠论(真实) + 概览/资金面派生：来自新浪主连日线（全周期 OHLC）。
  - 前20 / 标准仓单 / 厂库：来自 NeoData（collect/raw -> parse_neodata.py -> neodata_<CODE>.json），
    全品种真实接入（主力合约按「持买合计最大」自动识别）。
  - 社会库存：经 collect/inventory_<CODE>.json 注入（Mysteel 周报等），未注入显示「待接入」。

运行：
  python collect/build_all.py                 # 全部品种
  python collect/build_all.py <板块> <CODE>    # 仅重建单个品种（供 /api/recompute 调用）
"""
import json
import os
import re
import sys
import statistics
from datetime import date, datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(BASE, "data")
# 缠论引擎路径：优先用环境变量；本地有则用之，云端/其他机器回退到项目内可选目录（缺失则优雅降级）。
_CHANLUN_LOCAL = "C:/Users/zzq/.workbuddy/skills/chanlun/scripts"
CHANLUN_PATH = os.environ.get("WB_CHANLUN_PATH") or (
    _CHANLUN_LOCAL if os.path.isdir(_CHANLUN_LOCAL) else os.path.join(BASE, "chanlun")
)
NEODATA_DIR = os.path.join(BASE, "collect")

# 动态快照日期：默认取「最新已收盘交易日」（collect/*_daily.json 末日期最大值），
# 可用环境变量 WB_SNAP_DATE=2026-10-30 显式覆盖。支撑每日自动刷新。
from wb_common import snap_date as _snap_date
SNAP_DATE = _snap_date()

# 近一月前20净持仓历史曲线：复用 inject_t20_history 的滚动窗口逻辑，
# 让 build_all 在构建快照时直接写入 top20.history（根治「重建后 history 丢失」问题）。
from inject_t20_history import build_history as _t20_build_history
RAWHIST_DIR = os.path.join(NEODATA_DIR, "raw_hist")


def load_agg_rawhist(code):
    """读取 collect/raw_hist/agg_<CODE>.json（前20全合约合计净持仓历史，每日滚动采集）。"""
    fp = os.path.join(RAWHIST_DIR, f"agg_{code}.json")
    if not os.path.exists(fp):
        return None
    try:
        return json.load(open(fp, "r", encoding="utf-8"))
    except Exception:
        return None


CAPFLOW_DIR = os.path.join(NEODATA_DIR, "raw_capflow")


def _norm8(d):
    return str(d or "").replace("-", "")[:8]


def all_months_rows(am, kline):
    """第⑦部分专用合成日线：价格取主力/主连（不同月份价格不可加总），
    量 / 仓取全部月份合计。严格按同一交易日 join，缺日即跳过，绝不跨日兜底。"""
    series = {_norm8(r.get("date")): r for r in (am.get("totals") or {}).get("series") or []}
    out = []
    for r in ((kline or {}).get("rows") or []):
        d = _norm8(r.get("date"))
        s = series.get(d)
        if not s:
            continue
        out.append({
            "date": d,
            "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
            "close": r.get("close"), "settle": r.get("settle"),
            "oi": s.get("hold"), "oi_chg": s.get("chg"),
            "volume": s.get("volume"), "chg_pct": r.get("chg_pct"),
        })
    return out


def apply_all_months(cf, am, kline):
    """把资金分析框架的数据源整体切到「品种全部月份合约」口径。
    字段名保持不变，前端占比 / 分层 / 速读卡无需改计算逻辑。"""
    d = _norm8(am.get("as_of"))
    sec = (am.get("data") or {}).get(d)
    if not sec:
        return cf
    tp = am.get("totals") or {}
    cf = dict(cf or {})
    cf["available"] = True
    cf["scope"] = "all_months"
    cf["source"] = am.get("source")
    cf["as_of"] = am.get("as_of")
    cf["all_months_note"] = am.get("note")
    cf["months"] = am.get("months") or []
    cf["month_stats"] = am.get("stats") or {}
    cf["rollover_daily"] = am.get("daily") or []
    cf["total_position"] = {k: tp.get(k) for k in
                            ("latest", "prev", "chg_1d", "chg_5d", "chg_20d", "series", "as_of")}
    for k in ("member_count", "tiers", "institutional_vs_retail", "large_orders", "top_members",
              "long_total", "short_total", "total_oi", "months_with_rank", "months_hold"):
        if k in sec:
            cf[k] = sec[k]
    cf["all_months_rows"] = all_months_rows(am, kline)
    # contract / main_contract 保留主力合约代码：L4 移仓识别需要据其推算交割月
    return cf


def load_capflow(code, kline=None):
    """读取 collect/raw_capflow/capflow_<CODE>.json（AKShare 资金流采集器产出）。
    第7部分（资金动向）优先使用；缺失或 available=False 时前端回退到 NeoData top20。
    若存在 allmonths_<CODE>.json，则整体切到「品种全部月份」口径（用户选定口径）。"""
    fp = os.path.join(CAPFLOW_DIR, f"capflow_{code}.json")
    cf = None
    if os.path.exists(fp):
        try:
            cf = json.load(open(fp, "r", encoding="utf-8"))
        except Exception:
            cf = None
    amfp = os.path.join(CAPFLOW_DIR, f"allmonths_{code}.json")
    if os.path.exists(amfp):
        try:
            am = json.load(open(amfp, "r", encoding="utf-8"))
            if am.get("data") and am.get("totals", {}).get("series"):
                return apply_all_months(cf or {}, am, kline)
        except Exception:
            pass
    return cf

# ---------------------------------------------------------------------------
# 品种注册表（与 app/config/products.json 对应；前20/仓单/厂库统一由 NeoData 接入）
# ---------------------------------------------------------------------------
PRODUCTS = [
    # 黑色建材
    {"code": "RB", "name": "螺纹钢", "cat": "黑色建材", "var": "RB.SHF", "exch": "上海期货交易所",
     "sina": "RB0", "sub": {"code": "RB2703", "close": 3021.0},
     "qualitative_view": {"source": "中信建投期货研究（楚新莉）", "date": "20261011",
        "title": "节后钢材库存分化，螺纹热卷银十验证开启",
        "summary": "长假后黑色系低开消化累库预期，螺纹先下探后小幅修复，整体震荡。市场对节后累库、"
                   "工地开工延迟存担忧，终端持续性采购待观察。当前处于“弱现实+政策预期博弈”阶段："
                   "下方有钢厂亏损/主动减产托底，上方受累库与终端需求恢复不及预期压制，螺纹大概率区间震荡。"
                   "重点关注社库能否由增转降。"},
     "public_opinions": [
        {"title": "黑色系行情逻辑：1个视频讲透上下游", "source": "小Q的财经频道·东证期货", "date": "20261015",
         "url": "https://www.xiaohongshu.com/discovery/item/6ad09044000000000b005b6a"},
        {"title": "钢筋里的明星（螺纹钢入门）", "source": "七侠客", "date": "20261014",
         "url": "https://www.xiaohongshu.com/discovery/item/6acfa632000000001402cfd3"},
     ]},
    {"code": "HC", "name": "热卷", "cat": "黑色建材", "var": "HC.SHF", "exch": "上海期货交易所", "sina": "HC0"},
    {"code": "I",  "name": "铁矿石", "cat": "黑色建材", "var": "I.DCE", "exch": "大连商品交易所", "sina": "I0"},
    {"code": "J",  "name": "焦炭", "cat": "黑色建材", "var": "J.DCE", "exch": "大连商品交易所", "sina": "J0"},
    {"code": "JM", "name": "焦煤", "cat": "黑色建材", "var": "JM.DCE", "exch": "大连商品交易所", "sina": "JM0"},
    {"code": "FG", "name": "玻璃", "cat": "黑色建材", "var": "FG.ZCE", "exch": "郑州商品交易所", "sina": "FG0"},
    {"code": "SA", "name": "纯碱", "cat": "黑色建材", "var": "SA.ZCE", "exch": "郑州商品交易所", "sina": "SA0"},

    # 农产品
    {"code": "M",  "name": "豆粕", "cat": "农产品", "var": "M.DCE", "exch": "大连商品交易所", "sina": "M0"},
    {"code": "C",  "name": "玉米", "cat": "农产品", "var": "C.DCE", "exch": "大连商品交易所", "sina": "C0"},
    {"code": "SR", "name": "白糖", "cat": "农产品", "var": "SR.ZCE", "exch": "郑州商品交易所", "sina": "SR0"},
    {"code": "CF", "name": "棉花", "cat": "农产品", "var": "CF.ZCE", "exch": "郑州商品交易所", "sina": "CF0"},
    {"code": "A",  "name": "豆一", "cat": "农产品", "var": "A.DCE", "exch": "大连商品交易所", "sina": "A0"},
    {"code": "Y",  "name": "豆油", "cat": "农产品", "var": "Y.DCE", "exch": "大连商品交易所", "sina": "Y0"},
    {"code": "P",  "name": "棕榈油", "cat": "农产品", "var": "P.DCE", "exch": "大连商品交易所", "sina": "P0"},
    {"code": "RM", "name": "菜粕", "cat": "农产品", "var": "RM.ZCE", "exch": "郑州商品交易所", "sina": "RM0"},
    {"code": "OI", "name": "菜油", "cat": "农产品", "var": "OI.ZCE", "exch": "郑州商品交易所", "sina": "OI0"},
    {"code": "AP", "name": "苹果", "cat": "农产品", "var": "AP.ZCE", "exch": "郑州商品交易所", "sina": "AP0"},
    {"code": "JD", "name": "鸡蛋", "cat": "农产品", "var": "JD.DCE", "exch": "大连商品交易所", "sina": "JD0"},
    {"code": "CJ", "name": "红枣", "cat": "农产品", "var": "CJ.ZCE", "exch": "郑州商品交易所", "sina": "CJ0"},
    {"code": "LH", "name": "生猪", "cat": "农产品", "var": "LH.DCE", "exch": "大连商品交易所", "sina": "LH0"},

    # 化工品
    {"code": "TA", "name": "PTA", "cat": "化工品", "var": "TA.ZCE", "exch": "郑州商品交易所", "sina": "TA0"},
    {"code": "MA", "name": "甲醇", "cat": "化工品", "var": "MA.ZCE", "exch": "郑州商品交易所", "sina": "MA0"},
    {"code": "RU", "name": "橡胶", "cat": "化工品", "var": "RU.SHF", "exch": "上海期货交易所", "sina": "RU0"},
    {"code": "L",  "name": "塑料", "cat": "化工品", "var": "L.DCE", "exch": "大连商品交易所", "sina": "L0"},
    {"code": "PP", "name": "聚丙烯", "cat": "化工品", "var": "PP.DCE", "exch": "大连商品交易所", "sina": "PP0"},
    {"code": "BU", "name": "沥青", "cat": "化工品", "var": "BU.SHF", "exch": "上海期货交易所", "sina": "BU0"},
    {"code": "V",  "name": "PVC", "cat": "化工品", "var": "V.DCE", "exch": "大连商品交易所", "sina": "V0"},
    {"code": "UR", "name": "尿素", "cat": "化工品", "var": "UR.ZCE", "exch": "郑州商品交易所", "sina": "UR0"},

    # 有色金属
    {"code": "CU", "name": "铜", "cat": "有色金属", "var": "CU.SHF", "exch": "上海期货交易所", "sina": "CU0"},
    {"code": "AL", "name": "铝", "cat": "有色金属", "var": "AL.SHF", "exch": "上海期货交易所", "sina": "AL0"},
    {"code": "AU", "name": "黄金", "cat": "有色金属", "var": "AU.SHF", "exch": "上海期货交易所", "sina": "AU0"},
    {"code": "NI", "name": "镍", "cat": "有色金属", "var": "NI.SHF", "exch": "上海期货交易所", "sina": "NI0"},
    {"code": "ZN", "name": "锌", "cat": "有色金属", "var": "ZN.SHF", "exch": "上海期货交易所", "sina": "ZN0"},
    {"code": "AG", "name": "白银", "cat": "有色金属", "var": "AG.SHF", "exch": "上海期货交易所", "sina": "AG0"},
    {"code": "PB", "name": "铅", "cat": "有色金属", "var": "PB.SHF", "exch": "上海期货交易所", "sina": "PB0"},
    {"code": "SN", "name": "锡", "cat": "有色金属", "var": "SN.SHF", "exch": "上海期货交易所", "sina": "SN0"},

    # 广期板块（广州期货交易所·新能源金属）
    {"code": "SI", "name": "工业硅", "cat": "广期板块", "var": "SI.GFE", "exch": "广州期货交易所", "sina": "SI0"},
    {"code": "LC", "name": "碳酸锂", "cat": "广期板块", "var": "LC.GFE", "exch": "广州期货交易所", "sina": "LC0"},
    {"code": "PS", "name": "多晶硅", "cat": "广期板块", "var": "PS.GFE", "exch": "广州期货交易所", "sina": "PS0"},
    {"code": "PT", "name": "铂", "cat": "广期板块", "var": "PT.GFE", "exch": "广州期货交易所", "sina": "PT0"},
    {"code": "PD", "name": "钯", "cat": "广期板块", "var": "PD.GFE", "exch": "广州期货交易所", "sina": "PD0"},
]

CAT_TERMINAL = {
    "黑色建材": {"real_estate": "地产端偏弱，新开工同比承压；节后开工节奏是关键",
               "infra": "基建托底但平缓，资金到位为变量",
               "manufacturing": "制造业用钢韧性，汽车/家电出口形成支撑"},
    "农产品": {"real_estate": "饲料养殖需求（生猪存栏/禽类）主导",
              "infra": "储备/政策托市与进口到港节奏",
              "manufacturing": "食用消费 + 压榨替代 + 工业（淀粉/糖）"},
    "化工品": {"real_estate": "地产建材链（玻璃/纯碱/PVC）受竣工与资金影响",
              "infra": "基建与农需（尿素）托底",
              "manufacturing": "聚酯(PTA/乙二醇)/汽车/包装/纺织链"},
    "有色金属": {"real_estate": "地产后周期（铜/铝）待修复",
                "infra": "电网投资与风光装机拉动铜铝需求",
                "manufacturing": "新能源（锂电/电车/储能）+ 电力电子与家电支撑"},
    "广期板块": {"real_estate": "地产链关联弱，主要由新能源产业驱动",
               "infra": "光伏装机与电网投资拉动工业硅/多晶硅需求",
               "manufacturing": "新能源车与储能拉动碳酸锂；汽车尾气催化拉动铂钯"},
}


def load_neodata(code):
    """读取 collect/neodata_<CODE>.json（由 parse_neodata.py 从 NeoData 原始数据解析）。"""
    p = os.path.join(NEODATA_DIR, f"neodata_{code}.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def load_warehouse_daily(code):
    """读取 collect/warehouse_<CODE>.json（由 refresh_wh_daily.py 每日直连交易所官网采集）。
    仓单优先用此每日滚动数据；不存在时 build_product 回退到静态 neodata_<CODE>.json。"""
    p = os.path.join(BASE, "collect", f"warehouse_{code}.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        return d if d.get("standard") else None
    except Exception:
        return None


def _parse_opinion_date(s, ref):
    """解析观点日期为 date；无法判断的返回 None。特殊标记：
    - 含'年度/年报'或纯年份 -> 返回极旧日期(必被过滤)
    - 含'近期/最近' -> 视为最新(ref)
    """
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)  # 月初，偏严
        except Exception:
            return None
    if "年度" in s or "年报" in s or re.match(r"^\d{4}$", s):
        return date(2000, 1, 1)
    if "近期" in s or "最近" in s:
        return None  # 模糊标记不再伪造为最新；交由调用方按无效日期剔除
    return None


def is_recent_opinion(view, ref, days=30):
    d = _parse_opinion_date(view.get("date", ""), ref)
    if d is None:
        return False  # 无法判断日期的观点（含「近期/最近」模糊标记）不参与按日情绪
    return (ref - d).days <= days


# 动态可刷新来源族群（每次刷新只替换同族群，不影响其他族群）：与 apply_websearch_opinions.py 保持一致。
FRESH_SOURCE_FAMILIES = ("websearch全网", "金十期货", "东方财富期货资讯", "文华财经")


def _source_priority(v):
    """来源优先级：qhkch 直抓(3) > 动态可刷新门户(2) > 其余历史多源(1)。数值大者优先保留。"""
    if "qhkch.com" in (v.get("url") or ""):
        return 3
    src = v.get("source") or ""
    if src.startswith("websearch全网") or src in set(FRESH_SOURCE_FAMILIES):
        return 2
    return 1


def dedupe_views_by_institution(views):
    """按 (机构名, 日期, 多空立场) 跨源去重：同一期货公司同一天同一立场的观点只保留一条，
    优先保留来源更权威 / 摘要更完整的记录，避免同一观点在 qhkch / WebSearch / 金十期货 /
    东方财富期货资讯 / 文华财经 / Mysteel 等多源被重复计入。
    这是『标注期货公司机构名称避免数据重复』的核心实现——机构名即去重键。"""
    best = {}
    for v in views:
        inst = (v.get("institution") or "").strip()
        if not inst:
            continue
        key = (inst, (v.get("date") or "").strip(), v.get("stance"))
        score = (_source_priority(v), len(v.get("summary") or ""))
        if key not in best or score > best[key][0]:
            best[key] = (score, v)
    return [b[1] for b in best.values()]


def _valid_opinion_date(view):
    """返回可解析的 YYYY-MM-DD 观点日期；模糊/无法解析（近期/年度/空）返回 None。"""
    s = (view.get("date") or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def _parse_date_only(s):
    """把 'YYYY-MM-DD' / 'YYYYMMDD' 解析为 date，失败返回 None。"""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})$", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def opinion_series_for_history(views, hist):
    """把观点列表按历史曲线日期对齐，生成「累计 (看多−看空)/(看多+看空+中性)」比例序列（用于与 COT 净持仓做背离分析）。
    hist: [{date:'YYYYMMDD', ...}]（升序）；返回 [{date, net, bull, bear, neutral, z}] 或 None。
    net 为每个历史日之前（含）所有观点的情绪比例 = (看多 − 看空) / (看多 + 看空 + 中性)，区间 [-1,1]；z 为该序列的 z-score。"""
    parsed = []
    for v in views:
        d = _parse_date_only(v.get("date", ""))
        st = v.get("stance")
        if d and st in ("bullish", "bearish", "neutral"):
            parsed.append((d, st))
    if not parsed or not hist:
        return None
    out = []
    for h in hist:
        hd = _parse_date_only(h["date"])
        if not hd:
            continue
        bull = sum(1 for d, st in parsed if d <= hd and st == "bullish")
        bear = sum(1 for d, st in parsed if d <= hd and st == "bearish")
        neutral = sum(1 for d, st in parsed if d <= hd and st == "neutral")
        tot = bull + bear + neutral
        net = round((bull - bear) / tot, 4) if tot else 0.0
        out.append({"date": h["date"], "net": net, "bull": bull, "bear": bear, "neutral": neutral})
    if not out:
        return None
    nets = [o["net"] for o in out]
    if len(nets) >= 2:
        mu = statistics.mean(nets)
        sd = statistics.pstdev(nets) or 1
        for o in out:
            o["z"] = round((o["net"] - mu) / sd, 3) if sd else 0
    return out


def load_opinions(code):
    """读取 collect/opinions_<CODE>.json（由 WebSearch 抓取机构研报观点落盘）。
    返回规范化后的观点列表，每条含 source/institution/date/stance/title/summary/url。"""
    p = os.path.join(BASE, "collect", f"opinions_{code}.json")
    if not os.path.exists(p):
        return []
    try:
        data = json.load(open(p, encoding="utf-8"))
    except Exception:
        return []
    views = data.get("views") or []
    out = []
    for v in views:
        if not isinstance(v, dict):
            continue
        inst = v.get("institution") or ""
        src = v.get("source")
        if not src:
            # 来源缺失时推断：qhkch 链接→奇货可查；否则退回机构名
            if "qhkch.com" in (v.get("url") or ""):
                src = "奇货可查(qhkch)"
            else:
                src = inst or "未知来源"
        out.append({
            "source": src,
            "institution": inst,
            "date": v.get("date") or "",
            "stance": v.get("stance") or "neutral",   # bullish / bearish / neutral
            "title": v.get("title") or "",
            "summary": v.get("summary") or "",
            "url": v.get("url") or "",
        })
    return out


def moving_average(values, window):
    out = []
    for i in range(len(values)):
        out.append(round(sum(values[i + 1 - window:i + 1]) / window, 2) if i + 1 >= window else None)
    return out


def run_chanlun(kline_rows):
    try:
        if CHANLUN_PATH not in sys.path:
            sys.path.insert(0, CHANLUN_PATH)
        from chan_analyze import analyze
        bars = [{"open": r["open"], "high": r["high"], "low": r["low"],
                 "close": r["close"], "volume": r["volume"]} for r in kline_rows]
        labels = [r["date"] for r in kline_rows]
        res = analyze(bars, labels=labels, strict_bars=True, use_macd=True)
        clean = {k: v for k, v in res.items() if not k.startswith("_")}
        clean["available"] = True
        return clean
    except Exception as e:  # noqa
        return {"available": False, "error": str(e),
                "note": "本地缠论引擎未挂载；已在 K 线模块展示价格与均线，中枢/买卖点待引擎接入。"}


def load_kline(code):
    path = os.path.join(BASE, "collect", f"{code}_daily.json")
    if not os.path.exists(path):
        return None
    raw = json.load(open(path, encoding="utf-8"))
    rows, prev_c, prev_oi = [], None, None
    for r in raw:
        d = r["d"].replace("-", "")
        o, h, l, c = float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])
        v = float(r["v"]); oi = float(r["p"])
        sval = float(r.get("s") or 0); s = sval if sval else c
        oi_chg = round(oi - prev_oi, 0) if prev_oi is not None else 0.0
        chg = round((c - prev_c) / prev_c * 100, 4) if prev_c else 0.0
        rows.append({"date": d, "open": o, "high": h, "low": l, "close": c, "settle": s,
                     "oi": int(oi), "oi_chg": int(oi_chg), "volume": int(v), "chg_pct": chg})
        prev_c, prev_oi = c, oi
    return rows


def build_top20(cfg, rank_long, rank_short):
    if not rank_long or not rank_short:
        return {"available": False, "source": "neodata(未接入)", "members": [],
                "note": "前20会员多空对比待接入（NeoData futures_rank）。接入后按同名字段填充 member 表。"}
    long_total = sum(v for _, v, _ in rank_long)
    short_total = sum(v for _, v, _ in rank_short)
    long_chg = sum(c for _, _, c in rank_long)
    short_chg = sum(c for _, _, c in rank_short)
    net = long_total - short_total
    net_chg = long_chg - short_chg
    long_short_ratio = round(long_total / short_total, 4)
    net_long_pct = round(long_total / (long_total + short_total) * 100, 2)
    members = {}
    for name, vol, chg in rank_long:
        members.setdefault(name, {"name": name, "long": 0, "short": 0, "long_chg": 0, "short_chg": 0})
        members[name]["long"] = vol; members[name]["long_chg"] = chg
    for name, vol, chg in rank_short:
        members.setdefault(name, {"name": name, "long": 0, "short": 0, "long_chg": 0, "short_chg": 0})
        members[name]["short"] = vol; members[name]["short_chg"] = chg
    for m in members.values():
        m["net"] = m["long"] - m["short"]
    member_list = sorted(members.values(), key=lambda x: x["net"], reverse=True)
    add_rank = sorted(member_list, key=lambda x: (x["long_chg"] + x["short_chg"]), reverse=True)[:3]
    reduce_rank = sorted(member_list, key=lambda x: (x["long_chg"] + x["short_chg"]))[:3]
    return {"available": True, "source": "neodata", "contract": cfg.get("main") or cfg["code"] + "0(主连)",
            "long_total": long_total, "short_total": short_total, "net": net,
            "long_chg": long_chg, "short_chg": short_chg, "net_chg": net_chg,
            "long_short_ratio": long_short_ratio, "net_long_pct": net_long_pct,
            "members": member_list,
            "top_add": [{"name": m["name"], "chg": m["long_chg"] + m["short_chg"]} for m in add_rank],
            "top_reduce": [{"name": m["name"], "chg": m["long_chg"] + m["short_chg"]} for m in reduce_rank],
            "net_trend": [{"date": SNAP_DATE, "net": net}],
            "note": "前20会员口径为主力合约；净持仓=持买合计-持卖合计。趋势需多日连续采集构建。"}


def build_inventory(cfg, nd_wh):
    """库存模块：3 柱（标准仓单 / 社会库存 / 厂库）+ 较上期变化。
    nd_wh: parse_neodata 输出 {standard:{total,change,unit,date}, mill:{...}|None, breakdown:[...]}
    社会库存经 collect/inventory_<CODE>.json 注入（Mysteel 等），未注入显示待接入。
    """
    inv_file = os.path.join(BASE, "collect", f"inventory_{cfg['code']}.json")
    social = {"date": None, "total": None, "unit": "吨", "change": None,
              "note": "待接入（Mysteel 周报）"}
    if os.path.exists(inv_file):
        try:
            inv = json.load(open(inv_file, encoding="utf-8"))
            social.update(inv.get("social", {}))
        except Exception:
            pass
    standard = (nd_wh or {}).get("standard") or {"total": None, "change": None, "unit": "吨", "date": None}
    mill = (nd_wh or {}).get("mill")  # 可能 None（该品种无厂库）
    breakdown = (nd_wh or {}).get("breakdown") or []
    return {
        "standard": {"total": standard.get("total"), "change": standard.get("change"),
                     "unit": standard.get("unit") or "吨", "date": standard.get("date")},
        "social": {"total": social.get("total"), "change": social.get("change"),
                   "unit": social.get("unit") or "吨", "date": social.get("date")},
        "mill": ({"total": mill.get("total"), "change": mill.get("change"),
                  "unit": mill.get("unit") or "吨", "date": mill.get("date")} if mill else None),
        "breakdown": breakdown,
        "social_series": None, "yoy": None, "mom": None,
        "note": "标准仓单/厂库为交易所真实数据（单日快照，含较上期变化）；社会库存经 "
                "collect/inventory_%s.json 注入（未注入显示待接入）。3 柱对比：标准仓单 / 社会库存 / 厂库。" % cfg["code"],
    }


def build_basis(cfg, latest):
    sub = cfg.get("sub")
    if sub:
        spread = round(latest["close"] - sub["close"], 1)
        structure = "back" if spread > 0 else "contango"
        return {"spot": None, "main_contract": cfg.get("main") or cfg["code"] + "0(主连)",
                "main_close": latest["close"], "sub_contract": sub["code"], "sub_close": sub["close"],
                "spread": spread, "structure": structure,
                "structure_label": "近月升水(Back)" if spread > 0 else "远月升水(Contango)",
                "roll_yield": None, "note": "期限结构基于主力-次主力价差；现货基差需接入现货价后补全。"}
    return {"spot": None, "main_contract": cfg["code"] + "0(主连)", "main_close": latest["close"],
            "sub_contract": "（未配置）", "sub_close": None, "spread": None, "structure": None,
            "structure_label": "待接入(需次主力/现货)", "roll_yield": None,
            "note": "期限结构待接入次主力或现货价后补全（NeoData/交易所价差序列）。"}


def build_market_sentiment(cfg, net_info, structure, spread, warehouse_total, warehouse_change):
    cot_signal, net_long_pct, long_short_ratio, seat_bias = "待接入", None, None, "待接入"
    if net_info:
        net = net_info["net"]; long_chg = net_info["long_chg"]; short_chg = net_info["short_chg"]
        long_total = net_info["long_total"]; short_total = net_info["short_total"]
        cot_signal = "偏多" if (net > 0 and long_chg > short_chg) else ("偏空" if net < 0 else "中性")
        net_long_pct = round(long_total / (long_total + short_total) * 100, 2)
        long_short_ratio = round(long_total / short_total, 4)
        seat_bias = f"多头增仓 +{long_chg} / 空头增仓 +{short_chg}"
    struct_signal = "待接入"
    if structure is not None:
        struct_signal = "偏多" if structure == "back" else "偏空"
    # 综合情绪以持仓 COT（依赖前20会员）为主信号；期限结构为可选增强项。
    # 只要有 COT 信号即给出综合倾向，避免云端缺期限结构数据时整体显“待接入”。
    if cot_signal != "待接入":
        label = cot_signal
    else:
        label = "待接入"
    rc_signal = "中性" if warehouse_total is not None else "待接入"
    return {"label": label, "score": None,
            "cot": {"net_long_pct": net_long_pct, "long_short_ratio": long_short_ratio,
                    "seat_bias": seat_bias, "signal": cot_signal},
            "structure": {"regime": structure, "spread": spread, "signal": struct_signal},
            "receipt": {"total": warehouse_total, "change": warehouse_change, "signal": rc_signal},
            "option": {"available": False, "note": "期权 IV/PCR 增强项待接入。"},
            "news": {"available": False, "qualitative": cfg.get("qualitative_view") or {},
                     "note": "量化舆情需接入 NeoData news/social_opinion 连续流。"},
            "macro": {"available": False, "note": "宏观/板块资金情绪待接入 NeoData 宏观日历/板块资金。"},
            "note": "综合：持仓 COT 与期限结构为可计算信号，其余为待接入增强项。仅标注信号，不构成投资建议。"}


def build_product(cfg):
    rows = load_kline(cfg["code"])
    if not rows:
        return None
    closes = [r["close"] for r in rows]
    latest = rows[-1]; prev = rows[-2]
    kline_contract = f"{cfg['code']}0(主连)"
    kline = {"contract": kline_contract, "source": f"sina_{cfg['sina']}_main_continuous",
             "note": f"主连跨月连续（自动换月），{rows[0]['date']} 至今共 {len(rows)} 个交易日；"
                     f"用于缠论多年结构识别（笔/线段/中枢）。",
             "rows": rows, "ma5": moving_average(closes, 5), "ma10": moving_average(closes, 10)}

    # ① 概览
    sub = cfg.get("sub")
    spread = round(latest["close"] - sub["close"], 1) if sub else None
    vol_chg = latest["volume"] - prev["volume"]
    overview = {"main_contract": cfg.get("main") or kline_contract, "last": latest["close"],
                "prev_settle": prev["settle"], "chg_pct": latest["chg_pct"], "settle": latest["settle"],
                "oi": latest["oi"], "oi_chg": latest["oi_chg"], "volume": latest["volume"],
                "volume_chg": vol_chg, "basis": None, "basis_rate": None,
                "near_far_spread": spread,
                "near_far_label": ("近月升水(Back)" if spread > 0 else "远月升水(Contango)") if spread is not None else "—"}

    # ⑧ 缠论
    chanlun = run_chanlun(rows)

    # ⑦ 前20 / ⑥ 资金面 —— 统一从 NeoData 解析结果接入
    nd = load_neodata(cfg["code"])
    rank_long = rank_short = None
    warehouse_nd = None
    dominant = None
    if nd and nd.get("available"):
        members = nd.get("members") or []
        rank_long = [(m["name"], m.get("long") or 0, m.get("long_chg") or 0) for m in members]
        rank_short = [(m["name"], m.get("short") or 0, m.get("short_chg") or 0) for m in members]
        dominant = nd.get("dominant_contract")
    # 仓单与龙虎榜解耦：即使该品种暂无龙虎榜（如铂/钯），仓单数据仍要接入
    # 优先用每日滚动直连采集的 warehouse_<CODE>.json，回退到静态 neodata
    wh_daily = load_warehouse_daily(cfg["code"])
    if wh_daily:
        warehouse_nd = wh_daily
    elif nd and nd.get("warehouse"):
        warehouse_nd = nd.get("warehouse")
    if dominant:
        cfg = dict(cfg); cfg["main"] = dominant

    top20 = build_top20(cfg, rank_long, rank_short)
    # 近一月历史曲线：来源独立于当日前20（来自 raw_hist/agg 滚动采集），
    # 即使当日前20未接入（available=False）也应展示历史走势。
    agg = load_agg_rawhist(cfg["code"])
    if agg:
        hist = _t20_build_history(agg, SNAP_DATE)
        if hist:
            # z-score 归一化轴：以本窗口样本均值/标准差标准化净持仓，便于与观点净情绪叠加做背离分析
            nets = [h["net"] for h in hist]
            if len(nets) >= 2:
                mu = statistics.mean(nets)
                sd = statistics.pstdev(nets) or 1
                for h in hist:
                    h["z"] = round((h["net"] - mu) / sd, 3) if sd else 0
            top20["history"] = hist
    net_info = None
    if top20.get("available"):
        net_info = {"net": top20["net"], "net_chg": top20["net_chg"],
                    "long_chg": top20["long_chg"], "short_chg": top20["short_chg"],
                    "long_total": top20["long_total"], "short_total": top20["short_total"]}
    if net_info:
        bias = "偏多" if (net_info["net"] > 0 and net_info["long_chg"] > net_info["short_chg"]) else ("偏空" if net_info["net"] < 0 else "均衡")
        capital = {"oi": latest["oi"], "oi_chg": latest["oi_chg"], "volume": latest["volume"], "volume_chg": vol_chg,
                   "net_position": net_info["net"], "net_position_chg": net_info["net_chg"],
                   "net_long_pct": top20["net_long_pct"], "long_short_ratio": top20["long_short_ratio"],
                   "bias": bias, "note": "净持仓与多空增减均来自前20会员合计（主力合约）。"}
    else:
        capital = {"oi": latest["oi"], "oi_chg": latest["oi_chg"], "volume": latest["volume"], "volume_chg": vol_chg,
                   "net_position": None, "net_position_chg": None, "net_long_pct": None, "long_short_ratio": None,
                   "bias": None, "note": "净持仓(前20)待接入（NeoData futures_rank）。"}

    # ③ 库存 / ② 供需
    inventory = build_inventory(cfg, warehouse_nd)
    std = inventory["standard"]
    inv_change = std.get("change")
    if inv_change is not None:
        balance = "阶段性累库" if inv_change > 0 else ("阶段性去库" if inv_change < 0 else "库存暂稳（单日无变化，趋势待连续采集）")
    else:
        balance = "待接入（仓单数据待采集）"
    supply_demand = {"inventory": {
            "standard": {"total": std.get("total"), "change": std.get("change"),
                         "unit": std.get("unit"), "date": std.get("date")},
            "social": inventory["social"],
            "mill": inventory["mill"] if inventory["mill"] else {"total": None, "change": None,
                                                               "unit": std.get("unit"), "date": None}},
        "production": {"value": None, "unit": "万吨", "note": "周度产量待接入（aistockresearcher / IMA 供需表）"},
        "apparent_consumption": {"value": None, "unit": "万吨", "note": "表观消费量 = 产量 + 净进口 − 库存变化，待接入"},
        "demand_terminal": CAT_TERMINAL.get(cfg["cat"], {}),
        "balance": balance,
        "note": "供给端看利润与主动减产；需求端看终端。当前多数品种数据待接入，"
                "下方有成本/减产托底，上方受累库与需求恢复不及预期压制的框架可通用参考。"}

    # ④ 基差
    basis = build_basis(cfg, latest)

    # ⑤ 产业利润
    profit = {"model": f"{cfg['code'].lower()}_gross", "value": None, "pct_rank": None, "series": None,
              "status": "数据待接入",
              "formula": "盘面利润 ≈ 品种盘面价 − 成本端组合；现货利润 ≈ 现货价 − 成本。需接入成本端行情后按品种模型计算。",
              "note": "模型参数见 products.yaml；当前未配置成本端行情，暂以公式占位。"}

    # ⑩ 观点情绪
    market_sentiment = build_market_sentiment(cfg, net_info, basis.get("structure"), basis.get("spread"),
                                              std.get("total"), inv_change)
    web_views = load_opinions(cfg["code"])
    # 情绪数据「按机构去重 + 真实日期」：
    # ① 保留所有带真实期货公司机构名 + 真实日期(YYYY-MM-DD)的观点，不限来源
    #    （qhkch 直抓 / WebSearch 全网 / Mysteel 等聚合源皆可用）——解决「数据源偏少」；
    # ② 剔除无机构名或日期模糊（「近期/年度」等无法解析）的观点，避免永不刷新的存量垃圾污染最新情绪；
    # ③ 按 (机构, 日期, 多空) 跨源去重（dedupe_views_by_institution），同一期货公司观点在
    #    qhkch / WebSearch 全网 / 金十期货 / 东方财富期货资讯 / 文华财经 / Mysteel 多源被重复
    #    计入时只算一次——即『标注期货公司机构名避免数据重复』。
    web_views = [v for v in web_views if v.get("institution") and _valid_opinion_date(v) is not None]
    web_views = dedupe_views_by_institution(web_views)
    # 未来占位日期收拢：把晚于今天 2 天以上的观点（多为「11月报」写成 11-15 这类未来占位）收拢到今天，
    # 使其计入「今天」的 7 日滚动情绪，避免个别品种曲线画不到今天。
    _today = date.today()
    for _v in web_views:
        _od = _valid_opinion_date(_v)
        if _od and (_od - _today).days > 2:
            _v["date"] = _today.strftime("%Y-%m-%d")
    _ref = date(*(int(x) for x in SNAP_DATE.split("-")))
    web_views = [v for v in web_views if is_recent_opinion(v, _ref, 30)]
    stance_summary = {"bullish": 0, "bearish": 0, "neutral": 0}
    for _v in web_views:
        _st = _v.get("stance")
        if _st in stance_summary:
            stance_summary[_st] += 1
    # 观点净情绪序列（按历史曲线日期对齐，累计 (看多−看空)/(看多+看空+中性) 比例），用于与 COT 净持仓做背离分析
    if top20.get("history"):
        _op_series = opinion_series_for_history(web_views, top20["history"])
        if _op_series:
            top20["opinion_series"] = _op_series
    opinion = {"wind_alice": {"opinion": None, "daily": None, "quant": None, "available": False,
                "reason": "wind-finance 连接器未连接；连接后于 WorkBuddy 会话内运行三技能并落盘。"},
               "qualitative_view": cfg.get("qualitative_view") or {},
               "public_opinions": (cfg.get("public_opinions") or []) + web_views,
               "web_opinions_count": len(web_views),
               "stance_summary": stance_summary,
               "web_opinions_as_of": (web_views[0].get("as_of") if web_views and web_views[0].get("as_of") else None)}


    capital_flow = load_capflow(cfg["code"], kline)

    snap = {"product": cfg["code"], "name": cfg["name"], "category": cfg["cat"], "variety_code": cfg["var"],
            "exchange": cfg["exch"], "main_contract": cfg.get("main") or kline_contract, "as_of": SNAP_DATE,
            "data_date": latest["date"], "updated_at": SNAP_DATE + "T14:00:00",
            "kline": kline, "overview": overview, "inventory": inventory, "supply_demand": supply_demand,
            "basis": basis, "profit": profit, "capital": capital, "top20": top20,
            "capital_flow": capital_flow, "chanlun": chanlun,
            "market_sentiment": market_sentiment, "opinion": opinion}
    return snap


def main():
    arg_cat = sys.argv[1] if len(sys.argv) > 1 else None
    arg_code = sys.argv[2] if len(sys.argv) > 2 else None
    cfgs = PRODUCTS
    if arg_cat and arg_code:
        cfgs = [c for c in PRODUCTS if c["cat"] == arg_cat and c["code"] == arg_code]
        if not cfgs:
            print(f"[跳过] 未找到品种 {arg_cat}/{arg_code}")
            return

    done, skip = [], []
    for cfg in cfgs:
        out_dir = os.path.join(OUT_ROOT, cfg["cat"], cfg["code"])
        os.makedirs(out_dir, exist_ok=True)
        try:
            snap = build_product(cfg)
        except Exception as e:  # noqa
            print(f"[错误] {cfg['code']}: {e}")
            skip.append(cfg["code"])
            continue
        if snap is None:
            print(f"[跳过] {cfg['code']}: 缺少 collect/{cfg['code']}_daily.json")
            skip.append(cfg["code"])
            continue
        out_path = os.path.join(out_dir, f"{SNAP_DATE}.json")
        json.dump(snap, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        ch = snap["chanlun"]
        cnt = ch.get("center_count") if ch.get("available") else "-"
        print(f"[OK] {cfg['cat']}/{cfg['code']} {cfg['name']}: K线{len(snap['kline']['rows'])}日 "
              f"中枢{cnt} 笔{ch.get('bi_count') if ch.get('available') else '-'} "
              f"前20{'有' if snap['top20'].get('available') else '待接入'} "
              f"仓单{'有' if snap['inventory']['standard']['total'] is not None else '无'}")
        done.append(cfg["code"])

    print(f"\n完成：生成 {len(done)} 个，跳过 {len(skip)} 个（{skip or '无'}）")


if __name__ == "__main__":
    main()
