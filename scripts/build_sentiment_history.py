#!/usr/bin/env python3
"""
市场情绪三因子历史数据回溯
数据源：akshare（历史涨停/炸板）+ 腾讯财经K线（历史指数涨跌幅）
输出：sentiment_history.json
"""
import sys, os, json
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import akshare as ak
except ImportError:
    print("需要 akshare: pip3 install akshare --break-system-packages")
    sys.exit(1)

import etf_v7_threefactor as etf

WORKSPACE = os.path.expanduser(os.environ.get("ETF_WORKSPACE", "~/.etf-skill/workspace"))
OUTPUT = os.path.join(WORKSPACE, "sentiment_history.json")

def norm(v, a, b):
    return max(0, min(100, (v - a) / (b - a) * 100))

def calc_market(sh_chg, up_ratio=0.5, north_yi=0):
    a = norm(sh_chg, -2, 2)
    b = norm(up_ratio * 100, 30, 70)
    c = norm(north_yi, -80, 80)
    return round(a * 0.45 + b * 0.35 + c * 0.20)

def calc_sentiment(zt_count, zb_rate, sh_chg):
    # 超短情绪：
    # 涨停数（主因子，范围0-120，实测标定）+ 封板率（反映质量）+ 指数涨跌
    a = norm(zt_count, 0, 120)         # 涨停数 [0-120只] → [0-100]
    b = norm(100 - zb_rate, 20, 90)   # 封板率（炸板率反向）[20-90%] → [0-100]
    c = norm(sh_chg, -2, 2)            # 指数当日涨跌
    return round(a * 0.55 + b * 0.30 + c * 0.15)

def calc_weak(zb_rate, sh_chg):
    # 亏钱效应（已标定：norm(zb_rate, 0, 60) 与截图高度吻合）
    # 炸板率 [0-60%] → [0-100]，加少量大盘反向修正
    a = norm(zb_rate, 0, 60)           # 炸板率主因子
    b = norm(-sh_chg, -2, 2)           # 指数跌 = 亏钱加重
    return round(a * 0.80 + b * 0.20)

def calc_three_line(market, sentiment, weak):
    return round((market + sentiment + (100 - weak)) / 3)

def calc_composite(market, sentiment, weak, three_line):
    return round(market * 0.35 + sentiment * 0.25 + (100 - weak) * 0.20 + three_line * 0.20)

def get_trading_dates(n=75):
    dates = []
    d = datetime.now()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    return list(reversed(dates))

def build_history(days=60):
    print(f"=== 开始回溯 {days} 日市场情绪历史数据 ===")
    
    # 1. 获取上证指数历史K线（包含每日涨跌幅）
    print("获取上证指数历史K线...")
    sh_klines = etf.fetch("000001", 90)
    sh_dates = sorted(k['date'] for k in sh_klines)
    sh_map = {k['date']: k for k in sh_klines}
    
    def get_sh_chg(date_key):
        if date_key not in sh_map:
            return 0
        idx = sh_dates.index(date_key) if date_key in sh_dates else -1
        if idx <= 0:
            return 0
        c0 = sh_map[date_key]['c']
        c1 = sh_map[sh_dates[idx-1]]['c']
        return round((c0 - c1) / c1 * 100, 2) if c1 else 0
    
    # 2. 逐日获取涨停/炸板数据
    trading_dates = get_trading_dates(days + 15)  # 多取一些应对节假日
    valid_days = [d for d in trading_dates if f"{d[:4]}-{d[4:6]}-{d[6:]}" in sh_map]
    valid_days = valid_days[-days:]  # 取最近 days 个交易日
    
    history = []
    ok_count = 0
    
    for d8 in valid_days:
        date_key = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        label = f"{d8[4:6]}/{d8[6:]}"  # MM/DD 格式
        
        print(f"  {date_key}...", end=" ")
        
        sh_chg = get_sh_chg(date_key)
        
        try:
            df_zt = ak.stock_zt_pool_em(date=d8)
            df_zb = ak.stock_zt_pool_zbgc_em(date=d8)
            zt = len(df_zt)
            zb = len(df_zb)
        except Exception as e:
            print(f"跳过（{str(e)[:30]}）")
            continue
        
        total = zt + zb
        zb_rate = round(zb / total * 100, 1) if total > 0 else 0
        fb_rate = round(100 - zb_rate, 1)
        
        market     = calc_market(sh_chg)
        sentiment  = calc_sentiment(zt, zb_rate, sh_chg)
        weak       = calc_weak(zb_rate, sh_chg)
        three_line = calc_three_line(market, sentiment, weak)
        composite  = calc_composite(market, sentiment, weak, three_line)
        
        history.append({
            "date": label,
            "date_full": date_key,
            "sh_chg": sh_chg,
            "zt_count": zt,
            "zb_count": zb,
            "zb_rate": zb_rate,
            "fb_rate": fb_rate,
            "market": market,
            "sentiment": sentiment,
            "weak": weak,
            "three_line": three_line,
            "composite": composite,
        })
        ok_count += 1
        print(f"✅ 大盘={market} 超短={sentiment} 亏钱={weak} 综合={composite} (涨停={zt} 炸板率={zb_rate}%)")
    
    print(f"\n✅ 完成！成功回溯 {ok_count}/{len(valid_days)} 个交易日")
    
    # 输出 JSON
    os.makedirs(WORKSPACE, exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "days": len(history),
            "history": history,
        }, f, ensure_ascii=False, indent=2)
    print(f"已保存到: {OUTPUT}")
    
    return history

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    build_history(args.days)
