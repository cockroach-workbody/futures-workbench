#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy SessionStart 钩子：开机上线后自动抓取机构观点。

机制：每次打开本项目的新会话时触发一次。脚本从 stdin 读取钩子 JSON，
检查"今天是否已抓过观点"（以 collect/opinions_RB.json 的 mtime 日期为准）。
- 今天已抓：静默输出 continue=true，不打扰会话。
- 今天未抓：向会话注入一条 additionalContext 指令，由当前代理执行
  「cloud_cron.py opinion（qhkch 直抓+合并+重建）+ 部署 web/ 到 WorkBuddy 链接」。
  代理执行成功后 opinions_RB.json 的 mtime 会更新为今天，下次会话即自动跳过。

注意：
- 只在本项目目录（E:/workbuddy/2026-10-28-11-22-26）下生效，其它项目会话不复现。
- 部署必须由代理调用发布工具完成，脚本本身不部署（无 CLI 发布通道）。
"""
import json
import os
import sys
import time

PROJECT_DIR = r"E:\workbuddy\2026-10-28-11-22-26"
# 用多个品种的观点文件任一为今天即视为"今天已抓"，主判据 RB，兜底 CU
SENTINELS = [
    os.path.join(PROJECT_DIR, "collect", "opinions_RB.json"),
    os.path.join(PROJECT_DIR, "collect", "opinions_CU.json"),
]

INSTRUCTION = (
    "【开机自动任务·机构观点抓取】检测到今天尚未抓取机构观点，请自动执行以下步骤，"
    "完成后简要汇报结果（无需等待用户确认）：\n"
    "1) 在工作区 E:/workbuddy/2026-10-28-11-22-26 运行：python cloud_cron.py opinion "
    "（qhkch 直抓 + 本地已有观点补丁安全合并 + build_all + build_web，约 1 分钟）。\n"
    "2) 确认 web/ 已重建成功后，调用 workbuddy_sites_deploy 工具把 "
    "E:/workbuddy/2026-10-28-11-22-26/web 重新发布到已上线应用 "
    "（appId wbapp_45EKXpSZ4nC4gVsPZbfE2U，domainPrefix futures-workbench，"
    "startCmd 'python app/main.py'，port 8011，action deploy，userAskedToPublish true），"
    "复用同一应用、分享链接不变，切勿新建应用。\n"
    "3) 若抓取或部署失败，保留上一次好数据，不要清空；简要说明失败原因即可。"
)


def today_str():
    return time.strftime("%Y-%m-%d", time.localtime())


def is_fresh(path, today):
    try:
        mtime_day = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
        return mtime_day == today
    except OSError:
        return False


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
    already = any(is_fresh(p, today) for p in SENTINELS)

    if already:
        # 今天已抓，静默跳过
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    # 今天未抓：注入指令，让当前代理自动执行抓取+发布
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
