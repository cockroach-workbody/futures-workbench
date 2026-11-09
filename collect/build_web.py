#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建「线上版」工作台：
  1) web/  —— 可直接部署的精简应用（python 服务端 + 静态资源 + 裁剪后的数据）
  2) dist/期货分析工作台.html —— 单文件离线网页（CSS/JS/ECharts/数据全内联），
     便于存入资料库或用手机直接打开，无需服务端。

用法：
  python collect/build_web.py                # 默认保留最近 1000 根 K 线
  python collect/build_web.py --bars 1500    # 自定义 K 线根数
  python collect/build_web.py --no-html      # 只构建 web/
"""
import argparse
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 缠论引擎路径：优先用环境变量；本地有则用之，云端/其他机器回退到项目内可选目录（缺失则优雅降级）。
_CHANLUN_LOCAL = "C:/Users/zzq/.workbuddy/skills/chanlun/scripts"
CHANLUN_PATH = os.environ.get("WB_CHANLUN_PATH") or (
    _CHANLUN_LOCAL if os.path.isdir(_CHANLUN_LOCAL) else os.path.join(BASE, "chanlun")
)

# 动态快照日期：与 build_all.py 保持一致（最新已收盘交易日），支撑每日自动刷新。
from wb_common import snap_date as _snap_date
SNAP_DATE = _snap_date()
# 与后端 /api/market_divergence 共用同一份计算口径
sys.path.insert(0, os.path.join(BASE, "app"))
from market_view import compute_market_view  # noqa: E402

WEB_DIR = os.path.join(BASE, "web")
DIST_DIR = os.path.join(BASE, "dist")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- 缠论重算
def run_chanlun(kline_rows):
    """对裁剪后的 K 线重算缠论结构（保证笔/中枢索引与行数一致）。"""
    try:
        if CHANLUN_PATH not in sys.path:
            sys.path.insert(0, CHANLUN_PATH)
        from chan_analyze import analyze
        bars = [{"open": r["open"], "high": r["high"], "low": r["low"],
                 "close": r["close"], "volume": r["volume"]} for r in kline_rows]
        labels = [r["date"] for r in kline_rows]
        res = analyze(bars, labels=labels, strict_bars=True, use_macd=True)
        clean = {k: v for k, v in res.items() if not k.startswith("_")}
        clean["available"] = True
        return clean
    except Exception as e:  # noqa
        return {"available": False, "error": str(e),
                "note": "缠论引擎未挂载；K 线与均线仍可正常查看。"}


# ---------------------------------------------------------------- 数据裁剪
def slim_snapshot(snap, bars, recompute=True):
    """保留最近 bars 根 K 线，并同步裁剪均线、重算缠论。"""
    out = json.loads(json.dumps(snap))  # 深拷贝
    rows = out.get("kline", {}).get("rows") or []
    if bars and len(rows) > bars:
        n = len(rows)
        keep = rows[n - bars:]
        out["kline"]["rows"] = keep
        for key in ("ma5", "ma10"):
            arr = out["kline"].get(key)
            if isinstance(arr, list) and len(arr) >= n:
                out["kline"][key] = arr[n - bars:]
        out["kline"]["trimmed_from"] = n
        if recompute:
            out["chanlun"] = run_chanlun(keep)
    return out


def load_categories():
    with open(os.path.join(BASE, "app", "config", "products.json"), encoding="utf-8") as f:
        return json.load(f)


def iter_snapshots(cats):
    for c in cats["categories"]:
        for p in c["products"]:
            path = os.path.join(BASE, "data", c["name"], p["code"], SNAP_DATE + ".json")
            if os.path.exists(path):
                yield c["name"], p["code"], path


def write_json_compact(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- 1) web/
def build_web_dir(slim_map, cats):
    # 增量覆盖：不整目录删除 web/。部署目录可能含运行期资源（如 __wb_rsc），
    # 静态资源与快照均为固定文件名，逐一覆盖即可达到与重建相同的效果。
    os.makedirs(WEB_DIR, exist_ok=True)
    # 代码
    os.makedirs(os.path.join(WEB_DIR, "app"), exist_ok=True)
    shutil.copy2(os.path.join(BASE, "app", "main.py"), os.path.join(WEB_DIR, "app", "main.py"))
    shutil.copy2(os.path.join(BASE, "app", "market_view.py"), os.path.join(WEB_DIR, "app", "market_view.py"))
    shutil.copytree(os.path.join(BASE, "app", "static"), os.path.join(WEB_DIR, "app", "static"), dirs_exist_ok=True)
    shutil.copytree(os.path.join(BASE, "app", "config"), os.path.join(WEB_DIR, "app", "config"), dirs_exist_ok=True)
    # 数据（紧凑）
    for (cat, code), snap in slim_map.items():
        write_json_compact(os.path.join(WEB_DIR, "data", cat, code, SNAP_DATE + ".json"), snap)
    # 同步最近一次刷新状态（供页面顶部状态条展示，不依赖本地/桌面）
    # 不能整文件覆盖：cloud_cron 写入的「本次任务运行信息」(generated_at / mode /
    # summary) 不在 collect 状态里，粗暴 copy 会让顶部时间倒退回上次数据主跑。
    src_status = os.path.join(BASE, "collect", "refresh_status.json")
    dst_status = os.path.join(WEB_DIR, "refresh_status.json")
    if os.path.isfile(src_status):
        cur = {}
        if os.path.isfile(dst_status):
            try:
                with open(dst_status, encoding="utf-8") as f:
                    cur = json.load(f)
            except Exception:
                cur = {}
        try:
            with open(src_status, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        merged = dict(st)
        merged["data_refreshed_at"] = st.get("finished_at")
        for k in ("generated_at", "run_started_at", "run_finished_at",
                  "mode", "summary", "available", "python", "host"):
            if k in cur:
                merged[k] = cur[k]
        # 旧字段兼容：finished_at 指向最近一次任务运行，而非上一次数据主跑
        merged["started_at"] = cur.get("run_started_at") or cur.get("started_at") or st.get("started_at")
        merged["finished_at"] = cur.get("run_finished_at") or cur.get("generated_at") or st.get("finished_at")
        merged["available"] = True
        os.makedirs(os.path.dirname(dst_status), exist_ok=True)
        with open(dst_status, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    log(f"[OK] web/ 已生成：{len(slim_map)} 个品种")


# ---------------------------------------------------------------- 2) 单文件 HTML
def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def build_single_html(slim_map, cats, sentiment, divergence):
    os.makedirs(DIST_DIR, exist_ok=True)
    css = read(os.path.join(BASE, "app", "static", "styles.css"))
    echarts = read(os.path.join(BASE, "app", "static", "vendor", "echarts.min.js"))
    appjs = read(os.path.join(BASE, "app", "static", "app.js"))
    chartsjs = read(os.path.join(BASE, "app", "static", "charts.js"))
    index = read(os.path.join(BASE, "app", "static", "index.html"))

    # 取 index.html 的 body 内容（去掉 script 引用，改为内联）
    body = index.split("<body>", 1)[1].split("</body>", 1)[0]
    for tag in ('<script src="/static/vendor/echarts.min.js"></script>',
                '<script src="/static/charts.js"></script>',
                '<script src="/static/app.js"></script>'):
        body = body.replace(tag, "")

    snapshots = {f"{cat}/{code}": snap for (cat, code), snap in slim_map.items()}
    payload = {"categories": cats, "sentiment": sentiment, "divergence": divergence,
               "snapshots": snapshots, "as_of": SNAP_DATE}
    _payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 防御：内联数据若含 </script> 会导致浏览器提前结束 script 块、破坏整页；
    # 转义为 <\/script>（JSON 中 \/ 解析后仍回退为 /，浏览器不误判结束）。
    _payload_json = _payload_json.replace("</script>", "<\\/script>")
    data_js = "window.__WB__=" + _payload_json + ";"

    shim = r"""
