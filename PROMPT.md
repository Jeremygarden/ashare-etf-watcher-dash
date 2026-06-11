# 轮5：写最终验证报告

你在 `~/.etf-skill/scripts/` 工作。请只完成本轮任务。

## 目标
生成最终报告 `VOLUME_VERIFY_REPORT.md`，总结 2026-06-10 沪深300 ETF 成交量数据、是否异常、三因子系统为何未检测到，以及是否有 bug。

## 输入材料
- `RALPH_ROUND1_NOTES.md`
- `verify_volume.py`
- `volume_verify_2026-06-10.txt`
- `RALPH_ROUND3_ANALYSIS.md`
- `RALPH_ROUND4_FACTOR_CHECK.md`
- `etf_v7_threefactor.py`

## 报告必须包含
1. 执行摘要。
2. 数据源与口径：腾讯财经 K 线成交量字段；系统没有用 akshare；份额来自东方财富但本报告重点是成交量。
3. 四只沪深300ETF 2026-06-10 成交量表格：成交量、前20日倍率、60日窗口倍率、`vp`。
4. 异常性判定：是否放量，阈值说明。
5. 三因子系统未检测到的原因。
6. bug 结论：量能因子是否有计算 bug；如有文案/口径风险也说明。
7. 后续建议：若用户仍认为异常，应核对成交额/实时盘口/其他ETF范围/不同数据源。

最后输出 `COMPLETE`。
