# IMPLEMENTATION_PLAN.md — ETF Watcher Bug Fix

## Status: IN PROGRESS

## Task List

| # | Area | Bug | Status | Notes |
|---|------|-----|--------|-------|
| 1 | Backend | 沪深300 K线 fetch 偶发失败，fallback 逻辑验证 | N/A | fetch_with_retry() 已实现双入口轮转，代码逻辑完整，实测运行正常 |
| 2 | Backend | 板块资金流 502 时 HTML 模板空数据崩溃 | DONE | fetch_sector_flow() 已返回 {top_in:[], top_out:[], updated:"--"}；前端已有 \|\| [] 保护 |
| 3 | Backend | 东方财富份额 588000 获取失败，fallback 验证 | N/A | _EM_SHARE_URLS 双入口 + fetch_with_retry 已实现，逻辑完整 |
| 4 | Data | SQLite 并发写入 database locked 错误 | DONE | 新增 _connect() 方法：WAL mode + busy_timeout=10000ms；所有 sqlite3.connect 改用 self._connect() |
| 5 | Data | save_shares_history 写失败无事务回滚 | DONE | 改为原子写入：先写 .tmp 文件，os.replace() 替换（POSIX 原子操作）；失败时清理 .tmp |
| 6 | Data | shares_history.json / sentiment_history.json 初次运行初始化 | N/A | gen_dashboard.py 已有 fallback：先检查 shares_history.json 文件，失败则继续；market_sentiment.html 加载失败时有 catch + generateHistory(60) fallback |
| 7 | Frontend | etf_dashboard.html auto-refresh 竞争条件 | N/A | index.html 使用 setInterval(location.reload, 31min) 全页重载，无竞争条件；fetchLiveData() 有 try/catch 降级到 reload |
| 8 | Frontend | market_sentiment.html compositeVal 静态默认值/实时更新 | DONE | 修复 self-test 中 calcSentiment 参数错误：testData.avgTo → testData.zbRate，testData.mainNetYi → testData.sh3 |
| 9 | Frontend | 前端 JSON 加载失败时白屏无 fallback | N/A | index.html loadSentimentCard() 已有 catch → badge 显示"数据加载失败"；主 BACKEND 数据在 HTML 内联，不依赖外部 JSON |
| 10 | Frontend/Backend | etf_data.json schema 一致性验证 | N/A | gen_dashboard.py 输出字段与 index.html BACKEND 消费字段一致（均含 indices/etfs/sector_flow/ns_flow） |

## Completed Tasks
- Task 4: SQLite WAL mode + busy_timeout（etf_data_store.py）
- Task 5: save_shares_history 原子写入（etf_v7_threefactor.py）
- Task 8: market_sentiment.html self-test 参数修复

## Iteration Log

### Iteration 1 (2026-06-03)
- 读取 specs/bugs.md 和 IMPLEMENTATION_PLAN.md
- 运行 backpressure tests：SYNTAX OK，20 tests passed
- 调查所有10个bug
- 修复：Task 4（SQLite WAL），Task 5（原子写入），Task 8（calcSentiment 参数）
- N/A：Task 1/3/6/7/9/10（代码已有正确实现）
- 确认：Task 2（sector_flow 空数据安全）
- 所有 backpressure tests 仍通过

STATUS: COMPLETE
