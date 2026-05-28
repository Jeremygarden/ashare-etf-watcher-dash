#!/usr/bin/env python3
"""
ETF国家队监控看板生成器 v1.0
生成包含实时数据的独立 HTML 看板（etf_dashboard.html）
用法: python3 gen_dashboard.py
"""
import json, ssl, urllib.request, os, sys, re
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import etf_v7_threefactor as etf

WORKSPACE    = etf.WORKSPACE
TEMPLATE_IN  = os.path.join(os.path.dirname(_SCRIPT_DIR), "workspace", "etf_dashboard.html")
OUTPUT_HTML  = os.path.join(WORKSPACE, "etf_dashboard.html")

# ── 取指数数据 ──
def fetch_index_chg(bare_code):
    pfx = "sh" if bare_code.startswith(("000","600","601","603","688","51","56","58")) else "sz"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pfx}{bare_code},day,,,3,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        key = f"{pfx}{bare_code}"
        rows = (d.get("data",{}).get(key,{}).get("qfqday") or
                d.get("data",{}).get(key,{}).get("day") or [])
        if len(rows) >= 2:
            c0, c1 = float(rows[-1][2]), float(rows[-2][2])
            return {"price": round(c0,2), "chg": round((c0-c1)/c1*100,2)}
    except Exception:
        pass
    return {"price": 0, "chg": 0}

