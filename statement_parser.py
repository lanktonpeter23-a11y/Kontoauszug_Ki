"""EBENE 3 -- Parsing (regelbasiert, KEINE KI).

Aus dem OCR-Text eines PDFs wird ein ``Auszug`` mit allen ``Buchung``-
Objekten gebaut: IBAN/Kontonummer, Jahr, Buchungen (Datum, Verwendungszweck,
Betrag mit Vorzeichen), alter/neuer Saldo.

Zwei Wege:
  * Bank-Layout-Profile (bank_profiles.json)  -- fuer bekannte Banken
  * generischer Fallback-Parser               -- fuer alles andere

So koennen neue Banken OHNE Codeaenderung ergaenzt werden.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from models import Auszug, Buchung
from textutils import (
    finde_alle_betraege,
    finde_datum_am_anfang,
    finde_erstes_volljahr,
    sicheres_datum,
)

# ---------------------------------------------------------------------------
# Generische Saldo-Bezeichnungen (klein geschrieben verglichen)
# ---------------------------------------------------------------------------
_SALDO_ALT_LABELS = [
    "alter kontostand", "kontostand alt", "alter saldo", "saldovortrag",
    "saldo vortrag", "anfangssaldo", "anfangsbestand", "uebertrag",
    "übertrag", "vortrag", "kontostand vom", "kontostand alt/neu",
]
_SALDO_NEU_LABELS = [
    "neuer kontostand", "kontostand neu", "neuer saldo", "endsaldo",
    "schlusssaldo", "schluss-saldo", "endbestand", "neuer saldo/kontostand",
    "aktueller kontostand", "kontostand aktuell",
]

# Saldo-/Summenzeilen: NIE als Buchung werten (auch nicht mit Datum davor).
_SALDO_SUMMEN_LABELS = _SALDO_ALT_LABELS + _SALDO_NEU_LABELS + [
    "summe", "zwischensumme", "uebertrag auf", "übertrag auf",
]

# Reine Kopf-/Fusszeilen: als Verwendungszweck-Fortsetzung ueberspringen.
# (Wird NUR auf Zeilen OHNE fuehrendes Datum angewandt, damit echte
# Buchungen mit z.B. "IBAN" im Zweck nicht verloren gehen.)
_KOPF_LABELS = [
    "seite", "kontoauszug", "bic", "blz", "auszug nr", "auszugnummer",
    "iban", "bank", "erstellt am",
]

_IBAN_RE = re.compile(r"\bDE\d{2}(?:[ ]?\d{4}){4}[ ]?\d{2}\b")
_KONTONR_RE = re.compile(r"(?:konto(?:nummer|-?nr)?\.?\s*:?\s*)(\d{6,12})", re.IGNORECASE)
_PERIODE_RE = re.compile(r"vom\s+\d{1,2}\.\d{1,2}\.(\d{4})")


def parse_auszug(ocr_text: str, quelle_pdf: str, profile: List[Dict[str, Any]]) -> Auszug:
    """Baut aus dem kompletten OCR-Text eines PDFs einen ``Auszug``."""
    auszug = Auszug(quelle_pdf=quelle_pdf)

    profil = _erkenne_profil(ocr_text, profile)
    auszug.bank_profil = profil.get("name", "generisch") if profil else "generisch"

    auszug.konto = _finde_konto(ocr_text, profil)
    auszug.jahr = _finde_jahr(ocr_text)
    auszug.saldo_alt = _finde_saldo(ocr_text, _labels(profil, "saldo_alt", _SALDO_ALT_LABELS))
    auszug.saldo_neu = _finde_saldo(ocr_text, _labels(profil, "saldo_neu", _SALDO_NEU_LABELS))

    auszug.buchungen = _parse_buchungen(ocr_text, auszug, profil)
    _smart_year(auszug)
    return auszug


# ---------------------------------------------------------------------------
# Profil-Erkennung
# ---------------------------------------------------------------------------
def _erkenne_profil(text: str, profile: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Erstes Profil, dessen Erkennungs-Stichwoerter im Text vorkommen."""
    tl = text.lower()
    for p in profile:
        stichworte = [s.lower() for s in p.get("erkennung", [])]
        if stichworte and any(s in tl for s in stichworte):
            return p
    return None


