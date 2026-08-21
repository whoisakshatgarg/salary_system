"""Which currency a customer's money is written in — one map, every caller.

CONVENTIONS §9-D: the currency is defaulted from the CUSTOMER's country, not
fixed to the 'U.S.D.' the reference documents mislabel it with. Two very
different screens need that answer — the ledger (Quotations & Invoices, the
order record) and the generated papers — so the map lives in core: both
``modules`` and ``documents`` import it, and neither imports the other.

Sterling for a British customer, rupees at home (a blank country is a
domestic customer: that is what the ledger has always shown), U.S. dollars for
everyone else. On a paper the answer is only a DEFAULT — ``currency`` is an
editable payload field.
"""

from __future__ import annotations

INR = {"code": "INR", "symbol": "₹", "header": "Prices (I.N.R.)"}
USD = {"code": "USD", "symbol": "$", "header": "Prices (U.S.D.)"}
GBP = {"code": "GBP", "symbol": "£", "header": "Prices (G.B.P.)"}

_STERLING = ("UK", "U.K.", "ENGLAND", "BRITAIN", "SCOTLAND", "WALES",
             "UNITED KINGDOM")
_HOME = ("INDIA", "BHARAT")


def currency_for(country) -> dict:
    """``{"code", "symbol", "header"}`` for a customer's country.

    A fresh dict every call: callers hang their own keys on it (the papers add
    a ``head`` column title) and must not mutate the shared constants.
    """
    up = ("" if country is None else str(country)).strip().upper()
    if any(w in up for w in _STERLING):
        return dict(GBP)
    if not up or any(w in up for w in _HOME):
        return dict(INR)
    return dict(USD)
