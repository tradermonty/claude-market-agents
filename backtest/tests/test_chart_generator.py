#!/usr/bin/env python3
"""Unit tests for chart_generator module."""

import pytest

from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import TradeResult


def make_trade(
    ticker="TEST",
    grade="A",
    entry_date="2025-10-02",
    exit_date="2025-11-15",
    entry_price=100.0,
    exit_price=115.0,
    return_pct=15.0,
    exit_reason="max_holding",
    score=85.0,
):
    return TradeResult(
        ticker=ticker,
        grade=grade,
        grade_source="html",
        score=score,
        report_date="2025-10-01",
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        shares=100,
        invested=10000.0,
        pnl=(exit_price - entry_price) * 100,
        return_pct=return_pct,
        holding_days=44,
        exit_reason=exit_reason,
    )


def make_bar(date, open_p, high, low, close, adj_close=None, volume=100000):
    return PriceBar(
        date=date,
        open=open_p,
        high=high,
        low=low,
        close=close,
        adj_close=adj_close if adj_close is not None else close,
        volume=volume,
    )


class TestGenerateChartFilename:
    def test_basic(self):
        from backtest.chart_generator import ChartGenerator

        trade = make_trade(ticker="AAPL", entry_date="2025-10-02", grade="A")
        assert ChartGenerator._generate_chart_filename(trade) == "AAPL_2025-10-02_A.png"

    def test_special_ticker(self):
        from backtest.chart_generator import ChartGenerator

        trade = make_trade(ticker="BRK.B", entry_date="2025-10-02", grade="B")
        assert ChartGenerator._generate_chart_filename(trade) == "BRK-B_2025-10-02_B.png"

    def test_slash_ticker(self):
        from backtest.chart_generator import ChartGenerator

        trade = make_trade(ticker="BF/A", entry_date="2025-10-02", grade="C")
        assert ChartGenerator._generate_chart_filename(trade) == "BF-A_2025-10-02_C.png"


class TestSlicePriceWindow:
    def _make_bars(self, start_month=9, start_day=15, count=60):
        """Generate sequential bars from a start date."""
        from datetime import datetime, timedelta

        bars = []
        dt = datetime(2025, start_month, start_day)
        for i in range(count):
            d = dt + timedelta(days=i)
            # Skip weekends
            if d.weekday() >= 5:
                continue
            date_str = d.strftime("%Y-%m-%d")
            price = 100 + i * 0.5
            bars.append(make_bar(date_str, price, price + 2, price - 1, price + 1))
        return bars

    def test_basic_slice(self):
        from backtest.chart_generator import ChartGenerator

        bars = self._make_bars(start_month=9, start_day=15, count=80)
        trade = make_trade(entry_date="2025-10-02", exit_date="2025-11-05")
        window = ChartGenerator._slice_price_window(trade, bars)
        assert len(window) > 0
        # Window should include bars before entry
        first_date = window[0].date
        assert first_date < "2025-10-02"

    def test_entry_at_start_of_data(self):
        from backtest.chart_generator import ChartGenerator

        bars = self._make_bars(start_month=10, start_day=2, count=50)
        trade = make_trade(entry_date="2025-10-02", exit_date="2025-11-05")
        window = ChartGenerator._slice_price_window(trade, bars)
        assert len(window) > 0
        assert window[0].date == "2025-10-02"

    def test_no_matching_bars(self):
        from backtest.chart_generator import ChartGenerator

        bars = self._make_bars(start_month=12, start_day=1, count=20)
        trade = make_trade(entry_date="2025-10-02", exit_date="2025-10-15")
        window = ChartGenerator._slice_price_window(trade, bars)
        assert window == []

    def test_insufficient_bars(self):
        from backtest.chart_generator import ChartGenerator

        bars = [make_bar("2025-10-02", 100, 102, 99, 101)]
        trade = make_trade(entry_date="2025-10-02", exit_date="2025-10-02")
        window = ChartGenerator._slice_price_window(trade, bars)
        assert len(window) == 1


