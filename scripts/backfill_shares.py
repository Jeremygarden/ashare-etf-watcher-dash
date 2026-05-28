#!/usr/bin/env python3
"""
历史份额回溯脚本 — 回补最近240个交易日的ETF份额数据
数据源：akshare 上交所/深交所官方接口
"""
import sys, os, json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_v7_threefactor as etf

try:
    import akshare as ak
    import pandas as pd
except ImportError:
    print("❌ akshare 未安装，运行: pip3 install akshare --break-system-packages")
    sys.exit(1)

WORKSPACE = etf.WORKSPACE
SHARES_OUT = os.path.join(WORKSPACE, "etf_shares_history.json")

def load_history():
    if not os.path.exists(SHARES_OUT):
        return {}
    with open(SHARES_OUT) as f:
        return json.load(f)

def save_history(h):
    dates = sorted(h.keys())
    if len(dates) > 400:
        for old in dates[:-400]:
            del h[old]
    os.makedirs(WORKSPACE, exist_ok=True)
    with open(SHARES_OUT, 'w') as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

def get_trading_dates(n=240):
    """生成过去n个工作日的日期列表"""
    dates = []
    d = datetime.now()
    while len(dates) < n:
        if d.weekday() < 5:  # 周一到周五
            dates.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    return list(reversed(dates))

def backfill(n=240):
    history = load_history()
    dates = get_trading_dates(n)
    
    # 找出还没有数据的日期
    sse_codes = [c for c in etf.ETFS if not c.startswith('159')]
    szse_codes = [c for c in etf.ETFS if c.startswith('159')]
    
    missing = [d for d in dates if d[:4]+'-'+d[4:6]+'-'+d[6:] not in history]
    print(f"需要补充: {len(missing)} 个日期 (共 {n} 个交易日)")
    
    ok_count = 0
    for dt in dates:
        date_key = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
        if date_key in history and all(c in history[date_key] for c in sse_codes[:2]):
            continue  # 已有数据
        
        print(f"  {dt}...", end=" ")
        day_data = {}
        
        # 上交所 ETF
        try:
            df_sse = ak.fund_etf_scale_sse(date=dt)
            if df_sse is not None and '基金代码' in df_sse.columns:
                for code in sse_codes:
                    row = df_sse[df_sse['基金代码'] == code]
                    if len(row) > 0:
                        try:
                            shares_yi = round(float(row['基金份额'].values[0]) / 1e8, 4)
                            day_data[code] = {"shares_yi": shares_yi, "ts": date_key + "T19:00:00"}
                        except:
                            pass
        except Exception as e:
            if "None of" not in str(e):  # 非交易日报错正常
                print(f"SSE err: {str(e)[:30]}", end=" ")
        
        # 深交所 ETF（批量获取较慢，只在缺失时请求）
        if szse_codes and not all(c in day_data for c in szse_codes):
            try:
                df_sz = ak.fund_scale_daily_szse(start_date=dt, end_date=dt, symbol='ETF')
                if df_sz is not None and len(df_sz) > 0:
                    for code in szse_codes:
                        rows = df_sz[df_sz['基金代码'] == code]
                        if len(rows) > 0:
                            shares_yi = round(float(rows['基金份额'].values[0]) / 1e8, 4)
                            day_data[code] = {"shares_yi": shares_yi, "ts": date_key + "T19:00:00"}
            except Exception as e:
                if "Connection" not in str(e):
                    print(f"SZSE err: {str(e)[:30]}", end=" ")
        
        if day_data:
            history[date_key] = day_data
            ok_count += 1
            print(f"✅ {len(day_data)}/9只")
        else:
            print("⏭️ 非交易日")
    
    save_history(history)
    total_days = len([d for d in history if any(c in history[d] for c in etf.ETFS)])
    print(f"\n✅ 完成！历史份额: {total_days} 个交易日")
    return history

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    print(f"=== 开始回溯 {n} 日历史份额 ===")
    backfill(n)
