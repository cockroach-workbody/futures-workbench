#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货分析工作台 —— 多源机构观点「合并写入」助手

背景：
  多空情绪（机构观点）现在有多个自动数据源，统一写 collect/opinions_<CODE>.json：
    1) collect/refresh_opinions_daily.py  —— 奇货可查(qhkch) 纯 HTTP 直抓（每日，覆盖式更新）
    2) 云端/本地自动化「每日机构观点自动抓取」—— 基于 WebSearch 的全网检索（每日）
    3) 金十期货 / 东方财富期货资讯 / 文华财经 —— 各门户机构观点（每日，可独立刷新）

  为避免互相覆盖，按「来源族群(family)」切分：
    - qhkch 来源：url 含 'qhkch.com'              → 永久保留（HTTP 直抓独立管线）
    - 动态可刷新来源(fresh)：WebSearch 全网 / 金十期货 / 东方财富期货资讯 / 文华财经
                                                  → 每次刷新仅替换「同族群」观点，不影响其他族群
    - 其余多源（Mysteel / 和讯 / 同花顺等历史手工源）→ 永久保留（多源保留）

本脚本只负责「安全合并」：
  - 永久保留 qhkch 来源观点
  - 永久保留其余历史多源观点
  - 仅替换「与本次补丁同族群」的动态可刷新观点，其余动态族群原样保留
  → 因此 qhkch / WebSearch / 金十 / 东方财富 / 文华 各条流水线可独立、反复运行，互不丢数据。

用法（由观点抓取自动化调用）：
  1) LLM 用 WebSearch / 门户检索各品种机构观点，抽取为结构化列表，
     写入补丁文件（默认 collect/_websearch_opinions.json），格式：
       { "RB": [ {institution,date,stance,title,summary,url,source}, ... ], ... }
  2) python collect/apply_websearch_opinions.py [--patch <补丁文件>] [--source <来源族群>]

  --source 可选：强制把补丁内所有观点的 source 设为该族群标签
                 （便于采集器只dump原始观点、由本脚本统一打标）。
  未指定 --source 时，沿用每条观点自带的 source 字段。

安全机制：
  - 只更新 patch 中出现的 CODE；未出现的 CODE 原样不动（支持增量/部分运行）
  - 写回前断言 qhkch 条数不减少；若异常则跳过该 CODE 并报错，不破坏旧文件
  - 立场自动归一化（看多/强烈看多→bullish，看空/强烈看空→bearish，震荡/中性→neutral）
  - 补丁内按 (机构, 日期, 多空) 去重，避免同一观点重复写入
