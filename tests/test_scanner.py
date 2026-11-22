
"""Tests for the Forex scanner service."""

from agents.services import ScannerService


def test_scan_normalizes_pairs():
    scanner = ScannerService()

    result = scanner.scan(["eurusd", " GBPUSD ", "USD/JPY"])

    assert result == ["EURUSD", "GBPUSD", "USDJPY"]


def test_scan_removes_duplicates():
    scanner = ScannerService()

    result = scanner.scan(["EURUSD", "eurusd", "GBPUSD"])

    assert result == ["EURUSD", "GBPUSD"]


def test_scan_ignores_invalid_pairs():
    scanner = ScannerService()

    result = scanner.scan(
        ["EURUSD", "INVALID", "EUR", "EURXXX", "EUR-EUR", "EUR/EUR"]
    )

    assert result == ["EURUSD"]


def test_scan_preserves_order():
    scanner = ScannerService()

    result = scanner.scan(["GBPUSD", "EURUSD", "USDJPY"])

    assert result == ["GBPUSD", "EURUSD", "USDJPY"]


def test_scan_empty_input():
    scanner = ScannerService()

    assert scanner.scan([]) == []
