#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货品种分析工作台 —— 零依赖后端（Python 标准库 http.server）

启动：  python app/main.py   （默认 http://127.0.0.1:8011）
接口：
  GET /                        工作台首页
  GET /static/<file>           静态资源
  GET /api/categories          板块与品种清单
  GET /api/snapshot/<cat>/<prod>?date=YYYY-MM-DD   读取最新/指定日期快照
  GET /api/market_divergence     全市场资金结构清单（分歧 + 无分歧 + 价仓风险提示）
  GET /api/recompute/<cat>/<prod>                  重新计算（RB 重跑构建器）
"""
import json
import os
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

# 与离线单文件共用同一份全市场资金结构计算口径（同目录模块，零第三方依赖）
from market_view import compute_market_view

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE, "app", "static")
CONFIG_PATH = os.path.join(BASE, "app", "config", "products.json")
DATA_ROOT = os.path.join(BASE, "data")
BUILD_SCRIPT = os.path.join(BASE, "collect", "build_all.py")

PORT = int(os.environ.get("PORT", "8011"))
# 线上部署要求绑定 0.0.0.0（本地可用 HOST=127.0.0.1 覆盖）
HOST = os.environ.get("HOST", "0.0.0.0")


def load_categories():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_snapshot(category, product, date=None):
    d = os.path.join(DATA_ROOT, category, product)
    if not os.path.isdir(d):
        return None, None
    files = [f for f in os.listdir(d) if f.endswith(".json") and f[:4].isdigit()]
    if not files:
        return None, None
    files.sort()
    target = (date + ".json") if date else files[-1]
    if target not in files:
        return None, files
    path = os.path.join(d, target)
    with open(path, encoding="utf-8") as f:
        return json.load(f), target


_SENT_CACHE = {"sig": None, "data": None}


def _data_signature():
    """数据目录最新修改时间，用于缓存失效判断。"""
    latest = 0.0
    for root, _dirs, files in os.walk(DATA_ROOT):
        for f in files:
            if f.endswith(".json"):
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
    return latest


# 刷新状态文件：cloud_cron.py 每次运行后写入，供页面顶部状态条展示。
STATUS_PATHS = [
    os.path.join(BASE, "refresh_status.json"),
    os.path.join(BASE, "web", "refresh_status.json"),
    os.path.join(BASE, "collect", "refresh_status.json"),
]


def read_refresh_status():
    for p in STATUS_PATHS:
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {"available": False}


def build_sentiment_summary():
    """聚合全部品种的机构研报观点多空立场，生成全市场情绪总览（带缓存）。"""
    sig = _data_signature()
    if _SENT_CACHE["sig"] == sig and _SENT_CACHE["data"] is not None:
        return _SENT_CACHE["data"]
    data = _compute_sentiment_summary()
    _SENT_CACHE["sig"] = sig
    _SENT_CACHE["data"] = data
    return data


def _compute_sentiment_summary():
    """聚合全部品种的机构研报观点多空立场，生成全市场情绪总览。"""
    cats = load_categories()
    totals = {"bullish": 0, "bearish": 0, "neutral": 0, "opinions": 0}
    by_category = {}
    ranking = []
    as_of_set = set()
    for c in cats.get("categories", []):
        cname = c["name"]
        cb = {"bullish": 0, "bearish": 0, "neutral": 0, "opinions": 0, "count": 0}
        for p in c.get("products", []):
            code = p["code"]
            name = p.get("name", "")
            snap, _fn = find_snapshot(cname, code)
            b = be = n = opc = 0
            if snap:
                if snap.get("as_of"):
                    as_of_set.add(snap["as_of"])
                op = snap.get("opinion") or {}
                ss = op.get("stance_summary") or {}
                b = int(ss.get("bullish", 0) or 0)
                be = int(ss.get("bearish", 0) or 0)
                n = int(ss.get("neutral", 0) or 0)
                opc = int(op.get("web_opinions_count") or 0)
            totals["bullish"] += b
            totals["bearish"] += be
            totals["neutral"] += n
            totals["opinions"] += opc
            cb["bullish"] += b
            cb["bearish"] += be
            cb["neutral"] += n
            cb["opinions"] += opc
            cb["count"] += 1
            ranking.append({"code": code, "name": name, "category": cname,
                            "bullish": b, "bearish": be, "neutral": n,
                            "net": b - be, "opinions": opc})
        by_category[cname] = cb
    ranking.sort(key=lambda x: (x["net"], x["bullish"]), reverse=True)
    top_bullish = [r for r in ranking if r["net"] > 0][:12]
    top_bearish = sorted([r for r in ranking if r["net"] < 0], key=lambda x: x["net"])[:12]
    as_of = sorted(as_of_set)[-1] if as_of_set else ""
    return {"as_of": as_of, "total_products": len(ranking), "totals": totals,
            "by_category": by_category, "ranking": ranking,
            "top_bullish": top_bullish, "top_bearish": top_bearish}


# ---------------------------------------------------------------------------
# 全市场资金结构清单：分歧品种 + 无分歧品种（结合价量仓判断趋势 / 回落风险）。
# 席位口径：前5席位 = 按 |净持仓| 排名前 5；其余席位 = 三榜合并去重后第 6 名及以后（不披露客户属性）。
# 计算口径统一放在 market_view.py，离线单文件复用，避免两边漂移。
# ---------------------------------------------------------------------------
_DIV_CACHE = {"sig": None, "data": None}


def build_market_divergence():
    """生成全市场资金结构清单（带数据签名缓存）。"""
    sig = _data_signature()
    if _DIV_CACHE["sig"] == sig and _DIV_CACHE["data"] is not None:
        return _DIV_CACHE["data"]
    data = _compute_market_divergence()
    _DIV_CACHE["sig"] = sig
    _DIV_CACHE["data"] = data
    return data


def _compute_market_divergence():
    """遍历全市场快照，交由 market_view 统一计算（后端与离线版同口径）。"""
    def items():
        for c in load_categories().get("categories", []):
            cname = c["name"]
            for p in c.get("products", []):
                snap, _fn = find_snapshot(cname, p["code"])
                yield {"code": p["code"], "name": p.get("name", ""),
                       "category": cname, "snap": snap}
    return compute_market_view(items())


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel):
        # 防目录穿越
        rel = rel.replace("\\", "/").lstrip("/")
        path = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            self._send(404, {"error": "not found"})
            return
        ctype = "text/html; charset=utf-8"
        if rel.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif rel.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif rel.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        parts = [p for p in path.split("/") if p]

        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path == "/api/categories":
            self._send(200, load_categories())
            return
        if path == "/api/sentiment_summary":
            self._send(200, build_sentiment_summary())
            return
        if path == "/api/market_divergence":
            self._send(200, build_market_divergence())
            return
        if path == "/api/refresh_status":
            self._send(200, read_refresh_status())
            return
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "snapshot":
            category, product = parts[2], parts[3]
            qs = urllib.parse.parse_qs(parsed.query)
            date = qs.get("date", [None])[0]
            if date:
                date = date.replace("-", "")
            snap, fn = find_snapshot(category, product, date)
            if snap is None:
                avail = fn if isinstance(fn, list) else []
                self._send(404, {"error": "no_snapshot", "available": avail,
                                 "message": f"{category}/{product} 暂无快照，请先采集。"})
                return
            snap["_file"] = fn
            self._send(200, snap)
            return
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "recompute":
            category, product = parts[2], parts[3]
            try:
                r = subprocess.run(["python", BUILD_SCRIPT, category, product],
                                   capture_output=True, text=True, cwd=BASE, timeout=180)
                ok = r.returncode == 0
                self._send(200, {"ok": ok, "returncode": r.returncode,
                                 "stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]})
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "error": str(e)})
            return

        self._send(404, {"error": "unknown route"})

    def log_message(self, fmt, *args):
        pass  # 静默


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"期货品种分析工作台已启动: http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}")
    print("按 Ctrl+C 停止。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
