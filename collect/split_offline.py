"""把离线单文件 HTML 拆成「轻量 index.html + 独立数据脚本」。

背景：单文件版把 10MB+ 的 window.__WB__ 数据内联在 HTML 末尾，浏览器必须等
整个文件下载并解析完才会执行 app.js；在慢网络 / 手机 / 内置 webview 上，
这段时间页面只显示骨架「加载中…」，容易被误认为「没有数据」。

拆分后：
  - index.html 约 1MB（CSS + ECharts + charts.js + app.js + shim），可秒开并显示骨架提示；
  - wb-data.<hash>.js 单独加载（带内容哈希，避免 CDN/浏览器缓存新旧不一致）；
  - 外链脚本保持同步执行顺序，shim 与 app.js 无需任何改动。

用法：python collect/split_offline.py <源单文件.html> <输出目录>
若源文件不含内嵌数据脚本，返回非 0，由调用方回退为单文件复制。
"""

import hashlib
import os
import re
import sys

PATTERN = re.compile(r"<script>\s*window\.__WB__=(.*?);\s*</script>", re.S)


def split(src, out_dir):
    with open(src, encoding="utf-8") as f:
        html = f.read()
    m = PATTERN.search(html)
    if not m:
        raise SystemExit("未找到 window.__WB__ 内嵌数据脚本，保持单文件")
    payload = m.group(1)
    data_js = "window.__WB__=" + payload + ";"
    digest = hashlib.sha256(data_js.encode("utf-8")).hexdigest()[:10]
    name = "wb-data.%s.js" % digest

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        f.write(data_js)
    light = html[:m.start()] + '<script src="%s"></script>' % name + html[m.end():]
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(light)

    src_mb = os.path.getsize(src) / 1024 / 1024
    idx = os.path.join(out_dir, "index.html")
    dat = os.path.join(out_dir, name)
    print("[OK] 拆分完成：%s (%.2f MB) -> index.html %.2f MB + %s %.2f MB" % (
        os.path.basename(src), src_mb, os.path.getsize(idx) / 1024 / 1024,
        name, os.path.getsize(dat) / 1024 / 1024))
    return name


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法: python collect/split_offline.py <源.html> <输出目录>")
    split(sys.argv[1], sys.argv[2])
