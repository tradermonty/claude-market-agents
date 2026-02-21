#!/usr/bin/env python3
"""Tests for live.executor using mocked AlpacaClient."""

from unittest.mock import MagicMock, patch

import pytest

from live.config import LiveConfig
from live.executor import _is_market_hours_et, execute_poll_phase, execute_signals
from live.state_db import StateDB


@pytest.fixture
def config() -> LiveConfig:
    """Default config with entry_tif='day' to preserve existing bracket tests."""
    return LiveConfig(entry_tif="day")


@pytest.fixture
def opg_config() -> LiveConfig:
    """OPG config for OPG-specific tests."""
    return LiveConfig(entry_tif="opg")


@pytest.fixture
def db() -> StateDB:
    return StateDB(":memory:")


@pytest.fixture
def mock_alpaca() -> MagicMock:
    client = MagicMock(
        spec_set=[
            "cancel_order",
            "place_order",
            "place_bracket_order",
            "get_order",
            "get_order_by_client_id",
            "get_positions",
            "get_account",
            "get_clock",
        ]
    )
    client.get_order_by_client_id.return_value = None
    client.get_positions.return_value = []
    client.get_account.return_value = {"buying_power": "100000"}
    client.get_clock.return_value = {"is_open": False}
    return client


TRADE_DATE = "2026-02-17"
RUN_ID = "exec-2026-02-17-test"


def _make_signals(exits=None, entries=None):
    return {
        "trade_date": TRADE_DATE,
        "exits": exits or [],
        "entries": entries or [],
    }


class TestKillSwitch:
    def test_kill_switch_blocks(self, config, db, mock_alpaca):
        """Kill switch on => sys.exit(3)."""
        db.set_kill_switch(True)
        with pytest.raises(SystemExit) as exc_info:
            execute_signals(
                config,
                db,
                mock_alpaca,
                _make_signals(),
                TRADE_DATE,
                RUN_ID,
            )
        assert exc_info.value.code == 3


class TestPhaseA:
    def test_cancel_stop_before_sell(self, config, db, mock_alpaca):
        """Stop order is canceled before placing market sell."""
        mock_alpaca.place_order.return_value = {"id": "sell-001"}
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "150.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            exits=[
                {
                    "ticker": "AAPL",
                    "position_id": None,
                    "qty": 10,
                    "stop_order_id": "stop-old-001",
                }
            ]
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
            )

        mock_alpaca.cancel_order.assert_called_once_with("stop-old-001")
        mock_alpaca.place_order.assert_called_once()
        assert counts["exits_executed"] == 1

    def test_stop_already_filled(self, config, db, mock_alpaca):
        """If stop was already filled, skip market sell."""
        mock_alpaca.cancel_order.side_effect = Exception("422 order already filled")

        signals = _make_signals(
            exits=[
                {
                    "ticker": "AAPL",
                    "position_id": 1,
                    "qty": 10,
                    "stop_order_id": "stop-filled-001",
                    "stop_price": 135.0,
                }
            ]
        )

        # Add a position so close_position works
        pid = db.add_position(
            ticker="AAPL",
            entry_date="2026-02-10",
            entry_price=150.0,
            target_shares=10,
            actual_shares=10,
            invested=1500.0,
            stop_price=135.0,
            stop_order_id="stop-filled-001",
            score=None,
            grade=None,
            grade_source=None,
            report_date=None,
            company_name=None,
            gap_size=None,
        )

        signals["exits"][0]["position_id"] = pid

        counts = execute_signals(
            config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
        )

        # No market sell should be placed
        mock_alpaca.place_order.assert_not_called()
        assert counts["exits_executed"] == 1

        # Position should be closed
        open_pos = db.get_open_positions()
        assert len(open_pos) == 0


class TestIdempotency:
    def test_idempotent_order(self, config, db, mock_alpaca):
        """Same client_order_id in DB means skip (no duplicate)."""
        # Pre-insert order in DB
        db.add_order(
            client_order_id=f"{TRADE_DATE}_AAPL_exit_sell",
            ticker="AAPL",
            side="sell",
            intent="exit",
            trade_date=TRADE_DATE,
            qty=10,
        )

        signals = _make_signals(
            exits=[
                {
                    "ticker": "AAPL",
                    "position_id": None,
                    "qty": 10,
                    "stop_order_id": None,
                }
            ]
        )

        counts = execute_signals(
            config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
        )

        mock_alpaca.place_order.assert_not_called()
        assert counts["exits_executed"] == 1


