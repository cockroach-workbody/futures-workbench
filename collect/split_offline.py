"""把离线单文件 HTML 拆成「轻量首屏 + 按需 gzip 品种分片」。

背景：单文件版把 11MB+ 的 window.__WB__ 内联，拆分版（v2）虽外置数据脚本，
但那个脚本仍是同步加载、一次性把 11MB 文本解析成 JS 对象。国内手机访问
GitHub Pages、或手机内存/CPU 较弱时，会长时间停在「加载中…」甚至直接失败。

v3 做法：
  - index.html 只内联「概览数据（约 80 KB）+ 默认品种快照（约 300 KB）」；
    ECharts 抽成独立脚本（内容哈希，浏览器可长期缓存）；
  - 其余 40 个品种各生成 .json.gz（约 70 KB）与 .json（明文回退）；
  - 离线数据层 shim 在用户点开某品种时才 fetch 对应分片，优先用
    DecompressionStream 解压 gzip，不支持的浏览器自动回退明文；
  - 因此首屏无需等待、也无需解析全量数据，且切换品种有可见加载提示。

用法：python collect/split_offline.py <源单文件.html> <输出目录>
源文件必须含内嵌 window.__WB__；否则返回非 0，由调用方回退为单文件复制。
"""

import gzip
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATTERN = re.compile(r"<script>\s*window\.__WB__=(.*?);\s*</script>", re.S)
SCRIPT_PATTERN = re.compile(r"(<script>)(.*?)(</script>)", re.S)
LOADING_PATTERN = re.compile(r'<div class="loading" id="loading">.*?</div>', re.S)
PART_DIR = "wb-p"
ONLINE_URL = "https://futures-workbench-01415.app.workbuddy.host/?sharecode=24XEIRXDFJ"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def enrich_status(status, as_of):
    """补齐离线页状态栏的「运行时间 / 数据刷新时间」。

    web/ 与 collect/refresh_status.json 均在 .gitignore 中，GitHub Actions 干净检出
    后不存在；build_web.py 因此只能写入 snap_date，Pages 状态栏缺两个时间字段。
    本函数在 CI「准备 Pages 产物」阶段运行，此时 cloud_cron.py 已生成：
      - run_report.json（本次任务 started_at / finished_at / mode，最权威）
      - collect/refresh_status.json（数据侧 finished_at / snap_date / kline_*）
    取不到的字段一律不写，绝不伪造时间。
    """
    report = _read_json(os.path.join(BASE, "run_report.json"))
    cstat = _read_json(os.path.join(BASE, "collect", "refresh_status.json"))
    wstat = _read_json(os.path.join(BASE, "web", "refresh_status.json"))

    out = dict(status or {"available": True, "mode": "offline"})
    run_finished = (report.get("finished_at") or wstat.get("run_finished_at")
                    or wstat.get("generated_at") or wstat.get("run_finished_at"))
    run_started = report.get("started_at") or wstat.get("run_started_at")
    data_refreshed = (cstat.get("data_refreshed_at") or cstat.get("finished_at")
                      or wstat.get("data_refreshed_at"))
    if run_finished:
        out["run_finished_at"] = run_finished
    if run_started:
        out["run_started_at"] = run_started
    if data_refreshed:
        out["data_refreshed_at"] = data_refreshed
    cst = cstat.get("snap_date") or as_of
    if cst:
        out["snap_date"] = cst
    for k in ("kline_ok", "kline_fail", "build_all_ok"):
        if k in cstat:
            out[k] = cstat[k]
    out["available"] = True
    out["mode"] = out.get("mode") or "offline"
    return out


