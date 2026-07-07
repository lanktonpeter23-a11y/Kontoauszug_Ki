#!/usr/bin/env python3
"""Anonymisiert einen echten OCR-Kontoauszug-Text fuer eine oeffentliche Test-Fixture.

WICHTIG: Das Repo ist PUBLIC. Deshalb werden personenbezogene Daten entfernt:
  * Personennamen        -> NAME
  * Anschrift            -> ADRESSE
  * Kontonummer/Kundennr -> maskiert
  * IBANs                -> DE00 ... (Laenge/Format erhalten)
  * BICs                 -> BICXXXXX
  * lange Referenz-/EREF/MREF/CRED-Strings -> gekuerzt

UNVERAENDERT bleiben (parsing-relevant!):
  * alle Betraege + S/H-Kennzeichen
  * alle Datumsangaben (Kurz "TT.MM." UND Lang "TT.MM.JJJJ um HH:MM:SS Uhr")
  * die komplette ZEILENSTRUKTUR (inkl. der delaminierten Seite 1 und der
    ===SEITENENDE===-Trenner)

Aufruf:
    python tools/anonymize_ocr.py <roh_ocr.txt> <ziel_fixture.txt>
"""

from __future__ import annotations

import re
import sys

# Explizite Personennamen aus dem Quelltext (Kontoinhaberin + Gegenparteien).
# Firmen-/Handelsnamen (AXA, AMAZON, EDEKA, Tankstelle, Telekom ...) bleiben
# stehen -- sie sind keine personenbezogenen Daten und werden fuer den
# Kategorisierungs-Test benoetigt.
_PERSONENNAMEN = [
    "Ulrike Weinzierl", "ULRIKE WEINZIERL",
    "Veronika Weinzierl",
    "Sabine Neppl",
    "Kristina Huber",
    "Marc Polednik",
    "A. Neumann",
    "Puralei Monika Pernstecher", "Puralei Monika Pe", "Monika Pernstecher",
    "Weinzierl", "WEINZIERL",   # bare Nachname (z.B. "HE Weinzierl")
]

_ADRESSEN = [
    "Am Ährenfeld 19", "Am AEhrenfeld 19", "AM AEHRENFELD 19",
    "84056 Rottenburg a.d.Laaber",
]


def anonymisiere(text: str) -> str:
    # 1) IBANs (mit Leerzeichen gruppiert) -> DE00 0000 ...  (Format erhalten)
    def mask_iban_spaced(m):
        rest = m.group(0)[2:]
        return "DE" + re.sub(r"\d", "0", rest)
    text = re.sub(r"DE\d{2}(?:\s?\d{2,4}){3,6}", mask_iban_spaced, text)
    # 1b) IBANs am Stueck (z.B. IBAN: DE04300500000000444166)
    text = re.sub(r"DE\d{20}", "DE00000000000000000000", text)

    # 2) BICs -> BICXXXXX (8-11 Stellen, bankspezifisch)
    text = re.sub(r"\b[A-Z]{4}DE[0-9A-Z]{2}(?:[0-9A-Z]{3})?\b", "BICXXXXX", text)

    # 3) Lange Referenzfelder kuerzen (EREF/MREF/CRED + langer Token)
    text = re.sub(r"\b(EREF|MREF|CRED):\s*\S+", r"\1: REF", text)
    # 3b) lange reine Ziffernketten (Karten-/Kunden-/Belegnummern) maskieren.
    #     >=10 Ziffern am Stueck; Betraege (max. 3 Ziffern am Stueck durch
    #     Punkt/Komma getrennt) bleiben unangetastet.
    text = re.sub(r"\b\d{10,}\b", "REFNR", text)
    # 3c) lange GEMISCHTE Referenz-Tokens (Buchstaben UND Ziffern, >=14) -> REF.
    #     Reine Buchstabenwoerter (Firmen-/Merchant-Namen) bleiben erhalten.
    def _ref_token(m):
        t = m.group(0)
        if any(c.isdigit() for c in t) and any(c.isalpha() for c in t):
            return "REF"
        return t
    text = re.sub(r"\b[0-9A-Za-z]{14,}\b", _ref_token, text)

    # 4) Kontonummer / Kundennummer maskieren
    text = re.sub(r"Kontonummer\s*\d+", "Kontonummer 0000000", text)
    text = re.sub(r"Kd\s*\d+", "Kd 0000000", text)
    text = re.sub(r"Kundennummer\s*\d+", "Kundennummer 0000000", text)

    # 5) Anschriften -> ADRESSE
    for adr in _ADRESSEN:
        text = text.replace(adr, "ADRESSE")

    # 6) Personennamen -> NAME (laengste zuerst, damit Teilnamen nicht stehenbleiben)
    for name in sorted(_PERSONENNAMEN, key=len, reverse=True):
        text = re.sub(re.escape(name), "NAME", text)

    return text


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        roh = fh.read()
    anon = anonymisiere(roh)
    with open(argv[2], "w", encoding="utf-8") as fh:
        fh.write(anon)
    print(f"Anonymisiert geschrieben: {argv[2]} ({len(anon.splitlines())} Zeilen)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
