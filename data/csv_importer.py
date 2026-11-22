
import csv
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    MarketDataSnapshot,
    Timeframe,
    TimeframeSeries,
)
from database.market_data_repository import MarketDataRepository


@dataclass(frozen=True)
class CSVImportResult:
    rows_read: int
    bars_imported: int
    rows_rejected: int
    duplicates: int


class CSVMarketDataImporter:
    """Validate and import historical OHLCV data from CSV files."""

    REQUIRED_COLUMNS: ClassVar[set[str]] = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    def __init__(self, repository: MarketDataRepository) -> None:
        self.repository = repository

    def import_file(
        self,
        file_path: str | Path,
        pair: str,
        timeframe: Timeframe,
        provider_id: str = "csv_import",
    ) -> CSVImportResult:
        pair = pair.upper().strip().replace("/", "")

        if len(pair) != 6 or not pair.isalpha() or pair[:3] == pair[3:]:
            raise ValueError("Invalid Forex pair")

        if not provider_id or provider_id != provider_id.strip():
            raise ValueError(
                "provider_id must be a non-empty trimmed string"
            )

        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        bars_by_timestamp: dict[datetime, MarketBar] = {}
        rows_read = 0
        rows_rejected = 0
        duplicates = 0

        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)

            if not reader.fieldnames:
                raise ValueError("CSV file has no header")

            missing_columns = self.REQUIRED_COLUMNS - set(reader.fieldnames)

            if missing_columns:
                raise ValueError(
                    f"Missing CSV columns: {sorted(missing_columns)}"
                )

            for row in reader:
                rows_read += 1

                try:
                    raw_timestamp = (row.get("timestamp") or "").strip()
                    timestamp = datetime.fromisoformat(raw_timestamp)

                    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                        raise ValueError(
                            "Timestamp must include timezone"
                        )

                    timestamp = timestamp.astimezone(UTC)

                    open_price = float(row["open"])
                    high_price = float(row["high"])
                    low_price = float(row["low"])
                    close_price = float(row["close"])
                    volume = float(row["volume"])

                    values = (
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    )

                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("Non-finite OHLCV value")

                    if (
                        high_price < max(open_price, close_price, low_price)
                        or low_price > min(
                            open_price,
                            close_price,
                            high_price,
                        )
                        or volume < 0
                    ):
                        raise ValueError("Invalid OHLCV values")

                    bar = MarketBar(
                        timestamp=timestamp,
                        open=open_price,
                        high=high_price,
                        low=low_price,
                        close=close_price,
                        volume=volume,
                    )

                    if timestamp in bars_by_timestamp:
                        duplicates += 1

                    bars_by_timestamp[timestamp] = bar

                except (ValueError, TypeError, KeyError, AttributeError):
                    rows_rejected += 1

        bars = sorted(
            bars_by_timestamp.values(),
            key=lambda bar: bar.timestamp,
        )

        if not bars:
            raise ValueError("CSV contains no valid market bars")

        received_at = datetime.now(UTC)

        snapshot = MarketDataSnapshot(
            pair=pair,
            as_of=bars[-1].timestamp,
            series={
                timeframe: TimeframeSeries(
                    timeframe=timeframe,
                    bars=bars,
                    provenance=DataProvenance(
                        kind=DataSourceKind.EXTERNAL,
                        provider_id=provider_id,
                        received_at=received_at,
                    ),
                    quality=DataQuality.PARTIAL,
                )
            },
        )

        bars_imported = self.repository.save_snapshot(snapshot)

        return CSVImportResult(
            rows_read=rows_read,
            bars_imported=bars_imported,
            rows_rejected=rows_rejected,
            duplicates=duplicates,
        )
