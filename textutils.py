"""Deutsche Betrags- und Datums-Erkennung (robust, regelbasiert).

Kein Machine Learning -- reine Regeln. Diese Funktionen sind das Fundament
von EBENE 3 (Parsing). Sie werden mit den eingebauten Selbsttests unten am
Ende der Datei geprueft (``python textutils.py``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# BETRAEGE
# ---------------------------------------------------------------------------
# Deutsches Format: Tausenderpunkt, Dezimalkomma.
#   1.234,56    1234,56    12,00    -1.234,56    1.234,56-   1.234,56 S
# Der eigentliche Zahlenkoerper (ohne Vorzeichen):
_AMOUNT_BODY = r"\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}"

# Ein Betrag samt optionalem Vorzeichen davor/danach und optionaler
# Soll/Haben-Kennung (S/H) bzw. +/- direkt dahinter.
_AMOUNT_RE = re.compile(
    r"(?P<pre>[-+]?)\s*"
    r"(?P<body>" + _AMOUNT_BODY + r")"
    r"\s*(?P<post>[-+]|[SH]\b|EUR|€)?",
    re.IGNORECASE,
)


def parse_german_amount(token: str) -> Optional[float]:
    """Wandelt EINEN deutschen Betragsstring in float um (mit Vorzeichen).

    Beispiele:
        "1.234,56"   ->  1234.56
        "-1.234,56"  -> -1234.56
        "1.234,56-"  -> -1234.56
        "12,00 S"    -> -12.00   (Soll = Belastung)
        "12,00 H"    ->  12.00   (Haben = Gutschrift)
    Gibt None zurueck, wenn kein Betrag erkennbar ist.
    """
    if token is None:
        return None
    m = _AMOUNT_RE.search(token)
    if not m:
        return None
    return _finalize_amount(m)


def _finalize_amount(m: "re.Match") -> float:
    body = m.group("body")
    # Tausenderpunkte weg, Dezimalkomma -> Punkt.
    wert = float(body.replace(".", "").replace(",", "."))
    pre = (m.group("pre") or "").strip()
    post = (m.group("post") or "").strip().upper()

    negativ = False
    if pre == "-":
        negativ = True
    if post == "-":
        negativ = True
    if post == "S":            # Soll = Belastung = Ausgabe
        negativ = True
    if post == "H":            # Haben = Gutschrift = Einnahme
        negativ = False
    if pre == "+" or post == "+":
        negativ = False
    return -wert if negativ else wert


def finde_letzten_betrag(zeile: str) -> Optional[Tuple[float, int, int]]:
    """Findet den RECHTS stehenden Betrag einer Zeile (typ. die Buchungsspalte).

    Gibt (wert_mit_vorzeichen, start, ende) zurueck oder None.
    Wir nehmen den letzten Treffer, weil der Buchungsbetrag in fast allen
    Layouts am rechten Zeilenrand steht (davor stehen oft Datum/Zahlen im
    Verwendungszweck).
    """
    letzte = None
    for m in _AMOUNT_RE.finditer(zeile):
        letzte = m
    if not letzte:
        return None
    return (_finalize_amount(letzte), letzte.start("body"), letzte.end())


def finde_alle_betraege(zeile: str):
    """Alle Betraege einer Zeile (fuer Saldo-Zeilen mit mehreren Zahlen)."""
    return [(_finalize_amount(m), m.start("body"), m.end()) for m in _AMOUNT_RE.finditer(zeile)]


# ---------------------------------------------------------------------------
# DATEN
# ---------------------------------------------------------------------------
# dd.mm.  |  dd.mm.yy  |  dd.mm.yyyy   (Punkte, auch mit fehlender Endziffer)
_DATE_RE = re.compile(r"\b(?P<d>[0-3]?\d)\.(?P<m>[01]?\d)\.(?P<y>\d{2,4})?")


def finde_datum_am_anfang(zeile: str) -> Optional[Tuple[int, int, Optional[int], int]]:
    """Sucht ein Datum moeglichst am Zeilenanfang.

    Rueckgabe: (tag, monat, jahr_oder_None, end_position) oder None.
    Das Jahr ist oft nicht angegeben ("04.07.") -> Smart-Year ergaenzt es
    spaeter. Wir akzeptieren ein Datum nur in den ersten ~15 Zeichen, damit
    Zahlen im Verwendungszweck nicht faelschlich als Buchungsdatum gelten.
    """
    m = _DATE_RE.search(zeile)
    if not m or m.start() > 15:
        return None
    tag = int(m.group("d"))
    monat = int(m.group("m"))
    if not (1 <= tag <= 31 and 1 <= monat <= 12):
        return None
    jahr = _normalisiere_jahr(m.group("y"))
    return (tag, monat, jahr, m.end())


def finde_erstes_volljahr(text: str) -> Optional[int]:
    """Sucht eine vierstellige Jahreszahl (z.B. im Kopf "Kontoauszug 2024")."""
    for m in re.finditer(r"\b(19|20)\d{2}\b", text):
        jahr = int(m.group(0))
        if 1990 <= jahr <= 2100:
            return jahr
    return None


def _normalisiere_jahr(roh: Optional[str]) -> Optional[int]:
    if not roh:
        return None
    j = int(roh)
    if j < 100:                 # zweistellig -> 20xx
        j += 2000
    if 1990 <= j <= 2100:
        return j
    return None


def sicheres_datum(tag: int, monat: int, jahr: int) -> Optional[date]:
    """date() ohne Absturz bei kaputtem OCR (z.B. 31.02.)."""
    try:
        return date(jahr, monat, tag)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Selbsttests (nur bei direktem Aufruf)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    faelle = {
        "1.234,56": 1234.56,
        "1234,56": 1234.56,
        "-1.234,56": -1234.56,
        "1.234,56-": -1234.56,
        "12,00 S": -12.00,
        "12,00 H": 12.00,
        "+99,90": 99.90,
        "0,00": 0.00,
    }
    ok = True
    for txt, erwartet in faelle.items():
        got = parse_german_amount(txt)
        status = "OK " if got == erwartet else "FEHLER"
        if got != erwartet:
            ok = False
        print(f"  [{status}] {txt!r:16} -> {got} (erwartet {erwartet})")

    zeile = "04.07. LASTSCHRIFT Stadtwerke Musterstadt 89,90-"
    print("\n  Zeile:", zeile)
    print("  Datum:", finde_datum_am_anfang(zeile))
    print("  Betrag(rechts):", finde_letzten_betrag(zeile))
    print("\n  Selbsttest", "bestanden." if ok else "FEHLGESCHLAGEN.")
