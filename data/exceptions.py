class DataError(Exception):
    """
    Eccezione base per tutti gli errori del layer data.
    Qualsiasi fallimento nel recupero o validazione dei dati storici
    deve sollevare questa eccezione o una sua derivata, senza mai
    restituire barre vuote, parziali silenziose o valori fittizi.
    """


class ProviderError(DataError):
    """
    Sollevata quando il provider upstream (broker o fonte esterna) 
    restituisce un errore generico (es. 500 Internal Server Error, 
    servizio non disponibile o autenticazione fallita).
    """


class TimeoutError(DataError):
    """
    Sollevata quando la richiesta al provider impiega troppo tempo 
    e supera il limite configurato, impedendo la ricezione dei dati.
    """


class RateLimitError(DataError):
    """
    Sollevata quando il provider blocca la richiesta a causa 
    del superamento dei limiti di frequenza (es. HTTP 429).
    """


class InvalidDataError(DataError):
    """
    Sollevata quando il provider restituisce una risposta con successo,
    ma i dati sono malformati, incompleti o non convertibili in un 
    oggetto `MarketBar` canonico (es. mancano campi obbligatori o la 
    geometria OHLC è incoerente e non risolvibile).
    """
