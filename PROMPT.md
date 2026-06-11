# 轮3：执行验证脚本并分析异常性

你在 `~/.etf-skill/scripts/` 工作。请只完成本轮任务，不要提前做后续轮次。

## 目标
执行 `verify_volume.py`，分析 `2026-06-10` 沪深300 ETF 成交量是否异常。

## 要求
1. 运行：`python3 verify_volume.py --target-date 2026-06-10 --limit 90`
2. 保存完整输出到 `volume_verify_2026-06-10.txt`。
3. 分析每只 ETF 目标日成交量相对前20日/60日均值的倍率。
4. 新建或更新 `RALPH_ROUND3_ANALYSIS.md`：
   - 每只ETF的目标日成交量、20日倍率、60日倍率
   - 是否异常（给出阈值，例如 >1.5x 才算温和放量，>2x 明显放量）
   - 总体结论
5. 最后输出 `COMPLETE`。
