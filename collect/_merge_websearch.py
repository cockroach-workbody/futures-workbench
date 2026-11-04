#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 6 个 WebSearch 部分文件 -> collect/_websearch_opinions.json
只写入实际检索到观点的品种（不写空数组）。"""
import json, os, glob

BASE = os.path.dirname(os.path.abspath(__file__))
PARTS = ["black", "agria", "agrib", "chem", "metal", "gfex"]
merged = {}
for key in PARTS:
    fp = os.path.join(BASE, f"_websearch_partial_{key}.json")
    if not os.path.exists(fp):
        print(f"[跳过] 缺失部分文件: {fp}")
        continue
    data = json.load(open(fp, encoding="utf-8"))
    for code, views in data.items():
        if not isinstance(views, list) or not views:
            continue  # 不写空数组
        merged.setdefault(code, [])
        merged[code].extend(views)

out = os.path.join(BASE, "_websearch_opinions.json")
json.dump(merged, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
total = sum(len(v) for v in merged.values())
print(f"[OK] 合并完成：{len(merged)} 个品种，{total} 条观点 -> {out}")
for code in sorted(merged):
    print(f"  {code}: {len(merged[code])}")
