"""EBENE 4 -- KONTROLLSCHICHT (Pflicht, Herzstueck).

Pro Auszug wird hart geprueft:

    alter Saldo + Summe aller Buchungen == neuer Saldo   (auf Cent gerundet)

  * Rechnung geht auf   -> Buchungen sind VERIFIZIERT (Status OK).
  * Rechnung geht NICHT auf -> ALLE Buchungen des Auszugs bekommen Status
    PRUEFEN + der Differenzbetrag wird ausgewiesen. NIEMALS still uebernehmen.
  * Kein Saldo lesbar   -> Status OHNE_SALDO_PRUEFUNG.
"""

from __future__ import annotations

from models import (
    STATUS_OHNE_SALDO,
    STATUS_OK,
    STATUS_PRUEFEN,
    Auszug,
)

# Toleranz fuer Rundungsfehler (1 Cent).
TOLERANZ = 0.005


def pruefe_auszug(auszug: Auszug) -> Auszug:
    """Wendet die Saldo-Kontrolle auf einen Auszug an und setzt die Status."""
    summe = round(sum(b.betrag for b in auszug.buchungen), 2)

    if auszug.saldo_alt is None or auszug.saldo_neu is None:
        # Kein (vollstaendiger) Saldo lesbar -> nicht pruefbar.
        auszug.status = STATUS_OHNE_SALDO
        auszug.saldo_differenz = 0.0
        _setze_status(auszug, STATUS_OHNE_SALDO)
        return auszug

    erwartet = round(auszug.saldo_alt + summe, 2)
    differenz = round(erwartet - auszug.saldo_neu, 2)
    auszug.saldo_differenz = differenz

    if abs(differenz) <= TOLERANZ:
        auszug.status = STATUS_OK
        _setze_status(auszug, STATUS_OK)
    else:
        auszug.status = STATUS_PRUEFEN
        _setze_status(auszug, STATUS_PRUEFEN)
    return auszug


def _setze_status(auszug: Auszug, status: str) -> None:
    for b in auszug.buchungen:
        b.status = status
        b.auszug_differenz = auszug.saldo_differenz


def kontroll_report(auszug: Auszug) -> str:
    """Menschlich lesbarer Kontroll-Report fuer Konsole/--dry-run."""
    zeilen = []
    zeilen.append(f"  Datei      : {auszug.quelle_pdf}")
    zeilen.append(f"  Bank-Profil: {auszug.bank_profil}")
    zeilen.append(f"  Konto      : {auszug.konto}")
    zeilen.append(f"  Jahr       : {auszug.jahr}")
    zeilen.append(f"  Buchungen  : {len(auszug.buchungen)}")
    zeilen.append(f"  Saldo alt  : {_fmt(auszug.saldo_alt)}")
    summe = round(sum(b.betrag for b in auszug.buchungen), 2)
    zeilen.append(f"  Summe Buch.: {_fmt(summe)}")
    zeilen.append(f"  Saldo neu  : {_fmt(auszug.saldo_neu)}")

    if auszug.status == STATUS_OK:
        zeilen.append("  ERGEBNIS   : OK -- Saldo-Rechnung geht auf, Buchungen verifiziert.")
    elif auszug.status == STATUS_PRUEFEN:
        zeilen.append(
            f"  ERGEBNIS   : !!! PRUEFEN -- Differenz {_fmt(auszug.saldo_differenz)} "
            f"(alt+Summe != neu). Alle Buchungen markiert."
        )
    else:
        zeilen.append("  ERGEBNIS   : OHNE_SALDO_PRUEFUNG -- kein Saldo lesbar.")
    return "\n".join(zeilen)


def _fmt(wert) -> str:
    if wert is None:
        return "—"
    return f"{wert:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " EUR"
