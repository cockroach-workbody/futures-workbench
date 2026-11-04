#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货分析工作台 —— WebSearch 全网机构观点「合并写入」助手

背景：
  多空情绪（机构观点）现在有两个自动数据源：
    1) collect/refresh_opinions_daily.py  —— 奇货可查(qhkch) 纯 HTTP 直抓（每日）
    2) 云端自动化「每日机构观点自动抓取」—— 基于 WebSearch 的全网检索（每日）
  两者都写 collect/opinions_<CODE>.json。为避免互相覆盖，统一按「来源」切分：
    - qhkch 来源：url 含 'qhkch.com'
    - WebSearch 来源：source 以 'websearch全网' 开头
    - 其余多源（Mysteel / 和讯 / 同花顺等历史手工源）：source 不含上述两项

本脚本只负责「安全合并」：
  - 永久保留 qhkch 来源观点（不被 WebSearch 覆盖）
  - 永久保留其余历史多源观点（不被 WebSearch 覆盖）
  - 仅替换「WebSearch 来源」那一份（每日刷新），其余不动
  → 因此 qhkch 与 WebSearch 两条流水线可独立、反复运行，互不丢数据。

用法（由 WebSearch 自动化调用）：
  1) LLM 用 WebSearch 全网检索各品种机构观点，抽取为结构化列表，
     写入 collect/_websearch_opinions.json，格式：
       { "RB": [ {institution,date,stance,title,summary,url,source}, ... ],
         "AU": [ ... ], ... }
  2) python collect/apply_websearch_opinions.py
     （自动读取 collect/_websearch_opinions.json，按来源合并写回 opinions_*.json）

安全机制：
  - 只更新 patch 中出现的 CODE；未出现的 CODE 原样不动（支持增量/部分运行）
  - 写回前断言 qhkch 条数不减少；若异常则跳过该 CODE 并报错，不破坏旧文件
  - 立场自动归一化（看多/强烈看多→bullish，看空/强烈看空→bearish，震荡/中性→neutral）
"""
import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECT_DIR = os.path.join(BASE, "collect")
PATCH_PATH = os.path.join(COLLECT_DIR, "_websearch_opinions.json")
WS_TAG = "websearch全网"

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


def is_ws_view(v):
    return (v.get("source") or "").startswith(WS_TAG)


def load_existing(code):
    path = os.path.join(COLLECT_DIR, f"opinions_{code}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa
        return None


def merge_into(old, new_views, today):
    """old: 已有 opinions dict 或 None；new_views: WebSearch 新抓列表。
    返回合并后的新 dict（含 qhkch 保留、多源保留、WebSearch 替换）。"""
    old_views = (old or {}).get("views") or []
    qhkch_kept = [v for v in old_views if is_qhkch_view(v)]
    other_kept = [v for v in old_views if not is_qhkch_view(v) and not is_ws_view(v)]
    # 新 WebSearch 观点：归一化 + 打标
    seen = set()
    ws_fresh = []
    for v in new_views:
        inst = (v.get("institution") or "").strip()
        date = (v.get("date") or today.strftime("%Y-%m-%d")).strip()
        stance = norm_stance(v.get("stance"))
        if not inst:
            continue
        key = (inst, date, stance)
        if key in seen:
            continue
        seen.add(key)
        ws_fresh.append({
            "institution": inst,
            "date": date,
            "stance": stance,
            "title": (v.get("title") or "").strip(),
            "summary": (v.get("summary") or "").strip(),
            "url": (v.get("url") or "").strip(),
            "source": (WS_TAG + "·" + (v.get("source") or "未知")) if v.get("source") else WS_TAG,
        })
    views = qhkch_kept + other_kept + ws_fresh
    views.sort(key=lambda v: v.get("date", ""), reverse=True)
    parts = ["奇货可查(qhkch)每日直抓"]
    if ws_fresh:
        parts.append("WebSearch全网")
    parts.append("多源保留")
    return {
        "code": (old or {}).get("code") or "",
        "as_of": today.strftime("%Y-%m-%d"),
        "source": " + ".join(parts),
        "views": views,
    }


def apply_patch(patch, today):
    summary = []
    for code, new_views in patch.items():
        code = code.strip().upper()
        if not isinstance(new_views, list):
            continue
        path = os.path.join(COLLECT_DIR, f"opinions_{code}.json")
        old = load_existing(code)
        # 写回前断言：qhkch 条数不减少
        old_qhkch = len([v for v in (old or {}).get("views", []) if is_qhkch_view(v)]) if old else 0
        merged = merge_into(old, new_views, today)
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
        n_ws = len([v for v in merged["views"] if is_ws_view(v)])
        n_other = len([v for v in merged["views"] if not is_qhkch_view(v) and not is_ws_view(v)])
        summary.append((code, True,
                        f"qhkch保留{new_qhkch} | WebSearch写入{n_ws} | 多源保留{n_other} | 合计{len(merged['views'])}"))
    return summary


def main():
    if not os.path.exists(PATCH_PATH):
        print(f"[错误] 未找到 patch 文件: {PATCH_PATH}")
        print("请先由 WebSearch 自动化检索并写入 collect/_websearch_opinions.json")
        return 2
    try:
        with open(PATCH_PATH, encoding="utf-8") as f:
            patch = json.load(f)
    except Exception as e:  # noqa
        print(f"[错误] patch 解析失败: {e}")
        return 2
    if not isinstance(patch, dict) or not patch:
        print("[错误] patch 为空或非字典格式")
        return 2
    today = datetime.date.today()
    print(f"==== WebSearch 全网观点合并（{today}）开始，待处理 {len(patch)} 个品种 ====")
    summary = apply_patch(patch, today)
    ok = sum(1 for _, s, _ in summary if s)
    for code, s, msg in summary:
        print(f"  {'+' if s else '!'} {code}: {msg}")
    print(f"==== 合并完成：{ok}/{len(summary)} 成功 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
