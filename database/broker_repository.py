
"""SQLite repository for broker configurations."""

import json
import sqlite3
from contextlib import closing
from datetime import date, time
from pathlib import Path

from data.broker_config import BrokerConfig


class BrokerRepository:
    """Persist and retrieve broker-specific market configurations."""

    def __init__(self, db_path: str | Path = "data/fats.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_configs (
                    provider_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    sunday_open TEXT NOT NULL,
                    friday_close TEXT NOT NULL,
                    closed_dates TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _serialize(config: BrokerConfig) -> tuple:
        return (
            config.provider_id,
            config.name,
            config.timezone,
            config.sunday_open.isoformat(),
            config.friday_close.isoformat(),
            json.dumps(
                sorted(day.isoformat() for day in config.closed_dates)
            ),
        )

    @staticmethod
    def _deserialize(record: sqlite3.Row) -> BrokerConfig:
        return BrokerConfig(
            provider_id=record["provider_id"],
            name=record["name"],
            timezone=record["timezone"],
            sunday_open=time.fromisoformat(record["sunday_open"]),
            friday_close=time.fromisoformat(record["friday_close"]),
            closed_dates=frozenset(
                date.fromisoformat(value)
                for value in json.loads(record["closed_dates"])
            ),
        )

    def save(self, config: BrokerConfig) -> None:
        """Insert or update a broker configuration."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO broker_configs (
                    provider_id,
                    name,
                    timezone,
                    sunday_open,
                    friday_close,
                    closed_dates
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id)
                DO UPDATE SET
                    name = excluded.name,
                    timezone = excluded.timezone,
                    sunday_open = excluded.sunday_open,
                    friday_close = excluded.friday_close,
                    closed_dates = excluded.closed_dates
                """,
                self._serialize(config),
            )

    def get(self, provider_id: str) -> BrokerConfig | None:
        """Retrieve a broker configuration by provider ID."""
        with closing(self._connect()) as connection:
            record = connection.execute(
                """
                SELECT *
                FROM broker_configs
                WHERE provider_id = ?
                """,
                (provider_id,),
            ).fetchone()

        return self._deserialize(record) if record is not None else None

    def list_all(self) -> list[BrokerConfig]:
        """Return all configured brokers in stable ID order."""
        with closing(self._connect()) as connection:
            records = connection.execute(
                """
                SELECT *
                FROM broker_configs
                ORDER BY provider_id ASC
                """
            ).fetchall()

        return [self._deserialize(record) for record in records]

    def delete(self, provider_id: str) -> bool:
        """Delete a broker configuration; return whether one existed."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                DELETE FROM broker_configs
                WHERE provider_id = ?
                """,
                (provider_id,),
            )

        return cursor.rowcount > 0
