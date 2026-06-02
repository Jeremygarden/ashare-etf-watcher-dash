#!/usr/bin/env python3
import os
import sqlite3
import tempfile
import unittest

from etf_data_store import ETFDataStore
from etf_v7_threefactor import analyze_all


class TestBackendRegressions(unittest.TestCase):
    def test_upsert_record_uses_explicit_date_code(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "etf_test.db")
            store = ETFDataStore(db_path=db_path)
            ok = store.upsert_record(
                "2026-06-01",
                "510300",
                {
                    "name": "测试ETF",
                    "composite_prob": 66.6,
                    "shares_yi": 123.4,
                },
            )
            self.assertTrue(ok)

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT date, code, name, composite_prob, shares_yi FROM etf_daily WHERE code='510300'"
                ).fetchone()
            self.assertEqual(row[0], "2026-06-01")
            self.assertEqual(row[1], "510300")
            self.assertEqual(row[2], "测试ETF")
            self.assertAlmostEqual(row[3], 66.6)
            self.assertAlmostEqual(row[4], 123.4)

    def test_analyze_all_reads_share_by_current_code_only(self):
        data_a = []
        data_b = []
        idx = []
        base = 100.0
        for i in range(30):
            d = f"2026-05-{i+1:02d}"
            price = base + i * 0.2
            row = {"date": d, "o": price, "c": price, "h": price, "l": price, "v": 1000000 + i * 1000}
            data_a.append(dict(row))
            data_b.append(dict(row))
            idx.append({"date": d, "c": 3000 + i})

        target_date = "2026-05-30"
        shares_map = {
            "510300": {
                target_date: {"shares_yi": 100.0, "delta_yi": 10.0, "delta_pct": 10.0},
            },
            "510500": {
                target_date: {"shares_yi": 200.0, "delta_yi": -1.0, "delta_pct": -1.0},
            },
        }

        hist_a = analyze_all(data_a, idx, shares_map, "510300", target_date, days=2)
        hist_b = analyze_all(data_b, idx, shares_map, "510500", target_date, days=2)
        rec_a = [x for x in hist_a if x["d"] == target_date][0]
        rec_b = [x for x in hist_b if x["d"] == target_date][0]

        # 510300 should use +10% share delta => high sp
        self.assertGreaterEqual(rec_a["sp"], 90)
        # 510500 should use -1% share delta => very low sp
        self.assertLessEqual(rec_b["sp"], 20)


if __name__ == "__main__":
    unittest.main()
