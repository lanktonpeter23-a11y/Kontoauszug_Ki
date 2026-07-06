"""Turnus-Berechnung + Preiserhoehungs-Erkennung -- reine Python-Logik.

Die KI liefert nur die semantische Kategorie (z.B. "Fixkosten"). Den TURNUS
(monatlich/vierteljaehrlich/...) berechnet Python aus den tatsaechlichen
Buchungsabstaenden. Preiserhoehungen erkennt Python aus Betragswechseln
innerhalb einer Fixkosten-Gruppe (gleicher Zahlungsempfaenger).
"""

from __future__ import annotations

import re
from statistics import median
from typing import Dict, List

from models import KAT_FIXKOSTEN, Buchung

# Erwartete Abstaende (Tage) -> Turnus-Bezeichnung. (mitte, label)
_TURNUS_BUCKETS = [
    (7, "woechentlich"),
    (14, "zweiwoechentlich"),
    (30, "monatlich"),
    (61, "zweimonatlich"),
    (91, "vierteljaehrlich"),
    (182, "halbjaehrlich"),
    (365, "jaehrlich"),
]


def empfaenger_schluessel(zweck: str) -> str:
    """Bildet einen robusten Gruppierungsschluessel fuer den Zahlungsempfaenger.

    Ziffern/Referenznummern werden entfernt, damit z.B. "Stadtwerke ... RG
    1234" und "Stadtwerke ... RG 5678" in dieselbe Gruppe fallen.
    """
    t = zweck.lower()
    t = re.sub(r"\d+", " ", t)                 # Zahlen (Referenzen) raus
    t = re.sub(r"[^a-zäöüß ]", " ", t)         # Sonderzeichen raus
    worte = [w for w in t.split() if len(w) > 2]
    return " ".join(worte[:4]) if worte else zweck.strip().lower()[:20]


def empfaenger_anzeige(zweck: str) -> str:
    """Lesbarer Empfaengername (erste sinnvollen Worte des Verwendungszwecks)."""
    worte = zweck.split()
    return " ".join(worte[:5]) if worte else zweck


def berechne_turnus_und_preise(buchungen: List[Buchung]) -> None:
    """Setzt Turnus + Preiserhoehungs-Vermerk fuer alle Fixkosten-Buchungen.

    Arbeitet in-place auf den uebergebenen Buchungen.
    """
    gruppen: Dict[str, List[Buchung]] = {}
    for b in buchungen:
        if b.kategorie != KAT_FIXKOSTEN:
            continue
        if not b.empfaenger:
            b.empfaenger = empfaenger_anzeige(b.verwendungszweck)
        gruppen.setdefault(empfaenger_schluessel(b.verwendungszweck), []).append(b)

    for gruppe in gruppen.values():
        _verarbeite_gruppe(gruppe)


def _verarbeite_gruppe(gruppe: List[Buchung]) -> None:
    """Turnus aus Abstaenden + Preiswechsel innerhalb EINER Empfaenger-Gruppe."""
    mit_datum = sorted([b for b in gruppe if b.datum], key=lambda b: b.datum)

    # --- TURNUS aus den tatsaechlichen Abstaenden ---
    turnus = "unregelmaessig"
    if len(mit_datum) >= 2:
        abstaende = [
            (mit_datum[i + 1].datum - mit_datum[i].datum).days
            for i in range(len(mit_datum) - 1)
        ]
        abstaende = [a for a in abstaende if a > 0]
        if abstaende:
            turnus = _naechster_turnus(median(abstaende))
    elif len(mit_datum) == 1:
        turnus = "einmalig/unklar"
    for b in gruppe:
        b.turnus = turnus

    # --- PREISERHOEHUNG: Betragswechsel innerhalb der Gruppe ---
    vorher = None
    for b in mit_datum:
        betrag = round(abs(b.betrag), 2)
        if vorher is not None and betrag != vorher:
            richtung = "Preiserhoehung" if betrag > vorher else "Betragsaenderung"
            b.vermerk = f"{richtung}: {_g(vorher)} -> {_g(betrag)}"
        vorher = betrag


def _naechster_turnus(tage: float) -> str:
    """Ordnet einen mittleren Tagesabstand dem naechstliegenden Turnus zu."""
    bestes_label = "unregelmaessig"
    beste_abweichung = float("inf")
    for mitte, label in _TURNUS_BUCKETS:
        # relative Abweichung -> toleranter bei grossen Intervallen
        abw = abs(tage - mitte) / mitte
        if abw < beste_abweichung and abw <= 0.35:
            beste_abweichung = abw
            bestes_label = label
    return bestes_label


def _g(wert: float) -> str:
    return f"{wert:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
