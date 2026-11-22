import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from core.schemas import DataSourceKind, Timeframe, VolumeType
from data.adapters.ibkr import IBKRAdapter
from data.adapters.twelve_data import TwelveDataAdapter


@pytest.fixture
def fake_ibkr_data():
    """Fixture che simula una risposta grezza da IBKR, specificando il pricing MIDPOINT senza volumi."""
    return [
        {
            "timestamp": datetime.now(UTC),
            "open": 1.1000,
            "high": 1.1020,
            "low": 1.0990,
            "close": 1.1010,
            # Nessun volume in IBKR MIDPOINT
        }
    ]


@pytest.fixture
def fake_twelve_data():
    """Fixture che simula una risposta API da TwelveData per il Forex (senza volume reale)."""
    return {
        "meta": {
            "symbol": "EUR/USD",
            "interval": "5min",
            "price_type": "TRADED",  # Il provider potrebbe dichiararlo, ma noi lo forziamo ad UNAVAILABLE
        },
        "values": [
            {
                "timestamp": "2026-11-21T15:30:00Z",
                "open": 1.1000,
                "high": 1.1020,
                "low": 1.0990,
                "close": 1.1010,
                # Volume rimosso per riflettere la natura delle coppie Forex
            }
        ],
    }


@pytest.mark.asyncio
async def test_ibkr_adapter_midpoint(fake_ibkr_data):
    """Verifica che l'adapter IBKR configuri le barre MIDPOINT in conformità allo schema 1.1."""
    adapter = IBKRAdapter()

    # Mocking del metodo asincrono di fetch per evitare chiamate HTTP di rete
    with patch.object(adapter, "_fetch_raw_data", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = fake_ibkr_data

        snapshot = await adapter.get_snapshot("EUR/USD", [Timeframe.M5])

        assert Timeframe.M5 in snapshot.series
        series = snapshot.series[Timeframe.M5]

        # Verifica Provenienza
        assert series.provenance.kind == DataSourceKind.BROKER
        assert series.provenance.provider_id == "IBKR"
        assert series.provenance.price_type == "MIDPOINT"

        # Verifica Barra e Semantica Volume
        bar = series.bars[0]
        assert bar.volume is None, "Il volume Forex/MIDPOINT deve essere None"
        assert bar.volume_type == VolumeType.UNAVAILABLE, (
            "Il volume_type deve essere UNAVAILABLE"
        )


@pytest.mark.asyncio
async def test_twelve_data_adapter_forex(fake_twelve_data):
    """Verifica che l'adapter Twelve Data gestisca il Forex forzando il volume a None."""
    # Simula l'inserimento della variabile d'ambiente
    os.environ["TWELVE_DATA_API_KEY"] = "fake-key-123"
    adapter = TwelveDataAdapter()

    # Mocking del metodo asincrono di fetch
    with patch.object(adapter, "_fetch_raw_data", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = fake_twelve_data

        snapshot = await adapter.get_snapshot("EUR/USD", [Timeframe.M5])

        assert Timeframe.M5 in snapshot.series
        series = snapshot.series[Timeframe.M5]

        # Verifica Provenienza
        assert series.provenance.kind == DataSourceKind.EXTERNAL
        assert series.provenance.provider_id == "TwelveData"
        # Il price_type resta quello dichiarato nei meta del provider per tracciabilità
        assert series.provenance.price_type == "TRADED"

        # Verifica Barra e Semantica Volume coerente col Forex
        bar = series.bars[0]
        assert bar.volume is None, "Il volume Forex/Twelve Data deve essere None"
        assert bar.volume_type == VolumeType.UNAVAILABLE, (
            "Il volume_type deve essere UNAVAILABLE"
        )
