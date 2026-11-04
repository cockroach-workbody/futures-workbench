import sys, os
sys.path.insert(0, r"C:/Users/zzq/.workbuddy/skills/aistockresearcher__skillhub")
# 国内直连（腾讯/东财/新浪/期货所接口开代理反而失败）
os.environ["STOCK_RESEARCHER_DIRECT_CONNECT"] = "1"

def section(t): print("\n" + "=" * 60 + f"\n{t}\n" + "=" * 60)

# ---------- 1) 离线能力自检 ----------
section("1) 离线能力自检 (doctor --offline)")
try:
    from stock_researcher import get_router
    router = get_router()
    print("get_router OK ->", type(router).__name__)
except Exception as e:
    print("get_router FAIL:", repr(e))

# ---------- 2) 期货交易所适配器 ----------
section("2) FuturesExchangeAdapter 交易所解析")
try:
    from stock_researcher.data_multi_source import FuturesExchangeAdapter
    ad = FuturesExchangeAdapter()
    for c in ["RB2601", "HC2601", "CU2601", "M2501", "SR2501", "AU2502", "I2601"]:
        try:
            print(f"  {c:8s} -> {ad._resolve_exchange(c)}")
        except Exception as e:
            print(f"  {c:8s} -> ERR {e}")
except Exception as e:
    print("FuturesExchangeAdapter FAIL:", repr(e))

# ---------- 3) 期货行情/基差/持仓 在线查询 ----------
section("3) 期货在线查询 (行情/基差/持仓) — 螺纹钢 RB")
try:
    from stock_researcher.data_multi_source import FuturesExchangeAdapter
    ad = FuturesExchangeAdapter()
    # 尝试取主力合约行情（最新价/涨跌/持仓/成交）
    res = ad.query_realtime_quote("RB2601") if hasattr(ad, "query_realtime_quote") else None
    print("  query_realtime_quote(RB2601):", str(res)[:300] if res else "None / no method")
except Exception as e:
    print("  行情查询 ERR:", repr(e))

# ---------- 4) AI 期货分析模板 ----------
section("4) ai_analyze_futures 模板 (无网/无Key 降级)")
try:
    from stock_researcher import analyze
    r = analyze("futures_analysis", {
        "contract": "RB2601", "symbol": "RB", "exchange": "SHFE",
        "price": "3500", "change_pct": "0.8",
        "open_interest": "1800000", "volume": "2200000",
        "basis": "-50", "roll_yield": "1.2",
        "top5_long": "永安/中信", "top5_short": "国君/华泰",
    })
    print(type(r).__name__)
    print(getattr(r, "content", str(r))[:600])
except Exception as e:
    print("ai_analyze_futures ERR:", repr(e))

# ---------- 5) 列出期货相关可用函数 ----------
section("5) 期货相关 API 探测")
import stock_researcher as sr
names = [n for n in dir(sr) if not n.startswith("_")]
print("  top-level:", [n for n in names if "future" in n.lower() or "analy" in n.lower()][:20])
try:
    import stock_researcher.data_multi_source as dm
    fns = [n for n in dir(dm) if not n.startswith("_") and ("future" in n.lower() or "quote" in n.lower() or "basis" in n.lower() or "oi" in n.lower() or "position" in n.lower())]
    print("  data_multi_source futures fns:", fns[:30])
except Exception as e:
    print("  data_multi_source probe ERR:", repr(e))
