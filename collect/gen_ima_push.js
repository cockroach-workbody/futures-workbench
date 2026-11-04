// 由 IMA 知识库「期货研报」库（2026-10-29 抓取）的真实最近研报，按板块归类，生成 per-code 的 ima_push_<CODE>.json
// 说明：IMA 内资料为券商每日板块综合研报（广发/五矿/永安/格林大华等），覆盖全品种，最新到 10/29。
// 这些资料是 IMA 私有 PDF，没有可外链的网页地址；故 url 留空，前端按时间排序展示，不渲染无效链接。
const fs = require("fs");
const path = require("path");
// 项目根目录：优先用环境变量 PROJECT_ROOT，否则取本脚本所在目录的上一级（collect 的上一级）。
const ROOT = process.env.PROJECT_ROOT || path.resolve(__dirname, "..");
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, "app/config/products.json"), "utf8"));

// 真实最近研报（来源：IMA「期货研报」库，create_time 转 ms 时间戳用于排序）
const RAW = [
  // 综合（归入所有板块）
  { title: "永安期货晨会纪要20261029", date: "昨天", source: "永安期货", sector: "综合", ts: 1793235758435, summary: "每日晨会纪要：覆盖宏观/股指/黑色/有色/能化/农产品全板块行情与研判。" },
  { title: "五矿期货文字早评20261029", date: "昨天", source: "五矿期货", sector: "综合", ts: 1793235006939, summary: "文字早评：宏观金融、股指及全品种行情资讯综述。" },
  { title: "永安瞭望周刊20261027", date: "10/27", source: "永安期货", sector: "综合", ts: 1793089940658, summary: "瞭望周刊：宏观、股指、黑色、能化、有色、农产品各板块回顾与展望。" },
  // 黑色建材
  { title: "五矿期货黑色建材早报20261029", date: "昨天", source: "五矿期货", sector: "黑色建材", ts: 1793235011579, summary: "黑色建材日报：钢材、铁矿石、焦煤焦炭等最新行情资讯与基差数据。" },
  { title: "广发期货《黑色》日报20261028", date: "10/28", source: "广发期货", sector: "黑色建材", ts: 1793148879460, summary: "钢材产业期现日报：螺纹钢现货(华东)3190、05合约等价格及基差。" },
  // 有色金属
  { title: "广发期货《有色》日报20261029", date: "昨天", source: "广发期货", sector: "有色金属", ts: 1793235161279, summary: "铜产业期现日报：SMM电解铜价格、升贴水及基差。" },
  { title: "五矿期货有色金属早报20261029", date: "昨天", source: "五矿期货", sector: "有色金属", ts: 1793235095861, summary: "有色金属日报：铜铝锌镍锡铅等品种行情与小组研判。" },
  { title: "广发期货《有色》日报20261028", date: "10/28", source: "广发期货", sector: "有色金属", ts: 1792974902694, summary: "铝产业链期现日报及铜等有色金属价格价差。" },
  // 化工品
  { title: "广发期货《能源化工》日报20261029", date: "昨天", source: "广发期货", sector: "化工品", ts: 1793235177401, summary: "天然橡胶产业期现日报：现货价格、基差及价差。" },
  { title: "五矿期货能源化工早报20261029", date: "昨天", source: "五矿期货", sector: "化工品", ts: 1793235086904, summary: "能源化工日报：橡胶、甲醇、尿素、聚烯烃、聚酯等。" },
  { title: "广发期货《能源化工》日报20261028", date: "10/28", source: "广发期货", sector: "化工品", ts: 1793148876755, summary: "甲醇产业期现日报：MA2701收盘价3448等价格及价差。" },
  // 农产品
  { title: "广发期货《农产品》日报20261029", date: "昨天", source: "广发期货", sector: "农产品", ts: 1793235173919, summary: "油脂产业期现日报：豆油、棕榈油现货与期价基差。" },
  { title: "五矿期货农产品早报20261029", date: "昨天", source: "五矿期货", sector: "农产品", ts: 1793235090515, summary: "农产品早报：白糖、豆类、玉米、生猪等品种资讯。" },
  { title: "广发期货《农产品》日报20261028", date: "10/28", source: "广发期货", sector: "农产品", ts: 1793148873072, summary: "油脂产业期现日报：豆油、棕榈油菜油价格及基差。" },
  { title: "【鸡蛋专题20261029】：交易空间有限", date: "昨天", source: "五矿期货", sector: "农产品", ts: 1793235114289, summary: "鸡蛋专题：现货反弹但老鸡偏多、新开产增加等利空未解，盘面带动有限。" },
  { title: "格林大华期市早班车-玉米生猪鸡蛋20261026", date: "10/26", source: "格林大华", sector: "农产品", ts: 1792974700501, summary: "玉米/生猪/鸡蛋早盘提示：新粮上量、储备库开收、惜售等市场分析。" },
  // 贵金属（额外归入 AU/AG）
  { title: "广发期货贵金属日报20261029", date: "昨天", source: "广发期货", sector: "贵金属", ts: 1793235168709, summary: "贵金属期现日报：AU2612合约867.40、AG2612合约13010等收盘价。" },
  { title: "五矿期货贵金属早报20261029", date: "昨天", source: "五矿期货", sector: "贵金属", ts: 1793235093258, summary: "贵金属日报：沪金、沪银及COMEX金银行情资讯。" },
  { title: "广发期货贵金属日报20261028", date: "10/28", source: "广发期货", sector: "贵金属", ts: 1792974893068, summary: "贵金属期现日报：AU2612、AG2612、PT2612收盘价及涨跌。" }
];

function pick(code, sector) {
  const extra = (code === "AU" || code === "AG") ? ["贵金属"] : [];
  const items = RAW.filter(r => r.sector === sector || r.sector === "综合" || extra.includes(r.sector));
  return items.sort((a, b) => b.ts - a.ts).slice(0, 6);
}

let count = 0;
cfg.categories.forEach(c => {
  c.products.forEach(p => {
    const items = pick(p.code, c.name).map(r => ({
      title: r.title, date: r.date, source: r.source, media_type: "PDF研报",
      summary: r.summary, _ts: r.ts, url: ""
    }));
    const f = path.join(ROOT, "collect", "ima_push_" + p.code + ".json");
    fs.writeFileSync(f, JSON.stringify(items, null, 2), "utf8");
    count++;
  });
});
console.log("生成 ima_push 文件:", count);
