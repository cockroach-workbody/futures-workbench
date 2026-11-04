import json, glob, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "_t20_part_B.json")
PATTERN = os.path.join(BASE, "_call_*.json")


def main():
    files = sorted(glob.glob(PATTERN))
    if not files:
        print("No _call_*.json files to process.")
        return

    # (date, code) -> accumulators
    agg = {}
    processed = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        code = d["code"]
        date = d["date"]
        dr = d["dir"]
        result = d.get("result", [])
        key = (date, code)
        a = agg.setdefault(key, {"long_total": 0.0, "long_chg": 0.0,
                                 "short_total": 0.0, "short_chg": 0.0})
        for r in result:
            iv = float(r.get("indicator_volume") or 0)
            cv = float(r.get("change_volume") or 0)
            if dr == "long":
                a["long_total"] += iv
                a["long_chg"] += cv
            else:
                a["short_total"] += iv
                a["short_chg"] += cv
        processed.append((date, code, dr, len(result)))

    # Load or init output
    if os.path.exists(OUT):
        with open(OUT, "r", encoding="utf-8") as fh:
            out = json.load(fh)
    else:
        out = {"generated": "2026-10-31", "by_code": {}}
    out["generated"] = "2026-10-31"
    by = out.setdefault("by_code", {})

    n_written = 0
    for (date, code), a in agg.items():
        net = int(round(a["long_total"] - a["short_total"]))
        rec = {
            "date": date,
            "net": net,
            "long_total": int(round(a["long_total"])),
            "short_total": int(round(a["short_total"])),
            "long_chg": int(round(a["long_chg"])),
            "short_chg": int(round(a["short_chg"])),
        }
        lst = by.setdefault(code, [])
        lst[:] = [x for x in lst if x.get("date") != date]
        lst.append(rec)
        lst.sort(key=lambda x: x["date"])
        n_written += 1

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # Clean up processed raw call files
    for f in files:
        try:
            os.remove(f)
        except OSError:
            pass

    print("Processed raw files:", len(files))
    for p in processed:
        print("  ", p)
    print("Aggregated (date,code) entries:", n_written)
    print("Total codes in part_B:", len(by))
    print("part_B path:", OUT)


if __name__ == "__main__":
    main()
