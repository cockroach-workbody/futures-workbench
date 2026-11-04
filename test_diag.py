import sys, os, traceback, socket, urllib.request
sys.path.insert(0, r"C:/Users/zzq/.workbuddy/skills/aistockresearcher__skillhub")
os.environ["STOCK_RESEARCHER_DIRECT_CONNECT"] = "1"

print("=== 网络连通性测试 (timeout 8s) ===")
socket.setdefaulttimeout(8)
for url in ["https://www.shfe.com.cn", "https://hq.sinajs.cn",
            "https://push2his.eastmoney.com", "https://www.baidu.com"]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=8)
        print("  NET OK :", url)
    except Exception as e:
        print("  NET FAIL:", url, "->", repr(e)[:100])

print("\n=== FuturesDataClient.history 直接调用 ===")
try:
    from stock_researcher.research_data import FuturesDataClient
    print("  FuturesDataClient methods:", [m for m in dir(FuturesDataClient) if not m.startswith("_")][:20])
    for code in ["RB2601", "rb2601", "RB"]:
        try:
            s = FuturesDataClient.history(code, days=10)
            if s and getattr(s, "closes", None):
                print(f"  history({code}) OK -> closes={len(s.closes)} source={getattr(s,'source',None)}")
            else:
                print(f"  history({code}) -> 空/None (s={s})")
        except Exception as e:
            print(f"  history({code}) ERR -> {repr(e)[:160]}")
except Exception as e:
    print("  import/调用 ERR:")
    traceback.print_exc()

print("\n=== 顶层 analyze_futures 模块函数可用性 ===")
try:
    from stock_researcher.quantitative_futures_analyzer import analyze_futures, FuturesAnalyzer
    print("  analyze_futures OK, FuturesAnalyzer OK")
    fa = FuturesAnalyzer()
    print("  FuturesAnalyzer has predict_futures:", hasattr(fa, "predict_futures"))
    print("  FuturesAnalyzer has analyze_contract:", hasattr(fa, "analyze_contract"))
except Exception as e:
    print("  ERR:", repr(e))