def _labels(profil: Optional[Dict[str, Any]], key: str, default: List[str]) -> List[str]:
    """Profil-spezifische Labels + generische Labels (Profil hat Vorrang)."""
    if profil and profil.get(key):
        return [s.lower() for s in profil[key]] + default
    return default


# ---------------------------------------------------------------------------
# Konto / Jahr / Saldo
# ---------------------------------------------------------------------------
def _finde_konto(text: str, profil: Optional[Dict[str, Any]]) -> str:
    """IBAN bevorzugt, sonst Kontonummer, sonst ''."""
    m = _IBAN_RE.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(0))
    m = _KONTONR_RE.search(text)
    if m:
        return m.group(1)
    return "UNBEKANNT"


def _finde_jahr(text: str) -> Optional[int]:
    """Basisjahr: bevorzugt aus Zeitraum 'vom TT.MM.JJJJ', sonst erste
    Volljahreszahl im Kopf."""
    m = _PERIODE_RE.search(text)
    if m:
        j = int(m.group(1))
        if 1990 <= j <= 2100:
            return j
    return finde_erstes_volljahr(text)


def _finde_saldo(text: str, labels: List[str]) -> Optional[float]:
    """Sucht die Zeile mit einem Saldo-Label und nimmt den letzten Betrag darin."""
    for zeile in text.splitlines():
        zl = zeile.lower()
        if any(lab in zl for lab in labels):
            betraege = finde_alle_betraege(zeile)
            if betraege:
                # Der Saldo ist der rechts stehende (letzte) Betrag der Zeile.
                return betraege[-1][0]
    return None


# ---------------------------------------------------------------------------
# Buchungen
# ---------------------------------------------------------------------------
def _ist_saldo_summen_zeile(zeile: str) -> bool:
    """Saldo-/Summenzeile -> nie eine Buchung, auch mit Datum davor."""
    zl = zeile.lower()
    return any(lab in zl for lab in _SALDO_SUMMEN_LABELS)


def _ist_kopf_zeile(zeile: str) -> bool:
    """Reine Kopf-/Fusszeile (nur relevant fuer Zeilen ohne Datum)."""
    zl = zeile.lower()
    return any(lab in zl for lab in _KOPF_LABELS)


def _laufender_saldo_layout(kandidaten: List[str], profil: Optional[Dict[str, Any]]) -> bool:
    """Bestimmt, ob das Layout eine laufende Saldo-Spalte hat.

    Bei laufendem Saldo stehen pro Buchungszeile ZWEI Betraege
    (Umsatz + neuer Kontostand); dann ist der Umsatz der VORLETZTE Betrag.
    Profil kann es hart vorgeben ('laufender_saldo': true/false), sonst
    Heuristik: haben die meisten Buchungszeilen >=2 Betraege?
    """
    if profil and "laufender_saldo" in profil:
        return bool(profil["laufender_saldo"])
    if not kandidaten:
        return False
    mit_zwei = sum(1 for z in kandidaten if len(finde_alle_betraege(z)) >= 2)
    return mit_zwei >= 0.6 * len(kandidaten)