"""
import argparse
import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECT_DIR = os.path.join(BASE, "collect")
PATCH_PATH = os.path.join(COLLECT_DIR, "_websearch_opinions.json")
WS_TAG = "websearch全网"

# 动态可刷新来源族群：每次刷新只替换同族群观点，不影响其他族群。
FRESH_SOURCES = {"websearch全网", "金十期货", "东方财富期货资讯", "文华财经"}

STANCE_MAP = {
    "看多": "bullish", "强烈看多": "bullish", "偏多": "bullish", "多": "bullish",
    "看空": "bearish", "强烈看空": "bearish", "偏空": "bearish", "空": "bearish",
    "震荡": "neutral", "中性": "neutral", "观望": "neutral",
}


def norm_stance(s):
    if s in ("bullish", "bearish", "neutral"):
        return s
    return STANCE_MAP.get((s or "").strip(), "neutral")


def is_qhkch_view(v):
    return "qhkch.com" in (v.get("url") or "")


def is_fresh_view(v):
    src = v.get("source") or ""
    if src.startswith(WS_TAG):
        return True
    return src in FRESH_SOURCES


def family_of(v):
    """把来源归一成「族群」：websearch 系列→websearch全网；其余→原样 source。"""
    src = v.get("source") or ""
    if src.startswith(WS_TAG):
        return WS_TAG
    return src


def load_existing(code):
    path = os.path.join(COLLECT_DIR, f"opinions_{code}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa
        return None


def merge_into(old, new_views, today, force_source=None):
    """old: 已有 opinions dict 或 None；new_views: 本次补丁新抓列表。
    返回合并后的新 dict：
      qhkch(永久) + 多源(永久) + 其他动态族群(保留) + 本次补丁(替换同族群)。"""
    old_views = (old or {}).get("views") or []
    qhkch_kept = [v for v in old_views if is_qhkch_view(v)]
    other_kept = [v for v in old_views if not is_qhkch_view(v) and not is_fresh_view(v)]

    # 归一化 + 打标 + 补丁内去重
    seen = set()
    fresh_new = []
    for v in new_views:
        inst = (v.get("institution") or "").strip()
        if force_source:
            src = force_source
        else:
            src = (v.get("source") or "").strip() or WS_TAG
        date = (v.get("date") or today.strftime("%Y-%m-%d")).strip()
        stance = norm_stance(v.get("stance"))
        if not inst:
            continue
        key = (inst, date, stance)
        if key in seen:
            continue
        seen.add(key)
        fresh_new.append({
            "institution": inst,
            "date": date,
            "stance": stance,
            "title": (v.get("title") or "").strip(),
            "summary": (v.get("summary") or "").strip(),
            "url": (v.get("url") or "").strip(),
            "source": src,
        })

    # 本次补丁覆盖的族群：仅替换这些族群的旧动态观点
    patch_families = {family_of(v) for v in fresh_new}
    fresh_kept = [v for v in old_views
                  if is_fresh_view(v) and family_of(v) not in patch_families]

    views = qhkch_kept + other_kept + fresh_kept + fresh_new
    views.sort(key=lambda v: v.get("date", ""), reverse=True)

    parts = ["奇货可查(qhkch)每日直抓"]
    if fresh_kept or fresh_new:
        fams = sorted({family_of(v) for v in (fresh_kept + fresh_new)})
        parts.append(" / ".join(fams))
    parts.append("多源保留")
    return {
        "code": (old or {}).get("code") or "",
        "as_of": today.strftime("%Y-%m-%d"),
        "source": " + ".join(parts),
        "views": views,
    }


def apply_patch(patch, today, force_source=None):
    summary = []
    for code, new_views in patch.items():
        code = code.strip().upper()
        if not isinstance(new_views, list):
            continue
        path = os.path.join(COLLECT_DIR, f"opinions_{code}.json")
        old = load_existing(code)
        # 写回前断言：qhkch 条数不减少
        old_qhkch = len([v for v in (old or {}).get("views", []) if is_qhkch_view(v)]) if old else 0
        merged = merge_into(old, new_views, today, force_source=force_source)
        if merged["code"] == "":
            merged["code"] = code
        new_qhkch = len([v for v in merged["views"] if is_qhkch_view(v)])
        if new_qhkch < old_qhkch:
            summary.append((code, False,
                            f"qhkch 条数异常({old_qhkch}→{new_qhkch})，跳过写回以保数据安全"))
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa
            summary.append((code, False, f"写回失败: {e}"))
            continue
        n_new = len([v for v in merged["views"]
                     if is_fresh_view(v) and family_of(v) in
                     {family_of(x) for x in new_views}]) if new_views else 0
        n_kept_fam = len([v for v in merged["views"]
                          if is_fresh_view(v) and family_of(v) not in
                          {family_of(x) for x in new_views}])
        n_other = len([v for v in merged["views"] if not is_qhkch_view(v) and not is_fresh_view(v)])
        tag = force_source or "WebSearch全网"
        summary.append((code, True,
                        f"qhkch保留{new_qhkch} | {tag}写入{len(new_views)} | 其他动态保留{n_kept_fam} | 多源保留{n_other} | 合计{len(merged['views'])}"))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", default=PATCH_PATH, help="补丁文件路径（默认 collect/_websearch_opinions.json）")
    ap.add_argument("--source", default=None,
                    help="强制把补丁内所有观点的 source 设为该族群标签（如 金十期货 / 东方财富期货资讯 / 文华财经）")
    args = ap.parse_args()

    patch_path = args.patch
    if not os.path.exists(patch_path):
        print(f"[错误] 未找到补丁文件: {patch_path}")
        print("请先由观点抓取自动化检索并写入补丁文件")
        return 2
    try:
        with open(patch_path, encoding="utf-8") as f:
            patch = json.load(f)
    except Exception as e:  # noqa
        print(f"[错误] 补丁解析失败: {e}")
        return 2
    if not isinstance(patch, dict) or not patch:
        print("[错误] 补丁为空或非字典格式")
        return 2
    today = datetime.date.today()
    tag = args.source or "WebSearch全网"
    print(f"==== 多源观点合并（{today}）开始，来源族群={tag}，待处理 {len(patch)} 个品种 ====")
    summary = apply_patch(patch, today, force_source=args.source)
    ok = sum(1 for _, s, _ in summary if s)
    for code, s, msg in summary:
        print(f"  {'+' if s else '!'} {code}: {msg}")
    print(f"==== 合并完成：{ok}/{len(summary)} 成功 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
