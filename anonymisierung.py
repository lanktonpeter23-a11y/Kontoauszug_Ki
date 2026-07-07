"""FEATURE 2 -- Anonymisierung (nur echte PERSONENNAMEN + IBAN/BIC).

Ziel: echte Personennamen und volle IBANs/BICs entfernen. FIRMEN BLEIBEN
(fuer Wiederkehrer-Gruppierung + Lesbarkeit). Anonymisierung ist der LETZTE
Schritt vor der anonymen Ausgabe (Gruppierung laeuft VORHER am echten Namen).

Deterministisch (kein KI). Kernregeln:
  * FIRMA (bleibt) NUR ueber echte Marker: Rechtsform (GmbH/AG/SE/KG/mbH/
    e.V./e.G./eG/UG/OHG/GbR), Typwort (Versicherung/Bank/Sparkasse/Rundfunk/
    Finanzamt/Landratsamt/vhs/...) oder bekannte Marke (AMAZON/PAYPAL/...).
  * GROSSSCHREIBUNG ist KEIN Firmenmarker -- "ULRIKE WEINZIERL" ist ein
    Personenname und wird geschwaerzt.
  * Schwaerzung CASE-INSENSITIV: "ULRIKE WEINZIERL", "ulrike weinzierl",
    "Ulrike Weinzierl" werden ALLE zu [NAME].
  * IM ZWEIFEL -> Person (schwaerzen). Datenschutz gewinnt.
"""

from __future__ import annotations

import copy
import re
from typing import List

from models import Buchung

PLATZHALTER = "[NAME]"

# --- IBAN / BIC ------------------------------------------------------------
_IBAN_RE = re.compile(r"DE\d{2}(?:\s?\d){10,22}", re.IGNORECASE)
_BIC_LABEL_RE = re.compile(r"(BIC[:\s]+)[A-Z0-9]{6,11}", re.IGNORECASE)
_BIC_TOKEN_RE = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{0,2}\d[A-Z0-9]{0,3}\b")


def maskiere_iban(text: str) -> str:
    def repl(m: "re.Match") -> str:
        ziffern = re.sub(r"\s", "", m.group(0))
        return "DE**..." + ziffern[-4:]
    return _IBAN_RE.sub(repl, text)


def maskiere_bic(text: str) -> str:
    text = _BIC_LABEL_RE.sub(lambda m: m.group(1) + "BIC***", text)
    text = _BIC_TOKEN_RE.sub("BIC***", text)
    return text


# --- Firmen-Marker / Marken / Stoppwoerter ---------------------------------
# Rechtsform + Typwort als GANZE Woerter -> Firma (Span wird behalten).
_FIRMA_RE = re.compile(
    r"\b(?:gmbh|mbh|ag|se|kg|kgaa|ohg|gbr|ug|eg|e\.?\s?v|e\.?\s?g)\b"
    r"|aktiengesellschaft"
    r"|\b(?:versicherung(?:en)?|bank|sparkasse|volksbank|raiffeisen|rundfunk|"
    r"finanzamt|landratsamt|stadtwerke|vhs|genossenschaft)\b",
    re.IGNORECASE,
)

# Bekannte Marken/Haendler (ganze Tokens, klein).
_MARKEN = {
    "amazon", "amzn", "paypal", "netflix", "spotify", "vodafone", "telekom",
    "congstar", "devk", "axa", "edeka", "rewe", "aldi", "lidl", "penny",
    "vattenfall", "vorwerk", "ikea", "rossmann", "dm", "shell", "aral",
    "esso", "jet", "total", "esb", "google", "apple", "microsoft", "ebay",
    "otto", "zalando", "dhl", "hermes", "ups", "fedex", "klarna", "ing",
    "dkb", "n26", "mastercard", "visa", "swiss",
}

# Compound-Marker: kommt eines dieser Fragmente IN einem Wort vor, ist es ein
# Firmen-/Zweckwort und beendet einen Namenslauf (z.B. "Pflegeversicherung").
_MARKER_FRAG = ("versicherung", "bank", "gmbh", "aktiengesell", "kasse",
                "amt", "werke")

# Haeufige Nicht-Namen-Woerter (Buchungsarten, Funktionswoerter, Belegtext).
_STOP = {
    "euro", "ueberweisung", "überweisung", "lastschrift", "kartenzahlung",
    "girocard", "maestro", "gutschrift", "gutschr", "lohn", "gehalt",
    "auszahlung", "einzahlung", "sparrate", "dauerauftrag", "sepa", "pn",
    "gir", "elv", "ectl", "cicc", "npin", "fpin", "ga", "ref", "eref",
    "mref", "cred", "svwz", "abwa", "iban", "bic", "btr", "rg", "vs",
    "ihr", "ihre", "der", "die", "das", "den", "dem", "und", "oder", "mit",
    "von", "bei", "aus", "fuer", "für", "plus", "secure", "securego", "am",
    "beitrag", "anteil", "miete", "rechnung", "strom", "gas", "wasser",
    "abschlag", "konto", "kontostand", "blatt", "wert", "vorgang", "uhr",
    "danke", "prime", "video", "mktp", "ort", "kredit", "leben", "unfall",
    "vertragskonto", "kundennummer", "kundennr", "kd", "zahlbeleg",
    "festnetz", "abrechnung", "erstatt", "prowin", "klo", "karte", "de",
    "ag", "se", "kg", "gmbh", "mbh", "eg", "ug", "gbr", "ohg", "co", "name",
}