class TestPhaseC:
    def test_recount_positions(self, config, db, mock_alpaca):
        """After sells, available slots reflect real Alpaca positions."""
        # Alpaca reports 18 positions after sells
        mock_alpaca.get_positions.return_value = [{"symbol": f"S{i}"} for i in range(18)]
        mock_alpaca.place_bracket_order.return_value = {
            "id": "buy-001",
            "legs": [{"id": "stop-leg-001"}],
        }
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            entries=[
                {"ticker": "NEW1", "qty": 10, "stop_price": 90.0},
                {"ticker": "NEW2", "qty": 10, "stop_price": 90.0},
                {"ticker": "NEW3", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
            )

        # max_positions=20, open=18, so only 2 entries can go through
        assert counts["entries_executed"] == 2
        assert counts["skipped"] == 1


class TestBracketOrder:
    def test_bracket_order_preferred(self, config, db, mock_alpaca):
        """Bracket order is tried first."""
        mock_alpaca.place_bracket_order.return_value = {
            "id": "bracket-001",
            "legs": [{"id": "stop-leg-001"}],
        }
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
            )

        mock_alpaca.place_bracket_order.assert_called_once()
        # place_order should NOT be called for the buy (only bracket)
        mock_alpaca.place_order.assert_not_called()

    def test_bracket_fallback_to_separate(self, config, db, mock_alpaca):
        """When bracket fails, fallback to separate buy + stop orders."""
        mock_alpaca.place_bracket_order.side_effect = Exception("bracket not supported")
        mock_alpaca.place_order.side_effect = [
            # First call: buy order
            {"id": "buy-fallback-001"},
            # Second call: stop order (after fill)
            {"id": "stop-fallback-001"},
        ]
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
            )

        assert counts["entries_executed"] == 1
        # place_order called twice: buy + stop
        assert mock_alpaca.place_order.call_count == 2
        # Verify the stop order params
        stop_call = mock_alpaca.place_order.call_args_list[1]
        assert stop_call.kwargs["type"] == "stop"
        assert stop_call.kwargs["time_in_force"] == "gtc"
        assert stop_call.kwargs["stop_price"] == 90.0


class TestKillSwitchOnStopFailure:
    def test_kill_switch_on_stop_failure(self, config, db, mock_alpaca):
        """Kill switch activates when stop placement fails after buy fill."""
        mock_alpaca.place_bracket_order.side_effect = Exception("bracket not supported")
        mock_alpaca.place_order.side_effect = [
            # Buy order succeeds
            {"id": "buy-001"},
            # Stop order fails
            Exception("network error"),
        ]
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
            )

        assert db.is_kill_switch_on() is True


class TestEntryTimeGuard:
    def test_entry_time_guard(self, config, db, mock_alpaca):
        """Entry blocked after cutoff minutes past open."""
        mock_alpaca.get_clock.return_value = {"is_open": True}

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        # Mock datetime.now(ET) to be 30 min after open
        fake_now = MagicMock()
        fake_now.replace.return_value = MagicMock()
        # 30 min after open = 1800 seconds
        fake_now.__sub__ = MagicMock(return_value=MagicMock())
        fake_now.__sub__.return_value.total_seconds.return_value = 1800.0

        with patch("live.executor.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.fromisoformat = MagicMock()
            counts = execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=False,
            )

        assert counts["skipped"] == 1
        assert counts["entries_executed"] == 0

    def test_entry_time_guard_skip(self, config, db, mock_alpaca):
        """--skip-time-check bypasses time guard."""
        mock_alpaca.place_bracket_order.return_value = {
            "id": "bracket-001",
            "legs": [{"id": "stop-leg-001"}],
        }
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_signals(
                config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
            )

        assert counts["entries_executed"] == 1
        # get_clock should not be called when skip_time_check=True
        mock_alpaca.get_clock.assert_not_called()


class TestDailyOrderLimit:
    def test_daily_order_limit(self, config, db, mock_alpaca):
        """Orders stop at daily limit."""
        # Fill up daily trade orders (entry+exit)
        for i in range(config.max_daily_trade_orders):
            db.add_order(
                client_order_id=f"fill-{i}",
                ticker=f"S{i}",
                side="buy",
                intent="entry",
                trade_date=TRADE_DATE,
                qty=10,
            )

        signals = _make_signals(
            entries=[
                {"ticker": "NEWSTOCK", "qty": 10, "stop_price": 90.0},
            ]
        )

        counts = execute_signals(
            config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
        )

        assert counts["skipped"] == 1
        assert counts["entries_executed"] == 0
        mock_alpaca.place_bracket_order.assert_not_called()


