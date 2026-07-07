"""FEATURE 1 -- Anonymisierung (nur echte PERSONENNAMEN + IBAN/BIC).

Ziel: echte Personennamen und volle IBANs/BICs aus dem Text entfernen.
FIRMEN BLEIBEN ERHALTEN (fuer Wiederkehrer-Gruppierung + Lesbarkeit).

Reihenfolge im Gesamtablauf: Anonymisierung ist der LETZTE Schritt vor der
anonymen Ausgabe -- die Gruppierung (Wiederkehrer) laeuft VORHER auf den
echten Empfaengern.

Deterministische Person/Firma-Unterscheidung:
  * FIRMA (bleibt): Firmen-Marker (GmbH, AG, SE, KG, mbH, e.V., e.G.,
    "Versicherung", "Bank", "AMAZON", "PAYPAL", ...), ALL-CAPS-Woerter oder
    bekannte Haendler/Dienste.
  * PERSON (-> [NAME]): "Vorname Nachname"-Muster ohne Firmen-Marker.
  * IM ZWEIFEL -> Person (schwaerzen). Datenschutz gewinnt.
"""

from __future__ import annotations

import copy
import re
from typing import List

from models import Buchung

PLATZHALTER = "[NAME]"

# --- IBAN / BIC ------------------------------------------------------------
# IBAN (mit oder ohne Leerzeichen) -> auf die letzten 4 Stellen maskieren.
_IBAN_RE = re.compile(r"DE\d{2}(?:\s?\d){10,22}", re.IGNORECASE)
# BIC nach dem Label "BIC" ...
_BIC_LABEL_RE = re.compile(r"(BIC[:\s]+)[A-Z0-9]{6,11}", re.IGNORECASE)
# ... und BIC-foermige Tokens mit Ziffer (z.B. GENODEF1MO5), auch ohne Label.
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


# --- Person vs. Firma ------------------------------------------------------
_FIRMA_MARKER = (
    "gmbh", "mbh", " ag", " ag.", "aktiengesellschaft", " se", " se.", " kg",
    "kgaa", " ohg", " gbr", " ug", "e.v", "e. v", "e.g", "e. g", " eg", " eg.",
    "& co", "co.", "versicherung", "bank", "sparkasse", "volksbank",
    "raiffeisen", "amazon", "paypal", "telekom", "vattenfall", "edeka",
    "finanzamt", "rundfunk", "stadtwerke", "netflix", "spotify", "vodafone",
    "congstar", "devk", "axa", "swiss life", "card process", "energie",
)

# Haeufige (grossgeschriebene) Nicht-Namen-Woerter, die keinen Namen einleiten.
_KEIN_NAME_START = {
    "Ihr", "Ihre", "Der", "Die", "Das", "Am", "Für", "Fuer", "Bei", "Mit",
    "Von", "Und", "Prime", "Video", "Mktp", "Klo", "Danke", "Anteil", "Ort",
    "Rechnung", "Beitrag", "Kd", "Strom", "Leben", "Unfall", "Abschlag",
    "Kredit", "Konto", "Kontostand", "Blatt", "Wert", "Vorgang", "Uhr",
    "Prowin", "Secure", "Zahlbeleg", "Festnetz", "Abrechnung", "Erstatt",
}

# Sequenz aus 2-4 Titel-Woertern (oder Initialen), z.B. "Veronika Weinzierl",
# "A. Neumann", "Puralei Monika Pernstecher". ALL-CAPS-Woerter matchen NICHT
# (die gelten als Firma).
_WORT = r"(?:[A-ZÄÖÜ][a-zäöüß]+|[A-ZÄÖÜ]\.)"
_PERSON_RE = re.compile(rf"{_WORT}(?:\s+{_WORT}){{1,3}}")


def _ist_firma(span: str) -> bool:
    low = " " + span.lower() + " "
    return any(mk in low for mk in _FIRMA_MARKER)


def maskiere_personen(text: str) -> str:
    """Ersetzt "Vorname Nachname"-Sequenzen ohne Firmen-Marker durch [NAME].

    Nur der FUEHRENDE Namenslauf wird geschwaerzt -- ein nachfolgendes
    Firmen-/Zweck-Wort (z.B. "... Weinzierl Pflegeversicherung") bricht den
    Namen ab und bleibt erhalten.
    """
    def repl(m: "re.Match") -> str:
        worte = m.group(0).split()
        namensworte = []
        for w in worte:
            # Ein Wort, das selbst einen Firmen-Marker traegt (z.B.
            # "Pflegeversicherung"), beendet den Namenslauf.
            if any(mk.strip() in w.lower() for mk in _FIRMA_MARKER):
                break
            namensworte.append(w)
        if len(namensworte) < 2:
            return m.group(0)                    # kein "Vorname Nachname"
        if namensworte[0].rstrip(".") in _KEIN_NAME_START:
            return m.group(0)                    # haeufiges Nicht-Namen-Wort
        namerun = " ".join(namensworte)
        if _ist_firma(namerun):                  # mehrwortiger Firmenname -> behalten
            return m.group(0)
        rest = worte[len(namensworte):]
        return PLATZHALTER + ((" " + " ".join(rest)) if rest else "")
    return _PERSON_RE.sub(repl, text)


def anonymisiere_text(text: str) -> str:
    """Kompletter Anonymisierungs-Durchlauf fuer EINEN Textstring."""
    if not text:
        return text
    text = maskiere_iban(text)
    text = maskiere_bic(text)
    text = maskiere_personen(text)
    return text


def anonymisiere_buchungen(buchungen: List[Buchung]) -> List[Buchung]:
    """Liefert ANONYMISIERTE KOPIEN der Buchungen (Original bleibt unveraendert)."""
    kopien = []
    for b in buchungen:
        k = copy.copy(b)
        k.verwendungszweck = anonymisiere_text(b.verwendungszweck)
        k.empfaenger = anonymisiere_text(b.empfaenger)
        kopien.append(k)
    return kopien


def anonymisiere_wiederkehrer(gruppen: list) -> list:
    """Anonymisiert den Anzeige-Empfaenger der Wiederkehrer-Gruppen (Personen
    -> [NAME], Firmen bleiben). Betraege/Turnus/Klassifikation unveraendert."""
    kopien = []
    for g in gruppen:
        k = copy.copy(g)
        k.empfaenger = anonymisiere_text(g.empfaenger)
        kopien.append(k)
    return kopien
