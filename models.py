"""Datenmodelle fuer die Kontoauszug-Analyse.

Alle Ebenen der Pipeline arbeiten mit diesen beiden Klassen:
- ``Buchung``  = eine einzelne Zeile aus einem Auszug (eine Transaktion)
- ``Auszug``   = ein kompletter Kontoauszug (eine oder mehrere PDF-Seiten,
                 ein Konto, ein Zeitraum) mit allen Buchungen + Salden.

Bewusst nur Standardbibliothek (dataclasses), damit das Tool ohne
Zusatzpakete auf Termux/aarch64 laeuft.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


# ---------------------------------------------------------------------------
# Status-Konstanten der KONTROLLSCHICHT (Ebene 4)
# ---------------------------------------------------------------------------
STATUS_OK = "OK"                                  # Saldo-Rechnung geht auf
STATUS_PRUEFEN = "PRUEFEN"                         # Saldo-Rechnung geht NICHT auf
STATUS_OHNE_SALDO = "OHNE_SALDO_PRUEFUNG"          # kein Saldo lesbar

# ---------------------------------------------------------------------------
# Kategorien der lokalen KI (strikt begrenzt)
# ---------------------------------------------------------------------------
KAT_FIXKOSTEN = "Fixkosten"
KAT_LEBENSHALTUNG = "Lebenshaltung"
KAT_TANKEN = "Tanken"
KAT_SONSTIGES = "Sonstiges"
KAT_UMBUCHUNG = "Umbuchung"
KAT_UNKATEGORISIERT = "unkategorisiert"

ERLAUBTE_KATEGORIEN = {
    KAT_FIXKOSTEN,
    KAT_LEBENSHALTUNG,
    KAT_TANKEN,
    KAT_SONSTIGES,
    KAT_UMBUCHUNG,
    KAT_UNKATEGORISIERT,
}

# ---------------------------------------------------------------------------
# Buchungstypen fuer das Journal
# ---------------------------------------------------------------------------
TYP_EINNAHME = "Einnahme"
TYP_AUSGABE = "Ausgabe"
TYP_UMBUCHUNG = "UMBUCHUNG"


@dataclass
class Buchung:
    """Eine einzelne Transaktion aus einem Kontoauszug."""

    konto: str                      # IBAN oder Kontonummer (Quell-Auszug)
    datum: Optional[date]           # Buchungsdatum (mit Smart-Year vervollstaendigt)
    verwendungszweck: str           # kompletter Text (ggf. mehrzeilig zusammengefuehrt)
    betrag: float                   # VORZEICHENBEHAFTET: + = Einnahme, - = Ausgabe

    # Von der Kontrollschicht gesetzt
    status: str = STATUS_OHNE_SALDO

    # Von der KI / Python-Nachbearbeitung gesetzt
    kategorie: str = KAT_UNKATEGORISIERT
    typ: str = TYP_AUSGABE          # Einnahme / Ausgabe / UMBUCHUNG
    empfaenger: str = ""            # Zahlungsempfaenger (heuristisch aus Zweck)
    turnus: str = ""                # z.B. "monatlich" (nur bei Fixkosten, von Python berechnet)
    vermerk: str = ""               # z.B. "Preiserhoehung: 12,99 -> 14,99"

    # Von der Kontrollschicht gesetzt (fuer das Pruefen-Sheet)
    auszug_differenz: float = 0.0   # Saldo-Differenz des Auszugs (nur bei PRUEFEN relevant)

    # True, wenn KEIN S/H- bzw. +/--Kennzeichen lesbar war und das Vorzeichen
    # nur per Heuristik gesetzt wurde (siehe FIX 1: S/H ist primaere Quelle).
    vorzeichen_unsicher: bool = False

    # Herkunft / Debugging
    quelle_pdf: str = ""            # Dateiname der Ursprungs-PDF
    roh_zeile: str = ""             # Original-OCR-Zeile (fuer Fehlersuche)

    def dedup_key(self) -> tuple:
        """Schluessel zur Duplikaterkennung im Append-Modus:
        Datum + Verwendungszweck + Betrag + Kontonummer."""
        datum_str = self.datum.isoformat() if self.datum else ""
        # Verwendungszweck normalisieren, damit kleine OCR-Whitespace-
        # Unterschiede nicht als "neu" durchrutschen.
        zweck_norm = " ".join(self.verwendungszweck.split()).lower()
        return (datum_str, zweck_norm, round(self.betrag, 2), self.konto.strip())


@dataclass
class Auszug:
    """Ein kompletter Kontoauszug (ein Konto, ein Zeitraum)."""

    konto: str = ""                 # IBAN / Kontonummer
    jahr: Optional[int] = None      # pro PDF ermitteltes Basisjahr (Smart-Year)
    bank_profil: str = "generisch"  # Name des erkannten Layout-Profils

    saldo_alt: Optional[float] = None
    saldo_neu: Optional[float] = None

    buchungen: List[Buchung] = field(default_factory=list)

    # Von der Kontrollschicht gesetzt
    status: str = STATUS_OHNE_SALDO
    saldo_differenz: float = 0.0    # (saldo_alt + summe) - saldo_neu

    quelle_pdf: str = ""
