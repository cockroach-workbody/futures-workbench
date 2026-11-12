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
  3.7) 动态门户机构观点合并（最佳努力）：依次合并下列补丁（若存在）
        collect/_websearch_opinions.json（WebSearch全网，沿用补丁自带 source）
        collect/_jin10_opinions.json      （金十期货，强制打标族群）
        collect/_eastmoney_opinions.json  （东方财富期货资讯，强制打标族群）
        —— 经 apply_websearch_opinions.py 按「来源族群」安全合并：
           保留 qhkch 与多源观点，仅替换「与本次补丁同族群」的动态观点，
           其余动态族群原样保留。各门户流水线独立、反复运行互不丢数据。
       注意：WebSearch 实际检索需 AI 代理能力（见下「注意」），本脚本只负责合并补丁。
  4) 运行 build_all.py 生成 data/<板块>/<CODE>/<SNAP_DATE>.json（首遍，生成 main_contract）
  4.5) AKShare 资金流采集 akshare_capital_flow.py（读取快照 main_contract，产出 raw_capflow；
       含 --with-history 历史回溯；最佳努力，失败则第7部分回退前20）
  4.52) 品种全部月份数据源 capflow_all_months.py（逐月合约日线 + 逐日三榜跨月合并；
       依赖 4.5 的 main_contract，必须早于 4.55/4.56；每日只增量补当天，缓存可断点续传）
  4.55) 三榜合一近一月合计净持仓曲线 capflow_net_history.py（全月份口径下直接读 4.52 产物，
       不再联网重抓；写入 net_history；必须在 4.6 二次 build_all 之前）
  4.56) 近一月 L4 四维共振读数曲线 capflow_score_history.py（逐日截面取自 4.52 产物，
       经 Node 桥接复用前端 capFlowAnalytics 打分；依赖 4.55 的交易日窗口）
  4.6) 二次 build_all.py 把 capital_flow（含 net_history / score_history）注入快照（供第7部分渲染）
  5) 运行 build_web.py 生成 web/（线上部署源）+ dist/ 单文件
  6) 写 refresh_status.json 供前端/日志查看

调用方式：
  python collect/refresh_daily.py                # 完整刷新
  python collect/refresh_daily.py --no-neodata   # 跳过 neodata（纯 K 线+重建）
  python collect/refresh_daily.py --no-opinions  # 跳过机构观点(qhkch)每日直抓
  python collect/refresh_daily.py --no-websearch # 跳过动态门户观点合并（WebSearch全网/金十期货/东方财富期货资讯）
  python collect/refresh_daily.py --no-html      # 不重建 dist 单文件
  python collect/refresh_daily.py --no-capflow   # 跳过 AKShare 资金流采集（第7部分回退前20）

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

from wb_common import latest_kline_date, snap_date, bj_now, BJ_LABEL  # noqa: E402
import build_all  # noqa: E402  (取 PRODUCTS / SINA 映射)

SINA_URL = ("https://stock2.finance.sina.com.cn/futures/api/json.php/"
            "InnerFuturesNewService.getDailyKLine?symbol={}")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://finance.sina.com.cn/"}