class TestBuyingPowerCheck:
    def test_buying_power_check(self, config, db, mock_alpaca):
        """Entries stop when buying power is below minimum."""
        mock_alpaca.get_account.return_value = {"buying_power": "1000"}

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        counts = execute_signals(
            config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
        )

        assert counts["skipped"] == 1
        assert counts["entries_executed"] == 0


class TestDryRun:
    def test_dry_run(self, config, db, mock_alpaca):
        """No actual API calls in dry run mode."""
        signals = _make_signals(
            exits=[
                {
                    "ticker": "AAPL",
                    "position_id": None,
                    "qty": 10,
                    "stop_order_id": "stop-001",
                }
            ],
            entries=[
                {"ticker": "MSFT", "qty": 10, "stop_price": 90.0},
            ],
        )

        counts = execute_signals(
            config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            dry_run=True,
            skip_time_check=True,
        )

        assert counts["exits_executed"] == 1
        assert counts["entries_executed"] == 1
        mock_alpaca.place_order.assert_not_called()
        mock_alpaca.place_bracket_order.assert_not_called()
        mock_alpaca.cancel_order.assert_not_called()
        mock_alpaca.get_order.assert_not_called()


# ── OPG-specific tests ──────────────────────────────────────────────────


class TestOPGSkipsBracket:
    def test_opg_skips_bracket(self, opg_config, db, mock_alpaca):
        """OPG mode places plain buy with tif=opg, no bracket."""
        mock_alpaca.place_order.return_value = {"id": "opg-buy-001"}

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        counts = execute_signals(
            opg_config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
            skip_poll=True,
        )

        assert counts["entries_executed"] == 1
        # Bracket should NOT be called
        mock_alpaca.place_bracket_order.assert_not_called()
        # place_order should be called with tif="opg"
        mock_alpaca.place_order.assert_called_once()
        call_kwargs = mock_alpaca.place_order.call_args.kwargs
        assert call_kwargs["time_in_force"] == "opg"
        assert call_kwargs["type"] == "market"

        # Verify planned_stop_price was stored in DB
        order = db.get_order_by_client_id(f"{TRADE_DATE}_AAPL_entry_buy")
        assert order is not None
        assert order["planned_stop_price"] == 90.0


class TestOPGBlockedDuringMarketHours:
    def test_opg_blocked_during_market_hours(self, opg_config, db, mock_alpaca):
        """OPG entry blocked when market is open (9:28-19:00 ET)."""
        # Mock _is_market_hours_et to return True
        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor._is_market_hours_et", return_value=True):
            counts = execute_signals(
                opg_config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=False,
            )

        assert counts["skipped"] == 1
        assert counts["entries_executed"] == 0
        mock_alpaca.place_order.assert_not_called()

    def test_opg_allowed_premarket(self, opg_config, db, mock_alpaca):
        """OPG entry allowed when market is closed (pre-market)."""
        mock_alpaca.place_order.return_value = {"id": "opg-buy-001"}

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        with patch("live.executor._is_market_hours_et", return_value=False):
            counts = execute_signals(
                opg_config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=False,
                skip_poll=True,
            )

        assert counts["entries_executed"] == 1


class TestOPGAllPhaseRejected:
    def test_opg_all_phase_rejected(self):
        """entry_tif=opg + phase=all should sys.exit(6)."""
        with (
            patch(
                "sys.argv",
                [
                    "executor",
                    "--signals-file",
                    "dummy.json",
                    "--phase",
                    "all",
                    "--state-db",
                    ":memory:",
                ],
            ),
            patch(
                "live.executor.LiveConfig",
                return_value=LiveConfig(entry_tif="opg"),
            ),
        ):
            from live.executor import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 6


