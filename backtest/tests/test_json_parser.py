#!/usr/bin/env python3
"""Unit tests for the JSON candidates parser."""

import json

from backtest.json_parser import parse_candidates_json


class TestValidJson:
    """Normal case: valid JSON -> List[TradeCandidate]."""

    def test_single_candidate(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "generated_at": "2026-02-19T09:06:00-05:00",
            "candidates": [
                {
                    "ticker": "TS",
                    "grade": "A",
                    "score": 90,
                    "price": 52.86,
                    "gap_size": 6.30,
                    "company_name": "Tenaris S.A. ADR",
                }
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 1
        c = result[0]
        assert c.ticker == "TS"
        assert c.grade == "A"
        assert c.grade_source == "json"
        assert c.score == 90.0
        assert c.price == 52.86
        assert c.gap_size == 6.30
        assert c.company_name == "Tenaris S.A. ADR"
        assert c.report_date == "2026-02-19"

    def test_multiple_candidates(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {"ticker": "TS", "grade": "A", "score": 90, "price": 52.86},
                {"ticker": "PLTR", "grade": "B", "score": 78, "price": 25.0},
                {"ticker": "LOW", "grade": "C", "score": 60, "price": 100.0},
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 3
        assert result[0].ticker == "TS"
        assert result[1].ticker == "PLTR"
        assert result[2].ticker == "LOW"

    def test_optional_fields_null(self, tmp_path):
        """gap_size and company_name are optional (null allowed)."""
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {
                    "ticker": "TS",
                    "grade": "A",
                    "score": 90,
                    "price": 52.86,
                    "gap_size": None,
                    "company_name": None,
                }
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 1
        assert result[0].gap_size is None
        assert result[0].company_name is None

    def test_optional_fields_missing(self, tmp_path):
        """gap_size and company_name can be omitted entirely."""
        data = {
            "report_date": "2026-02-19",
            "candidates": [{"ticker": "TS", "grade": "A", "score": 90, "price": 52.86}],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 1
        assert result[0].gap_size is None
        assert result[0].company_name is None


class TestMissingRequiredFields:
    """Candidates missing required fields are skipped."""

    def test_missing_ticker(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {"grade": "A", "score": 90, "price": 52.86},
                {"ticker": "PLTR", "grade": "B", "score": 78, "price": 25.0},
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 1
        assert result[0].ticker == "PLTR"

    def test_missing_score(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {"ticker": "TS", "grade": "A", "price": 52.86},
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 0

    def test_missing_price(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {"ticker": "TS", "grade": "A", "score": 90},
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 0

    def test_missing_grade(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {"ticker": "TS", "score": 90, "price": 52.86},
            ],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert len(result) == 0


class TestEmptyCandidates:
    """Empty candidates list -> empty result."""

    def test_empty_list(self, tmp_path):
        data = {"report_date": "2026-02-19", "candidates": []}
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert result == []

    def test_missing_candidates_key(self, tmp_path):
        data = {"report_date": "2026-02-19"}
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert result == []


class TestInvalidJson:
    """Invalid JSON -> empty list (no exception)."""

    def test_malformed_json(self, tmp_path):
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text("{invalid json content")
        result = parse_candidates_json(str(f))
        assert result == []

    def test_nonexistent_file(self, tmp_path):
        result = parse_candidates_json(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_json_array_not_object(self, tmp_path):
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text('[{"ticker": "TS"}]')
        result = parse_candidates_json(str(f))
        assert result == []


class TestReportDateExtraction:
    """report_date from JSON takes precedence, fallback to filename."""

    def test_report_date_from_json(self, tmp_path):
        data = {
            "report_date": "2026-02-19",
            "candidates": [{"ticker": "TS", "grade": "A", "score": 90, "price": 52.86}],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-19.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert result[0].report_date == "2026-02-19"

    def test_report_date_fallback_to_filename(self, tmp_path):
        data = {
            "candidates": [{"ticker": "TS", "grade": "A", "score": 90, "price": 52.86}],
        }
        f = tmp_path / "earnings_trade_candidates_2026-02-20.json"
        f.write_text(json.dumps(data))
        result = parse_candidates_json(str(f))
        assert result[0].report_date == "2026-02-20"
