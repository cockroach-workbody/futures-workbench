# 期货分析工作台 · GitHub Pages 自动部署（方案 C）

把工作台变成「定时自动刷新 + 纯静态网页 + 电脑关机也能跑」的最简方案，**不需要任何常驻主机或云端沙箱**。

## 原理

- `dist/期货分析工作台.html` 是一个**单文件、纯静态、自包含**的网页：所有数据（K线/前20净持仓/仓单/机构观点/情绪）在构建时内联进 `window.__WB__`，运行时通过前端 `fetch` 垫片（shim）拦截所有 `/api/*` 返回内嵌数据，**完全不需要后端服务器**。
- GitHub Actions 定时（工作日 19:30 北京）跑 `cloud_cron.py data` 重新抓取数据并重建 `dist`，再把 `dist` 部署到 GitHub Pages。
- 前20净持仓历史曲线依赖 `collect/raw_hist/`，已纳入 git，每次运行后回写仓库以累积曲线。

## 你的机器要做的（一次性）

1. 在 GitHub 新建一个**空仓库**（例如 `futures-workbench`），不要勾选自动生成 README/LICENSE。
2. 在本项目根目录执行：
   ```bash
   git remote add origin git@github.com:<你的用户名>/futures-workbench.git
   git push -u origin main
   ```
   （已 `git init` 并做好了初始提交，含 `.gitignore` 与 workflow 文件。）
3. 仓库 → **Settings → Pages → Build and deployment → Source 选 "GitHub Actions"**。
4. 进入 **Actions** 页，手动 **Run workflow** 跑一次验证（首次建议手动，观察日志）。

## 之后

- 每天工作日 **19:30（北京）** 自动刷新并上线，电脑关机也不影响。
- 访问地址：`https://<你的用户名>.github.io/futures-workbench/`
  （单文件 HTML 在子路径下也能正常工作，因为资源全部内联。）
- 想立刻更新？Actions 页 → 对应工作流 → **Run workflow**。

## 注意事项

- **依赖**：Actions 装 `akshare` + `requests`（运行时联网抓取新浪/东方财富/奇货可查）。已实测 akshare 可装可用。
- **网络**：GitHub 的 ubuntu 运行器出网自由，应能访问上述数据源。若某日新浪接口抖动导致个别品种失败，`build_all` 对单品种失败已容错（仅跳过该品种），不影响整体。
- **时区**：`TZ: Asia/Shanghai` 已设，快照日期按北京时间计算。
- **历史曲线**：`collect/raw_hist/` 必须保留在 git 中（`.gitignore` 已正确处理）。若误删，曲线会从头累积。
- **数据规模**：`data/`（120M 多日快照）、`web/`、`dist/` 均为每次重建产物，已排除在 git 外，仓库保持轻量。
- **不再依赖 WorkBuddy 发布工具**：本方案完全自托管于 GitHub Pages。

## 本地预览 dist

```bash
# 任意静态服务器即可（无需后端）
cd dist && python3 -m http.server 8080
# 浏览器打开 http://127.0.0.1:8080/futures-workbench工作台.html
```
