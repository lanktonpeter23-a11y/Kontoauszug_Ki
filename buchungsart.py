"""Buchungsart-Erkennung (deterministisch, KEINE KI).

Die Buchungsart steht bereits im OCR-Text am Anfang des Verwendungszwecks
(z.B. "LASTSCHRIFT PN:931 ...", "EURO-UEBERWEISUNG ...", "Kartenzahlung
girocard ..."). Sie wird per Prefix-Match zu einem kurzen Kuerzel extrahiert
und in eine eigene Spalte "Art" geschrieben:

    LASTSCHRIFT                     -> LS
    EURO-UEBERWEISUNG / UEBERWEISUNG-> UEW
    Kartenzahlung girocard/Maestro  -> KA
    GUTSCHRIFT                      -> GUT
    LOHN / GEHALT                   -> LOHN
    Auszahlung / Geldautomat / GA   -> GA
    sonst                          -> SONST
"""

from __future__ import annotations

import re

ART_LS = "LS"
ART_UEW = "UEW"
ART_KA = "KA"
ART_GUT = "GUT"
ART_LOHN = "LOHN"
ART_GA = "GA"
ART_SONST = "SONST"

# Fuehrende Datums-/Wert-Tags und "PN:xxx" vor dem eigentlichen Vorgangswort
# wegschneiden, damit der Prefix-Match sauber greift.
_VORLAUF_RE = re.compile(r"^(?:\s*\d{1,2}\.\d{1,2}\.?\s*){0,2}", re.IGNORECASE)


def extrahiere_art(zweck: str) -> str:
    """Bestimmt das Buchungsart-Kuerzel aus dem Verwendungszweck (Prefix-Match)."""
    if not zweck:
        return ART_SONST
    z = _VORLAUF_RE.sub("", zweck).strip().lower()

    # Reihenfolge: spezifische vor generischen Treffern.
    if z.startswith("lastschrift"):
        return ART_LS
    if z.startswith("kartenzahlung"):
        return ART_KA
    if z.startswith("gutschrift") or z.startswith("gutschr"):
        return ART_GUT
    if z.startswith("lohn") or z.startswith("gehalt"):
        return ART_LOHN
    if z.startswith("auszahlung") or z.startswith("geldautomat") or z.startswith("ga "):
        return ART_GA
    # EURO-UEBERWEISUNG samt OCR-Verlesern (EURO-VUVEBERWEISUNG, EURO-UVEBER...)
    # sowie schlichtes UEBERWEISUNG / SEPA-Ueberweisung.
    if z.startswith("euro") or z.startswith("ueberweisung") or z.startswith("überweisung") \
            or z.startswith("sepa"):
        return ART_UEW
    return ART_SONST
