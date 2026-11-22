from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.schemas import EvaluationResponse, MarketBar, VolumeType


def test_market_bar_valid_traded():
    """Test di una barra valida con volume negoziato (es. Azionario/Crypto)."""
    ts = datetime.now(UTC)
    bar = MarketBar(
        timestamp=ts,
        open=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        volume=100.5,
        volume_type=VolumeType.TRADED,
    )
    assert bar.volume == 100.5
    assert bar.volume_type == VolumeType.TRADED


def test_market_bar_volume_midpoint():
    """Test di una barra valida senza volume (es. Forex Midpoint OTC)."""
    ts = datetime.now(UTC)
    bar = MarketBar(
        timestamp=ts,
        open=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        volume=None,
        volume_type=VolumeType.UNAVAILABLE,
    )
    assert bar.volume is None
    assert bar.volume_type == VolumeType.UNAVAILABLE


def test_market_bar_volume_coherence_fails():
    """Verifica che lo schema blocchi le incoerenze tra valore del volume e semantica."""
    ts = datetime.now(UTC)

    # 1. Volume presente, ma marcato come UNAVAILABLE
    with pytest.raises(ValidationError, match="volume_type non può essere UNAVAILABLE"):
        MarketBar(
            timestamp=ts,
            open=1.0,
            high=1.2,
            low=0.9,
            close=1.1,
            volume=100.0,
            volume_type=VolumeType.UNAVAILABLE,
        )

    # 2. Volume assente (None), ma marcato come TRADED
    with pytest.raises(ValidationError, match="volume_type deve essere UNAVAILABLE"):
        MarketBar(
            timestamp=ts,
            open=1.0,
            high=1.2,
            low=0.9,
            close=1.1,
            volume=None,
            volume_type=VolumeType.TRADED,
        )

    # 3. Volume negativo (usa il messaggio standard del vincolo ge=0.0)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        MarketBar(
            timestamp=ts,
            open=1.0,
            high=1.2,
            low=0.9,
            close=1.1,
            volume=-50.0,
            volume_type=VolumeType.TICK_COUNT,
        )


def test_market_bar_ohlc_coherence_fails():
    """Verifica la logica di validazione OHLC su dati impossibili o malformati."""
    ts = datetime.now(UTC)

    # Low > High
    with pytest.raises(ValidationError, match="OHLC incoerente"):
        MarketBar(
            timestamp=ts,
            open=1.0,
            high=1.1,
            low=1.5,
            close=1.05,
            volume=10.0,
            volume_type=VolumeType.TRADED,
        )

    # Open fuori range (minore del Low)
    with pytest.raises(ValidationError):
        MarketBar(
            timestamp=ts,
            open=0.8,
            high=1.5,
            low=1.0,
            close=1.2,
            volume=10.0,
            volume_type=VolumeType.TRADED,
        )

    # Close fuori range (maggiore dell'High)
    with pytest.raises(ValidationError):
        MarketBar(
            timestamp=ts,
            open=1.2,
            high=1.5,
            low=1.0,
            close=1.6,
            volume=10.0,
            volume_type=VolumeType.TRADED,
        )


def test_evaluation_response_schema_version():
    """La risposta usa la versione corrente del contratto canonico."""
    version_field = EvaluationResponse.model_fields["schema_version"]
    assert version_field.default == "1.4"