def _parse_buchungen(text: str, auszug: Auszug, profil: Optional[Dict[str, Any]]) -> List[Buchung]:
    """Generischer, zeilenbasierter Buchungs-Parser.

    Eine Buchung beginnt an einer Zeile mit fuehrendem Datum. Folgezeilen
    ohne Datum (Verwendungszweck-Fortsetzung) werden angehaengt. Der Betrag
    ist der rechte Betrag der Zeile (bzw. der vorletzte bei laufendem Saldo).
    """
    zeilen = [z for z in text.splitlines() if z.strip()]

    # Erst Kandidaten-Zeilen (mit fuehrendem Datum, keine Saldo/Kopf-Zeilen)
    # bestimmen, um das Layout (laufender Saldo?) zu erkennen.
    kandidaten = [
        z for z in zeilen
        if finde_datum_am_anfang(z) and not _ist_saldo_summen_zeile(z)
    ]
    laufend = _laufender_saldo_layout(kandidaten, profil)

    buchungen: List[Buchung] = []
    aktuell: Optional[Buchung] = None

    for zeile in zeilen:
        datum_info = finde_datum_am_anfang(zeile)

        if datum_info and not _ist_saldo_summen_zeile(zeile):
            # Neue Buchung beginnt hier (Betrag darf None sein -> folgt spaeter).
            tag, monat, jahr, ende = datum_info
            betrag, zweck = _betrag_und_zweck(zeile, ende, laufend)
            aktuell = _bau_buchung(auszug, tag, monat, jahr, zweck, betrag, zeile)
            buchungen.append(aktuell)

        elif aktuell is not None and not _ist_kopf_zeile(zeile) \
                and not _ist_saldo_summen_zeile(zeile):
            # Fortsetzung des Verwendungszwecks oder nachgereichter Betrag.
            betraege = finde_alle_betraege(zeile)
            if aktuell.betrag is None and betraege:
                idx = -2 if (laufend and len(betraege) >= 2) else -1
                aktuell.betrag = betraege[idx][0]
                vor_betrag = zeile[: betraege[idx][1]].strip()
                if vor_betrag:
                    aktuell.verwendungszweck = (aktuell.verwendungszweck + " " + vor_betrag).strip()
            else:
                aktuell.verwendungszweck = (aktuell.verwendungszweck + " " + zeile.strip()).strip()

    # Buchungen ohne erkannten Betrag verwerfen (unbrauchbar).
    return [b for b in buchungen if b.betrag is not None]


def _betrag_und_zweck(zeile: str, datum_ende: int, laufend: bool):
    """Extrahiert (Betrag, Zweck) aus einer Buchungszeile ab Datum-Ende."""
    betraege = finde_alle_betraege(zeile)
    if not betraege:
        return None, zeile[datum_ende:].strip()
    idx = -2 if (laufend and len(betraege) >= 2) else -1
    betrag, start, _ = betraege[idx]
    zweck = zeile[datum_ende:start].strip()
    # Fuehrende zweite Datumsangabe (Wertstellung) aus dem Zweck entfernen.
    zweck = re.sub(r"^\d{1,2}\.\d{1,2}\.(\d{2,4})?\s*", "", zweck).strip()
    return betrag, zweck


def _bau_buchung(auszug: Auszug, tag, monat, jahr, zweck, betrag, roh) -> Buchung:
    b = Buchung(
        konto=auszug.konto,
        datum=None,
        verwendungszweck=zweck,
        betrag=betrag,
        quelle_pdf=auszug.quelle_pdf,
        roh_zeile=roh.strip(),
    )
    # Rohdatum temporaer in Attributen ablegen (fuer Smart-Year).
    b._tag, b._monat, b._jahr = tag, monat, jahr  # type: ignore[attr-defined]
    return b


# ---------------------------------------------------------------------------
# SMART-YEAR
# ---------------------------------------------------------------------------
def _smart_year(auszug: Auszug) -> None:
    """Vervollstaendigt unvollstaendige Daten ("04.07.") mit dem PDF-Jahr und
    behandelt den Jahreswechsel (Dez -> Jan) innerhalb eines Auszugs korrekt.

    Regel: In Dokument-Reihenfolge das Arbeitsjahr mitfuehren. Faellt der
    Monat stark ab (z.B. 12 -> 1, Differenz >= 6), liegt ein Jahreswechsel
    vor -> Arbeitsjahr + 1. Ein an der Buchung explizit erkanntes Volljahr
    setzt das Arbeitsjahr neu.
    """
    arbeitsjahr = auszug.jahr
    vor_monat: Optional[int] = None

    for b in auszug.buchungen:
        tag = getattr(b, "_tag", None)
        monat = getattr(b, "_monat", None)
        jahr = getattr(b, "_jahr", None)
        if tag is None or monat is None:
            continue

        if jahr is not None:                    # Buchung hatte volles Jahr
            arbeitsjahr = jahr
        elif arbeitsjahr is not None:
            if vor_monat is not None and (vor_monat - monat) >= 6:
                arbeitsjahr += 1                # Jahreswechsel Dez -> Jan
            jahr = arbeitsjahr

        if jahr is not None:
            b.datum = sicheres_datum(tag, monat, jahr)
        vor_monat = monat

    # Temporaere Attribute wieder entfernen (sauberes Objekt).
    for b in auszug.buchungen:
        for attr in ("_tag", "_monat", "_jahr"):
            if hasattr(b, attr):
                delattr(b, attr)
