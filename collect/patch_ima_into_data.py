import json, os, glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DATE = "2026-10-28"
n = 0
# 只更新本地 data 源（web/data 与 dist 由 build_web.py 基于 data 重算生成）
for snap in glob.glob(os.path.join(BASE, "data", "*", "*", SNAP_DATE + ".json")):
    code = os.path.basename(os.path.dirname(snap))
    ima_f = os.path.join(BASE, "collect", f"ima_push_{code}.json")
    items = json.load(open(ima_f, encoding="utf-8")) if os.path.exists(ima_f) else []
    s = json.load(open(snap, encoding="utf-8"))
    if items:
        s["ima_push"] = {
            "available": True, "items": items,
            "note": f"已合并 collect/ima_push_{code}.json（{len(items)} 条）；来源 IMA 知识库「期货研报」库，按更新时间排序（最新在上）。"
        }
    else:
        s["ima_push"] = {"available": False, "items": [],
                         "note": "IMA 资料推送未配置：暂无可展示的最近资料。"}
    json.dump(s, open(snap, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n += 1
print("patched data snapshots:", n)
