#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货分析工作台 —— 机构观点（多空情绪）每日直抓（奇货可查 qhkch）

数据源：奇货可查「机构观点」页
  https://x.qhkch.com/reports/viewpoint?variety=<品种中文名>
该页服务端直接渲染逐条观点（机构名 / 立场 / 强烈程度 / 发布日期 / 摘要 / 来源链接），
无需 JS 渲染、无需登录，纯 Python + 正则即可解析。内部 JSON 接口（/api/v2/aireportSummariesByDate）
经实测需未公开参数，统一走 HTML 解析更稳。

输出：覆盖式更新 collect/opinions_<CODE>.json
  合并策略：保留原文件中「非 qhkch 来源」的观点（Mysteel / 和讯 / 同花顺等多源），
            仅替换其中 qhkch 来源的部分（按 url 含 qhkch.com 识别），写入当日新抓的 qhkch 观点。
            → 既实现 qhkch 每日自动刷新，又不丢失历史手工/多源观点。

调用方式：
  python collect/refresh_opinions_daily.py                 # 抓取全部 41 品种
  python collect/refresh_opinions_daily.py --only RB AU    # 仅调试指定品种
  python collect/refresh_opinions_daily.py --no-merge      # 不保留旧多源观点，纯 qhkch

注意：最佳努力。单品种失败保留旧文件；全局不抛异常，由 refresh_daily 统一吞掉。
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECT_DIR = os.path.join(BASE, "collect")
sys.path.insert(0, COLLECT_DIR)

import build_all  # noqa: E402  (取 PRODUCTS / 品种中文名)

QHKCH_VIEWPOINT = "https://x.qhkch.com/reports/viewpoint?variety={}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://x.qhkch.com/"}

# qhkch 品种名与工作台显示名不一致的品种（多为上期所金属用「沪X」、RU 用「天然橡胶」、PP 用代码）。
# 其余品种直接用 cfg['name'] 即可命中。
QHKCH_NAME_OVERRIDE = {
    "RU": "天然橡胶", "PP": "PP", "CU": "沪铜", "AL": "沪铝", "AU": "沪金",
    "NI": "沪镍", "ZN": "沪锌", "AG": "沪银", "PB": "沪铅", "SN": "沪锡",
}
# 观点只保留最近 N 天，与 build_all.is_recent_opinion 的 30 天窗口一致（多留 15 天缓冲）
MAX_AGE_DAYS = 45

STANCE_MAP = {
    "看多": "bullish", "强烈看多": "bullish",
    "看空": "bearish", "强烈看空": "bearish",
    "震荡": "neutral", "中性": "neutral",
}


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ------------------------------------------------------------------ 抓取
def fetch_html(url, retries=3):
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            txt = raw.decode("utf-8", "ignore")
            if len(txt) < 2000:
                last_err = f"too-short({len(txt)})"
                time.sleep(1.5)
                continue
            return txt, None
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:  # noqa
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(2)
    return None, last_err


# ------------------------------------------------------------------ 解析
# 单条观点容器：<a class="h2 me-3 link-blue" href="...">机构名</a>
#   机构观点：<span class='loss h3'>看空</span>  (或纯文本 震荡)
#   强烈程度：中等
#   发布日期：YYYY-MM-DD
#   <div class="fw-bold"> 摘要 </div>
ITEM_RE = re.compile(
    r'<a class="h2 me-3 link-blue"[^>]*href="([^"]*)"[^>]*>(?P<inst>[^<]+)</a>'
    r'.*?机构观点：\s*(?:<span[^>]*>(?P<stance_s>[^<]+)</span>|(?P<stance_t>[^<]+?))\s*(?:</div>|<div)'
    r'.*?强烈程度：(?P<intensity>[^<]+?)\s*(?:</div>|<div)'
    r'.*?发布日期：(?P<date>\d{4}-\d{2}-\d{2})'
    r'.*?<div class="fw-bold">\s*(?P<summary>.*?)\s*</div>',
    re.S,
)


