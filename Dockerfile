# 期货分析工作台 —— 常驻容器镜像（脱离本机，cron 定时刷新 + 自托管网站）
FROM python:3.13-slim

WORKDIR /opt/futures-workbench

# 复制项目源码（受 .dockerignore 约束，不含 data/ dist/ 等运行时产物）
COPY . .

# 安装依赖（akshare 用于前20净持仓/标准仓单的 AKShare 直连兜底；requests 用于新浪/东方财富/奇货可查 HTTP 抓取）
RUN pip install --no-cache-dir akshare requests

# 安装 cron + tzdata，并写入定时任务；定时任务按北京时间触发（容器默认 UTC，不设置会差 8 小时）
ENV TZ=Asia/Shanghai
ENV HOST=0.0.0.0
ENV PORT=8011
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && crontab /opt/futures-workbench/crontab

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 建议把 /opt/futures-workbench/data 挂载为持久卷，使刷新数据跨容器重启保留
VOLUME ["/opt/futures-workbench/data"]

EXPOSE 8011
CMD ["/entrypoint.sh"]
