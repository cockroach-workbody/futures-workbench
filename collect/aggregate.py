import json, sys, os

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "_t20_part_B.json")

def aggregate_raw(raw_path):
    with open(raw_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    date = raw["date"]
    data = raw["data"]
    results = {}
    for code, dirs in data.items():
        long_rows = dirs.get("long", [])
        short_rows = dirs.get("short", [])
        long_total = sum(float(r["indicator_volume"]) for r in long_rows)
        long_chg = sum(float(r["change_volume"]) for r in long_rows)
        short_total = sum(float(r["indicator_volume"]) for r in short_rows)
        short_chg = sum(float(r["change_volume"]) for r in short_rows)
        net = long_total - short_total
        results[code] = {
            "date": date,
            "net": int(round(net)),
            "long_total": int(round(long_total)),
            "short_total": int(round(short_total)),
            "long_chg": int(round(long_chg)),
            "short_chg": int(round(short_chg)),
        }
    return results

def merge(out_path, results):
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            out = json.load(f)
    else:
        out = {"generated": "2026-10-31", "by_code": {}}
    by_code = out.setdefault("by_code", {})
    for code, rec in results.items():
        lst = by_code.setdefault(code, [])
        # replace if same date exists
        lst[:] = [x for x in lst if x.get("date") != rec["date"]]
        lst.append(rec)
        lst.sort(key=lambda x: x["date"])
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out

if __name__ == "__main__":
    raw_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "_raw_20261022_b1.json")
    res = aggregate_raw(raw_path)
    out = merge(OUT, res)
    print("Aggregated for date:", res and list(res.values())[0]["date"])
    for c, r in res.items():
        print(c, r)
    print("Total codes in output:", len(out["by_code"]))