# ── 主生成函数 ──
def generate():
    print("🏗️  生成ETF监控看板...")
    os.makedirs(WORKSPACE, exist_ok=True)

    # 1. 指数
    print("  📊 获取指数数据...")
    indices = {
        "sh001": fetch_index_chg("000001"),   # 上证指数
        "hs300": fetch_index_chg("000300"),   # 沪深300
        "sz50":  fetch_index_chg("000016"),   # 上证50
        "cyb":   fetch_index_chg("399006"),   # 创业板综
    }
    print(f"     沪深300: {indices['hs300']['chg']:+.2f}%  上证50: {indices['sz50']['chg']:+.2f}%")

    # 2. ETF数据（K线+份额）
    print("  📈 获取ETF行情+份额...")
    shares_history = etf.load_shares_history()
    idx_300_data   = etf.fetch("sh000300", 240)     # 复用主脚本 fetch（加前缀兼容）
    idx_300_data   = etf.fetch("000300", 240)

    etf_results = {}
    for code, info in etf.ETFS.items():
        print(f"     {code} {info['n'][:10]}...", end=" ")
        klines = etf.fetch(code, 240)
        # 实时份额：东方财富（双入口 fallback）
        sh = etf.fetch_fund_shares_realtime(code)
        # 如果实时接口失败，尝试从 akshare 获取（当日 or 最近交易日）
        if sh is None:
            try:
                import akshare as ak
                secid = etf._EM_SECID.get(code,'')
                is_szse = code.startswith('159')
                today8 = datetime.now().strftime('%Y%m%d')
                if is_szse:
                    df_fb = ak.fund_scale_daily_szse(start_date=today8, end_date=today8, symbol='ETF')
                    if df_fb is not None and len(df_fb) > 0:
                        rows = df_fb[df_fb['基金代码']==code]
                        if len(rows)>0:
                            shares_yi = round(float(rows['基金份额'].values[0])/1e8, 4)
                            sh = {"shares_yi": shares_yi, "price": 0}
                else:
                    df_fb = ak.fund_etf_scale_sse(date=today8)
                    if df_fb is not None and '基金代码' in df_fb.columns:
                        rows = df_fb[df_fb['基金代码']==code]
                        if len(rows)>0:
                            shares_yi = round(float(rows['基金份额'].values[0])/1e8, 4)
                            sh = {"shares_yi": shares_yi, "price": 0}
            except Exception:
                pass
        # 最终 fallback：使用历史最近一日的份额
        if sh is None:
            _share_dates2 = sorted(shares_history.keys())
            for _d in reversed(_share_dates2):
                if code in shares_history.get(_d, {}):
                    sh = {"shares_yi": shares_history[_d][code].get("shares_yi",0), "price": 0, "_from_history": True}
                    break

        if not klines or len(klines) < 22:
            print("⚠️ K线不足")
            continue

        last = klines[-1]
        v    = last["v"] / 10000
        pv   = [klines[j]["v"] / 10000 for j in range(len(klines)-21, len(klines)-1)]
        ma   = sum(pv) / len(pv) if pv else 1
        vr   = v / ma
        pc   = klines[-2]["c"]
        chg  = round((last["c"] - pc) / pc * 100, 2) if pc else 0

        vp = round(etf.vprob(vr), 1)
        dp = round(etf.dprob(chg, 0, 0, vr, indices["hs300"]["chg"]), 1)

        # 查最近有份额数据的交易日（今日可能是非交易日）
        _share_dates = sorted(shares_history.keys())
        _query_date = last["date"]
        if _query_date not in shares_history and _share_dates:
            # 找最近的一个有数据且不晚于今日的日期
            _cands = [d for d in _share_dates if d <= _query_date]
            _query_date = _cands[-1] if _cands else _share_dates[-1]
        t_sh, p_sh, delta_yi, delta_pct = etf.get_historical_share(code, _query_date, shares_history)
        sp = etf.sprob(delta_pct) if delta_pct is not None else None
        sp = round(sp, 1) if sp is not None else None

        if sp is not None:
            cp = round(vp*0.5 + dp*0.2 + sp*0.3, 1)
        else:
            cp = round(vp*0.7 + dp*0.3, 1)

        # 把近240日K线也打进去（用于柱状图）
        # 构建 klines，加入份额历史 s 字段（用于基金规模折线图）
        _shares_map = shares_history  # 借用已加载的历史
        klines_short = []
        for k in klines[-240:]:
            entry = {"date":k["date"],"c":round(k["c"],4),"v":k["v"]}
            day_shares = _shares_map.get(k["date"],{})
            if isinstance(day_shares, dict) and code in day_shares:
                entry["s"] = round(day_shares[code].get("shares_yi",0), 4)
            klines_short.append(entry)

        shares_val = sh["shares_yi"] if sh else (t_sh or 0)

        etf_results[code] = {
            "date":      last["date"],
            "price":     last["c"],
            "chg":       chg,
            "v":         round(v, 2),
            "vma":       round(ma, 2),
            "vr":        round(vr, 2),
            "vp":        vp,
            "dp":        dp,
            "sp":        sp,
            "cp":        cp,
            "shares_yi": round(shares_val, 2) if shares_val else 0,
            "delta_yi":  round(delta_yi, 2) if delta_yi is not None else None,
            "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
            "has_shares": sh is not None and sh.get("shares_yi") is not None,
            "klines":    klines_short,
        }
        flag = "三因子" if sp is not None else "二因子"
        print(f"✅ CP={cp}% vr={vr:.2f}x [{flag}]")

    print("  📊 获取板块资金流...")
    sector_flow = etf.fetch_sector_flow(10)
    print(f"     流入前3: {[x['name'] for x in sector_flow['top_in'][:3]]}")

    print("  📊 获取南北向资金...")
    ns_flow = etf.fetch_northsouth_flow()
    print(f"     北向: {ns_flow['north_yi']:+.1f}亿  南向: {ns_flow['south_yi']:+.1f}亿")

    # 3. 注入数据到 HTML 模板
    backend_data = json.dumps({
        "indices":     indices,
        "etfs":        etf_results,
        "sector_flow": sector_flow,
        "ns_flow":     ns_flow,
    }, ensure_ascii=False)

    # 读取模板（优先用 workspace，否则用脚本同级目录）
    candidates = [
        TEMPLATE_IN,
        os.path.join(_SCRIPT_DIR, "..", "workspace", "etf_dashboard.html"),
        os.path.join(WORKSPACE, "etf_dashboard.html"),
    ]
    template = None
    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                template = f.read()
            break

    if not template:
        print("❌ 找不到 HTML 模板文件")
        return

    if "__BACKEND_DATA__" in template:
        html = template.replace("__BACKEND_DATA__", backend_data)
    else:
        html = re.sub(r"let BACKEND = \{.*?\};", f"let BACKEND = {backend_data};", template, flags=re.DOTALL)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = len(html) / 1024

    # 写出 JSON（加入新字段）
    json_out = os.path.join(WORKSPACE, "etf_data.json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({
            "indices": indices,
            "etfs": etf_results,
            "sector_flow": sector_flow,
            "ns_flow": ns_flow,
            "generated_at": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)
    print(f"   JSON: {json_out}")

    print(f"\n✅ 看板已生成: {OUTPUT_HTML} ({size_kb:.0f}KB)")
    print(f"   分析日期: {list(etf_results.values())[0]['date'] if etf_results else 'N/A'}")
    print(f"   ETF覆盖:  {len(etf_results)}/{len(etf.ETFS)}")
    print(f"   份额覆盖: {sum(1 for v in etf_results.values() if v['has_shares'])}/{len(etf.ETFS)}")

if __name__ == "__main__":
    generate()
