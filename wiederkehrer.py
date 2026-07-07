"""FEATURE 2 -- Wiederkehrer-Erkennung (deterministisch, KEINE KI).

Gruppiert Buchungen am (normalisierten) ECHTEN Empfaenger und klassifiziert
regelmaessige Zahlungen. WICHTIG: laeuft VOR der Anonymisierung -- sonst sind
die Empfaenger unkenntlich und die Gruppierung wertlos.

Ausgabe je Gruppe (Sheet "Wiederkehrend"):
    Empfaenger | Art | Turnus | Anzahl | Erster | Letzter | Ø-Betrag |
    Summe | Klassifikation (SICHER/WAHRSCHEINLICH) | Betragsschwankung
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import List, Optional

from buchungsart import extrahiere_art
from models import Buchung
from turnus import _naechster_turnus as turnus_aus_tagen

# Vorlauf (Datum/Wert-Tag) + Art-Wort + PN-Nummer vor dem Empfaenger entfernen.
_ART_PREFIX_RE = re.compile(
    r"^(?:\s*\d{1,2}\.\d{1,2}\.?\s*){0,2}\s*"
    r"(?:lastschrift|euro-?\w*|[uü]berweisung|kartenzahlung(?:\s+\w+)?|"
    r"auszahlung(?:\s+\w+)?|gutschr\w*|lohn\s*/?\s*gehalt|sparrate|dauerauftrag|"
    r"sepa[- ]?\w*)?\s*",
    re.IGNORECASE,
)
_PN_RE = re.compile(r"\bpn[:\s]*\d+", re.IGNORECASE)
# Ab diesen Markern beginnt Referenz-/Rausch-Text (nicht mehr der Empfaenger).
_STOP_MARKER = (
    "eref", "mref", "cred", "iban", "bic", "btr.", "/*da", "ref ", "gir ",
    " elv", "abwa", "kundennummer", "vertragskonto", " kd ", " rg ", " vs ",
)


def _schneide_kern(zweck: str) -> str:
    """Extrahiert den Kern-Empfaenger (Originalschreibung, ohne Referenz-Rauschen)."""
    s = _ART_PREFIX_RE.sub("", zweck)
    s = _PN_RE.sub("", s)
    low = s.lower()
    cut = len(s)
    for mk in _STOP_MARKER:
        i = low.find(mk)
        if i != -1:
            cut = min(cut, i)
    s = s[:cut]
    s = re.sub(r"\b\S*\d\S*\b", " ", s)         # Tokens mit Ziffern (Refs/Datum) raus
    s = re.sub(r"[^\wäöüÄÖÜß .&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-&")
    return s


def normalisiere_empfaenger(zweck: str) -> str:
    """Normalisierter Gruppierungs-Schluessel (klein, erste sinnvolle Kernworte)."""
    kern = _schneide_kern(zweck).lower()
    worte = [w for w in re.split(r"[ .&-]+", kern) if len(w) >= 2]
    return " ".join(worte[:3])


def anzeige_empfaenger(zweck: str) -> str:
    """Lesbarer Empfaengername (Originalschreibung) fuer die Ausgabe."""
    kern = _schneide_kern(zweck)
    worte = [w for w in kern.split() if w]
    return " ".join(worte[:4]) if worte else (zweck.strip()[:30] or "?")


@dataclass
class WiederkehrerGruppe:
    empfaenger: str                 # Anzeige (Klartext; wird ggf. spaeter anonymisiert)
    schluessel: str                 # normalisierter Gruppierungsschluessel
    art: str
    turnus: str
    anzahl: int
    erster: Optional[date]
    letzter: Optional[date]
    schnitt: float
    summe: float
    klassifikation: str             # SICHER / WAHRSCHEINLICH
    schwankung: bool                # Betragsschwankung (Preiserhoehungs-Hinweis)
    buchungen: List[Buchung] = field(default_factory=list)


def _regelmaessig(intervalle: List[int]) -> bool:
    """Regelmaessig = alle Abstaende nahe am Median (kleine Varianz)."""
    if len(intervalle) < 2:
        return False
    m = median(intervalle)
    if m <= 0:
        return False
    return all(abs(i - m) <= 0.35 * m for i in intervalle)


def _haeufigste_art(buchungen: List[Buchung]) -> str:
    zaehler = {}
    for b in buchungen:
        art = b.art or extrahiere_art(b.verwendungszweck)
        zaehler[art] = zaehler.get(art, 0) + 1
    return max(zaehler, key=zaehler.get) if zaehler else "SONST"


def finde_wiederkehrer(buchungen: List[Buchung]) -> List[WiederkehrerGruppe]:
    """Findet wiederkehrende Zahlungen (>=2 Vorkommen je Empfaenger)."""
    gruppen: dict = {}
    for b in buchungen:
        key = normalisiere_empfaenger(b.verwendungszweck)
        if not key:
            continue
        gruppen.setdefault(key, []).append(b)

    ergebnis: List[WiederkehrerGruppe] = []
    for key, gb in gruppen.items():
        if len(gb) < 2:                          # nur Wiederkehrer (>= 2)
            continue
        mit_datum = sorted([b for b in gb if b.datum], key=lambda b: b.datum)
        intervalle = [
            (mit_datum[i + 1].datum - mit_datum[i].datum).days
            for i in range(len(mit_datum) - 1)
        ]
        intervalle = [d for d in intervalle if d > 0]
        turnus = turnus_aus_tagen(median(intervalle)) if intervalle else "unregelmaessig"

        anzahl = len(gb)
        if anzahl >= 3 and _regelmaessig(intervalle):
            klass = "SICHER"
        else:                                    # 2 Vorkommen, oder >=3 unregelmaessig
            klass = "WAHRSCHEINLICH"

        betraege = [b.betrag for b in gb]
        summe = round(sum(betraege), 2)
        schnitt = round(summe / anzahl, 2)
        schwankung = (max(abs(x) for x in betraege) - min(abs(x) for x in betraege)) > 0.005

        ergebnis.append(WiederkehrerGruppe(
            empfaenger=anzeige_empfaenger(gb[0].verwendungszweck),
            schluessel=key,
            art=_haeufigste_art(gb),
            turnus=turnus,
            anzahl=anzahl,
            erster=mit_datum[0].datum if mit_datum else None,
            letzter=mit_datum[-1].datum if mit_datum else None,
            schnitt=schnitt,
            summe=summe,
            klassifikation=klass,
            schwankung=schwankung,
            buchungen=gb,
        ))

    # groesste Summen zuerst (Betrag dem Betrage nach)
    ergebnis.sort(key=lambda g: -abs(g.summe))
    return ergebnis
