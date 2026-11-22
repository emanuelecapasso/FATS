from quantitative.ports import Strategy


class StrategyNotFoundError(Exception):
    """Sollevata quando si richiede un ID strategia non presente nel registry."""


class StrategyAlreadyRegisteredError(Exception):
    """Sollevata quando si tenta di registrare un ID strategia già esistente."""


class StrategyRegistry:
    """
    Gestisce la registrazione e la risoluzione delle strategie quantitative.
    Associa ogni strategy_id univoco alla sua implementazione (istanza di Strategy).
    """

    def __init__(self):
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        """
        Registra un'implementazione di Strategy.
        Rifiuta la registrazione se l'ID è già presente (duplicato).
        """
        strategy_id = strategy.strategy_id
        if strategy_id in self._strategies:
            raise StrategyAlreadyRegisteredError(
                f"Errore di registrazione: la strategia con ID '{strategy_id}' è già presente."
            )
        self._strategies[strategy_id] = strategy

    def get_strategy(self, strategy_id: str) -> Strategy:
        """
        Recupera la strategia corrispondente all'ID esplicito richiesto.
        Rifiuta la richiesta se l'ID non è stato registrato.
        """
        if strategy_id not in self._strategies:
            raise StrategyNotFoundError(
                f"Strategia non trovata: nessun implementazione registrata per l'ID '{strategy_id}'."
            )
        return self._strategies[strategy_id]
