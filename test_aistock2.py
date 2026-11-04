import sys, os, json
sys.path.insert(0, r"C:/Users/zzq/.workbuddy/skills/aistockresearcher__skillhub")
os.environ["STOCK_RESEARCHER_DIRECT_CONNECT"] = "1"

def section(t): print("\n" + "=" * 60 + f"\n{t}\n" + "=" * 60)

# ===== 联网取真实期货K线（含持仓/成交） =====
section("A) query_futures_history 联网取数 — RB2601")
from stock_researcher import get_router
router = get_router()
qr = router.query_futures_history("RB2601", days=10)
print("success:", qr.success)
print("source :", getattr(qr, "source", None))
print("freshness:", getattr(qr, "freshness", None))
print("tried:", getattr(qr, "tried_sources", None))
data = getattr(qr, "data", None)
print("data type:", type(data).__name__)
if isinstance(data, dict):
    print("data keys:", list(data.keys())[:20])
    # 尝试提取序列
    for k in ("klines","data","rows","history","ohlc"):
        if k in data:
            print(f"  -> {k}: {type(data[k]).__name__}, len={len(data[k]) if hasattr(data[k],'__len__') else '?'}")
            if isinstance(data[k], list) and data[k]:
                print("     sample:", str(data[k][-1])[:200])
elif isinstance(data, list) and data:
    print("list len:", len(data))
    print("last row:", str(data[-1])[:300])
else:
    print("data payload:", str(data)[:300])

# 试着拿价格序列做分析
prices = None
if isinstance(data, dict):
    for k in ("close","closes","klines","data"):
        v = data.get(k)
        if isinstance(v, list) and v and all(isinstance(x,(int,float)) for x in v[:3]):
            prices = v; break
        if isinstance(v, list) and v and isinstance(v[0], dict):
            col = [r.get("close") or r.get("c") for r in v]
            if all(isinstance(x,(int,float)) for x in col if x is not None):
                prices = [x for x in col if x is not None]; break
if prices is None and isinstance(data, list) and data and isinstance(data[0], dict):
    col = [r.get("close") or r.get("c") for r in data]
    if all(isinstance(x,(int,float)) for x in col if x is not None):
        prices = [x for x in col if x is not None]
print("extracted prices count:", len(prices) if prices else 0)

# ===== 基于真实(或样本)价格做分析 =====
section("B) analyze_futures / predict_futures")
from stock_researcher.quantitative_futures_analyzer import analyze_futures, predict_futures, FuturesAnalyzer
if not prices or len(prices) < 30:
    import random
    random.seed(7)
    p = [3500.0]
    for _ in range(59):
        p.append(p[-1]*(1+random.gauss(0.0001,0.008)))
    prices = p
    print("(用合成价格做分析演示，因未取到足够真实序列)")
try:
    af = analyze_futures("RB", prices)
    print("analyze_futures:", json.dumps(af, ensure_ascii=False)[:500])
except Exception as e:
    print("analyze_futures ERR:", repr(e))
try:
    pf = predict_futures("RB", prices, horizon="medium")
    print("predict_futures:", json.dumps(pf, ensure_ascii=False)[:500])
except Exception as e:
    print("predict_futures ERR:", repr(e))

# ===== 基差结构 + 展期 =====
section("C) _basis_analysis / roll_yield_estimate")
fa = FuturesAnalyzer()
print("basis(spot=3550, fut=3500):", fa._basis_analysis(3550, 3500))
print("basis(spot=3500, fut=3550):", fa._basis_analysis(3500, 3550))
ry = fa.roll_yield_estimate([3550]*30, [3500]*30, days_to_expiry=30)
print("roll_yield:", json.dumps(ry, ensure_ascii=False))
