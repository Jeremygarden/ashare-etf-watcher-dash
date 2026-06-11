# Ralph Round 1 Notes — 成交量数据源与量能因子

## 结论概览

`etf_v7_threefactor.py` 的成交量数据来自腾讯财经日 K 线接口，备用为新浪实时行情单日数据；系统没有计算“成交量分位数”，而是计算当日成交量相对前 20 个交易日均量的倍率 `vr`，再通过 `vprob(vr)` 映射为量能概率 `vp`（模型权重 50%）。

## 1. 成交量数据来源

- ETF 列表定义：`ETFS`，约第 63 行。
  - 沪深300 ETF 为：
    - `510300` 华泰柏瑞沪深300ETF
    - `510310` 易方达沪深300ETF（任务背景称“国泰”，但脚本当前名称为易方达）
    - `510330` 华夏沪深300ETF
    - `159919` 嘉实沪深300ETF
- 腾讯 K 线解析函数：`_parse_tencent_kline(raw, pfx, code)`，约第 129 行。
  - 读取 JSON 路径：`data.{pfx}{code}.day` 或 `data.{pfx}{code}.qfqday`。
  - 每行字段解析：`[date, open, close, high, low, volume]`。
  - `v = float(r[5])`。
- 主获取函数：`fetch(code, limit=60)`，约第 171 行。
  - 市场前缀：`pfx = "sz" if code.startswith("159") else "sh"`。
  - 腾讯财经 HTTP URL，约第 180 行：
    `http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pfx}{code},day,,,{limit},qfq`
  - 默认拉取 60 日，主流程调用时实际使用 `fetch(code, 90)`（见后续主流程附近）。
- 备用数据源：`_parse_sina_kline(raw, code)` + 新浪 `hq.sinajs.cn`，约第 143-169、193 行。
  - 只返回当日单条。
  - 新浪字段 `[8]` 是成交量（手），脚本转换为股/份：`float(fields[8]) * 100`。

## 2. 成交量如何存储/传递

- `fetch()` 返回 `list[dict]`，每条形如：
  `{"date": YYYY-MM-DD, "o": open, "c": close, "h": high, "l": low, "v": volume}`。
- 腾讯返回的 `v` 被直接存为 `float(r[5])`；脚本没有进一步保存原始 K 线到本地数据库，主流程将该列表传给 `analyze_all(data, idx_d, shares_map, target_date, days=35)`。
- `analyze_all()` 输出中的：
  - `v`: `round(d["v"] / 10000, 2)`，即以“万份/万股”为展示单位。
  - `vma`: 前 20 个交易日均量，同样除以 10000。
  - `vr`: 当日成交量 / 前 20 个交易日均量。

## 3. 量能因子计算方式

核心函数：

- `analyze_all()`，约第 393 行。
  - 当日成交量：第 401 行 `v = d["v"] / 10000`
  - 前 20 日成交量：第 402 行 `pv = [data[j]["v"] / 10000 for j in range(i - 20, i)]`
  - 前 20 日均量：第 403 行 `ma = sum(pv) / 20`
  - 量能倍率：第 406 行 `vr = v / ma`
  - 量能概率：第 423 行 `vp = vprob(vr)`
- `vprob(r)`，约第 315 行。
  - 这是分段线性映射，不是统计分位数：
    - `r < 0.5` → 0~5
    - `0.5 <= r < 1.0` → 5~17
    - `1.0 <= r < 1.3` → 17~35
    - `1.3 <= r < 1.5` → 35~55
    - `1.5 <= r < 2.0` → 55~80
    - `2.0 <= r < 3.0` → 80~95
    - `3.0 <= r < 5.0` → 95~98
    - `r >= 5.0` → 98~100 capped
- 综合分数：
  - 份额因子可用：`cp = round(vp * 0.5 + dp * 0.2 + sp * 0.3, 1)`（约第 440 行）
  - 份额因子不可用：`cp = round(vp * 0.7 + dp * 0.3, 1)`（约第 442 行）

## 4. 本轮发现的潜在风险点

1. 代码和文案里若称“量能分位数”，实际并不是 percentile，而是“相对 20 日均量倍率 + 分段映射”。
2. 腾讯 K 线 `r[5]` 的单位需要在下一轮独立验证（通常腾讯日 K 成交量字段可能为手/股/份，脚本直接使用，但倍率 `vr` 不受统一单位影响）。
3. 如果 2026-06-10 成交量异常但系统未报高分，可能原因包括：
   - `vr` 未显著高于前 20 日均量；
   - 方向因子 `dp` 或份额因子 `sp` 拉低综合分；
   - 目标日期数据未进入 `fetch(..., limit)` 返回窗口或没有作为 `target_date` 分析；
   - 份额历史数据缺失导致模型退化。

COMPLETE
