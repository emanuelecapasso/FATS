
"""Tests for historical CSV market data import."""

from datetime import UTC, datetime

import pytest

from core.schemas import Timeframe
from data.csv_importer import CSVMarketDataImporter
from database.market_data_repository import MarketDataRepository


def test_csv_importer_saves_valid_bars(tmp_path):
    csv_file = tmp_path / "eurusd.csv"
    csv_file.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T10:00:00+00:00,1.10,1.12,1.09,1.11,100\n"
        "2026-01-01T11:00:00+00:00,1.11,1.13,1.10,1.12,120\n",
        encoding="utf-8",
    )

    repository = MarketDataRepository(tmp_path / "test.db")
    importer = CSVMarketDataImporter(repository)

    result = importer.import_file(csv_file, "EURUSD", Timeframe.H1)

    bars = repository.load_bars(
        "EURUSD",
        Timeframe.H1,
        datetime(2026, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 0, tzinfo=UTC),
    )

    assert result.rows_read == 2
    assert result.bars_imported == 2
    assert result.rows_rejected == 0
    assert len(bars) == 2


def test_csv_importer_rejects_invalid_rows_and_counts_duplicates(tmp_path):
    csv_file = tmp_path / "eurusd.csv"
    csv_file.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T10:00:00+00:00,1.10,1.12,1.09,1.11,100\n"
        "2026-01-01T10:00:00+00:00,1.11,1.13,1.10,1.12,120\n"
        "invalid,1.10,1.12,1.09,1.11,100\n"
        "2026-01-01T12:00:00+00:00,1.10,1.00,1.09,1.11,100\n",
        encoding="utf-8",
    )

    repository = MarketDataRepository(tmp_path / "test.db")
    importer = CSVMarketDataImporter(repository)

    result = importer.import_file(csv_file, "EURUSD", Timeframe.H1)

    assert result.rows_read == 4
    assert result.bars_imported == 1
    assert result.rows_rejected == 2
    assert result.duplicates == 1


def test_csv_importer_rejects_invalid_pair(tmp_path):
    csv_file = tmp_path / "eurusd.csv"
    csv_file.write_text(
        "timestamp,open,high,low,close,volume\n",
        encoding="utf-8",
    )

    repository = MarketDataRepository(tmp_path / "test.db")
    importer = CSVMarketDataImporter(repository)

    with pytest.raises(ValueError, match="Invalid Forex pair"):
        importer.import_file(csv_file, "INVALID", Timeframe.H1)


def test_csv_importer_rejects_missing_timezone(tmp_path):
    csv_file = tmp_path / "eurusd.csv"
    csv_file.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T10:00:00,1.10,1.12,1.09,1.11,100\n",
        encoding="utf-8",
    )

    repository = MarketDataRepository(tmp_path / "test.db")
    importer = CSVMarketDataImporter(repository)

    with pytest.raises(ValueError, match="no valid market bars"):
        importer.import_file(csv_file, "EURUSD", Timeframe.H1)
