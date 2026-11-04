#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货分析工作台 —— 可移植的统一编排入口（供 cron / 云函数 / 本地定时调用）

设计目标：
  * 所有路径均相对本脚本所在目录解析，可在任意目录/任意云服务器上运行，
    不再依赖本机固定盘符（E:\\...、C:\\Users\\...）。
  * 幂等、最佳努力：任一步失败均保留上一次的好数据，不清空、不破坏。
  * 跑完把运行报告写到项目根目录 run_report.json，便于 cron 与外部观测。

用法：
  python cloud_cron.py data     # 交易所直抓数据刷新主跑（K线 + 前20净持仓 + 标准仓单 + qhkch观点 + 可选WebSearch合并 + 重建 + 部署）
  python cloud_cron.py opinion  # 机构观点轻量重建（qhkch直抓 + WebSearch合并 + 重建，不重抓K线，约 1 分钟）
  python cloud_cron.py both     # 数据 + 观点 一次性全做（= data，含全部步骤）
  python cloud_cron.py deploy   # 仅部署（复用 wbapp 应用；若不可用则给出自托管提示）

配合 crontab（示例，见 README_CLOUD.md）：
  # 工作日 19:30 数据主跑（K线/标准仓单/前20净持仓等收盘数据）
  30 19 * * 1-5 cd /opt/futures-workbench && python cloud_cron.py data  >> cron.log 2>&1
  # 每日 08:30 市场情绪/机构观点（WebSearch 全网 + qhkch 直抓）
  30 8 * * *    cd /opt/futures-workbench && python cloud_cron.py opinion >> cron.log 2>&1
  # 每日 12:30 / 18:30 补跑
  30 12 * * *   cd /opt/futures-workbench && python cloud_cron.py both   >> cron.log 2>&1
  30 18 * * *   cd /opt/futures-workbench && python cloud_cron.py both   >> cron.log 2>&1
