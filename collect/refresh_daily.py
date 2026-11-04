#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货分析工作台 —— 每日自动刷新编排器（开盘前执行）

流程：
  1) 抓取全部品种新浪主连日 K 线（强制刷新，失败保留旧文件）
  2) 计算快照日期 SNAP_DATE = 最新已收盘交易日（K 线末日期最大值）
  3) [可选] 若本会话已连接 neodata 连接器（CODEBUDDY_MCP_CONFIG 存在），
     重新抓取前20龙虎榜 + 标准仓单（最佳努力，失败跳过，保留上次好数据）
  3.5) 前20曲线 + 标准仓单 每日滚动（AKShare 直连交易所官网，最佳努力）
  3.6) 机构观点（多空情绪）每日直抓奇货可查 qhkch（最佳努力，失败保留旧文件）
  3.7) WebSearch 全网机构观点合并（最佳努力）：若 collect/_websearch_opinions.json
       补丁存在则按「来源」安全合并（保留 qhkch 与多源，仅替换 WebSearch 那份）；
       若不存在补丁则跳过——opinions_*.json 中已烘焙的 websearch 观点由 3.6 步骤保留。
       注意：WebSearch 实际检索需 AI 代理能力（见下「注意」），本脚本只负责合并补丁。
  4) 运行 build_all.py 生成 data/<板块>/<CODE>/<SNAP_DATE>.json
  5) 运行 build_web.py 生成 web/（线上部署源）+ dist/ 单文件
  6) 写 refresh_status.json 供前端/日志查看

调用方式：
  python collect/refresh_daily.py                # 完整刷新
  python collect/refresh_daily.py --no-neodata   # 跳过 neodata（纯 K 线+重建）
  python collect/refresh_daily.py --no-opinions  # 跳过机构观点(qhkch)每日直抓
  python collect/refresh_daily.py --no-websearch # 跳过 WebSearch 补丁合并
  python collect/refresh_daily.py --no-html      # 不重建 dist 单文件

注意（多空情绪两大自动数据源如何协同）：
  1) qhkch 直抓（collect/refresh_opinions_daily.py）：纯 HTTP 抓取，本脚本 3.6 步直接跑。
  2) WebSearch 全网检索：由 AI 代理（云端自动化「每日机构观点自动抓取」或本地代理对话）
     完成检索并写入 collect/_websearch_opinions.json 补丁，本脚本 3.7 步负责合并。
     WebSearch 是代理对话能力，Python 脚本本身无法发起检索——因此本地 PC 想补充 websearch，
     需先在代理对话里做一轮 WebSearch（会自动落盘补丁），再跑本脚本即可合并进去。
  两路数据经 apply_websearch_opinions.py 按「来源」切分，互不覆盖、互不丢数据。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECT_DIR = os.path.join(BASE, "collect")
sys.path.insert(0, COLLECT_DIR)

from wb_common import latest_kline_date, snap_date  # noqa: E402
import build_all  # noqa: E402  (取 PRODUCTS / SINA 映射)