class TestPollPhaseSkippedForDay:
    def test_poll_skipped_for_day_mode(self, config, db, mock_alpaca):
        """execute_poll_phase() returns immediately when entry_tif='day'."""
        result = execute_poll_phase(
            config=config,
            state_db=db,
            alpaca_client=mock_alpaca,
            trade_date="2026-01-01",
            run_id="test-poll-skip",
        )
        assert result == {
            "filled": 0,
            "stops_placed": 0,
            "unprotected": 0,
            "still_pending": 0,
        }
        mock_alpaca.get_order.assert_not_called()
        mock_alpaca.place_order.assert_not_called()
        # run_log に poll_skipped が記録されていることを確認
        with db._connect() as conn:
            row = conn.execute(
                "SELECT phase, status FROM run_log WHERE run_id = ?",
                ("test-poll-skip",),
            ).fetchone()
        assert row["phase"] == "poll_skipped"
        assert row["status"] == "completed"


class TestSkipPoll:
    def test_skip_poll(self, config, db, mock_alpaca):
        """skip_poll=True skips Phase E."""
        mock_alpaca.place_bracket_order.return_value = {
            "id": "bracket-001",
            "legs": [{"id": "stop-leg-001"}],
        }

        signals = _make_signals(
            entries=[
                {"ticker": "AAPL", "qty": 10, "stop_price": 90.0},
            ]
        )

        counts = execute_signals(
            config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
            skip_poll=True,
        )

        assert counts["entries_executed"] == 1
        # get_order should NOT be called (polling skipped)
        mock_alpaca.get_order.assert_not_called()


class TestPlacePhaseSlotCalculation:
    def test_place_phase_slot_calculation(self, opg_config, db, mock_alpaca):
        """OPG place phase: slot calculation subtracts exits from DB count."""
        # Pre-create 19 open positions in DB
        for i in range(19):
            db.add_position(
                ticker=f"S{i}",
                entry_date="2026-02-10",
                entry_price=100.0,
                target_shares=10,
                actual_shares=10,
                invested=1000.0,
                stop_price=90.0,
                stop_order_id=f"stop-{i}",
                score=None,
                grade=None,
                grade_source=None,
                report_date=None,
                company_name=None,
                gap_size=None,
            )

        # 1 exit signal → should free up 1 slot
        mock_alpaca.place_order.side_effect = [
            {"id": "sell-001"},  # exit sell
            {"id": "opg-buy-001"},  # entry buy 1
            {"id": "opg-buy-002"},  # entry buy 2
        ]
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        signals = _make_signals(
            exits=[
                {
                    "ticker": "S0",
                    "position_id": 1,
                    "qty": 10,
                    "stop_order_id": "stop-0",
                }
            ],
            entries=[
                {"ticker": "NEW1", "qty": 10, "stop_price": 90.0},
                {"ticker": "NEW2", "qty": 10, "stop_price": 90.0},
            ],
        )

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_signals(
                opg_config,
                db,
                mock_alpaca,
                signals,
                TRADE_DATE,
                RUN_ID,
                skip_time_check=True,
                skip_poll=True,
            )

        # 19 positions - 1 exit = 18 open, max=20, so 2 slots available
        assert counts["exits_executed"] == 1
        assert counts["entries_executed"] == 2
        assert counts["skipped"] == 0


class TestPollPhaseIdempotent:
    def test_poll_phase_idempotent(self, opg_config, db, mock_alpaca):
        """Poll phase: existing stop means no duplicate placement."""
        # Pre-create a pending entry order
        db.add_order(
            client_order_id=f"{TRADE_DATE}_AAPL_entry_buy",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date=TRADE_DATE,
            qty=10,
            alpaca_order_id="alp-entry-001",
            planned_stop_price=90.0,
        )
        # Pre-create the stop order (already placed)
        db.add_order(
            client_order_id=f"{TRADE_DATE}_AAPL_stop_sell",
            ticker="AAPL",
            side="sell",
            intent="stop",
            trade_date=TRADE_DATE,
            qty=10,
            alpaca_order_id="alp-stop-001",
        )

        # Mock: entry order is filled
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_poll_phase(
                opg_config,
                db,
                mock_alpaca,
                TRADE_DATE,
                "poll-run-001",
            )

        assert counts["filled"] == 1
        assert counts["stops_placed"] == 1
        # place_order should NOT be called (stop already exists)
        mock_alpaca.place_order.assert_not_called()


