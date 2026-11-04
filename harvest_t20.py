# -*- coding: utf-8 -*-
"""
harvest_t20.py
读取 collect/raw_persist/ 下由 DeferExecuteTool 自动持久化（或手工保存）的
futures_rank 返回文件（*.txt / *.json），提取 result 行，按 (end_date, variety_code)
分组，写出 collect/raw/<end_date>_<code>.json，并聚合出 collect/_t20_part_D.json。

关键字段（每行）：
- end_date      交易日
- variety_code  品种（带后缀，如 RB.SHF）
- indicator_code 成交 / 持买 / 持卖
- indicator_volume / change_volume 指标数量(手) / 较上期增减量(手)

净持仓 = sum(持买 indicator_volume) - sum(持卖 indicator_volume)
"""
import json, os, glob

WORK = os.path.dirname(os.path.abspath(__file__))
PERSIST = os.path.join(WORK, "collect", "raw_persist")
RAW = os.path.join(WORK, "collect", "raw")
OUT = os.path.join(WORK, "collect", "_t20_part_D.json")
DATES = ["20260924", "20260925", "20260928", "20260929", "20260930"]

def tnum(x):
    try:
        f = float(x)
    except Exception:
        return 0
    return int(f) if f == int(f) else f

def extract_rows(text):
    try:
        obj = json.loads(text)
    except Exception:
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            try:
                obj = json.loads(text[i:j + 1])
            except Exception:
                return None
        else:
            return None
    if isinstance(obj, dict):
        if "result" in obj and isinstance(obj["result"], list):
            return obj["result"]
        if "content" in obj and isinstance(obj["content"], list):
            try:
                txt = obj["content"][0]["text"]
                return extract_rows(txt)
            except Exception:
                return None
    return None

def main():
    groups = {}
    files = glob.glob(os.path.join(PERSIST, "*.txt")) + glob.glob(os.path.join(PERSIST, "*.json"))
    files = sorted(set(files))
    print("persist files:", len(files))
    for fp in files:
        try:
            text = open(fp, encoding="utf-8").read()
        except Exception as e:
            print("  skip", fp, e)
            continue
        rows = extract_rows(text)
        if not rows:
            print("  no rows", fp)
            continue
        for r in rows:
            ed = r.get("end_date")
            vc = r.get("variety_code")
            if not ed or not vc:
                continue
            groups.setdefault((ed, vc), []).append(r)
    print("groups (date,variety):", len(groups))

    os.makedirs(RAW, exist_ok=True)
    for (ed, vc), rows in groups.items():
        code = str(vc).split(".")[0]
        p = os.path.join(RAW, f"{ed}_{code}.json")
        json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)

    by_code = {}
    dates_covered = set()
    codes_with_data = set()
    for (ed, vc), rows in groups.items():
        code = str(vc).split(".")[0]
        if ed not in DATES:
            continue
        lt = sum(tnum(r.get("indicator_volume")) for r in rows if r.get("indicator_code") == "持买")
        lc = sum(tnum(r.get("change_volume")) for r in rows if r.get("indicator_code") == "持买")
        st = sum(tnum(r.get("indicator_volume")) for r in rows if r.get("indicator_code") == "持卖")
        sc = sum(tnum(r.get("change_volume")) for r in rows if r.get("indicator_code") == "持卖")
        net = lt - st
        by_code.setdefault(code, []).append({
            "date": ed, "net": net,
            "long_total": lt, "long_chg": lc,
            "short_total": st, "short_chg": sc,
        })
        dates_covered.add(ed)
        codes_with_data.add(code)
    for c in by_code:
        by_code[c].sort(key=lambda e: e["date"])
    out = {"generated": "2026-10-31", "by_code": by_code}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", OUT)
    print("dates_covered:", sorted(dates_covered))
    print("codes_with_data:", len(codes_with_data), sorted(codes_with_data))
    for d in DATES:
        n = sum(1 for c, lst in by_code.items() if any(e["date"] == d for e in lst))
        print(f"  {d}: {n} codes")

if __name__ == "__main__":
    main()
