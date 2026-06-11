#!/usr/bin/env python3
"""Verify CSI 300 ETF volume from Tencent daily K-line API.

Standalone script: standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any

ETFS = {
    "510300": "华泰柏瑞沪深300ETF",
    "510310": "易方达沪深300ETF",
    "510330": "华夏沪深300ETF",
    "159919": "嘉实沪深300ETF",
}


@dataclass
class KLine:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


def market_prefix(code: str) -> str:
    return "sz" if code.startswith(("159", "0", "3")) else "sh"


def tencent_url(code: str, limit: int) -> str:
    pfx = market_prefix(code)
    return f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pfx}{code},day,,,{limit},qfq"


def fetch_tencent_kline(code: str, limit: int) -> list[KLine]:
    pfx = market_prefix(code)
    url = tencent_url(code, limit)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    payload: dict[str, Any] = json.loads(raw)
    key = f"{pfx}{code}"
    node = payload.get("data", {}).get(key, {})
    rows = node.get("day") or node.get("qfqday") or []
    out: list[KLine] = []
    for row in rows:
        if len(row) < 6 or not row[0]:
            continue
        out.append(
            KLine(
                date=str(row[0]),
                open=float(row[1]),
                close=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                volume=float(row[5]),
            )
        )
    return out


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def fmt_num(x: float | None) -> str:
    if x is None:
        return "N/A"
    return f"{x:,.2f}"


def analyze_target(rows: list[KLine], target_date: str) -> dict[str, float | int | None | str]:
    idx = next((i for i, row in enumerate(rows) if row.date == target_date), None)
    if idx is None:
        return {"status": "missing"}

    target = rows[idx]
    prev20 = [r.volume for r in rows[max(0, idx - 20):idx]]
    prev60 = [r.volume for r in rows[max(0, idx - 60):idx]]
    avg20 = avg(prev20)
    avg60 = avg(prev60)
    return {
        "status": "ok",
        "index": idx,
        "volume": target.volume,
        "avg20": avg20,
        "ratio20": target.volume / avg20 if avg20 else None,
        "n20": len(prev20),
        "avg_window": avg60,
        "ratio_window": target.volume / avg60 if avg60 else None,
        "n_window": len(prev60),
    }


def print_table(code: str, name: str, rows: list[KLine], target_date: str, show_last: int = 30) -> None:
    print("=" * 96)
    print(f"{code} {name}")
    print(f"Tencent URL: {tencent_url(code, len(rows) or 90)}")
    print(f"Rows fetched: {len(rows)} | Showing last {min(show_last, len(rows))} rows | target={target_date}")
    print("date        close      volume_raw        volume_wan")
    print("----------  ---------  ----------------  ------------")
    for row in rows[-show_last:]:
        mark = "  <-- TARGET" if row.date == target_date else ""
        print(f"{row.date}  {row.close:9.3f}  {row.volume:16,.2f}  {row.volume/10000:12,.2f}{mark}")

    result = analyze_target(rows, target_date)
    print("\nTarget-day analysis:")
    if result["status"] == "missing":
        print(f"  {target_date}: NOT FOUND in fetched Tencent K-line rows")
        return
    print(f"  target_volume_raw: {fmt_num(result['volume'])} ({fmt_num(float(result['volume'])/10000)} 万单位)")
    print(f"  prev20_n: {result['n20']} | prev20_avg_raw: {fmt_num(result['avg20'])} | ratio20: {fmt_num(result['ratio20'])}x")
    print(
        f"  window_n: {result['n_window']} | window_avg_raw: {fmt_num(result['avg_window'])} "
        f"| ratio_window: {fmt_num(result['ratio_window'])}x"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify CSI 300 ETF volumes from Tencent K-line API")
    parser.add_argument("--target-date", default="2026-06-10", help="Target trading date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=90, help="Tencent K-line row limit")
    parser.add_argument("--show-last", type=int, default=30, help="Rows to print per ETF")
    args = parser.parse_args()

    print("CSI 300 ETF volume verification via Tencent daily K-line API")
    print(f"target_date={args.target_date} limit={args.limit}")
    print("Note: volume unit is Tencent raw K-line field r[5]; ratios are unit-invariant.\n")

    failures = 0
    for code, name in ETFS.items():
        try:
            rows = fetch_tencent_kline(code, args.limit)
            if not rows:
                raise RuntimeError("empty K-line rows")
            print_table(code, name, rows, args.target_date, args.show_last)
            print()
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            failures += 1
            print("=" * 96)
            print(f"{code} {name}: ERROR: {exc}", file=sys.stderr)

    print("COMPLETE")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
