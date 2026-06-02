#!/usr/bin/env python3
"""
ETF国家队资金监测流水线 v7.2 — 三因子模型 + 本地SQLite数据存储
量能概率 50% + 方向概率 20% + 份额概率 30%

数据源（v7.2）:
  - K线行情: 腾讯财经 web.ifzq.gtimg.cn (HTTP，主) + 新浪财经 hq.sinajs.cn (HTTPS，备)
  - ETF份额: 东方财富 push2 ut1 (主) → push2 ut2 (备，同域不同入口) → 自动 fallback
  - 零第三方依赖，仅 Python 标准库

安全修复（v7.1）:
  - 移除 SSL CERT_NONE（腾讯接口改用 HTTP，东方财富使用 HTTPS 正常校验）
  - 统一邮件密码环境变量为 ETF_SMTP_PASS（移除旧名 QQMAIL_AUTH_CODE/SMTP_PASS）

使用方式:
  python3 etf_v7_threefactor.py                    # 默认: 最近交易日完整分析
  python3 etf_v7_threefactor.py --date 2026-04-30  # 指定日期分析（历史回溯）
  python3 etf_v7_threefactor.py --send             # 完整分析 + 发邮件
  python3 etf_v7_threefactor.py --record           # 仅采集当日份额入库
  python3 etf_v7_threefactor.py --stats            # 查看本地DB状态

v7.1 → v7.2 升级:
  - 东方财富份额接口加双入口 fallback（ut1 → ut2，自动切换，带重试）
  - K线行情加新浪财经备用（腾讯失败时自动切换）
  - fetch_with_retry() 统一重试逻辑，超时/网络抖动自动恢复
"""

import json, urllib.request, ssl, os, sys, math, argparse, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta

# ---------- 本地数据存储模块 ----------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
try:
    from etf_data_store import ETFDataStore
    DATA_STORE_AVAILABLE = True
except ImportError:
    DATA_STORE_AVAILABLE = False
    print("⚠️ etf_data_store.py 未找到，本地数据存储功能不可用")

# ---------- SSL上下文 ----------
# 腾讯财经用 HTTP（无需SSL），东方财富用 HTTPS 正常校验
_SSL_CTX = ssl.create_default_context()   # 正常校验，安全

WORKSPACE = os.path.expanduser(os.environ.get("ETF_WORKSPACE", "~/.etf-skill/workspace"))
SHARES_OUT = os.path.join(WORKSPACE, "etf_shares_history.json")
THREE_FACTOR_OUT = os.path.join(WORKSPACE, "ETF三因子分析-v7.json")
THREE_FACTOR_HTML = os.path.join(WORKSPACE, "ETF三因子分析-v7.html")

# ---------- 邮件配置（统一从环境变量读取） ----------
EMAIL_TO   = os.environ.get("ETF_EMAIL_TO",   "")
EMAIL_FROM = os.environ.get("ETF_EMAIL_FROM", "")
SMTP_HOST  = os.environ.get("ETF_SMTP_HOST",  "smtp.qq.com")
SMTP_PORT  = int(os.environ.get("ETF_SMTP_PORT", "465"))
# 密码统一用 ETF_SMTP_PASS（移除旧名 QQMAIL_AUTH_CODE / SMTP_PASS）
SMTP_PASS  = os.environ.get("ETF_SMTP_PASS", "")

ETFS = {
    "510300": {"n": "华泰柏瑞沪深300ETF", "idx": "沪深300", "p": 5},
    "510310": {"n": "易方达沪深300ETF",   "idx": "沪深300", "p": 5},
    "510330": {"n": "华夏沪深300ETF",     "idx": "沪深300", "p": 5},
    "159919": {"n": "嘉实沪深300ETF",     "idx": "沪深300", "p": 4},
    "510050": {"n": "华夏上证50ETF",      "idx": "上证50",  "p": 4},
    "510500": {"n": "华泰柏瑞中证500ETF", "idx": "中证500", "p": 3},
    "512100": {"n": "南方中证1000ETF",    "idx": "中证1000","p": 3},
    "159915": {"n": "易方达创业板ETF",     "idx": "创业板",   "p": 3},
    "588000": {"n": "华夏科创50ETF",       "idx": "科创50",   "p": 3},
}

# 东方财富 secid 映射（上交所=1, 深交所=0）
_EM_SECID = {
    "510300": "1.510300",
    "510310": "1.510310",
    "510330": "1.510330",
    "159919": "0.159919",
    "510050": "1.510050",
    "510500": "1.510500",
    "512100": "1.512100",
    "159915": "0.159915",  # 深交所
    "588000": "1.588000",  # 上交所
}

SPECIAL = {
    "2026-04-30": "五一前", "2026-05-06": "五一后",
}

# ============================================================
# 通用重试工具
# ============================================================

def fetch_with_retry(urls, parse_fn, label="", retries=2, timeout=10):
    """
    依次尝试 urls 列表中的每个 URL，直到成功返回非空结果。
    parse_fn(raw_bytes) -> result or None
    urls 是一个列表，支持 (url, headers) 或 url 字符串。
    """
    for url_item in urls:
        if isinstance(url_item, tuple):
            url, headers = url_item
        else:
            url, headers = url_item, {}

        use_ssl = url.startswith("https://")
        ctx     = _SSL_CTX if use_ssl else None

        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **headers})
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    raw = r.read()
                result = parse_fn(raw)
                if result is not None:
                    return result
            except Exception as e:
                if attempt == retries:
                    pass   # 静默，继续下一个 URL
    return None


# ============================================================
# K线行情 — 腾讯财经(主) + 新浪财经(备)
# ============================================================

def _parse_tencent_kline(raw, pfx, code):
    """解析腾讯财经 K线 JSON"""
    try:
        d   = json.loads(raw.decode("utf-8"))
        key = f"{pfx}{code}"
        k   = (d.get("data", {}).get(key, {}).get("day") or
               d.get("data", {}).get(key, {}).get("qfqday") or [])
        rows = [{"date": r[0], "o": float(r[1]), "c": float(r[2]),
                 "h": float(r[3]), "l": float(r[4]), "v": float(r[5])}
                for r in k if len(r) >= 6 and r[0]]
        return rows if rows else None
    except Exception:
        return None


def _parse_sina_kline(raw, code):
    """
    解析新浪财经实时行情（单日，作为 K线不足时的补充）
    返回包含今日单条记录的列表，或 None
    """
    try:
        text   = raw.decode("gbk", "replace")
        content = text.split('"')[1] if '"' in text else ""
        fields  = content.split(",")
        if len(fields) < 32:
            return None
        # 字段: [0]名称,[1]开,[2]昨收,[3]现价,[4]高,[5]低,...,[8]成交量(手),[30]日期,[31]时间
        date_str = fields[30].strip()   # YYYY-MM-DD
        if not date_str:
            return None
        return [{
            "date": date_str,
            "o":    float(fields[1]),
            "c":    float(fields[3]),
            "h":    float(fields[4]),
            "l":    float(fields[5]),
            "v":    float(fields[8]) * 100,   # 手→股(份)
        }]
    except Exception:
        return None


