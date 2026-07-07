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


def _ref_schluessel(b: Buchung) -> str:
    """Stabiler Referenz-Schluessel: Mandatsreferenz (MREF) bevorzugt, sonst
    Vertrags-/Kundennr. Leer, wenn keine belastbare Referenz vorhanden."""
    for feld in (b.mandatsref, b.vertragsnr):
        norm = re.sub(r"[^0-9a-z]", "", (feld or "").lower())
        if len(norm) >= 5:                       # "REF"/kurzes Rauschen ausschliessen
            return norm
    return ""


def _intervalle(gb: List[Buchung]) -> List[int]:
    md = sorted([b for b in gb if b.datum], key=lambda b: b.datum)
    iv = [(md[i + 1].datum - md[i].datum).days for i in range(len(md) - 1)]
    return [d for d in iv if d > 0]


def _betrag_im_band(gb: List[Buchung], tol: float = 0.15) -> bool:
    """Alle Betraege innerhalb +-tol um den Mittelwert (fuer den Fallback)."""
    betr = [abs(b.betrag) for b in gb]
    m = sum(betr) / len(betr)
    return m > 0 and all(abs(x - m) <= tol * m for x in betr)


def _abstand_regelmaessig(gb: List[Buchung]) -> bool:
    iv = _intervalle(gb)
    if len(iv) >= 2:
        return _regelmaessig(iv)
    if len(iv) == 1:                             # 2 Buchungen: plausibler Turnus?
        return turnus_aus_tagen(iv[0]) != "unregelmaessig"
    return False


def _baue_gruppe(gb: List[Buchung], schluessel: str) -> WiederkehrerGruppe:
    md = sorted([b for b in gb if b.datum], key=lambda b: b.datum)
    iv = _intervalle(gb)
    turnus = turnus_aus_tagen(median(iv)) if iv else "unregelmaessig"
    anzahl = len(gb)
    klass = "SICHER" if (anzahl >= 3 and _regelmaessig(iv)) else "WAHRSCHEINLICH"

    betraege = [b.betrag for b in gb]
    summe = round(sum(betraege), 2)
    schwankung = (max(abs(x) for x in betraege) - min(abs(x) for x in betraege)) > 0.005
    # Anzeigename: der bereits bereinigte Klar-Empfaenger (wird spaeter ggf.
    # anonymisiert); Fallback auf die Kern-Extraktion.
    anzeige = gb[0].empfaenger or anzeige_empfaenger(gb[0].verwendungszweck)

    return WiederkehrerGruppe(
        empfaenger=anzeige,
        schluessel=schluessel,
        art=_haeufigste_art(gb),
        turnus=turnus,
        anzahl=anzahl,
        erster=md[0].datum if md else None,
        letzter=md[-1].datum if md else None,
        schnitt=round(summe / anzahl, 2),
        summe=summe,
        klassifikation=klass,
        schwankung=schwankung,
        buchungen=gb,
    )


def finde_wiederkehrer(buchungen: List[Buchung]) -> List[WiederkehrerGruppe]:
    """Wiederkehrer-Erkennung als deterministische Kaskade.

    1. PRIMAER: stabile Referenz (MREF / Vertrags-/Kundennr) -> danach
       gruppieren. Betrag irrelevant (gleiche Police, wechselnde Betraege =
       EINE Gruppe).
    2. FALLBACK (keine Referenz, z.B. Kartenzahlung): Gruppe nur, wenn
       normalisierter Empfaenger UND aehnlicher Betrag (+-15%) UND
       regelmaessiger Abstand zusammenpassen.
    Der Betrag ist NIE alleiniger/harter Schluessel -- variable Betraege mit
    stabiler Referenz bleiben in EINER Gruppe.
    """
    primaer: dict = {}
    ohne_ref: List[Buchung] = []
    for b in buchungen:
        rk = _ref_schluessel(b)
        if rk:
            primaer.setdefault(rk, []).append(b)
        else:
            ohne_ref.append(b)

    fallback: dict = {}
    for b in ohne_ref:
        nk = normalisiere_empfaenger(b.verwendungszweck) or normalisiere_empfaenger(b.empfaenger)
        if nk:
            fallback.setdefault(nk, []).append(b)

    ergebnis: List[WiederkehrerGruppe] = []
    # Mindestens 3 Vorkommen: 2-Vorkommen-Gruppen sind statistisch kein Abo/
    # keine Verpflichtung (Einkaufs-Rauschen wie REWE/EDEKA/Apotheke) und
    # bleiben ganz draussen.
    # PRIMAER: stabile Referenz KONSTANT (durch Gruppierung garantiert) UND
    # regelmaessiger Abstand. So bleibt AXA (feste MREF, Monatsrhythmus, Betrag
    # darf schwanken) drin, waehrend Amazon/PayPal (evtl. gleiche MREF, aber
    # UNREGELMAESSIGE Kauf-Abstaende) herausfallen.
    for key, gb in primaer.items():
        if len(gb) >= 3 and _abstand_regelmaessig(gb):
            ergebnis.append(_baue_gruppe(gb, "ref:" + key))
    # FALLBACK (keine stabile Referenz): nur echte Dauerauftraege/Miete --
    # MINDESTENS 3 Vorkommen (2 Punkte ergeben keinen belegten Rhythmus),
    # konstanter Empfaenger UND klar regelmaessiger Abstand (kleine Varianz)
    # UND NAHEZU konstanter Betrag (enge Toleranz). Der Fallback "gleicher
    # Haendler, wechselnder Betrag" ist bewusst ENTFERNT (Einkauf != Fixkosten);
    # 2 zufaellige Kartenzahlungen (z.B. Tanken) fallen so heraus.
    for key, gb in fallback.items():
        if len(gb) >= 3 and _abstand_regelmaessig(gb) and _betrag_im_band(gb, 0.10):
            ergebnis.append(_baue_gruppe(gb, "emp:" + key))

    ergebnis.sort(key=lambda g: -abs(g.summe))
    return ergebnis
