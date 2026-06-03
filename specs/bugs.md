# Bug Fix Specs — ashare-etf-watcher-dash

## Project Context
A股 ETF 国家队三因子监控看板。技术栈：
- Backend: Python 3 (etf_v7_threefactor.py, gen_dashboard.py, etf_data_store.py)
- Frontend: 纯 HTML/JS (index.html, etf_dashboard.html, market_sentiment.html)
- Data layer: SQLite (etf_data_store.py) + JSON files (shares_history.json, sentiment_history.json)
- CI: GitHub Actions (vercel deploy)

## Known & Suspected Bugs to Investigate and Fix

### 1. [Frontend] Dashboard data stale / auto-refresh issues
- index.html / etf_dashboard.html 的自动刷新逻辑可能有竞争条件或错误处理缺失
- etf_data.json 路径硬编码或相对路径不一致导致前端读取失败

### 2. [Frontend] market_sentiment.html
- compositeVal 初始值 hardcoded 到 29（静态默认值），实时更新逻辑可能断开
- calcSentiment / calcWeakMoney 参数校验缺失（边界值 NaN/Infinity 未处理）
- sentiment_history.json 加载失败时没有 fallback 显示

### 3. [Backend / Data] 沪深300 K线数据获取失败
- fetch() 腾讯财经主入口偶发失败，新浪备用入口 fallback 是否正确触发需验证
- _parse_tencent_kline / _parse_sina_kline 解析逻辑边界处理

### 4. [Backend] 板块资金流 502 Bad Gateway
- fetch_sector_flow() 东方财富板块 API 502 时程序只打印 warning，但 HTML 模板可能因空数据崩溃
- 需要确保空 sector_flow 时 gen_dashboard.py 和 HTML 模板不崩溃

### 5. [Data layer] etf_data_store.py
- SQLite 并发写入（GitHub Actions + 本地同时运行）可能导致 database is locked 错误
- save_shares_history() 在写失败时是否有事务回滚

### 6. [Backend] 东方财富份额接口 fallback
- fetch_fund_shares_realtime() ut1 → ut2 双入口是否真正 fallback（588000 获取失败）
- _parse_em_shares() 对非标准 f84 字段格式的容错

### 7. [Frontend/Backend] JSON 数据格式一致性
- etf_data.json schema 与 gen_dashboard.py 生成逻辑 vs index.html / etf_dashboard.html 消费逻辑是否对齐
- shares_history.json 和 sentiment_history.json 在初次运行（空文件）时的初始化

## Acceptance Criteria
- `python3 scripts/etf_v7_threefactor.py` 运行无 unhandled exception
- `python3 scripts/gen_dashboard.py` 运行无 unhandled exception，生成有效 HTML
- `node scripts/test_sentiment_functions.js` 所有测试通过
- `node scripts/health_check.js` 通过
- 前端 HTML 在 etf_data.json / sentiment_history.json 加载失败时显示错误提示而非白屏
- 所有 except 块有明确的错误处理，不静默吞掉关键异常