def fetch(code, limit=60):
    """
    获取ETF日K线。
    主: 腾讯财经 HTTP（60~90日历史）
    备: 新浪财经 HTTPS（仅当日单条，用于腾讯失败时至少有今日数据）
    """
    pfx = "sz" if code.startswith("159") else "sh"

    # 主: 腾讯财经（HTTP，无需 SSL）
    tencent_url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pfx}{code},day,,,{limit},qfq"
    result = fetch_with_retry(
        [(tencent_url, {})],
        lambda raw: _parse_tencent_kline(raw, pfx, code),
        label=f"腾讯K线/{code}",
        retries=2, timeout=15,
    )
    if result:
        return result

    # 备: 新浪财经（HTTPS，仅当日）
    sina_url = f"https://hq.sinajs.cn/list={pfx}{code}"
    result = fetch_with_retry(
        [(sina_url, {"Referer": "https://finance.sina.com.cn"})],
        lambda raw: _parse_sina_kline(raw, code),
        label=f"新浪备用/{code}",
        retries=2, timeout=10,
    )
    if result:
        print(f"    ⚠️ 腾讯K线失败，已切换至新浪财经备用（仅含今日单条）")
    return result or []


# ============================================================
# 份额数据 — 东方财富双入口 fallback
# ============================================================
# f84 = 流通股本（份），即ETF流通份额（盘中/盘后均实时）
# 主入口: ut=bd1d9ddb...  备入口: ut=fa5fd194...（同域不同鉴权参数，均已验证返回一致数据）

_EM_HEADERS = {
    "Referer": "https://www.eastmoney.com",
    "Accept":  "application/json, */*",
}

_EM_SHARE_URLS = [
    # 主: 标准 ut
    "https://push2.eastmoney.com/api/qt/stock/get?fields=f43,f84&secid={secid}&ut=bd1d9ddb04089700cf9c27f6f7426281",
    # 备: 行情页 ut（同接口同域名，不同鉴权参数，已实测一致）
    "https://push2.eastmoney.com/api/qt/stock/get?fields=f43,f84&secid={secid}&fltt=1&ut=fa5fd1943c7b386f172d6893dbfba10b",
]

def _parse_em_shares(raw):
    """解析东方财富 f84 份额字段"""
    try:
        d    = json.loads(raw.decode("utf-8"))
        data = d.get("data") or {}
        f84  = data.get("f84")
        f43  = data.get("f43")
        if f84 is None:
            return None
        return {
            "shares_yi": round(f84 / 1e8, 4),
            "price":     round((f43 or 0) / 1000, 4),
        }
    except Exception:
        return None

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.eastmoney.com",
    "Accept": "application/json, */*",
}

def fetch_fund_shares_realtime(code):
    """
    从东方财富实时API获取ETF份额。
    主入口失败自动切换备用入口，共2个入口各重试2次，最多4次尝试。

    返回: {"shares_yi": 份额(亿份), "price": 收盘价} 或 None
    """
    secid = _EM_SECID.get(code)
    if not secid:
        return None

    urls = [(url.format(secid=secid), _EM_HEADERS) for url in _EM_SHARE_URLS]
    return fetch_with_retry(urls, _parse_em_shares, label=f"EM份额/{code}", retries=2, timeout=10)


