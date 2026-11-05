#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性脚本：清空 collect/raw_hist/agg_*.json 中的旧「全合约合计」数据，
使刷新流程（refresh_t20_daily.py --backfill）以新的「主力合约」口径重新回填。

设计要点：
  - 只清空 data（保留文件名与文件，避免 git 误删空文件导致云端曲线缺失）。
  - 幂等：重复运行无害。
  - 不改 snapshot / web / dist 等其它产物。

用法：
  python collect/reset_t20_agg.py            # 清空全部 agg 的 data
  python collect/reset_t20_agg.py --dry       # 仅打印将被清空的品种与点数，不写盘
"""
import json
import os
import sys
import glob

BASE = os.path.dirname(os.path.abspath(__file__))
RAWHIST = os.path.join(BASE, "raw_hist")


def main():
    dry = "--dry" in sys.argv
    files = sorted(glob.glob(os.path.join(RAWHIST, "agg_*.json")))
    total_points = 0
    for fp in files:
        code = os.path.basename(fp).replace("agg_", "").replace(".json", "")
        try:
            p = json.load(open(fp, encoding="utf-8"))
        except Exception:
            print(f"  [跳过] {code}: 解析失败")
            continue
        n = len(p.get("data") or {})
        total_points += n
        if dry:
            print(f"  [DRY] {code}: 将清空 {n} 个日期点")
            continue
        p["data"] = {}
        json.dump(p, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"  [OK] {code}: 已清空 {n} 个旧(全合约)日期点")
    print(f"\n完成（{'dry-run' if dry else '已写盘'}）。共处理 {len(files)} 个 agg 文件，"
          f"清空 {total_points} 个旧日期点。下一步运行 refresh_t20_daily.py --backfill 22 重新回填主力合约数据。")


if __name__ == "__main__":
    main()