class TestGenerateTradeChart:
    @pytest.fixture
    def require_mplfinance(self):
        pytest.importorskip("mplfinance")
        pytest.importorskip("matplotlib")
        pytest.importorskip("pandas")

    def _make_bars_for_chart(self):
        """Generate enough bars for a valid chart."""
        from datetime import datetime, timedelta

        bars = []
        dt = datetime(2025, 9, 15)
        for i in range(80):
            d = dt + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            date_str = d.strftime("%Y-%m-%d")
            price = 100 + i * 0.3
            bars.append(make_bar(date_str, price, price + 2, price - 1, price + 1, volume=500000))
        return bars

    def test_generates_png(self, require_mplfinance, tmp_path):
        from backtest.chart_generator import ChartGenerator

        gen = ChartGenerator()
        trade = make_trade()
        bars = self._make_bars_for_chart()

        success = gen.generate_trade_chart(trade, bars, str(tmp_path), stop_loss_pct=10.0)
        assert success

        expected_file = tmp_path / "TEST_2025-10-02_A.png"
        assert expected_file.exists()
        assert expected_file.stat().st_size > 1000  # Non-trivial PNG

    def test_losing_trade_red_marker(self, require_mplfinance, tmp_path):
        from backtest.chart_generator import ChartGenerator

        gen = ChartGenerator()
        trade = make_trade(exit_price=85.0, return_pct=-15.0, exit_reason="stop_loss")
        bars = self._make_bars_for_chart()

        success = gen.generate_trade_chart(trade, bars, str(tmp_path))
        assert success

    def test_skips_with_no_data(self, require_mplfinance, tmp_path):
        from backtest.chart_generator import ChartGenerator

        gen = ChartGenerator()
        trade = make_trade(entry_date="2025-10-02", exit_date="2025-10-15")
        bars = []

        success = gen.generate_trade_chart(trade, bars, str(tmp_path))
        assert not success

    def test_score_none(self, require_mplfinance, tmp_path):
        from backtest.chart_generator import ChartGenerator

        gen = ChartGenerator()
        trade = make_trade(score=None)
        bars = self._make_bars_for_chart()

        success = gen.generate_trade_chart(trade, bars, str(tmp_path))
        assert success

    def test_png_dimensions_fixed(self, require_mplfinance, tmp_path):
        """Verify PNG output is exactly 1200x700 (figsize=12x7, dpi=100)."""
        from PIL import Image

        from backtest.chart_generator import ChartGenerator

        gen = ChartGenerator()
        trade = make_trade()
        bars = self._make_bars_for_chart()

        gen.generate_trade_chart(trade, bars, str(tmp_path), stop_loss_pct=10.0)

        png_path = tmp_path / "TEST_2025-10-02_A.png"
        img = Image.open(png_path)
        assert img.size == (1200, 700), f"Expected (1200, 700), got {img.size}"


class TestGenerateAllCharts:
    @pytest.fixture
    def require_mplfinance(self):
        pytest.importorskip("mplfinance")
        pytest.importorskip("matplotlib")
        pytest.importorskip("pandas")

    def test_counts_generated_and_skipped(self, require_mplfinance, tmp_path):
        from datetime import datetime, timedelta

        from backtest.chart_generator import ChartGenerator

        bars = []
        dt = datetime(2025, 9, 15)
        for i in range(80):
            d = dt + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            date_str = d.strftime("%Y-%m-%d")
            price = 100 + i * 0.3
            bars.append(make_bar(date_str, price, price + 2, price - 1, price + 1))

        trades = [
            make_trade(ticker="AAA"),
            make_trade(ticker="BBB"),  # no price data
        ]
        price_data = {"AAA": bars}

        gen = ChartGenerator()
        count = gen.generate_all_charts(trades, price_data, str(tmp_path))
        assert count == 1

        charts_dir = tmp_path / "charts"
        assert charts_dir.exists()
        pngs = list(charts_dir.glob("*.png"))
        assert len(pngs) == 1
        assert pngs[0].name == "AAA_2025-10-02_A.png"
