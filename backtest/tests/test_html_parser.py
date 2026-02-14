#!/usr/bin/env python3
"""Unit tests for the HTML parser."""

import os
import pytest
from pathlib import Path

from backtest.html_parser import EarningsReportParser, TradeCandidate

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def parser():
    return EarningsReportParser()


class TestFormatA:
    """Sept 2025 format: div.ticker with price, div.score, card CSS class grade."""

    def test_extract_candidates(self, parser):
        path = FIXTURES / "format_a_sept.html"
        candidates = parser.parse_single_report(str(path))
        # Should not crash even though filename has no date
        # Use a renamed file approach
        assert isinstance(candidates, list)

    def test_with_date_filename(self, parser, tmp_path):
        src = (FIXTURES / "format_a_sept.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-09-04.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 2

        aeo = next(c for c in candidates if c.ticker == "AEO")
        assert aeo.grade == "A"
        assert aeo.score == 88.0
        assert aeo.price == 17.28
        assert aeo.report_date == "2025-09-04"
        assert aeo.grade_source == "html"

        play = next(c for c in candidates if c.ticker == "PLAY")
        assert play.grade == "B"
        assert play.score == 72.0


class TestFormatB:
    """Oct 2025 format: h2 ticker with $, div.score fraction."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_b_oct.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-10-14.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "FBK"
        assert c.grade == "C"
        assert c.score == 67.0
        assert c.price == 56.31


class TestFormatB2:
    """Dec 2025 early: h2 ticker, grade-badge text, score in badge."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_b2_dec.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-12-02.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "CRDO"
        assert c.grade == "A"
        assert c.score == 88.5
        assert c.price == 198.12


class TestFormatD:
    """Dec 2025 mid: span.ticker, h4 score, grade-badge class."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_d_dec.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-12-10.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "PLAB"
        assert c.grade == "C"
        assert c.score == 69.0
        assert c.price == 31.27


class TestFormatE:
    """Jan 2026: span.ticker, grade-badge text, total-score-value."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_e_jan.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-01-15.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "TSM"
        assert c.grade == "B"
        assert c.score == 78.0
        assert c.price == 345.25


class TestFormatF:
    """Feb 2026 v1: stock-ticker, stock-score-value, stock-score-label."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_f_feb04.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-02-04.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "GSK"
        assert c.grade == "B"
        assert c.score == 84.0
        assert c.price == 56.46


class TestFormatG:
    """Feb 2026 v2: stock-ticker, score-breakdown Total Score, stock-grade class."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_g_feb11.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-02-11.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "VRT"
        assert c.grade == "A"
        assert c.score == 86.5
        assert c.price == 233.0

    def test_ignores_element_scores(self, parser, tmp_path):
        """Score should be 86.5 (total), not 2 or 5 (element scores)."""
        src = (FIXTURES / "format_g_feb11.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-02-11.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        c = candidates[0]
        assert c.score == 86.5
        assert c.score > 5  # Not an element score


class TestFormatH:
    """Feb 2026 v3: stock-ticker, score-value, stock-grade class, parent grade-header."""

    def test_extract(self, parser, tmp_path):
        src = (FIXTURES / "format_h_feb13.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-02-13.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "CRSR"
        assert c.grade == "A"
        assert c.score == 88.5
        assert c.price == 6.25
        assert c.gap_size == 22.1


class TestNoStocks:
    """No-stocks page should return empty list."""

    def test_no_stocks(self, parser, tmp_path):
        src = (FIXTURES / "no_stocks.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-12-16.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert candidates == []


class TestUpcomingSection:
    """Upcoming section tickers should be excluded."""

    def test_excludes_upcoming(self, parser, tmp_path):
        src = (FIXTURES / "upcoming_section.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-01-19.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        tickers = [c.ticker for c in candidates]
        assert "REAL" in tickers
        assert "FAKE" not in tickers


class TestDeduplication:
    """Deduplication by (report_date, ticker). First-seen wins; score=None replaced."""

    def test_dedup_first_seen_wins(self, parser):
        c1 = TradeCandidate("AAPL", "2025-10-01", "A", "html", 90.0)
        c2 = TradeCandidate("AAPL", "2025-10-01", "B", "inferred", 75.0)
        result = parser._deduplicate([c1, c2])
        assert len(result) == 1
        assert result[0].grade == "A"  # First seen wins
        assert result[0].score == 90.0

    def test_dedup_different_dates(self, parser):
        c1 = TradeCandidate("AAPL", "2025-10-01", "A", "html", 90.0)
        c2 = TradeCandidate("AAPL", "2025-10-02", "B", "html", 75.0)
        result = parser._deduplicate([c1, c2])
        assert len(result) == 2  # Different dates, both kept

    def test_dedup_first_with_score(self, parser):
        """score=None entry replaced by scored entry."""
        c1 = TradeCandidate("AAPL", "2025-10-01", "A", "html")  # score=None
        c2 = TradeCandidate("AAPL", "2025-10-01", "A", "html", 85.0)
        result = parser._deduplicate([c1, c2])
        assert len(result) == 1
        assert result[0].score == 85.0  # Replaced with scored entry

    def test_dedup_scored_not_replaced(self, parser):
        """Already-scored entry NOT replaced by another scored entry."""
        c1 = TradeCandidate("AAPL", "2025-10-01", "A", "html", 80.0)
        c2 = TradeCandidate("AAPL", "2025-10-01", "A", "html", 95.0)
        result = parser._deduplicate([c1, c2])
        assert len(result) == 1
        assert result[0].score == 80.0  # First seen wins


class TestTickerValidation:
    """Ticker validation edge cases."""

    def test_valid_tickers(self, parser):
        assert parser._clean_ticker("AAPL") == "AAPL"
        assert parser._clean_ticker("$AAPL") == "AAPL"
        assert parser._clean_ticker("BRK.B") == "BRK.B"
        assert parser._clean_ticker("UA") == "UA"
        assert parser._clean_ticker("UAA") == "UAA"
        assert parser._clean_ticker("USB") == "USB"

    def test_invalid_tickers(self, parser):
        assert parser._clean_ticker("") is None
        assert parser._clean_ticker("123") is None
        assert parser._clean_ticker("a") is None  # lowercase


class TestNestedTicker:
    """Oct 21 format: stock-ticker with nested ticker-symbol span."""

    def test_format_oct21_nested_ticker(self, parser, tmp_path):
        src = (FIXTURES / "format_oct21_nested_ticker.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-10-21.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "GM"
        assert c.score == 89.5


class TestScoreNumber:
    """Oct 22 format: score-number class."""

    def test_format_oct22_score_number(self, parser, tmp_path):
        src = (FIXTURES / "format_oct22_score_number.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-10-22.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "PLTR"
        assert c.score == 91.5


class TestBareScore:
    """Oct 23 format: bare number in div.score."""

    def test_format_oct23_bare_score(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <h2>$NFLX</h2>
            <div class="score">89.5</div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-10-23.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].score == 89.5


class TestH3Ticker:
    """Nov 3 format: h3 tag for ticker."""

    def test_format_nov03_h3_ticker(self, parser, tmp_path):
        src = (FIXTURES / "format_nov03_h3_ticker.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-11-03.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "CIFR"
        assert c.score == 72.0


class TestExchangePrefixTicker:
    """Jan 20 format: div.ticker with exchange prefix 'NYSE: USB'."""

    def test_exchange_prefix_ticker(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-b">
          <div class="stock-card b-grade">
            <div class="ticker">NYSE: USB</div>
            <div class="score">76.0 pts</div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2026-01-20.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].ticker == "USB"


class TestNoDefaultScore:
    """Grade available but no score -> score=None (no default assignment)."""

    def test_no_default_score(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <h2>$AAPL</h2>
            <div class="company-name">Apple Inc</div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-11-10.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.grade == "A"
        assert c.score is None


class TestArticleCard:
    """Jan 06 format: <article class='stock-card'> instead of <div>."""

    def test_article_stock_card(self, parser, tmp_path):
        src = (FIXTURES / "format_jan06_article_card.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-01-06.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "LEVI"
        assert c.grade == "B"
        assert c.score == 72.0
        assert c.price == 22.50


class TestRatingBadge:
    """Oct 10 format: rating-badge grade-b with '74.0 Points'."""

    def test_rating_badge_grade_and_score(self, parser, tmp_path):
        src = (FIXTURES / "format_oct10_rating_badge.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-10-10.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "FAST"
        assert c.grade == "B"
        assert c.grade_source == "html"
        assert c.score == 74.0
        assert c.price == 81.20


class TestLargeGrade:
    """Feb 09 format: large-grade grade-b + grade-score '74.0 points'."""

    def test_large_grade_and_grade_score(self, parser, tmp_path):
        src = (FIXTURES / "format_feb09_large_grade.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-02-09.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "LYFT"
        assert c.grade == "B"
        assert c.grade_source == "html"
        assert c.score == 74.0
        assert c.price == 15.80


class TestScoreSlash100:
    """S-1: score-value '73/100' should not be destroyed by re.sub."""

    def test_score_value_slash100(self, parser, tmp_path):
        src = (FIXTURES / "format_nov14_slash100.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2025-11-14.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "AMAT"
        assert c.score == 73.0
        assert c.grade == "B"
        assert c.grade_source == "html"


class TestH4ScoreSlash100:
    """S-2: h4 '5-Element Backtest Score: 88/100' -> score=88.0."""

    def test_h4_slash100(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <h2>$AVGO</h2>
            <h4>5-Element Backtest Score: 88/100</h4>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-12-05.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "AVGO"
        assert c.score == 88.0
        assert c.grade == "A"
        assert c.grade_source == "html"


class TestGradeCardClass:
    """G-1: stock-card grade-a-card -> grade='A' via GRADE_CLASS_RE."""

    def test_grade_card_class(self, parser, tmp_path):
        src = (FIXTURES / "format_jan30_grade_card.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-01-30.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "KLAC"
        assert c.grade == "A"
        assert c.grade_source == "html"
        assert c.score == 86.0


class TestScoreBadgeGrade:
    """G-2: score-badge grade-a + '86.0A-Grade...' -> grade='A', score=86.0."""

    def test_score_badge_grade_and_score(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section">
          <div class="stock-card">
            <div class="stock-ticker"><span class="ticker-symbol">MRVL</span></div>
            <span class="score-badge grade-a">86.0A-Grade&#9733;&#9733;&#9733;</span>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-12-03.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "MRVL"
        assert c.grade == "A"
        assert c.grade_source == "html"
        assert c.score == 86.0


class TestGradeHeaderText:
    """G-3: grade-header text 'A-GRADE ...' -> grade='A'."""

    def test_grade_header_text_only(self, parser, tmp_path):
        src = (FIXTURES / "format_feb05_text_header.html").read_text()
        f = tmp_path / "earnings_trade_analysis_2026-02-05.html"
        f.write_text(src)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "SNAP"
        assert c.grade == "A"
        assert c.grade_source == "html"
        assert c.score == 86.0


class TestH3Score:
    """h3 inside score-breakdown: '94/100 Points' -> score=94.0."""

    def test_h3_slash100_in_breakdown(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <div class="stock-ticker"><span class="ticker-symbol">GOOG</span></div>
            <div class="score-breakdown">
              <h3>Backtest Score Breakdown: 94/100 Points</h3>
              <div class="score-row">
                <span class="score-label">Earnings Surprise</span>
                <span class="score-value">4/5</span>
              </div>
            </div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-10-29.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "GOOG"
        assert c.score == 94.0
        assert c.grade == "A"
        assert c.grade_source == "html"

    def test_h4_slash100_in_breakdown(self, parser, tmp_path):
        """h4 inside score-breakdown: '88/100' -> score=88.0 (2026-02-10 format)."""
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <div class="stock-ticker"><span class="ticker-symbol">DDOG</span></div>
            <div class="score-breakdown">
              <h4>5-Element Backtest Score: 88/100</h4>
              <div class="score-item">
                <span>Gap Size (25%)</span>
                <span>20/25</span>
              </div>
            </div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2026-02-10.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.ticker == "DDOG"
        assert c.score == 88.0
        assert c.grade == "A"
        assert c.grade_source == "html"

    def test_h3_total_score_in_breakdown(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <div class="stock-ticker"><span class="ticker-symbol">MSFT</span></div>
            <div class="score-breakdown">
              <h3 style="color: green;">Total Score: 92.5/100</h3>
            </div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-10-28.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].score == 92.5


class TestScoreItemValue:
    """score-item with Total label: '88/100' -> score=88.0."""

    def test_score_item_total(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <div class="stock-ticker"><span class="ticker-symbol">NVDA</span></div>
            <div class="score-breakdown">
              <div class="score-item">
                <span class="score-item-name">Weighted Total Score</span>
                <span class="score-item-value">88/100</span>
              </div>
            </div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-11-26.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].score == 88.0


class TestTotalValuePts:
    """total-value '76.0 pts' -> score=76.0."""

    def test_total_value_pts(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-b">
          <div class="stock-card b-grade">
            <div class="stock-ticker"><span class="ticker-symbol">WMT</span></div>
            <div class="total-score">
              <span class="total-value">76.0 pts</span>
            </div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2026-01-27.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].score == 76.0

    def test_total_value_percentage_fallback(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-b">
          <div class="stock-card b-grade">
            <div class="stock-ticker"><span class="ticker-symbol">COST</span></div>
            <div class="total-score">
              <span class="total-value">3.45 / 5.00 (69%)</span>
            </div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2026-02-06.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].score == 69.0


class TestBadgeGradeClass:
    """grade-badge b-grade -> grade='B' via [abcd]-grade pattern."""

    def test_badge_with_grade_class(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section">
          <div class="stock-card">
            <div class="stock-ticker"><span class="ticker-symbol">AMZN</span></div>
            <div class="grade-badge b-grade">B</div>
            <div class="score">78.0</div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-11-15.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.grade == "B"
        assert c.grade_source == "html"
        assert c.score == 78.0


class TestBadgeGradeText:
    """grade-badge with bare text 'B' -> grade='B'."""

    def test_badge_bare_text(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section">
          <div class="stock-card">
            <div class="stock-ticker"><span class="ticker-symbol">META</span></div>
            <div class="grade-badge">B</div>
            <div class="score">75.0</div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-11-16.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.grade == "B"
        assert c.grade_source == "html"


class TestCompoundTicker:
    """UA/UAA -> 2 separate candidates ('UA' and 'UAA')."""

    def test_compound_ticker_split(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-b">
          <div class="stock-card b-grade">
            <div class="stock-ticker">UA/UAA</div>
            <div class="score">72.0</div>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-11-20.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 2
        tickers = sorted([c.ticker for c in candidates])
        assert tickers == ["UA", "UAA"]
        for c in candidates:
            assert c.score == 72.0
            assert c.grade == "B"


class TestH3ScoreOutsideBreakdown:
    """h3 outside score-breakdown with 'Score' -> extracted via step 5."""

    def test_h3_score_outside_breakdown(self, parser, tmp_path):
        html = """<html><body>
        <div class="grade-section grade-a">
          <div class="stock-card a-grade">
            <h2>$ORCL</h2>
            <h3>5-Factor Backtest Score: 88/100</h3>
          </div>
        </div>
        </body></html>"""
        f = tmp_path / "earnings_trade_analysis_2025-12-05.html"
        f.write_text(html)
        candidates = parser.parse_single_report(str(f))
        assert len(candidates) == 1
        assert candidates[0].score == 88.0
