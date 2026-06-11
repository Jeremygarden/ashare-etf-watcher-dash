# 轮4：对照实际数据验证量能因子计算

你在 `~/.etf-skill/scripts/` 工作。请只完成本轮任务，不要提前做后续轮次。

## 目标
检查 `etf_v7_threefactor.py` 中量能因子（P50%）计算方式，并用 `verify_volume.py` 拉取的实际数据验证 `2026-06-10` 计算是否正确。

## 要求
1. 明确量能因子公式：`vr = 当日成交量 / 前20个交易日均量`，`vp = vprob(vr)`，综合分里量能权重为 50%（有份额因子时）。
2. 写一个小脚本或临时 Python 片段，复用/复制 `vprob()` 公式，对四只沪深300ETF计算 `vr` 和 `vp`。
3. 如可行，调用 `etf_v7_threefactor.analyze_all()` 对照输出；如果因为环境/参数不便，说明原因并用等价公式验证。
4. 新建或更新 `RALPH_ROUND4_FACTOR_CHECK.md`：
   - 每只ETF的 `vr`、`vp`
   - 与 `verify_volume.py` 的倍率是否一致
   - 是否存在量能因子计算 bug
5. 最后输出 `COMPLETE`。