class TestPollPhasePendingOrders:
    def test_poll_phase_processes_various_statuses(self, opg_config, db, mock_alpaca):
        """Poll phase correctly handles partially_filled and accepted orders."""
        # Add orders with different non-terminal statuses
        for status, cid in [
            ("accepted", f"{TRADE_DATE}_NVDA_entry_buy"),
            ("new", f"{TRADE_DATE}_AMD_entry_buy"),
        ]:
            oid = db.add_order(
                client_order_id=cid,
                ticker=cid.split("_")[1],
                side="buy",
                intent="entry",
                trade_date=TRADE_DATE,
                qty=10,
                alpaca_order_id=f"alp-{cid}",
                planned_stop_price=90.0,
            )
            db.update_order_status(oid, status=status)

        # Both get filled
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }
        mock_alpaca.place_order.return_value = {"id": "stop-new-001"}

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_poll_phase(
                opg_config,
                db,
                mock_alpaca,
                TRADE_DATE,
                "poll-run-002",
            )

        assert counts["filled"] == 2
        assert counts["stops_placed"] == 2


class TestPollPhaseNullStopPrice:
    def test_poll_phase_null_stop_price(self, opg_config, db, mock_alpaca):
        """Poll phase: NULL planned_stop_price → CRITICAL log, no stop placed."""
        # Add entry order without planned_stop_price
        db.add_order(
            client_order_id=f"{TRADE_DATE}_AAPL_entry_buy",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date=TRADE_DATE,
            qty=10,
            alpaca_order_id="alp-entry-001",
            planned_stop_price=None,  # NULL
        )

        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_poll_phase(
                opg_config,
                db,
                mock_alpaca,
                TRADE_DATE,
                "poll-run-003",
            )

        assert counts["filled"] == 1
        assert counts["unprotected"] == 1
        assert counts["stops_placed"] == 0
        # No stop order should be placed
        mock_alpaca.place_order.assert_not_called()