"""
import datetime
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
COLLECT_DIR = os.path.join(BASE, "collect")
sys.path.insert(0, COLLECT_DIR)

# 可选：把缠论引擎路径注入环境，供 build_all / build_web 读取
if "WB_CHANLUN_PATH" not in os.environ:
    # 本地开发兜底；云端/容器通过环境变量覆盖
    pass


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run(script, *args, env=None, timeout=1800):
    """运行 collect/ 下的脚本，返回 (rc, tail_stdout, tail_stderr)。"""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    cmd = [sys.executable, os.path.join(COLLECT_DIR, script)] + list(args)
    log(f"→ 运行 {script} {' '.join(args)}")
    try:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                           env=full_env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT({timeout}s)"
    except Exception as e:  # noqa
        return -2, "", f"EXC: {e}"
    tail_o = (r.stdout or "")[-900:]
    tail_e = (r.stderr or "")[-900:]
    status = "OK" if r.returncode == 0 else f"FAIL(rc={r.returncode})"
    log(f"← {script} {status}")
    if tail_o.strip():
        log("   [out] " + tail_o.replace("\n", "\n         "))
    if tail_e.strip() and r.returncode != 0:
        log("   [err] " + tail_e.replace("\n", "\n         "))
    return r.returncode, tail_o, tail_e


def deploy():
    """部署钩子：优先复用 WorkBuddy 的 wbapp 发布；否则给出自托管提示。"""
    web_dir = os.path.join(BASE, "web")
    if not os.path.isdir(web_dir):
        log("[部署] web/ 不存在，请先执行 data/both 构建")
        return False, "web/ 不存在"
    # 1) 若环境提供 WorkBuddy CLI / 发布命令，则用它发布到 wbapp
    wbapp_cmd = os.environ.get("WBAPP_DEPLOY_CMD")
    if wbapp_cmd:
        rc, o, e = run_cmd(wbapp_cmd, timeout=600)
        if rc == 0:
            return True, f"wbapp 发布成功（{wbapp_cmd}）"
        return False, f"wbapp 发布失败(rc={rc}): {e[-500:]}"
    # 2) 否则提示自托管：web/ 是零依赖 Python 标准库网站，可直接运行
    log("[部署] 未配置 WBAPP_DEPLOY_CMD —— web/ 已构建完成（本进程未对外发布）：")
    log(f"        自托管: cd {web_dir} && HOST=0.0.0.0 PORT=8011 python app/main.py")
    log("        云端任务提示: 若以 WorkBuddy 云端任务运行，请由云端任务本身调用发布工具")
    log("                      发布 web/，或在环境变量中设置 WBAPP_DEPLOY_CMD 指向发布命令。")
    return True, "web/ 已构建完成（未发布；云端任务请改用发布工具或设置 WBAPP_DEPLOY_CMD）"


def run_cmd(cmd, timeout=600):
    log(f"→ 运行命令: {cmd}")
    try:
        r = subprocess.run(cmd, shell=True, cwd=BASE, capture_output=True,
                           text=True, timeout=timeout)
    except Exception as e:  # noqa
        return -2, "", str(e)
    return r.returncode, (r.stdout or "")[-900:], (r.stderr or "")[-900:]


def write_web_status(report):
    """把运行报告同步给 web 服务（供页面顶部状态条展示），不依赖本地/桌面。"""
    web_status = os.path.join(BASE, "web", "refresh_status.json")
    src = os.path.join(COLLECT_DIR, "refresh_status.json")
    merged = dict(report)
    if os.path.isfile(src):
        try:
            with open(src, encoding="utf-8") as f:
                merged.update(json.load(f))
        except Exception:
            pass
    merged["generated_at"] = report.get("finished_at")
    merged["mode"] = report.get("mode")
    merged["available"] = True
    os.makedirs(os.path.dirname(web_status), exist_ok=True)
    with open(web_status, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    log("[状态] 已写入 web/refresh_status.json（供页面状态条）")


def mode_data():
    """交易所直抓数据刷新主跑（完整流水线）。"""
    rc, _, _ = run("refresh_daily.py")
    return rc == 0


def mode_opinion():
    """机构观点轻量重建（不重抓K线）：qhkch 直抓 + WebSearch 合并 + 重建。"""
    # 1) qhkch 每日直抓（最佳努力）
    rc1, _, _ = run("refresh_opinions_daily.py")
    # 2) WebSearch 全网观点合并（若补丁 collect/_websearch_opinions.json 存在）
    run("apply_websearch_opinions.py")
    # 3) 重建 data / web / 注入前20历史
    sd = os.environ.get("WB_SNAP_DATE")
    env = {"WB_SNAP_DATE": sd} if sd else None
    run("build_all.py", env=env)
    run("build_web.py", env=env)
    run("inject_t20_history.py", env=env)
    # 按真实返回码标记情绪数据刷新结果（opinion 模式不重抓仓单，仓单状态沿用上次 data 刷新）
    try:
        src = os.path.join(COLLECT_DIR, "refresh_status.json")
        st = {}
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as f:
                st = json.load(f)
        st["sentiment_ok"] = (rc1 == 0)
        with open(src, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return True


def mode_both():
    """数据 + 观点 全做（= 完整 refresh_daily）。"""
    return mode_data()


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
    started = datetime.datetime.now()
    log(f"===== 期货工作台 cron 任务开始（mode={mode}） =====")

    ok = True
    summary = {}
    if mode in ("data", "both"):
        ok = mode_data()
        summary["data"] = "ok" if ok else "fail"
    elif mode == "opinion":
        mode_opinion()
        summary["opinion"] = "ok"
    elif mode == "deploy":
        d_ok, d_msg = deploy()
        summary["deploy"] = d_msg
        ok = d_ok
    else:
        log(f"未知模式: {mode}（可选 data/opinion/both/deploy）")
        return 2

    # data/both 之后自动部署；opinion 之后也部署（让观点即时上线）
    if mode in ("data", "both", "opinion"):
        d_ok, d_msg = deploy()
        summary["deploy"] = d_msg

    finished = datetime.datetime.now()
    elapsed = (finished - started).total_seconds()
    report = {
        "mode": mode,
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(elapsed, 1),
        "host": os.environ.get("HOST", "0.0.0.0"),
        "summary": summary,
        "python": sys.version.split()[0],
    }
    # 同步状态给 web 页面（页面顶部状态条会读取）
    try:
        write_web_status(report)
    except Exception as e:  # noqa
        log(f"[状态] 写入 web/refresh_status.json 失败（不影响主流程）: {e}")
    with open(os.path.join(BASE, "run_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"===== 任务结束（耗时 {elapsed:.1f}s）===== 报告: run_report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
