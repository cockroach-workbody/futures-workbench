#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署钩子（便捷封装）：把刷新好的 web/ 发布上线。
  python deploy.py
等价于：python cloud_cron.py deploy

行为：
  * 若设置环境变量 WBAPP_DEPLOY_CMD（例如 WorkBuddy 的发布命令），则执行它把 web/ 推到 wbapp；
  * 否则 web/ 已是零依赖 Python 网站，直接自托管：
        cd web && HOST=0.0.0.0 PORT=8011 python app/main.py
"""
import subprocess
import sys

if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "cloud_cron.py", "deploy"]).returncode)