class TestIsMarketHoursET:
    def test_premarket(self):
        """6:00 ET is not market hours."""
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        fake_time = real_dt(2026, 2, 17, 6, 0, tzinfo=et)
        with patch("live.executor.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.fromisoformat = real_dt.fromisoformat
            result = _is_market_hours_et(None)
        assert result is False

    def test_during_market(self):
        """10:00 ET is market hours."""
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        fake_time = real_dt(2026, 2, 17, 10, 0, tzinfo=et)
        with patch("live.executor.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.fromisoformat = real_dt.fromisoformat
            result = _is_market_hours_et(None)
        assert result is True

    def test_after_market(self):
        """20:00 ET is not market hours."""
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        fake_time = real_dt(2026, 2, 17, 20, 0, tzinfo=et)
        with patch("live.executor.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.fromisoformat = real_dt.fromisoformat
            result = _is_market_hours_et(None)
        assert result is False


class TestExitIdempotentTerminalNotCounted:
    """Finding 3: Terminal-status exit orders should not inflate slot calculation."""

    def test_terminal_exit_not_counted(self, opg_config, db, mock_alpaca):
        """Idempotent skip of terminal exit order does not count as exits_executed."""
        # Pre-create 20 open positions in DB
        for i in range(20):
            db.add_position(
                ticker=f"S{i}",
                entry_date="2026-02-10",
                entry_price=100.0,
                target_shares=10,
                actual_shares=10,
                invested=1000.0,
                stop_price=90.0,
                stop_order_id=f"stop-{i}",
                score=None,
                grade=None,
                grade_source=None,
                report_date=None,
                company_name=None,
                gap_size=None,
            )

        # Pre-insert a terminal (filled) exit order in DB
        oid = db.add_order(
            client_order_id=f"{TRADE_DATE}_S0_exit_sell",
            ticker="S0",
            side="sell",
            intent="exit",
            trade_date=TRADE_DATE,
            qty=10,
        )
        db.update_order_status(oid, status="filled", fill_price=100.0, filled_qty=10)

        signals = _make_signals(
            exits=[
                {
                    "ticker": "S0",
                    "position_id": 1,
                    "qty": 10,
                    "stop_order_id": None,
                }
            ],
            entries=[
                {"ticker": "NEW1", "qty": 10, "stop_price": 90.0},
            ],
        )

        counts = execute_signals(
            opg_config,
            db,
            mock_alpaca,
            signals,
            TRADE_DATE,
            RUN_ID,
            skip_time_check=True,
            skip_poll=True,
        )

        # Terminal exit order should NOT free a slot
        # 20 open positions - 0 real exits = 20, max=20, 0 slots
        assert counts["exits_executed"] == 0
        assert counts["entries_executed"] == 0
        assert counts["skipped"] == 1


class TestPollOrdersDoneForDay:
    """Finding 4: _poll_orders handles done_for_day/suspended as terminal."""

    def test_done_for_day_is_terminal(self, config, db, mock_alpaca):
        """done_for_day status is treated as terminal in _poll_orders."""
        from live.executor import _poll_orders

        db_oid = db.add_order(
            client_order_id="test-dfd",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date=TRADE_DATE,
            qty=10,
            alpaca_order_id="alp-dfd-001",
        )

        mock_alpaca.get_order.return_value = {
            "status": "done_for_day",
            "reject_reason": None,
            "qty": "10",
        }

        orders = [
            {
                "alpaca_order_id": "alp-dfd-001",
                "db_order_id": db_oid,
                "ticker": "AAPL",
            }
        ]

        with patch("live.executor.POLL_INTERVAL", 0):
            results = _poll_orders(mock_alpaca, db, orders, poll_timeout=5)

        # done_for_day should be in results (terminal)
        assert "alp-dfd-001" in results
        order = db.get_order_by_client_id("test-dfd")
        assert order["status"] == "done_for_day"


class TestPollPhaseReplacesTerminalStop:
    """Finding 5: Poll phase re-places stop when existing stop is terminal."""

    def test_replaces_canceled_stop(self, opg_config, db, mock_alpaca):
        """If existing stop is canceled, poll phase re-places it."""
        # Pre-create a pending entry order
        db.add_order(
            client_order_id=f"{TRADE_DATE}_AAPL_entry_buy",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date=TRADE_DATE,
            qty=10,
            alpaca_order_id="alp-entry-001",
            planned_stop_price=90.0,
        )
        # Pre-create a CANCELED stop order
        oid = db.add_order(
            client_order_id=f"{TRADE_DATE}_AAPL_stop_sell",
            ticker="AAPL",
            side="sell",
            intent="stop",
            trade_date=TRADE_DATE,
            qty=10,
            alpaca_order_id="alp-stop-canceled",
        )
        db.update_order_status(oid, status="canceled", reject_reason="user_canceled")

        # Mock: entry order is filled
        mock_alpaca.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": "100.0",
            "filled_qty": "10",
            "qty": "10",
        }
        mock_alpaca.place_order.return_value = {"id": "alp-stop-retry-001"}

        with patch("live.executor.POLL_INTERVAL", 0):
            counts = execute_poll_phase(
                opg_config,
                db,
                mock_alpaca,
                TRADE_DATE,
                "poll-run-retry",
            )

        assert counts["filled"] == 1
        assert counts["stops_placed"] == 1
        # place_order SHOULD be called to re-place the stop
        mock_alpaca.place_order.assert_called_once()
        call_kwargs = mock_alpaca.place_order.call_args.kwargs
        assert call_kwargs["type"] == "stop"
        assert call_kwargs["stop_price"] == 90.0
        # Uses retry client_order_id
        assert "retry" in call_kwargs["client_order_id"]


class TestAlpacaIdempotentTerminalNotCounted:
    """Finding: Alpaca-side idempotent check must not count terminal orders."""

    def test_alpaca_terminal_exit_not_counted(self, opg_config, db, mock_alpaca):
        """Alpaca has a canceled sell order — should NOT free a slot."""
        # Position open in DB
        db.add_position(
            ticker="TSLA",
            entry_date=TRADE_DATE,
            entry_price=200.0,
            target_shares=5,
            actual_shares=5,
            invested=1000.0,
            stop_price=180.0,
            stop_order_id=None,
            score=None,
            grade=None,
            grade_source=None,
            report_date=None,
            company_name=None,
            gap_size=None,
        )
        signals = {
            "trade_date": TRADE_DATE,
            "strategy": "ema_p10",
            "exits": [{"ticker": "TSLA", "reason": "trend_break", "qty": 5}],
            "entries": [],
        }

        # DB has no order, but Alpaca returns a CANCELED sell order
        mock_alpaca.get_order_by_client_id.return_value = {
            "id": "alp-sell-old",
            "status": "canceled",
            "client_order_id": f"{TRADE_DATE}_TSLA_exit_sell",
        }

        counts = execute_signals(
            opg_config,
            db,
            mock_alpaca,
            signals,
            trade_date=TRADE_DATE,
            run_id="test-alpaca-term",
            skip_poll=True,
        )

        # The exit was NOT actually executed (order is terminal)
        assert counts["exits_executed"] == 0