def split(src, out_dir):
    with open(src, encoding="utf-8") as f:
        html = f.read()

    m = DATA_PATTERN.search(html)
    if not m:
        raise SystemExit("未找到 window.__WB__ 内嵌数据脚本，保持单文件")
    payload = json.loads(m.group(1))
    snaps = payload.get("snapshots") or {}
    cats = payload.get("categories") or {}
    as_of = payload.get("as_of") or ""

    # 默认品种内联：首屏 init() 会立刻请求它，避免任何网络往返
    def_cat, def_prod = cats.get("default_category"), cats.get("default_product")
    def_key = "%s/%s" % (def_cat, def_prod) if def_cat and def_prod else None
    inline = {def_key: snaps[def_key]} if def_key in snaps else {}

    os.makedirs(out_dir, exist_ok=True)
    part_root = os.path.join(out_dir, PART_DIR)
    os.makedirs(part_root, exist_ok=True)

    parts = {}
    gz_total = plain_total = 0
    for key in sorted(snaps):
        if key == def_key:
            continue
        raw = json.dumps(snaps[key], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()[:12]
        rel = "%s/%s" % (PART_DIR, digest)
        plain_name, gz_name = rel + ".json", rel + ".json.gz"
        with open(os.path.join(out_dir, plain_name), "wb") as f:
            f.write(raw)
        packed = gzip.compress(raw, 9)
        with open(os.path.join(out_dir, gz_name), "wb") as f:
            f.write(packed)
        parts[key] = {"gz": gz_name, "json": plain_name}
        plain_total += len(raw)
        gz_total += len(packed)

    meta = {k: v for k, v in payload.items() if k != "snapshots"}
    meta["snapshots"] = inline
    meta["__parts"] = parts
    # 状态栏时间：CI 干净检出时 build_web.py 读不到 web/ 状态文件，这里从
    # run_report.json / collect 状态补齐，保证 Pages 显示真实运行与数据刷新时间。
    meta["status"] = enrich_status(payload.get("status"), as_of)
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    # 防御：内联数据若含 </script> 会提前结束 script 块
    meta_json = meta_json.replace("</script>", "<\\/script>")
    meta_script = "<script>window.__WB__=" + meta_json + ";</script>"

    light = html[:m.start()] + meta_script + html[m.end():]

    # ECharts（约 1 MB）抽成独立同步脚本：保持原有执行顺序，但首屏 HTML 变小、
    # 且浏览器可跨版本复用缓存。
    echarts_name = ""
    for sm in list(SCRIPT_PATTERN.finditer(light)):
        content = sm.group(2)
        if len(content) > 400000 and "window.__WB__=" not in content:
            eh = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
            echarts_name = "echarts.%s.js" % eh
            with open(os.path.join(out_dir, echarts_name), "w", encoding="utf-8") as f:
                f.write(content)
            tag = '<script src="%s"></script>' % echarts_name
            light = light[:sm.start()] + tag + light[sm.end():]
            break

    # 首屏骨架提示改成与按需加载一致的说明，并带版本标记便于确认是否拿到新版
    loading = (
        '<div class="loading" id="loading">离线版 v3 · 数据 ' + as_of +
        '<br /><span style="font-weight:400;font-size:12px">'
        '概览已就绪，正在载入品种详情（按需加载，无需等待全量数据）…<br />'
        '若长时间无响应：下拉刷新，或打开 <a href="' + ONLINE_URL +
        '" style="color:#0a7d4d">WorkBuddy 在线版</a></span></div>')
    light = LOADING_PATTERN.sub(lambda _: loading, light, count=1)

    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(light)

    idx_size = os.path.getsize(os.path.join(out_dir, "index.html"))
    print("[OK] v3 拆分完成：%s (%.2f MB) -> index.html %.2f MB + %s + %d 个品种分片"
          % (os.path.basename(src), os.path.getsize(src) / 1048576,
             idx_size / 1048576, echarts_name or "(未抽出 ECharts)", len(parts)))
    print("     分片合计：明文 %.2f MB / gzip %.2f MB（单次仅下载 1 个）"
          % (plain_total / 1048576, gz_total / 1048576))
    return echarts_name


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法: python collect/split_offline.py <源.html> <输出目录>")
    split(sys.argv[1], sys.argv[2])