def log(msg):
    ts = bj_now().strftime("%H:%M:%S")
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
    """最佳努力：依次合并各动态门户机构观点补丁（WebSearch全网 / 金十期货 / 东方财富期货资讯）。
    每个补丁若存在则调用 apply_websearch_opinions.py 按「来源族群」安全合并：
      - 保留 qhkch 与多源观点
      - 仅替换「与本次补丁同族群」的动态观点，其余动态族群原样保留
      → 各门户流水线独立、反复运行，互不丢数据。
    WebSearch 实际检索需 AI 代理完成并落盘补丁；金十/东方财富补丁为采集落盘。
    本函数只负责合并，返回 (ok:bool, msg:str)。"""
    PATCH_SOURCES = [
        ("_websearch_opinions.json", None),           # 沿用补丁自带 source（websearch全网...）
        ("_jin10_opinions.json", "金十期货"),          # 强制打标族群
        ("_eastmoney_opinions.json", "东方财富期货资讯"),
    ]
    results = []
    overall_ok = True
    for fname, source in PATCH_SOURCES:
        patch = os.path.join(COLLECT_DIR, fname)
        if not os.path.exists(patch):
            results.append(f"[跳过] 未找到补丁 {fname}")
            continue
        cmd = [sys.executable, os.path.join(COLLECT_DIR, "apply_websearch_opinions.py"),
               "--patch", patch]
        if source:
            cmd += ["--source", source]
        try:
            r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=300)
            tail = (r.stdout or "")[-700:]
            if r.returncode != 0:
                overall_ok = False
                results.append(f"[异常] {fname} 合并失败(rc={r.returncode}):\n{tail}")
            else:
                results.append(f"[完成] {fname} 合并:\n{tail}")
        except Exception as e:  # noqa
            overall_ok = False
            results.append(f"[异常] {fname} 合并运行异常(已跳过): {e}")
    return overall_ok, "\n".join(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-neodata", action="store_true", help="跳过 neodata 刷新")
    ap.add_argument("--no-opinions", action="store_true", help="跳过机构观点(qhkch)每日直抓")
    ap.add_argument("--no-websearch", action="store_true", help="跳过 WebSearch 补丁合并")
    ap.add_argument("--no-html", action="store_true", help="不重建 dist 单文件")
    ap.add_argument("--no-capflow", action="store_true", help="跳过 AKShare 资金流采集（第7部分回退前20）")
    ap.add_argument("--only", nargs="*", help="只刷新指定 CODE（调试用）")
    ap.add_argument("--allow-weekend", action="store_true",
                   help="允许周末也刷新（默认周末自动跳过，供本机开机自启场景使用）")
    args = ap.parse_args()

    # 本机开机自启场景：周末自动跳过（避免无意义运行）；手动运行加 --allow-weekend 可强制
    if not args.allow_weekend and bj_now().weekday() >= 5:
        log("今天是周末，自动跳过刷新（如需强制请加 --allow-weekend）")
        return 0

    started = bj_now()
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
    for sc, lab, extra in (
        ("refresh_t20_daily.py", "前20曲线滚动(主力合约·含回填)", ["--backfill", "22"]),
        ("refresh_wh_daily.py", "标准仓单滚动", []),
    ):
        log(f"{lab}采集 ...")
        try:
            r = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, sc)] + extra,
                               cwd=BASE, capture_output=True, text=True,
                               env=dict(os.environ, WB_SNAP_DATE=sd), timeout=900)
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

    # 3.7) 动态门户观点合并（WebSearch全网 / 金十期货 / 东方财富期货资讯；若存在补丁；最佳努力，build_all 前）
    ws_msg = "跳过（--no-websearch）"
    if not args.no_websearch:
        ws_ok, ws_msg = merge_websearch()
        log(f"动态门户观点合并: {ws_msg}")

    # 4) build_all
    log("构建 data/ 快照 ...")
    r1 = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, "build_all.py")],
                        cwd=BASE, capture_output=True, text=True,
                        env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1200)
    if r1.returncode != 0:
        log(f"[错误] build_all 失败:\n{r1.stderr[-2000:]}")
    else:
        log("build_all 完成")

    # 4.5) AKShare 资金流（奇货可查式·交易所会员持仓排名同源）→ raw_capflow
    #      读取刚生成的快照 main_contract，计算净持仓分布/总持仓变动/大单动向/历史回溯。
    cf_ok = True
    if args.no_capflow:
        log("AKShare 资金流采集：跳过（--no-capflow）")
    else:
        log("AKShare 资金流采集（会员持仓排名·净持仓分层·大单动向·历史回溯） ...")
        try:
            r_cf = subprocess.run(
                [sys.executable, os.path.join(COLLECT_DIR, "akshare_capital_flow.py"), "--with-history"],
                cwd=BASE, capture_output=True, text=True,
                env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1800)
            tail = (r_cf.stdout or "")[-900:]
            if r_cf.returncode != 0:
                cf_ok = False
                log(f"[警告] AKShare 资金流采集异常(rc={r_cf.returncode})，第7部分回退前20:\n{tail}")
            else:
                log(f"AKShare 资金流采集完成:\n{tail}")
        except Exception as e:  # noqa
            cf_ok = False
            log(f"[警告] AKShare 资金流采集运行异常(已跳过，第7部分回退前20): {e}")

    # 4.52) 品种全部月份数据源：逐月合约日线 + 逐日三榜跨月合并。
    #       必须在 4.5 之后（读 main_contract）、4.55/4.56 之前（它们从 allmonths 产物读截面）。
    #       日常只增量抓当日（41 品种 × 1 天），历史 22 天由 amrank 缓存复用。
    if not args.no_capflow and cf_ok:
        log("品种全部月份数据源采集（41 品种 · 每日增量） ...")
        try:
            r_am = subprocess.run(
                [sys.executable, os.path.join(COLLECT_DIR, "capflow_all_months.py"),
                 "--days", "22", "--workers", "8"],
                cwd=BASE, capture_output=True, text=True,
                env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1800)
            tail = (r_am.stdout or "")[-1200:]
            if r_am.returncode != 0:
                log(f"[警告] 全月份数据源采集异常(rc={r_am.returncode}):\n{tail}")
            else:
                log(f"全月份数据源采集完成:\n{tail}")
        except Exception as e:  # noqa
            log(f"[警告] 全月份数据源采集运行异常(已跳过，沿用上次全月份产物): {e}")
    else:
        log("全月份数据源采集：跳过（--no-capflow 或资金流采集失败）")

    # 4.55) 三榜合一近一月合计净持仓曲线：写入 net_history（逐日缓存，仅补新增交易日）
    #       口径与「三榜合一总持仓占比」一致；必须在 4.6 二次 build_all 之前，
    #       否则新字段进不了快照。失败不影响主流程，前端会回退旧 top20.history。
    if not args.no_capflow and cf_ok:
        log("三榜合一近一月合计净持仓曲线（39 品种 · 增量补采） ...")
        try:
            r_nh = subprocess.run(
                [sys.executable, os.path.join(COLLECT_DIR, "capflow_net_history.py"),
                 "--days", "22", "--workers", "4"],
                cwd=BASE, capture_output=True, text=True,
                env=dict(os.environ, WB_SNAP_DATE=sd), timeout=900)
            tail = (r_nh.stdout or "")[-900:]
            if r_nh.returncode != 0:
                log(f"[警告] 三榜合一历史曲线采集异常(rc={r_nh.returncode}):\n{tail}")
            else:
                log(f"三榜合一历史曲线采集完成:\n{tail}")
        except Exception as e:  # noqa
            log(f"[警告] 三榜合一历史曲线采集运行异常(已跳过，前端回退旧曲线): {e}")
    else:
        log("三榜合一近一月曲线：跳过（--no-capflow 或资金流采集失败）")

    # 4.56) 近一月 L4 四维共振读数：逐日复原榜单截面 + 截止该日行情，由前端同一份
    #       capFlowAnalytics 打分（Node 桥接），保证与页面当日读数零口径漂移。
    #       依赖 4.55 的 net_history 交易日窗口；失败不影响主流程，曲线自动隐藏。
    if not args.no_capflow and cf_ok:
        log("近一月 L4 综合读数曲线（逐日逐品种打分） ...")
        try:
            r_sh = subprocess.run(
                [sys.executable, os.path.join(COLLECT_DIR, "capflow_score_history.py"),
                 "--workers", "4"],
                cwd=BASE, capture_output=True, text=True,
                env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1200)
            tail = (r_sh.stdout or "")[-900:]
            if r_sh.returncode != 0:
                log(f"[警告] L4 历史读数采集异常(rc={r_sh.returncode}):\n{tail}")
            else:
                log(f"L4 历史读数采集完成:\n{tail}")
        except Exception as e:  # noqa
            log(f"[警告] L4 历史读数采集运行异常(已跳过，曲线自动隐藏): {e}")
    else:
        log("近一月 L4 综合读数曲线：跳过（--no-capflow 或资金流采集失败）")

    # 4.6) 二次 build_all：注入 capital_flow 到快照（供第7部分资金动向渲染；缺失则前端回退前20）
    log("重新构建 data/ 快照（注入 capital_flow） ...")
    r1b = subprocess.run([sys.executable, os.path.join(COLLECT_DIR, "build_all.py")],
                         cwd=BASE, capture_output=True, text=True,
                         env=dict(os.environ, WB_SNAP_DATE=sd), timeout=1200)
    if r1b.returncode != 0:
        log(f"[错误] 二次 build_all 失败:\n{r1b.stderr[-2000:]}")
    else:
        log("二次 build_all 完成（已注入 capital_flow）")

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

    elapsed = (bj_now() - started).total_seconds()
    status = {
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": BJ_LABEL,
        "snap_date": sd,
        "kline_ok": len(ok), "kline_skip": len(skip), "kline_fail": len(fail),
        "kline_fail_detail": [{"code": c, "msg": m} for c, m in fail],
        "neodata": nd_msg,
        "opinions": op_msg,
        "websearch": ws_msg,
        "warehouse_ok": wh_ok,
        "sentiment_ok": op_ok,
        "build_all_ok": r1.returncode == 0,
        "capflow_ok": cf_ok,
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
