#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy SessionStart 钩子：开机上线后自动抓取机构观点（全网 WebSearch + qhkch 直抓，来源不缩水）。

机制：每次打开本项目的新会话时触发一次。脚本从 stdin 读取钩子 JSON，
检查"今天是否已抓过观点"（以 collect/opinions_RB.json / opinions_CU.json 的 mtime 日期为准）。
- 今天已抓：静默输出 continue=true，不打扰会话。
- 今天未抓：向会话注入一条完整指令，由当前代理执行与旧 08:30 定时任务等价的全流程：
  WebSearch 按 6 板块分批检索 41 品种观点 -> _merge_websearch.py 合并 ->
  cloud_cron.py opinion（qhkch 直抓 + 安全合并各来源补丁 + build_all + build_web）-> 发布到 WorkBuddy 链接。
  代理执行成功后 opinions_RB.json 的 mtime 会更新为今天，下次会话即自动跳过。

防重复触发：WebSearch 全流程耗时较长（约 10-20 分钟），期间若用户新开会话，
opinions mtime 尚未更新，会重复注入。故额外用系统临时目录里的 stamp 记录"今天已注入"，
90 分钟内不重复注入；超过 90 分钟视为上次失败/中断，允许重新注入补跑。

注意：
- 只在本项目目录（E:/workbuddy/2026-10-28-11-22-26）下生效，其它项目会话不复现。
- 发布必须由代理调用发布工具完成，脚本本身不发布（无 CLI 发布通道）。
"""
import hashlib
import json
import os
import sys
import tempfile
import time

PROJECT_DIR = r"E:\workbuddy\2026-10-28-11-22-26"
# 用多个品种的观点文件任一为今天即视为"今天已抓"，主判据 RB，兜底 CU
SENTINELS = [
    os.path.join(PROJECT_DIR, "collect", "opinions_RB.json"),
    os.path.join(PROJECT_DIR, "collect", "opinions_CU.json"),
]
# "当天已注入"锁：放系统临时目录，按项目路径哈希命名，不污染项目 git 工作区
STAMP = os.path.join(
    tempfile.gettempdir(),
    "wb_opinion_hook_" + hashlib.md5(PROJECT_DIR.encode("utf-8")).hexdigest()[:10] + ".json",
)
STAMP_TTL_SEC = 90 * 60  # 90 分钟内不重复注入；超时视为失败允许补跑

INSTRUCTION = (
    "【开机自动任务·机构观点全网抓取】检测到今天尚未抓取机构观点，请自动执行以下完整流程，"
    "完成后简要汇报结果（无需等待用户确认）：\n"
    "1) 在工作区 E:/workbuddy/2026-10-28-11-22-26，用 WebSearch 按以下 6 个板块【分批】检索 41 品种"
    "最新机构研报与多空观点，【每批单独写入对应板块的部分文件】（不要一次把全部 41 品种写进一个文件，"
    "避免单次输出过长被截断）：\n"
    "   - black（黑色/建材）：FG HC I J JM RB SA\n"
    "   - agria（农产品A）：A C CF M P SR Y\n"
    "   - agrib（农产品B）：AP CJ JD LH OI RM\n"
    "   - chem（能源化工）：BU L MA PP RU TA UR V\n"
    "   - metal（金属）：AG AL AU CU NI PB SN ZN\n"
    "   - gfex（广期所）：LC PD PS PT SI\n"
    "   每批写入 collect/_websearch_partial_<板块>.json，格式：\n"
    "   {\"CODE\":[{\"institution\":机构名,\"date\":\"YYYY-MM-DD\","
    "\"stance\":\"看多/看空/震荡 或 bullish/bearish/neutral\",\"title\":标题,"
    "\"summary\":摘要,\"url\":来源链接,\"source\":来源名},...],...}\n"
    "   ★ 每品种至少 10-15 条观点，尽量覆盖 5 家以上不同机构，优先近 30 天有明确立场者；"
    "未检索到的品种不要写；不要编造或重复同一条。\n"
    "2) 6 个板块全部写完后，运行 python collect/_merge_websearch.py "
    "合并为 collect/_websearch_opinions.json。\n"
    "3) 运行 python cloud_cron.py opinion（qhkch 直抓 + 按来源安全合并 WebSearch/金十/东方财富补丁"
    " + build_all + build_web，约 1 分钟）。\n"
    "4) 确认 web/ 重建成功后，调用 workbuddy_sites_deploy 工具把 "
    "E:/workbuddy/2026-10-28-11-22-26/web 发布到已上线应用 "
    "（appId wbapp_45EKXpSZ4nC4gVsPZbfE2U，domainPrefix futures-workbench，"
    "startCmd 'python app/main.py'，port 8011，action deploy，userAskedToPublish true），"
    "复用同一应用、分享链接不变，切勿新建应用。\n"
    "5) 将本次运行结果写入 IMA 个人知识库「微信用户的知识库」"
    "(knowledge_base_id=ima_kb_b8Cbi5UB)：标题「期货工作台云端运行报告-机构观点-YYYY-MM-DD」，"
    "正文含执行日期、WebSearch 覆盖品种数（读 collect/_websearch_opinions.json 顶层 key 数）、"
    "cloud_cron 返回码、合并是否成功、部署是否成功、线上 7 日净情绪最新观点日期与结论。"
    "（若 IMA 知识库当前不可用，跳过此步，不影响抓取与发布。）\n"
    "6) 若 WebSearch、抓取或部署失败，保留上一次好数据，不要清空；简要说明失败原因即可。"
)


def today_str():
    return time.strftime("%Y-%m-%d", time.localtime())


def is_fresh(path, today):
    try:
        mtime_day = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
        return mtime_day == today
    except OSError:
        return False


def injected_recently(today):
    """当天已注入且未超时 -> True（避免长检索期间开新会话重复触发）。"""
    try:
        d = json.load(open(STAMP, encoding="utf-8"))
        if d.get("date") == today and (time.time() - d.get("ts", 0)) < STAMP_TTL_SEC:
            return True
    except Exception:
        pass
    return False


def mark_injected(today):
    try:
        with open(STAMP, "w", encoding="utf-8") as f:
            json.dump({"date": today, "ts": time.time()}, f)
    except Exception:
        pass


def main():
    # 读取钩子 stdin（拿 cwd 判断项目），读不到也容错继续
    cwd = ""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            cwd = data.get("cwd", "") or ""
    except Exception:
        pass

    # 必须是本项目会话才处理；其它项目静默放行
    norm = lambda p: os.path.normcase(os.path.abspath(p or ""))
    if cwd and norm(cwd) != norm(PROJECT_DIR):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    today = today_str()
    # 观点 mtime 已是今天 -> 抓完了，静默
    if any(is_fresh(p, today) for p in SENTINELS):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0
    # 今天已注入且未超时 -> 全流程进行中，静默，避免重复触发
    if injected_recently(today):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    # 今天未抓且未在检索中：写入注入锁并注入完整指令
    mark_injected(today)
    print(json.dumps({
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": INSTRUCTION,
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