def _ist_firma_span(span: str) -> bool:
    if _FIRMA_RE.search(span):
        return True
    return any(t.lower().strip(".") in _MARKEN for t in span.split())


def _hat_marker_frag(wort: str) -> bool:
    wl = wort.lower()
    return any(frag in wl for frag in _MARKER_FRAG)


def _stop_wort(wort: str) -> bool:
    wl = wort.lower().strip(".")
    return (wl in _STOP) or (wl in _MARKEN) or _hat_marker_frag(wort)


# Sequenz aus Namensworten (case-insensitiv): 2+ Buchstaben ODER Initiale "A.".
_TOKEN = r"(?:[A-Za-zÄÖÜäöüß]{2,}\.?|[A-ZÄÖÜ]\.)"
_SEQ_RE = re.compile(rf"{_TOKEN}(?:[ ]{_TOKEN})*")


def _maskiere_personen(text: str, min_len: int) -> str:
    """Schwaerzt Personennamen. ``min_len`` = wie viele aufeinanderfolgende
    Namensworte einen Namen bilden (2 im Fliesstext, 1 im reinen Empfaengerfeld).
    """
    def repl(m: "re.Match") -> str:
        span = m.group(0)
        if _ist_firma_span(span):           # echter Firmenmarker/Marke -> behalten
            return span
        tokens = span.split()
        out: List[str] = []
        lauf: List[str] = []

        def flush():
            if len(lauf) >= min_len:
                out.append(PLATZHALTER)
            else:
                out.extend(lauf)
            lauf.clear()

        for w in tokens:
            if _stop_wort(w):
                flush()
                out.append(w)
            else:
                lauf.append(w)
        flush()
        return " ".join(out)

    return _SEQ_RE.sub(repl, text)


def _empfaenger_personname(empfaenger: str) -> str:
    """Liefert den fuehrenden Personennamen eines Empfaengerfeldes oder ""
    (wenn Firma). Genutzt, um denselben Namen auch im Belegtext zu tilgen."""
    if not empfaenger or _ist_firma_span(empfaenger):
        return ""
    lauf = []
    for w in empfaenger.split():
        if _stop_wort(w):
            break
        lauf.append(w)
    return " ".join(lauf) if lauf else ""


def anonymisiere_text(text: str, feld: bool = False) -> str:
    """Anonymisiert EINEN Textstring. ``feld=True`` (reines Empfaengerfeld)
    schwaerzt auch Einzelnamen (min_len=1), sonst Fliesstext (min_len=2)."""
    if not text:
        return text
    text = maskiere_iban(text)
    text = maskiere_bic(text)
    text = _maskiere_personen(text, min_len=1 if feld else 2)
    return text


def anonymisiere_buchungen(buchungen: List[Buchung]) -> List[Buchung]:
    """Liefert ANONYMISIERTE KOPIEN der Buchungen (Original bleibt unveraendert)."""
    kopien = []
    for b in buchungen:
        k = copy.copy(b)
        name = _empfaenger_personname(b.empfaenger)
        # Empfaenger-Feld: auch Einzelnamen schwaerzen.
        k.empfaenger = anonymisiere_text(b.empfaenger, feld=True)
        # Belegtext: IBAN/BIC maskieren, den konkreten Empfaenger-Namen (auch
        # einzeln) tilgen, dann generischer 2-Wort-Namensdurchlauf.
        zweck = maskiere_iban(b.verwendungszweck)
        zweck = maskiere_bic(zweck)
        if name:
            zweck = _tilge_name(zweck, name)
        k.verwendungszweck = _maskiere_personen(zweck, min_len=2)
        kopien.append(k)
    return kopien


def _tilge_name(text: str, name: str) -> str:
    """Ersetzt einen konkreten (mehr-/einwortigen) Namen case-insensitiv."""
    muster = re.compile(r"\b" + r"\s+".join(re.escape(w) for w in name.split()) + r"\b",
                        re.IGNORECASE)
    return muster.sub(PLATZHALTER, text)


def anonymisiere_wiederkehrer(gruppen: list) -> list:
    """Anonymisiert den Anzeige-Empfaenger der Wiederkehrer-Gruppen (Personen
    -> [NAME], Firmen bleiben). Betraege/Turnus/Klassifikation unveraendert."""
    kopien = []
    for g in gruppen:
        k = copy.copy(g)
        k.empfaenger = anonymisiere_text(g.empfaenger, feld=True)
        kopien.append(k)
    return kopien
