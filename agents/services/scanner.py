
"""Application service for scanning configured Forex pairs."""

import re
from collections.abc import Iterable

_PAIR_PATTERN = re.compile(r"^[A-Z]{6}$")

# ISO 4217 currency codes commonly used in Forex markets.
_CURRENCIES = {
    "AED", "AUD", "CAD", "CHF", "CNH", "CNY", "CZK", "DKK",
    "EUR", "GBP", "HKD", "HUF", "ILS", "JPY", "MXN", "NOK",
    "NZD", "PLN", "RON", "RUB", "SAR", "SEK", "SGD", "THB",
    "TRY", "USD", "ZAR",
}


class ScannerService:
    """Discover valid and unique Forex pairs from a configured universe."""

    def scan(self, pairs: Iterable[str]) -> list[str]:
        """Normalize, validate and deduplicate Forex pair symbols."""
        discovered: list[str] = []
        seen: set[str] = set()

        for raw_pair in pairs:
            pair = raw_pair.strip().upper().replace("/", "")

            if not _PAIR_PATTERN.fullmatch(pair):
                continue

            base_currency = pair[:3]
            quote_currency = pair[3:]

            if (
                base_currency not in _CURRENCIES
                or quote_currency not in _CURRENCIES
                or base_currency == quote_currency
                or pair in seen
            ):
                continue

            seen.add(pair)
            discovered.append(pair)

        return discovered