def load_shares_history():
    """加载本地JSON历史份额（格式: {date: {code: {shares_yi, ts}}}）"""
    if not os.path.exists(SHARES_OUT):
        return {}
    try:
        with open(SHARES_OUT, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_shares_history(history):
    """保存本地JSON历史份额，保留最近60日"""
    dates = sorted(history.keys())
    if len(dates) > 60:
        for old in dates[:-60]:
            del history[old]
    os.makedirs(WORKSPACE, exist_ok=True)
    with open(SHARES_OUT, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_historical_share(code, target_date, history):
    """
    从历史记录中查找目标日期的份额数据
    返回: (share_yi, prev_share_yi, delta_yi, delta_pct)
    """
    if target_date not in history:
        return None, None, None, None
    entries = history[target_date]
    if not isinstance(entries, dict) or code not in entries:
        return None, None, None, None

    target_share = entries[code].get("shares_yi")
    prev_share = None
    all_dates = sorted(history.keys())
    idx = all_dates.index(target_date) if target_date in all_dates else -1
    if idx > 0:
        for prev_d in all_dates[idx - 1::-1]:
            prev_entries = history.get(prev_d, {})
            if isinstance(prev_entries, dict) and code in prev_entries:
                prev_share = prev_entries[code].get("shares_yi")
                break

    if target_share is not None and prev_share is not None:
        delta_yi = round(target_share - prev_share, 2)
        delta_pct = round(delta_yi / prev_share * 100, 2) if prev_share else 0
        return target_share, prev_share, delta_yi, delta_pct
    elif target_share is not None:
        return target_share, None, None, None
    return None, None, None, None


# ============================================================
# 三因子模型核心函数
# ============================================================

def vprob(r):
    """量能概率（权重50%）"""
    if r < 0.5:  return max(0, r / 0.5 * 5)
    if r < 1.0:  return 5  + (r - 0.5) / 0.5 * 12
    if r < 1.3:  return 17 + (r - 1)   / 0.3 * 18
    if r < 1.5:  return 35 + (r - 1.3) / 0.2 * 20
    if r < 2.0:  return 55 + (r - 1.5) / 0.5 * 25
    if r < 3.0:  return 80 + (r - 2)   / 1   * 15
    if r < 5.0:  return 95 + (r - 3)   / 2   * 3
    return min(100, 98 + (r - 5) / 5 * 2)


def dprob(chg, t5_etf, t5_idx, vr, idx_chg):
    """方向概率（权重20%）"""
    rally_discount = 1.0
    if   idx_chg > 2.0:  rally_discount = 0.60
    elif idx_chg > 1.5:  rally_discount = 0.70
    elif idx_chg > 1.0:  rally_discount = 0.80
    elif idx_chg > 0.5:  rally_discount = 0.90

    if   chg > 0.3 and t5_idx < -1:            f1 = 95
    elif chg > 0   and t5_idx < -0.5:           f1 = 85
    elif chg > 0   and t5_idx < 0:              f1 = 70
    elif abs(chg) < 0.15 and t5_idx < -1:       f1 = 80
    elif abs(chg) < 0.3  and t5_idx < -0.5:     f1 = 65
    elif chg > 1 and vr > 1.5 and idx_chg > 1:  f1 = 25
    elif chg > 1 and vr > 1.5:                  f1 = 45
    elif chg > 0.5 and vr > 1.3 and idx_chg > 1: f1 = 35
    elif chg > 0.5 and vr > 1.3:                f1 = 50
    elif chg > 0:                                f1 = 40
    elif chg < -1.5 and vr > 2:                 f1 = 8
    elif chg < -0.5 and vr > 1.5:               f1 = 15
    else:                                        f1 = 25

    gap = t5_etf - t5_idx
    if   gap > 3:    f2 = 95
    elif gap > 2:    f2 = 85
    elif gap > 1.2:  f2 = 75
    elif gap > 0.6:  f2 = 60
    elif gap > 0.2:  f2 = 50
    elif gap > -0.2: f2 = 40
    elif gap > -0.6: f2 = 30
    else:            f2 = 15

    if   t5_idx < -4:    f3 = 95
    elif t5_idx < -3:    f3 = 90
    elif t5_idx < -2:    f3 = 80
    elif t5_idx < -1:    f3 = 70
    elif t5_idx < -0.5:  f3 = 55
    elif t5_idx < 0:     f3 = 45
    elif t5_idx < 1:     f3 = 35
    elif t5_idx < 3:     f3 = 20
    else:                f3 = 10
    f4 = 35

    raw = f1 * 0.4 + f2 * 0.3 + f3 * 0.2 + f4 * 0.1
    return round(raw * rally_discount, 1)


def sprob(share_delta_pct):
    """份额概率（权重30%）"""
    if share_delta_pct is None:
        return None
    if   share_delta_pct > 10:  return 95
    elif share_delta_pct > 5:   return 80 + (share_delta_pct - 5)  / 5 * 15
    elif share_delta_pct > 3:   return 65 + (share_delta_pct - 3)  / 2 * 15
    elif share_delta_pct > 1:   return 45 + (share_delta_pct - 1)  / 2 * 20
    elif share_delta_pct > 0:   return 30 + share_delta_pct        / 1 * 15
    elif share_delta_pct > -1:  return 15 + (share_delta_pct + 1)  / 1 * 15
    elif share_delta_pct > -5:  return 5  + (share_delta_pct + 5)  / 4 * 10
    else:                       return max(0, 5 + (share_delta_pct + 5) / 5 * 5)


def align_idx(data, idx_d):
    idx_map = {d["date"]: j for j, d in enumerate(idx_d)}
    return [idx_map.get(d["date"]) for d in data]


def analyze_all(data, idx_d, shares_map, target_date, days=35):
    """三因子模型分析，shares_map: {code: {date: {shares_yi, delta_yi, delta_pct}}}"""
    if len(data) < 22:
        return []
    res = []
    aligned = align_idx(data, idx_d)
    for i in range(max(21, len(data) - days), len(data)):
        d = data[i]
        v  = d["v"] / 10000
        pv = [data[j]["v"] / 10000 for j in range(i - 20, i)]
        ma = sum(pv) / 20
        if ma == 0:
            continue
        vr   = v / ma
        pc   = data[i - 1]["c"]
        chg  = (d["c"] - pc) / pc * 100 if pc > 0 else 0
        t5   = (data[i - 5]["c"] > 0 and
                (d["c"] - data[i - 5]["c"]) / data[i - 5]["c"] * 100
                if i >= 6 else 0)
        t5i  = t5
        idchg = 0
        if i < len(aligned) and aligned[i] is not None:
            ii = aligned[i]
            if ii > 0 and idx_d[ii - 1]["c"] > 0:
                idchg = round((idx_d[ii]["c"] - idx_d[ii - 1]["c"]) / idx_d[ii - 1]["c"] * 100, 1)
            if i >= 6 and aligned[i - 5] is not None:
                j5 = aligned[i - 5]
                if idx_d[j5]["c"] > 0:
                    t5i = (idx_d[ii]["c"] - idx_d[j5]["c"]) / idx_d[j5]["c"] * 100

        vp = vprob(vr)
        dp = dprob(chg, t5, round(t5i, 2), vr, idchg)

        # 份额概率：从 shares_map 查对应 code + 日期
        sp              = None
        share_delta_pct = None
        share_delta_yi  = None
        for code_k, date_map in shares_map.items():
            if d["date"] in date_map:
                info            = date_map[d["date"]]
                share_delta_pct = info.get("delta_pct")
                share_delta_yi  = info.get("delta_yi")
                sp              = sprob(share_delta_pct)
                break

        # 三因子综合概率（份额不可用时退化为二因子 70/30）
        if sp is not None:
            cp = round(vp * 0.5 + dp * 0.2 + sp * 0.3, 1)
        else:
            cp = round(vp * 0.7 + dp * 0.3, 1)

        tag = SPECIAL.get(d["date"], "")
        res.append({
            "d": d["date"], "c": d["c"], "chg": round(chg, 2),
            "t5": round(t5, 2), "t5i": round(t5i, 2), "idx_chg": idchg,
            "v": round(v, 2), "vma": round(ma, 2), "vr": round(vr, 2),
            "vp": round(vp, 1), "dp": dp, "sp": sp, "cp": cp,
            "share_delta_pct": share_delta_pct, "share_delta_yi": share_delta_yi,
            "tag": tag, "has_shares": sp is not None,
        })
    return res


# ============================================================
# HTML 报告生成
# ============================================================

def gen_html(all_hist, latest_map, idx_300_data, shares_data, target_date):
    dates = sorted({h["d"] for hh in all_hist.values() for h in hh})
    primary_date = target_date if target_date in dates else (dates[-1] if dates else "N/A")

    primary    = {}
    high_codes = []
    mid_codes  = []
    for code, hist in all_hist.items():
        for h in hist:
            if h["d"] == primary_date:
                primary[code] = h
                if h["cp"] >= 70:   high_codes.append(code)
                elif h["cp"] >= 50: mid_codes.append(code)
                break

    hs300_codes  = [c for c in ETFS if ETFS[c]["idx"] == "沪深300"]
    hs300_alerts = sum(1 for c in hs300_codes if c in primary and primary[c]["cp"] >= 50)
    total_high   = len(high_codes)
    total_mid    = len(mid_codes)

    idx_300_hist = {d["date"]: d for d in (idx_300_data or [])}
    idx_gain = 0
    if primary_date in idx_300_hist and dates:
        pd = idx_300_hist[primary_date]
        idx_in_dates = [d for d in dates if d < primary_date]
        if idx_in_dates:
            prev_d = idx_in_dates[-1]
            if prev_d in idx_300_hist and idx_300_hist[prev_d]["c"]:
                idx_gain = round((pd["c"] - idx_300_hist[prev_d]["c"]) / idx_300_hist[prev_d]["c"] * 100, 2)

    avg_dp   = sum(primary[c]["dp"] for c in primary) / len(primary) if primary else 0

    net_purchase_total    = 0
    net_redempt_total     = 0
    shares_available_count = 0
    for code, sd in shares_data.items():
        d = sd.get("delta_yi")
        if d is not None:
            if d > 0:  net_purchase_total += d
            else:      net_redempt_total  += abs(d)
            shares_available_count += 1

    threef_tag = "三因子: 量能50%+方向20%+份额30%"
    if shares_available_count == 0:
        threef_tag += " (无份额数据，退化为二因子70/30)"

    net_tag = f"+{net_purchase_total:.1f}亿净申购" if net_purchase_total > net_redempt_total else f"-{net_redempt_total:.1f}亿净赎回"

    if total_high >= 2 and hs300_alerts >= 3:
        if idx_gain > 1.5 and avg_dp < 40:
            verdict = f"⚠️ 多ETF高确信({total_high}只)，但大盘涨{idx_gain:+.2f}%。{threef_tag}。份额:{net_tag}。"
            vcls = "warn"
        else:
            verdict = f"🔥 {total_high}只ETF高确信·{hs300_alerts}/4沪深300同步。{threef_tag}。份额:{net_tag}。"
            vcls = "warn"
    elif total_high >= 1:
        verdict = f"⚠️ 部分ETF高确信（{', '.join(ETFS[c]['n'][:6] for c in high_codes)}等）。{threef_tag}。份额:{net_tag}。"
        vcls = "warn"
    elif total_mid >= 2:
        verdict = f"📊 {total_mid}只中等信号。{threef_tag}。份额:{net_tag}。"
        vcls = "mid"
    else:
        verdict = f"✅ {primary_date} 全市场正常。{threef_tag}。"
        vcls = "ok"

    # 15日信号趋势柱
    date_score = {}
    for code, hist in all_hist.items():
        for h in hist:
            d = h["d"]
            if d not in date_score:
                date_score[d] = {"cnt": 0, "high": 0, "mid": 0, "avg": 0}
            date_score[d]["cnt"]  += 1
            date_score[d]["avg"]  += h["cp"]
            if h["cp"] >= 70:   date_score[d]["high"] += 1
            elif h["cp"] >= 50: date_score[d]["mid"]  += 1
    for d in date_score:
        if date_score[d]["cnt"]:
            date_score[d]["avg"] = round(date_score[d]["avg"] / date_score[d]["cnt"], 1)

    trend_dates = sorted(date_score.keys())[-15:]
    bars = ""
    for d in trend_dates:
        ds = date_score[d]
        h_px = min(42, max(3, ds["avg"] * 0.55))
        cls  = "bar-hi" if ds["high"] >= 2 else ("bar-md" if ds["high"] + ds["mid"] >= 2 else "bar-lo")
        tag  = SPECIAL.get(d, "")
        bars += (f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px">'
                 f'<div class="bar {cls}" style="height:{h_px}px" '
                 f'title="{d}{" "+tag if tag else ""}: {ds["high"]}高+{ds["mid"]}中 CP均{ds["avg"]:.0f}%"></div>'
                 f'<span style="font-size:8px;color:#4a5568">{d[5:]}</span></div>')

    # ETF表格行
    rows = ""
    for code, info in ETFS.items():
        p  = primary.get(code)
        sd = shares_data.get(code, {})
        cp = p["cp"] if p else 0
        if cp >= 70:   cls_r, sc, si = "tr-hi", "#ef4444", "🔴"
        elif cp >= 50: cls_r, sc, si = "tr-md", "#f59e0b", "🟡"
        else:          cls_r, sc, si = "",      "#22c55e", "🟢"

        if p:
            chg = p["chg"]
            chc = "#ef4444" if chg > 0 else ("#22c55e" if chg < 0 else "#94a3b8")
            tag_html = (f'<span style="font-size:8px;background:rgba(239,68,68,0.15);color:#fca5a5;'
                        f'padding:1px 4px;border-radius:2px;margin-left:4px">{p["tag"]}</span>'
                        if p.get("tag") else "")
            v_str   = f"{p['v']:.0f}万"
            vma_str = f"{p['vma']:.0f}万"
            vr_str  = f"{p['vr']:.2f}x"
            vp_str  = f"{p['vp']:.0f}%"
            dp_str  = f"{p['dp']:.0f}%"
            cp_str  = f"{cp:.0f}%{tag_html}"
        else:
            chg, chc, tag_html = 0, "#94a3b8", ""
            v_str = vma_str = vr_str = vp_str = dp_str = cp_str = "-"

        # 份额列
        if sd and sd.get("shares_yi") is not None:
            sh_yi = sd["shares_yi"]
            d_yi  = sd.get("delta_yi")
            d_pct = sd.get("delta_pct")
            if d_yi is not None:
                dc     = "#22c55e" if d_yi > 0 else ("#ef4444" if d_yi < 0 else "#94a3b8")
                arrow  = "↑" if d_yi > 0 else ("↓" if d_yi < 0 else "→")
                sh_html = (f'<td style="color:#94a3b8">{sh_yi:.1f}亿</td>'
                           f'<td style="font-weight:600;color:{dc}">{arrow}{abs(d_yi):.1f}亿({d_pct:+.2f}%)</td>')
            else:
                sh_html = (f'<td style="color:#94a3b8">{sh_yi:.1f}亿</td>'
                           f'<td style="color:#64748b">-</td>')
        else:
            sh_html = '<td style="color:#64748b">-</td><td style="color:#64748b">-</td>'

        # 份额概率列
        sp_val = p["sp"] if p and p.get("has_shares") else None
        if isinstance(sp_val, (int, float)):
            sp_col     = "#ef4444" if sp_val >= 70 else ("#f59e0b" if sp_val >= 50 else "#22c55e")
            sp_display = f"{sp_val:.0f}%"
        else:
            sp_col, sp_display = "#64748b", "-"

        rows += f'''<tr class="{cls_r}">
  <td style="white-space:nowrap">{si} <b>{info["n"]}</b></td>
  <td style="color:#64748b">{code}</td>
  <td style="color:{chc}">{chg:+.2f}%</td>
  <td>{v_str}</td><td>{vma_str}</td>
  <td style="font-weight:600;color:#cbd5e1">{vr_str}</td>
  {sh_html}
  <td style="color:#94a3b8">{vp_str}</td>
  <td style="color:#94a3b8">{dp_str}</td>
  <td style="color:{sp_col}">{sp_display}</td>
  <td style="font-weight:700;font-size:13px;color:{sc};white-space:nowrap">{cp_str}</td>
</tr>'''

    # 30日同步信号列表
    signal_dates = [(d, v) for d, v in date_score.items() if v["high"] + v["mid"] >= 3]
    signal_dates.sort(key=lambda x: x[0], reverse=True)
    sig_list = ""
    for d, v in signal_dates[:8]:
        tag     = SPECIAL.get(d, "")
        dots    = ('<div class="sig-dots">'
                   + '<div class="sig-dot hi"></div>' * v["high"]
                   + '<div class="sig-dot md"></div>' * v["mid"]
                   + '</div>')
        tag_html = f'<span class="sig-tag">{tag}</span>' if tag else ""
        cnt     = f'🔥{v["high"]} 🟡{v["mid"]} CP{v["avg"]:.0f}%'
        sig_list += (f'<div class="sig-row"><span class="sig-date">{d[5:]}</span>'
                     f'{tag_html}{dots}<span class="sig-cnt">{cnt}</span></div>')

    total_shares = sum((shares_data.get(c, {}).get("shares_yi") or 0) for c in ETFS)
    total_delta  = sum((shares_data.get(c, {}).get("delta_yi") or 0) for c in ETFS)
    delta_cls    = "#22c55e" if total_delta > 0 else ("#ef4444" if total_delta < 0 else "#94a3b8")
    delta_arrow  = "↑" if total_delta > 0 else ("↓" if total_delta < 0 else "→")
    model_desc   = "三因子: 量能P×50% + 方向P×20% + 份额P×30%"

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1440,initial-scale=1">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html{{display:flex;justify-content:center;align-items:center;min-height:100vh;background:#0a0f1a}}
body{{width:1440px;height:810px;overflow:hidden;font-family:-apple-system,"SF Pro Display","PingFang SC","Microsoft YaHei",sans-serif;background:#111c2e;color:#dfe6ef;display:flex;flex-direction:column;border-radius:12px;box-shadow:0 0 80px rgba(56,189,248,0.04)}}
.hdr{{padding:8px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(56,189,248,0.12);flex-shrink:0;background:rgba(17,28,46,0.7)}}
.hdr h1{{font-size:18px;font-weight:700;color:#f1f5f9;letter-spacing:-0.3px}}
.hdr .sub{{font-size:12px;color:#8896ab;margin-left:14px}}
.hdr .meta{{font-size:11px;color:#7a8ba0;text-align:right;line-height:1.6}}
.hdr .meta .dot{{display:inline-block;width:6px;height:6px;border-radius:50%;background:#22c55e;margin-right:5px;box-shadow:0 0 6px rgba(34,197,94,0.5)}}
.banner{{margin:6px 22px 0;padding:6px 16px;border-radius:8px;font-size:12px;line-height:1.45;flex-shrink:0;display:flex;align-items:flex-start;gap:8px}}
.banner.warn{{background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.18);color:#fca5a5}}
.banner.mid{{background:rgba(245,158,11,0.05);border:1px solid rgba(245,158,11,0.15);color:#fcd34d}}
.banner.ok{{background:rgba(34,197,94,0.04);border:1px solid rgba(34,197,94,0.12);color:#86efac}}
.banner .ico{{font-size:18px;flex-shrink:0;margin-top:1px}}
.stats{{display:flex;gap:10px;padding:6px 22px 4px;flex-shrink:0}}
.stat{{flex:1;background:rgba(24,36,56,0.5);border:1px solid rgba(56,189,248,0.1);border-radius:8px;padding:10px 14px;display:flex;align-items:center;gap:10px}}
.stat .vi{{font-size:26px;font-weight:900;line-height:1}}
.stat .tx{{font-size:11px;color:#8896ab;line-height:1.3}}
.stat .tx span{{display:block;font-size:12px;color:#dfe6ef;font-weight:600}}
.main{{display:flex;flex:1;padding:8px 22px 6px;gap:14px;overflow:hidden}}
.tbl-wrap{{flex:1;min-width:0;overflow:hidden;background:rgba(20,32,50,0.4);border-radius:8px;border:1px solid rgba(56,189,248,0.08);display:flex;flex-direction:column}}
.tbl-wrap table{{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}}
.tbl-wrap thead th{{text-align:left;padding:5px 6px;font-weight:600;color:#7a8ba0;font-size:10px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid rgba(56,189,248,0.1);white-space:nowrap;background:rgba(18,28,44,0.4)}}
.tbl-wrap td{{padding:12px 6px;border-bottom:1px solid rgba(20,30,50,0.5);color:#b0bdd0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.tbl-wrap tbody tr:hover td{{background:rgba(56,189,248,0.03)}}
.tbl-wrap tr.tr-hi td{{background:rgba(239,68,68,0.05)}}
.tbl-wrap tr.tr-md td{{background:rgba(245,158,11,0.03)}}
.tbl-wrap .tnote{{font-size:10px;color:#64748b;padding:6px 10px;border-top:1px solid rgba(56,189,248,0.08)}}
.rp{{width:380px;display:flex;flex-direction:column;gap:10px;overflow:hidden;flex-shrink:0;align-self:stretch}}
.rp .card{{background:rgba(22,34,52,0.45);border:1px solid rgba(56,189,248,0.08);border-radius:8px;overflow:hidden}}
.rp .card:last-child{{flex:1;display:flex;flex-direction:column}}
.rp .card .ttl{{font-size:11px;font-weight:600;color:#8b9bb5;padding:8px 10px 6px;display:flex;align-items:center;gap:6px}}
.rp .card .ttl::before{{content:'';width:3px;height:12px;background:linear-gradient(180deg,#38bdf8,#818cf8);border-radius:2px}}
.rp .trend{{display:flex;align-items:flex-end;gap:2px;height:44px;padding:4px 10px 8px}}
.rp .trend .bar{{flex:1;border-radius:2px 2px 0 0;min-width:4px}}
.bar-hi{{background:linear-gradient(180deg,#ef4444aa,#ef444444)}}
.bar-md{{background:linear-gradient(180deg,#f59e0baa,#f59e0b44)}}
.bar-lo{{background:linear-gradient(180deg,#334155,#1a2234)}}
.rp .sig{{padding:6px 12px 8px;display:flex;flex-direction:column;gap:5px;flex:1;overflow-y:auto}}
.rp .sig-row{{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;background:rgba(20,30,50,0.4)}}
.rp .sig-date{{font-size:12px;font-weight:600;color:#dfe6ef;min-width:60px}}
.rp .sig-tag{{font-size:9px;background:rgba(56,189,248,0.1);color:#7dd3fc;padding:1px 6px;border-radius:3px}}
.rp .sig-dots{{display:flex;gap:3px;flex:1}}
.rp .sig-dot{{width:7px;height:7px;border-radius:50%}}
.rp .sig-dot.hi{{background:#ef4444;box-shadow:0 0 4px rgba(239,68,68,0.4)}}
.rp .sig-dot.md{{background:#f59e0b;box-shadow:0 0 4px rgba(245,158,11,0.3)}}
.rp .sig-cnt{{font-size:11px;color:#8896ab;white-space:nowrap}}
.ftr{{padding:8px 24px;font-size:11px;color:#64748b;border-top:1px solid rgba(56,189,248,0.1);flex-shrink:0;display:flex;justify-content:center;gap:6px}}
</style></head><body>

<div class="hdr">
  <div style="display:flex;align-items:baseline">
    <h1>ETF三因子监测报告</h1>
    <span class="sub">{model_desc}</span>
  </div>
  <div class="meta">
    <div><span class="dot"></span>分析日: {primary_date}</div>
    <div>{datetime.now().strftime("%Y-%m-%d %H:%M")} · v7.1</div>
  </div>
</div>

<div class="banner {vcls}">
  <span class="ico">{'🔥' if total_high>=2 else '⚠️' if total_high>=1 or total_mid>=2 else '✅'}</span>
  <div>📋 <b>综合判断：</b>{verdict}</div>
</div>

<div class="stats">
  <div class="stat">
    <div class="vi" style="color:{'#ef4444' if total_high>0 else '#f59e0b' if total_mid>0 else '#22c55e'}">{total_high}</div>
    <div class="tx"><span>高确信</span>🔴 触发警报</div>
  </div>
  <div class="stat">
    <div class="vi" style="color:{'#f59e0b' if total_mid>0 else '#4a5568'}">{total_mid}</div>
    <div class="tx"><span>中等关注</span>🟡 需跟踪</div>
  </div>
  <div class="stat">
    <div class="vi" style="color:{'#ef4444' if hs300_alerts>=3 else '#f59e0b' if hs300_alerts>=2 else '#22c55e'}">{hs300_alerts}/4</div>
    <div class="tx"><span>沪深300</span>一致性</div>
  </div>
  <div class="stat">
    <div class="vi" style="color:{delta_cls}">{delta_arrow}{abs(total_delta):.1f}</div>
    <div class="tx"><span>份额日变</span>亿份 · 净申赎</div>
  </div>
  <div class="stat">
    <div class="vi" style="color:#818cf8">{shares_available_count}/7</div>
    <div class="tx"><span>份额覆盖</span>三因子完整度</div>
  </div>
</div>

<div class="main">
  <div class="tbl-wrap">
    <table>
    <thead><tr>
      <th style="width:20%">ETF名称</th>
      <th style="width:7%">代码</th>
      <th style="width:6%">涨跌</th>
      <th style="width:8%">成交量</th>
      <th style="width:8%">20日均</th>
      <th style="width:6%">倍量</th>
      <th style="width:7%">份额</th>
      <th style="width:8%">份额日变</th>
      <th style="width:6%">量能P</th>
      <th style="width:6%">方向P</th>
      <th style="width:6%">份额P</th>
      <th style="width:6%">综合</th>
    </tr></thead>
    <tbody>{rows}</tbody>
    </table>
    <div class="tnote">
      ⚡ {model_desc} · 数据源: 腾讯财经(K线) + 东方财富(份额) · 零第三方依赖 v7.1
    </div>
  </div>

  <div class="rp">
    <div class="card">
      <div class="ttl">📈 15日信号趋势（综合概率）</div>
      <div class="trend">{bars}</div>
    </div>
    <div class="card">
      <div class="ttl">📅 30日同步信号</div>
      <div class="sig">{sig_list}</div>
    </div>
  </div>
</div>

<div class="ftr">
  <span>ETF国家队资金监测 · 三因子模型 v7.1 · 腾讯财经K线 + 东方财富实时份额 · 零第三方依赖</span>
</div>

</body></html>'''


# ============================================================
# 邮件发送
# ============================================================

def send_email(html_path, json_path, target_date):
    if not SMTP_PASS:
        print("⚠️ 未设置 ETF_SMTP_PASS 环境变量，跳过邮件发送")
        print("   设置方法: export ETF_SMTP_PASS='你的QQ邮箱16位授权码'")
        return False
    if not EMAIL_FROM or EMAIL_FROM == "":
        print("⚠️ 未设置 ETF_EMAIL_FROM 环境变量，跳过邮件发送")
        return False

    msg = MIMEMultipart()
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO or EMAIL_FROM
    msg["Subject"] = f"ETF三因子分析报告 - {target_date} - v7.1"

    body = (f"📊 ETF三因子监测报告（v7.1）\n\n"
            f"分析日期: {target_date}\n"
            f"模型: 量能50% + 方向20% + 份额30%\n"
            f"数据源: 腾讯财经(K线) + 东方财富(实时份额)\n\n"
            f"报告详见附件。\n\n---\n此邮件由ETF三因子监测系统v7.1自动发送")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for fpath, fname in [(html_path, f"ETF三因子-{target_date}.html"),
                         (json_path,  f"ETF三因子-{target_date}.json")]:
        if os.path.exists(fpath):
            with open(fpath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={fname}")
                msg.attach(part)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(EMAIL_FROM, SMTP_PASS)
            server.sendmail(EMAIL_FROM, msg["To"], msg.as_string())
        print(f"✅ 邮件已发送至 {msg['To']}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


# ============================================================
# 主程序
# ============================================================

def _get_prev_shares_yi(code, today, shares_history):
    """获取today前最近一个交易日的份额，用于盘中陈旧数据检测"""
    all_dates = sorted(d for d in shares_history.keys() if d < today)
    for d in reversed(all_dates):
        entry = shares_history.get(d, {}).get(code)
        if entry and entry.get("shares_yi") is not None:
            return entry["shares_yi"]
    return None


def record_shares_only():
    """仅采集当日份额数据（不跑完整分析）。
    防盘中陈旧数据：若API返回值与前一交易日完全相同，
    标记 stale=True，不写入历史（避免日变显示为0）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📊 采集 {today} ETF份额数据（东方财富实时API）...")
    shares_history = load_shares_history()
    if today not in shares_history:
        shares_history[today] = {}
    ok = 0
    stale_count = 0
    for code, info in ETFS.items():
        print(f"  {code} {info['n'][:12]}...", end=" ")
        sh = fetch_fund_shares_realtime(code)
        if sh:
            new_val = sh["shares_yi"]
            prev_val = _get_prev_shares_yi(code, today, shares_history)
            # 盘中陈旧检测：API值与前日完全一致 → 数据未更新，跳过写入
            if prev_val is not None and new_val == prev_val:
                print(f"⏳ {new_val:.1f}亿份 (与前日相同，疑似盘中未更新，跳过写入)")
                stale_count += 1
                continue
            shares_history[today][code] = {
                "shares_yi": new_val,
                "ts": datetime.now().isoformat(),
            }
            print(f"✅ {new_val:.1f}亿份")
            ok += 1
        else:
            print("❌ 获取失败")
    # 若当日所有ETF都是陈旧数据，删除今日空entry避免污染历史
    if ok == 0 and today in shares_history and not shares_history[today]:
        del shares_history[today]
        print(f"⚠️  今日数据全部与前日相同（盘中未更新），已跳过保存")
    else:
        save_shares_history(shares_history)
        print(f"\n✅ 采集完成 {ok}/7 写入，{stale_count}/7 跳过（盘中陈旧）")

    if DATA_STORE_AVAILABLE:
        store = ETFDataStore()
        for code, info in ETFS.items():
            if code in shares_history.get(today, {}):
                store.upsert_record(today, code, {
                    "date": today, "code": code,
                    "name": info["n"], "idx_name": info["idx"],
                    "shares_yi": shares_history[today][code]["shares_yi"],
                })
        stats = store.get_stats()
        print(f"📦 DB: {stats['total_records']}条 / {stats['total_dates']}日")


def main(target_date=None, do_send=False, record_only=False):
    store = ETFDataStore() if DATA_STORE_AVAILABLE else None

    if record_only:
        record_shares_only()
        return

    print("=" * 70)
    print("🛡️  ETF国家队资金监测 v7.1 — 三因子模型")
    print("   量能50% + 方向20% + 份额30%")
    print("   数据源: 腾讯财经(K线) + 东方财富(实时份额) — 零第三方依赖")
    if store:
        db_stats = store.get_stats()
        print(f"   📦 本地DB: {db_stats['total_records']}条 / {db_stats['total_dates']}日")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # Step 1: 沪深300指数K线
    print("\n📊 Step 1: 获取沪深300指数数据...")
    idx_300 = fetch("sh000300", 60)
    if idx_300:
        print(f"  ✅ {len(idx_300)}条  {idx_300[0]['date']} ~ {idx_300[-1]['date']}")
    else:
        print("  ⚠️ 沪深300数据获取失败")

    # Step 2: 加载历史份额（本地JSON）
    print("\n📊 Step 2: 加载历史份额数据...")
    shares_history = load_shares_history()
    # 清理空日期
    empty = [d for d, v in shares_history.items() if isinstance(v, dict) and not v]
    for d in empty:
        del shares_history[d]
    if empty:
        print(f"  🧹 清理 {len(empty)} 个空日期")
    print(f"  📦 本地历史: {len(shares_history)} 日")

    # 采集今日份额（实时 or 指定日期）
    run_date = target_date or datetime.now().strftime("%Y-%m-%d")
    if run_date not in shares_history:
        shares_history[run_date] = {}

    print(f"  📡 采集 {run_date} ETF份额（东方财富实时API）...")
    stale_in_main = 0
    for code, info in ETFS.items():
        sh = fetch_fund_shares_realtime(code)
        if sh:
            new_val = sh["shares_yi"]
            prev_val = _get_prev_shares_yi(code, run_date, shares_history)
            # 盘中陈旧检测：与前日完全一致则跳过
            if prev_val is not None and new_val == prev_val:
                print(f"    ⏳ {code}: {new_val:.1f}亿份 (盘中未更新，跳过)")
                stale_in_main += 1
                continue
            shares_history[run_date][code] = {
                "shares_yi": new_val,
                "ts": datetime.now().isoformat(),
            }
            print(f"    ✅ {code}: {new_val:.1f}亿份")
        else:
            print(f"    ⚠️ {code}: 获取失败")
    # 清理今日空entry
    if run_date in shares_history and not shares_history[run_date]:
        del shares_history[run_date]
        print(f"  ⚠️  今日份额全部盘中未更新，未写入")
    else:
        save_shares_history(shares_history)
    if stale_in_main:
        print(f"  ⚠️  {stale_in_main}/7 ETF份额与前日相同（盘中陈旧），已跳过")
    print(f"  📊 累计历史: {len(shares_history)} 日")

    # Step 3: 构建份额映射
    shares_map = {}
    for code in ETFS:
        shares_map[code] = {}
        for date in shares_history:
            t_sh, p_sh, delta_yi, delta_pct = get_historical_share(code, date, shares_history)
            if t_sh is not None:
                shares_map[code][date] = {
                    "shares_yi":  t_sh,
                    "delta_yi":   delta_yi,
                    "delta_pct":  delta_pct,
                }

    # Step 4: ETF K线 + 三因子分析
    print("\n📊 Step 3: 获取ETF行情 + 三因子分析...")
    if target_date:
        print(f"  🎯 目标分析日期: {target_date}")

    all_hist         = {}
    latest_map       = {}
    target_shares_data = {}

    for code, info in ETFS.items():
        print(f"\n  📊 {code} {info['n']}")
        data = fetch(code, 60)
        if not data or len(data) < 22:
            print(f"    ⚠️ 数据不足（{len(data) if data else 0}条）")
            continue

        hist = analyze_all(data, idx_300, shares_map, target_date or "", 35)
        if not hist:
            print("    ⚠️ 分析失败")
            continue

        all_hist[code] = hist

        # 目标日期结果
        target_h = None
        if target_date:
            for h in hist:
                if h["d"] == target_date:
                    target_h = h
                    break
        if not target_h:
            target_h = hist[-1]

        sh_on_target = shares_map.get(code, {}).get(target_h["d"] if not target_date else target_date, {})
        target_shares_data[code] = sh_on_target

        latest_map[code] = {
            "d": target_h["d"], "c": target_h["c"], "chg": target_h["chg"],
            "cp": target_h["cp"], "vr": target_h["vr"],
            "vp": target_h["vp"], "dp": target_h["dp"], "sp": target_h["sp"],
            "v": target_h["v"], "vma": target_h["vma"],
            "shares_yi":  sh_on_target.get("shares_yi"),
            "delta_yi":   sh_on_target.get("delta_yi"),
            "delta_pct":  sh_on_target.get("delta_pct"),
        }

        sp_str = f"份额P:{target_h['sp']:.0f}%" if target_h.get("has_shares") else "份额P:N/A"
        s      = "🔥" if target_h["cp"] >= 70 else ("⚠️" if target_h["cp"] >= 50 else "○")
        flag   = "三因子" if target_h.get("has_shares") else "二因子"
        t_tag  = f"[{target_h['tag']}]" if target_h.get("tag") else ""
        print(f"    {s} {target_h['d']} {t_tag} | {target_h['chg']:+.2f}% | "
              f"{target_h['v']:.0f}万({target_h['vr']:.2f}x) | "
              f"量能P:{target_h['vp']:.0f}% 方向P:{target_h['dp']:.0f}% {sp_str} "
              f"→ CP:{target_h['cp']:.0f}% [{flag}]")

    # Step 5: 重要信号回溯
    print("\n" + "=" * 70)
    print("📋 30日重要信号回溯")
    print("=" * 70)
    date_sig = {}
    for code, hist in all_hist.items():
        for h in hist:
            d = h["d"]
            if d not in date_sig:
                date_sig[d] = {"total": 0, "high": 0, "mid": 0, "codes": []}
            date_sig[d]["total"] += 1
            if h["cp"] >= 70:
                date_sig[d]["high"] += 1
                date_sig[d]["codes"].append(f"{code}({h['cp']:.0f}%)")
            elif h["cp"] >= 50:
                date_sig[d]["mid"] += 1
    sigs = [(d, v) for d, v in date_sig.items() if v["high"] >= 2 or v["high"] + v["mid"] >= 4]
    sigs.sort(key=lambda x: x[0], reverse=True)
    if sigs:
        for d, v in sigs[:10]:
            t = SPECIAL.get(d, "")
            ts = f" [{t}]" if t else ""
            print(f"  📅 {d}{ts}: {v['high']}🔴+{v['mid']}🟡 → {', '.join(v['codes'][:5])}")
    else:
        print("  ℹ️ 无多ETF同步信号")

    actual_date = target_date or (sorted(date_sig.keys())[-1] if date_sig else run_date)

    # Step 6: 记录到本地DB
    if store:
        print(f"\n💾 Step 4: 保存分析结果到本地DB...")
        idx_gain = 0
        if idx_300 and len(idx_300) >= 2:
            for i, d in enumerate(idx_300):
                if d["date"] == actual_date and i > 0:
                    prev = idx_300[i - 1]
                    if prev["c"]:
                        idx_gain = round((d["c"] - prev["c"]) / prev["c"] * 100, 2)
                    break

        etf_results = {}
        for code, hist in all_hist.items():
            for h in hist:
                if h["d"] == actual_date:
                    sh = target_shares_data.get(code, {})
                    etf_results[code] = {
                        "name": ETFS[code]["n"], "idx_name": ETFS[code]["idx"],
                        "c": h["c"], "chg": h["chg"],
                        "v": h["v"], "vma": h["vma"], "vr": h["vr"],
                        "vp": h["vp"], "dp": h["dp"], "sp": h["sp"], "cp": h["cp"],
                        "shares_yi": sh.get("shares_yi"),
                        "delta_yi":  sh.get("delta_yi"),
                        "delta_pct": sh.get("delta_pct"),
                    }
                    break
        cnt = store.record_from_v6_result(actual_date, etf_results, idx_gain)
        print(f"  ✅ 已记录 {cnt} 条到本地DB")
        db_stats = store.get_stats()
        print(f"  📦 DB总量: {db_stats['total_records']}条 / {db_stats['total_dates']}日")

    # Step 7: 生成HTML报告
    print(f"\n🎨 Step 5: 生成HTML报告（分析日: {actual_date}）...")
    os.makedirs(WORKSPACE, exist_ok=True)
    html = gen_html(all_hist, latest_map, idx_300, target_shares_data, target_date or "")
    with open(THREE_FACTOR_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✅ {THREE_FACTOR_HTML}")

    # Step 8: 保存JSON
    with open(THREE_FACTOR_OUT, "w", encoding="utf-8") as f:
        json.dump({
            "run_time":     datetime.now().isoformat(),
            "version":      "v7.1",
            "model":        "三因子: 量能50%+方向20%+份额30%",
            "data_sources": ["腾讯财经K线(HTTP)", "东方财富实时份额(HTTPS)"],
            "target_date":  actual_date,
            "signal_dates": [(d, v["high"], v["mid"], v["codes"][:4]) for d, v in sigs[:10]],
            "latest":       latest_map,
            "shares_data":  target_shares_data,
        }, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {THREE_FACTOR_OUT}")

    # Step 9: 发邮件
    if do_send:
        print(f"\n📧 Step 6: 发送邮件...")
        send_email(THREE_FACTOR_HTML, THREE_FACTOR_OUT, actual_date)
    else:
        print(f"\n📧 跳过邮件发送（使用 --send 启用）")

    return html


def fetch_sector_flow(top_n=10):
    """
    获取板块资金流排行（东方财富板块资金流接口）
    返回: {"top_in": [...], "top_out": [...]}
    每项: {"name": str, "flow_yi": float, "chg_pct": float}
    """
    import ssl
    ctx = ssl.create_default_context()
    # 行业板块（申万一级）资金流
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           "?cb=&fid=f62&po=1&pz=50&pn=1&np=1&fltt=2&invt=2"
           "&ut=b2884a393a59ad64002292a3e90d46a5"
           "&fields=f12,f14,f2,f3,f62,f184"
           "&fs=m%3A90+t%3A2&cb=")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            d = json.loads(r.read().decode("utf-8"))
        items = d.get("data", {}).get("diff", []) or []
        result = []
        for item in items:
            flow = item.get("f62", 0) or 0
            chg = item.get("f3", 0) or 0
            name = item.get("f14", "")
            result.append({"name": name, "flow_yi": round(flow / 1e8, 2), "chg_pct": round(chg, 2)})
        result.sort(key=lambda x: x["flow_yi"], reverse=True)
        return {
            "top_in": result[:top_n],
            "top_out": sorted(result, key=lambda x: x["flow_yi"])[:top_n],
            "updated": datetime.now().strftime("%H:%M"),
        }
    except Exception as e:
        print(f"  ⚠️ 板块资金流获取失败: {e}")
        return {"top_in": [], "top_out": [], "updated": "--"}


def fetch_northsouth_flow():
    """
    获取北向/南向资金每日净流入（沪深港通）
    北向 = 港股通北向（外资买A股）
    南向 = 陆股通南向（内资买港股）
    返回 dict，单位亿元，异动阈值 ±50亿
    """
    import ssl
    ctx = ssl.create_default_context()
    url = ("https://push2.eastmoney.com/api/qt/kamt/get"
           "?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56"
           "&ut=b2884a393a59ad64002292a3e90d46a5&cb=")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/hsgtcg/"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            d = json.loads(r.read().decode("utf-8"))
        data = d.get("data", {})
        # 北向：hk2sh（沪股通）+ hk2sz（深股通）
        hk2sh = data.get("hk2sh", {})
        hk2sz = data.get("hk2sz", {})
        sh2hk = data.get("sh2hk", {})
        sz2hk = data.get("sz2hk", {})

        north = round(((hk2sh.get("dayNetAmtIn") or 0) + (hk2sz.get("dayNetAmtIn") or 0)) / 1e8, 2)
        south = round(((sh2hk.get("dayNetAmtIn") or 0) + (sz2hk.get("dayNetAmtIn") or 0)) / 1e8, 2)
        ALERT_THRESHOLD = 50

        # 非交易时段 dayNetAmtIn=0 是正常的，加上 status 字段说明
        sh_status  = hk2sh.get("status", 0)
        note = "交易中" if sh_status == 2 else ("已收盘" if sh_status == 1 else "休市")

        return {
            "north_yi": north,
            "south_yi": south,
            "north_alert": abs(north) >= ALERT_THRESHOLD,
            "south_alert": abs(south) >= ALERT_THRESHOLD,
            "north_label": "北向资金（陆股通）",
            "south_label": "南向资金（港股通）",
            "note": note,
            "sh_detail": round((hk2sh.get("dayNetAmtIn") or 0) / 1e8, 2),
            "sz_detail": round((hk2sz.get("dayNetAmtIn") or 0) / 1e8, 2),
            "date": hk2sh.get("date2", ""),
            "updated": datetime.now().strftime("%H:%M"),
        }
    except Exception as e:
        print(f"  ⚠️ 南北向资金获取失败: {e}")
        return {"north_yi": 0, "south_yi": 0, "north_alert": False, "south_alert": False,
                "north_label": "北向资金", "south_label": "南向资金", "date": "", "updated": "--"}


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETF三因子监测 v7.1 — 零第三方依赖")
    parser.add_argument("--date",   type=str, default=None, help="分析日期 (YYYY-MM-DD)，默认最近交易日")
    parser.add_argument("--send",   action="store_true",    help="完整分析后发送邮件")
    parser.add_argument("--record", action="store_true",    help="仅采集当日份额数据入库，不做完整分析")
    parser.add_argument("--stats",  action="store_true",    help="查看本地DB状态")
    args = parser.parse_args()

    if args.stats:
        if not DATA_STORE_AVAILABLE:
            print("❌ etf_data_store.py 不可用")
            sys.exit(1)
        store = ETFDataStore()
        stats = store.get_stats()
        print("=" * 60)
        print("📊 ETF本地数据库状态")
        print("=" * 60)
        print(f"  数据库路径: {store.db_path}")
        print(f"  总记录数:   {stats['total_records']}")
        print(f"  覆盖交易日: {stats['total_dates']}")
        print(f"  日期范围:   {stats['date_range'][0]} ~ {stats['date_range'][1]}")
        print(f"  含份额记录: {stats['records_with_shares']}/{stats['total_records']}")
        print(f"\n  最近5个交易日:")
        for d, cnt in stats["recent_dates"]:
            print(f"    {d}: {cnt}只ETF")
        if stats["total_records"] == 0:
            print("\n  💡 提示: 运行 --record 采集今日数据入库")
        sys.exit(0)

    main(args.date, args.send, args.record)