def parse_items(html, variety):
    items = []
    for m in ITEM_RE.finditer(html):
        inst = m.group("inst").strip()
        stance_raw = (m.group("stance_s") or m.group("stance_t") or "").strip()
        intensity = m.group("intensity").strip()
        date = m.group("date").strip()
        summary = re.sub(r"<[^>]+>", "", m.group("summary")).strip()
        broker_href = m.group(1).strip()
        stance = STANCE_MAP.get(stance_raw)
        if stance is None:
            # 兜底：含「多」偏多、含「空」偏空，其余中性
            if "多" in stance_raw:
                stance = "bullish"
            elif "空" in stance_raw:
                stance = "bearish"
            else:
                stance = "neutral"
        url = broker_href if broker_href.startswith("http") else ("https://x.qhkch.com" + broker_href)
        items.append({
            "institution": inst,
            "date": date,
            "stance": stance,
            "intensity": intensity,
            "title": f"{variety}机构观点(奇货可查)",
            "summary": summary,
            "url": url,
        })
    return items


# ------------------------------------------------------------------ 合并写回
def load_existing(code):
    path = os.path.join(COLLECT_DIR, f"opinions_{code}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa
        return None


def is_qhkch_view(v):
    u = (v.get("url") or "")
    return "qhkch.com" in u


def write_opinions(code, variety, new_views, merge=True, today=None):
    today = today or datetime.date.today()
    path = os.path.join(COLLECT_DIR, f"opinions_{code}.json")
    if merge:
        old = load_existing(code) or {}
        old_views = old.get("views") or []
        # 保留「非 qhkch」的旧观点（多源手工/其他来源），丢弃旧 qhkch 观点
        kept = [v for v in old_views if not is_qhkch_view(v)]
    else:
        kept = []
    # 去重（同名同日期的 qhkch 观点只留一条）
    seen = set()
    fresh = []
    for v in new_views:
        k = (v["institution"], v["date"], v["stance"])
        if k in seen:
            continue
        seen.add(k)
        fresh.append(v)
    views = fresh + kept
    # 按日期倒序
    views.sort(key=lambda v: v.get("date", ""), reverse=True)
    out = {
        "code": code,
        "as_of": today.strftime("%Y-%m-%d"),
        "source": "奇货可查(qhkch)每日直抓 + 多源保留",
        "views": views,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return len(fresh), len(kept)


# ------------------------------------------------------------------ 单品种
def refresh_one(code, variety, only=None, merge=True):
    if only and code not in only:
        return None
    variety = QHKCH_NAME_OVERRIDE.get(code) or variety
    url = QHKCH_VIEWPOINT.format(urllib.parse.quote(variety))
    html, err = fetch_html(url)
    if html is None:
        return {"code": code, "ok": False, "msg": f"抓取失败({err})，保留旧文件", "fresh": 0, "kept": 0}
    items = parse_items(html, variety)
    if not items:
        return {"code": code, "ok": True, "msg": "页面无观点（可能该品种暂无机构观点）", "fresh": 0, "kept": 0}
    today = datetime.date.today()
    # 仅保留近 MAX_AGE_DAYS 天
    cutoff = today - datetime.timedelta(days=MAX_AGE_DAYS)
    fresh = []
    for v in items:
        try:
            d = datetime.date(*(int(x) for x in v["date"].split("-")))
        except Exception:  # noqa
            d = today
        if d >= cutoff:
            fresh.append(v)
    nf, nk = write_opinions(code, variety, fresh, merge=merge, today=today)
    return {"code": code, "ok": True, "msg": f"新增qhkch {nf} 条，保留多源 {nk} 条", "fresh": nf, "kept": nk}


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="只刷新指定 CODE（调试用）")
    ap.add_argument("--no-merge", action="store_true", help="不保留旧多源观点，纯 qhkch 覆盖")
    args = ap.parse_args()

    started = datetime.datetime.now()
    log("==== 机构观点(多空情绪)每日直抓（qhkch）开始 ====")
    results = []
    for cfg in build_all.PRODUCTS:
        code = cfg["code"]
        variety = cfg.get("name") or code
        try:
            r = refresh_one(code, variety, only=args.only, merge=not args.no_merge)
        except Exception as e:  # noqa
            r = {"code": code, "ok": False, "msg": f"异常: {e}", "fresh": 0, "kept": 0}
        if r is None:
            continue
        results.append(r)
        flag = "+" if r["ok"] else "!"
        log(f"  {flag} {r['code']}({variety}): {r['msg']}")
        time.sleep(0.5)  # 礼貌限速

    ok = sum(1 for r in results if r["ok"])
    total_fresh = sum(r["fresh"] for r in results)
    elapsed = (datetime.datetime.now() - started).total_seconds()
    log(f"==== 观点直抓结束：{ok}/{len(results)} 成功，新增 qhkch 观点 {total_fresh} 条（耗时 {elapsed:.1f}s）====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