/* 离线数据层：用内嵌数据替代后端 /api 接口 */
(function(){
  var D = window.__WB__ || {};
  var real = window.fetch ? window.fetch.bind(window) : null;
  function resolve(path){
    if (path === "/api/categories") return D.categories;
    if (path === "/api/sentiment_summary") return D.sentiment;
    if (path === "/api/market_divergence") return D.divergence || {total:0,count:0,danger_count:0,covered:0,aligned_count:0,neutral_count:0,caution_count:0,nodata_count:0,all:[],divergences:[]};
    var m = path.match(/^\/api\/snapshot\/([^/]+)\/([^/?]+)/);
    if (m) {
      var k = decodeURIComponent(m[1]) + "/" + decodeURIComponent(m[2]);
      return D.snapshots[k] || {error:"no_snapshot"};
    }
    if (path.indexOf("/api/recompute/") === 0) return {ok:true, offline:true};
    if (path.indexOf("/api/refresh_status") === 0)
      return {available:true, snap_date:D.as_of, mode:"offline",
              finished_at:"离线版（数据 "+D.as_of+"）"};
    return {};
  }
  window.fetch = function(u, o){
    var p = String(u);
    if (p.indexOf("/api/") === 0) {
      return Promise.resolve({ ok:true, json:function(){ return Promise.resolve(resolve(p)); } });
    }
    return real ? real(u, o) : Promise.reject(new Error("offline"));
  };
})();
"""

    html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="UTF-8" />\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=2.0" />\n'
        '<title>futures-workbench工作台（离线版）</title>\n'
        '<style>\n' + css + '\n</style>\n'
        '<script>\n' + echarts + '\n</script>\n'
        '</head>\n<body>\n'
        + body.replace('<span class="subtitle">基本面 · 技术面 · 观点与情绪 一体化</span>',
                       '<span class="subtitle">基本面 · 技术面 · 观点与情绪 一体化（离线版 · 数据 '
                       + SNAP_DATE + '）</span>')
        + '\n<script>\n' + data_js + '\n</script>\n'
        + '<script>\n' + shim + '\n</script>\n'
        + '<script>\n' + chartsjs + '\n</script>\n'
        + '<script>\n' + appjs + '\n</script>\n'
        + '</body>\n</html>\n'
    )
    out = os.path.join(DIST_DIR, "futures-workbench工作台.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    size = os.path.getsize(out) / 1024 / 1024
    log(f"[OK] 单文件网页已生成：{out}（{size:.1f} MB）")
    return out


# ---------------------------------------------------------------- 全市场资金背离
def compute_divergence(cats, slim_map):
    """全市场资金结构清单；调用 app/market_view.py，与后端接口 100% 同口径。"""
    def items():
        for c in cats["categories"]:
            cname = c["name"]
            for p in c["products"]:
                yield {"code": p["code"], "name": p.get("name", ""),
                       "category": cname, "snap": slim_map.get((cname, p["code"]))}
    return compute_market_view(items(), default_as_of=SNAP_DATE)


# ---------------------------------------------------------------- 情绪汇总
def compute_sentiment(cats, slim_map):
    totals = {"bullish": 0, "bearish": 0, "neutral": 0, "opinions": 0}
    by_category = {}
    ranking = []
    for c in cats["categories"]:
        cb = {"bullish": 0, "bearish": 0, "neutral": 0, "opinions": 0, "count": 0}
        for p in c["products"]:
            snap = slim_map.get((c["name"], p["code"])) or {}
            op = snap.get("opinion") or {}
            ss = op.get("stance_summary") or {}
            b = int(ss.get("bullish", 0) or 0)
            be = int(ss.get("bearish", 0) or 0)
            n = int(ss.get("neutral", 0) or 0)
            opc = int(op.get("web_opinions_count") or 0)
            for tgt in (totals, cb):
                tgt["bullish"] += b; tgt["bearish"] += be
                tgt["neutral"] += n; tgt["opinions"] += opc
            cb["count"] += 1
            # net 与后端 /api/sentiment_summary 同口径：看多 − 看空的计数差（不是比例）。
            # 榜单展示统一由前端按 bullish/bearish/neutral 现算比例，避免线上线下口径不一致。
            ranking.append({"code": p["code"], "name": p["name"], "category": c["name"],
                            "bullish": b, "bearish": be, "neutral": n,
                            "net": b - be, "opinions": opc})
        by_category[c["name"]] = cb
    ranking.sort(key=lambda x: (x["net"], x["bullish"]), reverse=True)
    return {"as_of": SNAP_DATE, "total_products": len(ranking), "totals": totals,
            "by_category": by_category, "ranking": ranking,
            "top_bullish": [r for r in ranking if r["net"] > 0][:12],
            "top_bearish": sorted([r for r in ranking if r["net"] < 0], key=lambda x: x["net"])[:12]}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=1000, help="保留最近 N 根 K 线（0=不裁剪）")
    ap.add_argument("--no-html", action="store_true", help="只构建 web/，不生成单文件 HTML")
    ap.add_argument("--html-bars", type=int, default=600, help="单文件版保留的 K 线根数")
    args = ap.parse_args()

    cats = load_categories()
    slim_map = {}
    for cat, code, path in iter_snapshots(cats):
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        slim_map[(cat, code)] = slim_snapshot(snap, args.bars, recompute=True)
        log(f"  裁剪 {cat}/{code}: K线 {len(snap['kline']['rows'])} -> "
            f"{len(slim_map[(cat, code)]['kline']['rows'])}，缠论重算完成")

    build_web_dir(slim_map, cats)
    sentiment = compute_sentiment(cats, slim_map)
    divergence = compute_divergence(cats, slim_map)

    if not args.no_html:
        # 单文件版直接复用同一份裁剪数据（避免二次重算、保证缠论索引一致）
        build_single_html(slim_map, cats, sentiment, divergence)

    log("\n完成：web/ 可部署上线；dist/ 单文件网页可存入资料库。")


if __name__ == "__main__":
    main()
