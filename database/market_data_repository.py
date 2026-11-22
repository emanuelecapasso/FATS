
"""SQLite repository for historical Forex market data."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from core.schemas import MarketBar, MarketDataSnapshot, Timeframe


class MarketDataRepository:
    """Persist and retrieve provider-specific historical OHLCV data."""

    def __init__(self, db_path: str | Path = "data/fats.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _create_market_bars_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE market_bars (
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source_kind TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                received_at TEXT NOT NULL,
                quality TEXT NOT NULL,
                PRIMARY KEY (pair, timeframe, timestamp, provider_id)
            )
            """
        )

    def _initialize(self) -> None:
        """Create the table or migrate its primary key without losing data."""
        with closing(self._connect()) as connection, connection:
            table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'market_bars'
                """
            ).fetchone()

            if table is None:
                self._create_market_bars_table(connection)
                return

            columns = connection.execute(
                "PRAGMA table_info(market_bars)"
            ).fetchall()

            column_names = {column["name"] for column in columns}
            required_columns = {
                "pair",
                "timeframe",
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source_kind",
                "provider_id",
                "received_at",
                "quality",
            }

            if not required_columns.issubset(column_names):
                missing = sorted(required_columns - column_names)
                raise RuntimeError(
                    f"Cannot migrate market_bars: missing columns {missing}"
                )

            primary_key = [
                column["name"]
                for column in sorted(
                    (column for column in columns if column["pk"] > 0),
                    key=lambda column: column["pk"],
                )
            ]

            expected_primary_key = [
                "pair",
                "timeframe",
                "timestamp",
                "provider_id",
            ]

            if primary_key == expected_primary_key:
                return

            # The transaction ensures that a failed migration leaves the
            # original table and its data intact.
            connection.execute(
                "ALTER TABLE market_bars RENAME TO market_bars_legacy"
            )

            self._create_market_bars_table(connection)

            connection.execute(
                """
                INSERT INTO market_bars (
                    pair, timeframe, timestamp,
                    open, high, low, close, volume,
                    source_kind, provider_id, received_at, quality
                )
                SELECT
                    pair, timeframe, timestamp,
                    open, high, low, close, volume,
                    source_kind, provider_id, received_at, quality
                FROM market_bars_legacy
                """
            )

            connection.execute("DROP TABLE market_bars_legacy")

    @staticmethod
    def _validate_utc(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must be UTC-aware.")

    @staticmethod
    def _normalize_pair(pair: str) -> str:
        return pair.strip().upper().replace("/", "")

    @staticmethod
    def _validate_provider_id(provider_id: str) -> None:
        if not provider_id or provider_id != provider_id.strip():
            raise ValueError(
                "provider_id must be a non-empty trimmed string"
            )

    def save_snapshot(self, snapshot: MarketDataSnapshot) -> int:
        """Insert or update bars independently for each provider."""
        rows = []

        for timeframe, series in snapshot.series.items():
            provenance = series.provenance

            self._validate_provider_id(provenance.provider_id)

            for bar in series.bars:
                rows.append(
                    (
                        snapshot.pair,
                        timeframe.value,
                        bar.timestamp.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        provenance.kind.value,
                        provenance.provider_id,
                        provenance.received_at.isoformat(),
                        series.quality.value,
                    )
                )

        if not rows:
            return 0

        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO market_bars (
                    pair, timeframe, timestamp,
                    open, high, low, close, volume,
                    source_kind, provider_id, received_at, quality
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pair, timeframe, timestamp, provider_id)
                DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    source_kind = excluded.source_kind,
                    received_at = excluded.received_at,
                    quality = excluded.quality
                """,
                rows,
            )

        return len(rows)

    def load_bars(
        self,
        pair: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        provider_id: str | None = None,
    ) -> list[MarketBar]:
        """
        Load historical bars in [start, end).

        If provider_id is omitted, loading is allowed only when the selected
        interval contains data from at most one provider.
        """
        self._validate_utc(start)
        self._validate_utc(end)

        if start >= end:
            raise ValueError("Start timestamp must be earlier than end.")

        normalized_pair = self._normalize_pair(pair)

        if provider_id is not None:
            self._validate_provider_id(provider_id)

        parameters = (
            normalized_pair,
            timeframe.value,
            start.isoformat(),
            end.isoformat(),
        )

        with closing(self._connect()) as connection:
            if provider_id is None:
                providers = connection.execute(
                    """
                    SELECT DISTINCT provider_id
                    FROM market_bars
                    WHERE pair = ?
                      AND timeframe = ?
                      AND timestamp >= ?
                      AND timestamp < ?
                    """,
                    parameters,
                ).fetchall()

                if len(providers) > 1:
                    provider_ids = sorted(
                        record["provider_id"] for record in providers
                    )
                    raise ValueError(
                        "provider_id is required when multiple providers "
                        f"are available: {', '.join(provider_ids)}"
                    )

                if len(providers) == 1:
                    provider_id = providers[0]["provider_id"]

            query = """
                SELECT timestamp, open, high, low, close, volume
                FROM market_bars
                WHERE pair = ?
                  AND timeframe = ?
                  AND timestamp >= ?
                  AND timestamp < ?
            """

            query_parameters = parameters

            if provider_id is not None:
                query += " AND provider_id = ?"
                query_parameters = (*parameters, provider_id)

            query += " ORDER BY timestamp ASC"

            records = connection.execute(
                query,
                query_parameters,
            ).fetchall()

        return [
            MarketBar(
                timestamp=datetime.fromisoformat(record["timestamp"]),
                open=record["open"],
                high=record["high"],
                low=record["low"],
                close=record["close"],
                volume=record["volume"],
            )
            for record in records
        ]