SINA_URL = ("https://stock2.finance.sina.com.cn/futures/api/json.php/"
            "InnerFuturesNewService.getDailyKLine?symbol={}")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://finance.sina.com.cn/"}


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ------------------------------------------------------------------ K 线抓取
def fetch_kline(sym, retries=3):
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(SINA_URL.format(sym), headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode("utf-8", "ignore")
            i, j = txt.find("["), txt.rfind("]")
            if i < 0 or j <= i:
                last_err = "no-array"
                time.sleep(1.5)
                continue
            data = json.loads(txt[i:j + 1])
            if not isinstance(data, list) or len(data) < 30:
                last_err = f"short-n={len(data) if isinstance(data, list) else 0}"
                time.sleep(1.5)
                continue
            return data, None
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:  # noqa
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(2)
    return None, last_err


def refresh_klines(only=None):
    ok, skip, fail = [], [], []
    for cfg in build_all.PRODUCTS:
        code = cfg["code"]
        sym = cfg.get("sina")
        if not sym:
            skip.append(code)
            continue
        if only and code not in only:
            continue
        out_path = os.path.join(COLLECT_DIR, f"{code}_daily.json")
        data, err = fetch_kline(sym)
        if data is None:
            # 保留旧文件
            if os.path.exists(out_path):
                fail.append((code, f"FETCH_FAIL({err}) 保留旧文件"))
            else:
                fail.append((code, f"FETCH_FAIL({err}) 无旧文件"))
            continue
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        ok.append((code, f"{len(data)} bars 末日{data[-1]['d']}"))
        time.sleep(0.35)
    return ok, skip, fail


# ------------------------------------------------------------------ NeoData
def refresh_neodata():
    """最佳努力：若 neodata 连接器可用（CODEBUDDY_MCP_CONFIG 存在），重抓前20+仓单。"""
    cfg_env = os.environ.get("CODEBUDDY_MCP_CONFIG")
    if not cfg_env:
        return False, "未连接 neodata 连接器（CODEBUDDY_MCP_CONFIG 缺失），跳过前20/仓单刷新（保留上次好数据）"
    # 复用 collect_raw_new.py（已实现对 rank/warehouse 的抓取与落盘）
    script = os.path.join(COLLECT_DIR, "collect_raw_new.py")
    if not os.path.exists(script):
        return False, "collect_raw_new.py 缺失"
    try:
        r = subprocess.run([sys.executable, script], cwd=COLLECT_DIR,
                           capture_output=True, text=True, timeout=1800)
        ok = r.returncode == 0
        tail = (r.stdout or "")[-1200:]
        return ok, ("neodata 刷新完成" if ok else f"neodata 刷新异常 rc={r.returncode}\n{tail}")
    except Exception as e:  # noqa
        return False, f"neodata 刷新异常: {e}"


# ------------------------------------------------------------------ 主流程
def merge_websearch():
    """最佳努力：若存在 WebSearch 补丁文件 collect/_websearch_opinions.json，
    则调用 apply_websearch_opinions.py 按「来源」安全合并（保留 qhkch 与多源，
    仅替换 WebSearch 那份）。WebSearch 实际检索需 AI 代理完成并落盘补丁，本函数只合并。
    返回 (ok:bool, msg:str)。"""
    patch = os.path.join(COLLECT_DIR, "_websearch_opinions.json")
    if not os.path.exists(patch):
        return True, ("未找到 WebSearch 补丁（collect/_websearch_opinions.json）—— 跳过；"
                      "opinions_*.json 中已烘焙的 WebSearch 观点由 qhkch 步骤保留")
    try:
        r = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, "apply_websearch_opinions.py")],
                           cwd=BASE, capture_output=True, text=True, timeout=300)
        tail = (r.stdout or "")[-700:]
        if r.returncode != 0:
            return False, f"WebSearch 合并异常(rc={r.returncode})，保留旧数据:\n{tail}"
        return True, f"WebSearch 合并完成:\n{tail}"
    except Exception as e:  # noqa
        return False, f"WebSearch 合并运行异常(已跳过): {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-neodata", action="store_true", help="跳过 neodata 刷新")
    ap.add_argument("--no-opinions", action="store_true", help="跳过机构观点(qhkch)每日直抓")
    ap.add_argument("--no-websearch", action="store_true", help="跳过 WebSearch 补丁合并")
    ap.add_argument("--no-html", action="store_true", help="不重建 dist 单文件")
    ap.add_argument("--only", nargs="*", help="只刷新指定 CODE（调试用）")
    ap.add_argument("--allow-weekend", action="store_true",
                   help="允许周末也刷新（默认周末自动跳过，供本机开机自启场景使用）")
    args = ap.parse_args()

    # 本机开机自启场景：周末自动跳过（避免无意义运行）；手动运行加 --allow-weekend 可强制
    if not args.allow_weekend and datetime.datetime.now().weekday() >= 5:
        log("今天是周末，自动跳过刷新（如需强制请加 --allow-weekend）")
        return 0

    started = datetime.datetime.now()
    log("==== 期货工作台每日刷新开始 ====")

    # 1) K 线
    ok, skip, fail = refresh_klines(only=args.only)
    log(f"K线抓取：成功 {len(ok)}，跳过 {len(skip)}，异常 {len(fail)}")
    for c, s in ok[:5]:
        log(f"  + {c}: {s}")
    if len(ok) > 5:
        log(f"  ... 其余 {len(ok) - 5} 个成功")
    for c, s in fail:
        log(f"  ! {c}: {s}")

    # 2) 快照日期
    sd = latest_kline_date() or snap_date()
    log(f"快照日期 SNAP_DATE = {sd}")
    os.environ["WB_SNAP_DATE"] = sd

    # 3) NeoData（可选）
    nd_msg = "跳过（--no-neodata）"
    if not args.no_neodata:
        nd_ok, nd_msg = refresh_neodata()
        log(f"NeoData: {nd_msg}")

    # 3.5) 前20曲线 + 标准仓单 每日滚动（AKShare 直连交易所官网，覆盖 SHFE/CZCE/GFEX；DCE 走 neodata 最佳努力）
    wh_ok = None
    for sc, lab in (("refresh_t20_daily.py", "前20曲线滚动"), ("refresh_wh_daily.py", "标准仓单滚动")):
        log(f"{lab}采集 ...")
        try:
            r = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, sc)],
                               cwd=BASE, capture_output=True, text=True,
                               env=dict(os.environ, WB_SNAP_DATE=sd), timeout=600)
            tail = (r.stdout or "")[-700:]
            if r.returncode != 0:
                log(f"[警告] {lab}异常(rc={r.returncode}):\n{tail}")
            else:
                log(f"{lab}完成:\n{tail}")
            if sc == "refresh_wh_daily.py":
                wh_ok = (r.returncode == 0)
        except Exception as e:  # noqa
            log(f"[警告] {lab}运行异常(已跳过，不影响主流水线): {e}")
            if sc == "refresh_wh_daily.py":
                wh_ok = False

    # 3.6) 机构观点（多空情绪）每日直抓（qhkch，最佳努力，build_all 前刷新）
    op_ok = None
    op_msg = "跳过（--no-opinions）"
    if not args.no_opinions:
        log("机构观点(多空情绪)每日直抓（qhkch） ...")
        try:
            r = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, "refresh_opinions_daily.py")],
                               cwd=BASE, capture_output=True, text=True,
                               env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1800)
            tail = (r.stdout or "")[-700:]
            if r.returncode != 0:
                op_msg = f"观点直抓异常(rc={r.returncode})，保留旧数据:\n{tail}"
                op_ok = False
                log(f"[警告] {op_msg}")
            else:
                op_msg = "观点直抓完成（qhkch）"
                op_ok = True
                log(f"观点直抓完成:\n{tail}")
        except Exception as e:  # noqa
            op_msg = f"观点直抓运行异常(已跳过): {e}"
            op_ok = False
            log(f"[警告] {op_msg}")

    # 3.7) WebSearch 全网观点合并（若存在补丁；最佳努力，build_all 前）
    ws_msg = "跳过（--no-websearch）"
    if not args.no_websearch:
        ws_ok, ws_msg = merge_websearch()
        log(f"WebSearch 全网观点合并: {ws_msg}")

    # 4) build_all
    log("构建 data/ 快照 ...")
    r1 = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, "build_all.py")],
                        cwd=BASE, capture_output=True, text=True,
                        env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1200)
    if r1.returncode != 0:
        log(f"[错误] build_all 失败:\n{r1.stderr[-2000:]}")
    else:
        log("build_all 完成")

    # 5) build_web
    log("构建 web/ + dist/ ...")
    bw_args = [sys.executable, os.path.join(COLLECT_DIR, "build_web.py")]
    if args.no_html:
        bw_args.append("--no-html")
    r2 = subprocess.run(bw_args, cwd=BASE, capture_output=True, text=True,
                        env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1200)
    if r2.returncode != 0:
        log(f"[错误] build_web 失败:\n{r2.stderr[-2000:]}")
    else:
        log("build_web 完成")

    # 6) 注入前20历史曲线（top20.history）到最新快照（动态日期，与 build 一致）
    log("注入前20历史曲线 ...")
    inj = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, "inject_t20_history.py")],
                         cwd=BASE, capture_output=True, text=True,
                         env=dict(os.environ, WB_SNAP_DATE=sd), timeout=300)
    if inj.returncode != 0:
        log(f"[警告] inject_t20_history 失败:\n{inj.stderr[-1500:]}")
    else:
        log("inject_t20_history 完成")

    elapsed = (datetime.datetime.now() - started).total_seconds()
    status = {
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snap_date": sd,
        "kline_ok": len(ok), "kline_skip": len(skip), "kline_fail": len(fail),
        "kline_fail_detail": [{"code": c, "msg": m} for c, m in fail],
        "neodata": nd_msg,
        "opinions": op_msg,
        "websearch": ws_msg,
        "warehouse_ok": wh_ok,
        "sentiment_ok": op_ok,
        "build_all_ok": r1.returncode == 0,
        "build_web_ok": r2.returncode == 0,
        "elapsed_sec": round(elapsed, 1),
    }
    with open(os.path.join(BASE, "collect", "refresh_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    log(f"==== 刷新结束（耗时 {elapsed:.1f}s）====")
    log(f"状态已写入 collect/refresh_status.json")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
