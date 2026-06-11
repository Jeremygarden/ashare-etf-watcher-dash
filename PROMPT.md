# 轮2：写独立成交量验证脚本

你在 `~/.etf-skill/scripts/` 工作。请只完成本轮任务，不要提前做后续轮次。

## 目标
写一个独立 Python 脚本 `verify_volume.py`，用腾讯财经 K 线 API 直接拉取沪深300 ETF 近至少 30 日成交量数据，打印出来，重点关注 `2026-06-10`。

## ETF 列表
使用 `etf_v7_threefactor.py` 中沪深300 ETF 代码：
- 510300
- 510310
- 510330
- 159919

## 要求
- 不依赖 akshare 或第三方包，只用 Python 标准库。
- 直接请求腾讯 URL：`http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pfx}{code},day,,,{limit},qfq`
- 正确解析 `day` 或 `qfqday`。
- 输出每只 ETF 近 30 日日期、收盘价、成交量原始值、成交量万单位。
- 额外计算并输出目标日 `2026-06-10` 相对：
  - 前 20 个交易日均量倍率
  - 可用历史窗口均量倍率（如果不足 60 日也要说明）
- 目标日不存在时要明确输出。
- 脚本要支持命令行参数：`--target-date`，默认 `2026-06-10`；`--limit`，默认 `90`。
- 最后输出 `COMPLETE`。
