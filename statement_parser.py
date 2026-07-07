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
# Bewusst ENG gefasst (nur echter Anfangssaldo). Bare "uebertrag"/"vortrag"/
# "kontostand vom" sind hier NICHT enthalten, sonst wuerde _finde_saldo eine
# Zwischen-Uebertragszeile oder den NEUEN Kontostand als alten Saldo lesen.
# Die Carry-Erkennung fuer Uebertrag/Kontostand laeuft separat ueber _CARRY_RE.
_SALDO_ALT_LABELS = [
    "alter kontostand", "kontostand alt", "alter saldo",
    "saldovortrag", "saldo vortrag", "anfangssaldo", "anfangsbestand",
]
_SALDO_NEU_LABELS = [
    "neuer kontostand", "kontostand neu", "neuer saldo", "endsaldo",
    "schlusssaldo", "schluss-saldo", "endbestand", "neuer saldo/kontostand",
    "aktueller kontostand", "kontostand aktuell",
]

# FIX 2 -- Uebertrags-/Saldo-Zeilen sind KEINE Buchungen. Sie dienen nur der
# Saldo-Fortschreibung/Kontrolle und duerfen weder in die Summe noch ins
# Journal, noch einen Verwendungszweck "verlaengern". OCR-tolerant gematcht
# (Umlaut/ue/u bei "Uebertrag", Leerzeichen/Bindestrich bei "Kontostand"):
#   "alter/neuer Kontostand ...", "Kontostand vom ...",
#   "Uebertrag auf/von/Blatt X" (auch "Übertrag"/"Ubertrag"),
#   "Summe"/"Zwischensumme".
_CARRY_RE = re.compile(
    r"(?:alter|neuer|alte|neue)\s+konto[\s-]*stand"     # alter/neuer Kontostand
    r"|konto[\s-]*stand\s*(?:vom|alt|neu|am|:)"          # Kontostand vom/alt/neu
    r"|[uü]e?bertrag"                                    # Übertrag/Uebertrag/Ubertrag
    r"|\b(?:zwischen)?summe\b",                          # (Zwischen)Summe
    re.IGNORECASE,
)

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
# Auszugsnummer inkl. Jahr im Kopf, z.B. "Kontoauszug 1/2025", "Auszug Nr. 3/2025".
_AUSZUG_JAHR_RE = re.compile(r"(?:kontoauszug|auszug)[^\n]*?\b\d{1,2}\s*/\s*(20\d{2})\b", re.IGNORECASE)

# Seiten-Trenner im OCR-Text (mehrere PDF-Seiten in einem Textstrom).
_SEITE_RE = re.compile(r"=+\s*SEITENENDE\s*=+", re.IGNORECASE)

# Zeile, die NUR aus einem Kurz-Datum "TT.MM." besteht (delaminierte Bu-Tag-Spalte).
_NUR_KURZDATUM_RE = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\s*$")
# Zeile, die NUR aus einem Betrag (optional + S/H) besteht (delaminierte Betragsspalte).
_NUR_BETRAG_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*([SHsh])?\s*$")
# Beginn eines Buchungs-Vorgangs (fuer die Zweck-Segmentierung delaminierter Seiten).
_VORGANG_START_RE = re.compile(
    r"^\s*(?:LASTSCHRIFT|EURO-?[UÜ]E?BERWEISUNG|[UÜ]E?BERWEISUNG|KARTENZAHLUNG|"
    r"AUSZAHLUNG|EINZAHLUNG|GUTSCHRIFT|GUTSCHR|LOHN|GEHALT|SPARRATE|DAUERAUFTRAG|"
    r"ABBUCHUNG|SEPA)",
    re.IGNORECASE,
)


def parse_auszug(ocr_text: str, quelle_pdf: str, profile: List[Dict[str, Any]]) -> Auszug:
    """Baut aus dem kompletten OCR-Text eines PDFs einen ``Auszug``.

    Der Text kann mehrere Seiten enthalten (getrennt durch ===SEITENENDE===).
    Jede Seite wird einzeln geparst: saubere Zeilen-Layouts ueber den
    zeilenbasierten Parser, delaminierte Seiten (OCR hat Spalten getrennt) ueber
    den Spalten-Zip-Parser.
    """
    auszug = Auszug(quelle_pdf=quelle_pdf)

    profil = _erkenne_profil(ocr_text, profile)
    auszug.bank_profil = profil.get("name", "generisch") if profil else "generisch"

    auszug.konto = _finde_konto(ocr_text, profil)
    auszug.jahr = _finde_jahr(ocr_text)

    # Balance-Checkpoints (Uebertrag/Kontostand-Werte) doc-weit einsammeln --
    # damit delaminierte Betragsspalten die Saldo-Werte nicht als Buchung zaehlen.
    balance_werte = _sammle_balance_werte(ocr_text)
    auszug.saldo_alt = (_finde_saldo(ocr_text, _labels(profil, "saldo_alt", _SALDO_ALT_LABELS))
                        or _finde_saldo_alt_delaminiert(ocr_text, balance_werte))
    auszug.saldo_neu = _finde_saldo(ocr_text, _labels(profil, "saldo_neu", _SALDO_NEU_LABELS))

    ausschluss = set(balance_werte)
    for s in (auszug.saldo_alt, auszug.saldo_neu):
        if s is not None:
            ausschluss.add(round(s, 2))

    for seite in _SEITE_RE.split(ocr_text):
        if not seite.strip():
            continue
        if _ist_delaminiert(seite):
            seiten_buchungen = _parse_delaminiert(seite, auszug, ausschluss)
        else:
            seiten_buchungen = _parse_buchungen(seite, auszug, profil)
        # Bank-Running-Balance als Grundwahrheit: einzelne OCR-Betragsfehler,
        # die die Seiten-Uebertragsrechnung sprengen, deterministisch korrigieren.
        _korrigiere_seite_per_checkpoint(seite, seiten_buchungen)
        auszug.buchungen.extend(seiten_buchungen)

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
    """Basisjahr des Auszugs bestimmen.

    Reihenfolge:
      1. Auszugsnummer mit Jahr im Kopf ("Kontoauszug 1/2025").
      2. Zeitraum "vom TT.MM.JJJJ" -- ABER nicht die "Kontostand vom ..."-
         bzw. "Saldo"-Zeile (die traegt das VORJAHRES-Datum des alten
         Kontostandes und wuerde die Buchungen faelschlich ins Vorjahr legen).
      3. erste Volljahreszahl im Text.
    """
    m = _AUSZUG_JAHR_RE.search(text)
    if m:
        j = int(m.group(1))
        if 1990 <= j <= 2100:
            return j

    for m in _PERIODE_RE.finditer(text):
        vor = text[max(0, m.start() - 16):m.start()].lower()
        if "kontostand" in vor or "saldo" in vor:
            continue                       # "alter Kontostand vom ..." ignorieren
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
def _ist_carry_zeile(zeile: str) -> bool:
    """Uebertrags-/Saldo-/Summenzeile -> nie Buchung, nie Zweck-Fortsetzung.

    Deckt sowohl die OCR-tolerante Regex (_CARRY_RE: Kontostand/Uebertrag/
    Summe) als auch alle konfigurierten Saldo-Labels (alter/neuer Saldo,
    Saldovortrag, Endsaldo, ...) ab.
    """
    if _CARRY_RE.search(zeile):
        return True
    zl = zeile.lower()
    return any(lab in zl for lab in _SALDO_ALT_LABELS + _SALDO_NEU_LABELS)


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
    """Zeilenbasierter Buchungs-Parser -- Bu-Tag ist das Primaerkriterium.

    KERN-REGEL (deterministisch, kein Wortraten):
      * Eine Buchung ist GENAU eine Zeile, die mit dem Bu-Tag "TT.MM." BEGINNT.
      * Ihr Betrag steht in DERSELBEN Zeile rechts (mit S/H). Es wird NIE ein
        Betrag aus einer Nicht-Buchungszeile (Folgezeile, Uebertrag,
        Kontostand, IBAN, Referenz) uebernommen.
      * Zeilen ohne fuehrenden Bu-Tag sind reine Verwendungszweck-Fortsetzung
        (Text) und liefern niemals einen Buchungsbetrag.
      * Hat eine Buchungszeile keinen eigenen Betrag -> "betrag_fehlt": sie
        wird gemeldet (auszug.unvollstaendige) und NICHT in Summe/Journal
        aufgenommen.
    """
    zeilen = [z for z in text.splitlines() if z.strip()]

    # Kandidaten (Bu-Tag-Zeilen) bestimmen, um das Layout (laufender Saldo?)
    # zu erkennen. finde_datum_am_anfang matcht nur ein Datum GANZ am Anfang.
    kandidaten = [z for z in zeilen if finde_datum_am_anfang(z)]
    laufend = _laufender_saldo_layout(kandidaten, profil)

    buchungen: List[Buchung] = []
    aktuell: Optional[Buchung] = None

    for zeile in zeilen:
        datum_info = finde_datum_am_anfang(zeile)

        if datum_info:
            # BUCHUNGSZEILE: Betrag kommt AUSSCHLIESSLICH aus dieser Zeile.
            tag, monat, jahr, ende = datum_info
            betrag, zweck, explizit = _betrag_und_zweck(zeile, ende, laufend)
            aktuell = _bau_buchung(auszug, tag, monat, jahr, zweck, betrag, explizit, zeile)
            buchungen.append(aktuell)

        elif aktuell is not None and not _ist_kopf_zeile(zeile) and not _ist_carry_zeile(zeile):
            # NICHT-Buchungszeile: nur Verwendungszweck-Text anhaengen, NIE Betrag.
            aktuell.verwendungszweck = (aktuell.verwendungszweck + " " + zeile.strip()).strip()

    # Buchungen ohne eigenen Betrag als "betrag_fehlt" melden (nicht in Summe).
    gueltig: List[Buchung] = []
    for b in buchungen:
        if b.betrag is None:
            auszug.unvollstaendige.append(f"{b.roh_zeile.strip()}  [betrag_fehlt]")
        else:
            gueltig.append(b)
    return gueltig


def _betrag_und_zweck(zeile: str, datum_ende: int, laufend: bool):
    """Extrahiert (Betrag, Zweck, vorzeichen_explizit) ab Datum-Ende.

    FIX 1: Das Vorzeichen stammt aus dem S/H- bzw. +/--Kennzeichen am Betrag
    (in ``finde_alle_betraege`` bereits ausgewertet). ``explizit=False``
    bedeutet, dass kein solches Kennzeichen lesbar war.
    """
    betraege = finde_alle_betraege(zeile)
    if not betraege:
        return None, zeile[datum_ende:].strip(), False
    idx = -2 if (laufend and len(betraege) >= 2) else -1
    betrag, start, ende, explizit = betraege[idx]

    # OCR-KORREKTUR S/H-Spalte: Steht am Zeilenende hinter dem Betrag KEIN
    # sauberes S/H (explizit=False), aber ein einzelnes Zeichen, das das OCR
    # oft aus "S" verliest (5, $, §, s), gilt es als Soll (Ausgabe, negativ);
    # ein einzelnes "H"/"h" als Haben (Einnahme, positiv). Das S/H steht in
    # diesem Layout immer ganz rechts.
    if not explizit:
        tail = zeile[ende:].strip()
        if re.fullmatch(r"[5sS$§]", tail):
            betrag, explizit = -abs(betrag), True
        elif re.fullmatch(r"[hH]", tail):
            betrag, explizit = abs(betrag), True

    zweck = zeile[datum_ende:start].strip()
    # Fuehrende zweite Datumsangabe (Wertstellung) aus dem Zweck entfernen.
    zweck = re.sub(r"^\d{1,2}\.\d{1,2}\.(\d{2,4})?\s*", "", zweck).strip()
    return betrag, zweck, explizit


def _bau_buchung(auszug: Auszug, tag, monat, jahr, zweck, betrag, explizit, roh) -> Buchung:
    b = Buchung(
        konto=auszug.konto,
        datum=None,
        verwendungszweck=zweck,
        betrag=betrag,
        # FIX 1: unsicher, wenn ein Betrag ohne S/H- bzw. +/--Kennzeichen kam.
        vorzeichen_unsicher=(betrag is not None and not explizit),
        quelle_pdf=auszug.quelle_pdf,
        roh_zeile=roh.strip(),
    )
    # Rohdatum temporaer in Attributen ablegen (fuer Smart-Year).
    b._tag, b._monat, b._jahr = tag, monat, jahr  # type: ignore[attr-defined]
    return b


# ---------------------------------------------------------------------------
# DELAMINIERTE SEITEN (OCR hat die Tabellenspalten getrennt)
# ---------------------------------------------------------------------------
def _sammle_balance_werte(text: str) -> set:
    """Alle Saldo-Checkpoint-Werte (Uebertrag/Kontostand) aus TEXT-Zeilen.

    Diese Werte sind KEINE Buchungen. Auf delaminierten Seiten stehen die
    gleichen Werte in einer nackten Betragsspalte -- ueber diese Menge werden
    sie dort von den Buchungsbetraegen getrennt.
    """
    werte = set()
    for zeile in text.splitlines():
        if _ist_carry_zeile(zeile):
            for wert, _s, _e, _expl in finde_alle_betraege(zeile):
                werte.add(round(wert, 2))
    return werte


def _finde_saldo_alt_delaminiert(text: str, balance_werte: set) -> Optional[float]:
    """Alten Kontostand finden, wenn seine Betragszeile delaminiert wurde.

    Auf der ersten Seite steht der alte Kontostand als nackte Betragszeile
    (der Label-Text "alter Kontostand vom ..." wurde vom OCR in einen anderen
    Block getrennt). Er ist der nackte Betrag, der KEIN bekannter
    Uebertrags-/Kontostand-Checkpoint ist.
    """
    erste_seite = _SEITE_RE.split(text)[0]
    if not _ist_delaminiert(erste_seite):
        return None
    kandidaten = []
    for zeile in erste_seite.splitlines():
        m = _NUR_BETRAG_RE.match(zeile)
        if not m:
            continue
        wert = float((m.group(1) + "," + m.group(2)).replace(".", "").replace(",", "."))
        # Nur H-Salden (Guthaben) und keine bekannten Uebertrags-Checkpoints.
        if (m.group(3) or "").upper() == "H" and round(wert, 2) not in balance_werte:
            kandidaten.append(round(wert, 2))
    # Der alte Kontostand ist der erste solche Wert (Kredit-/Guthabenrahmen ohne
    # H-Kennung wurde durch die H-Pflicht bereits ausgeschlossen).
    return kandidaten[0] if kandidaten else None


def _ist_delaminiert(seite: str) -> bool:
    """Erkennt eine Seite, deren Tabellenspalten das OCR getrennt hat.

    Kennzeichen: mehrere Zeilen, die NUR aus einem Kurz-Datum bestehen, UND
    mehrere Zeilen, die NUR aus einem Betrag bestehen (statt beides pro Zeile).
    """
    zeilen = seite.splitlines()
    nur_datum = sum(1 for z in zeilen if _NUR_KURZDATUM_RE.match(z))
    nur_betrag = sum(1 for z in zeilen if _NUR_BETRAG_RE.match(z))
    return nur_datum >= 3 and nur_betrag >= 3


def _parse_delaminiert(seite: str, auszug: Auszug, ausschluss: set) -> List[Buchung]:
    """Baut Buchungen aus einer delaminierten Seite per Spalten-Zip.

    Sammelt (in Dokumentreihenfolge) die Bu-Tag-Kurzdaten, die Buchungsbetraege
    (nackte Betragszeilen mit S/H, ohne die Saldo-Checkpoints) und die
    Vorgangs-Textsegmente -- und fuegt sie index-weise zusammen.
    """
    zeilen = [z for z in seite.splitlines() if z.strip()]

    # 1) Bu-Tag-Kurzdaten (nur-Datum-Zeilen).
    daten = []
    for z in zeilen:
        if _NUR_KURZDATUM_RE.match(z):
            di = finde_datum_am_anfang(z)
            if di:
                daten.append(di)

    # 2) Buchungsbetraege: nackte Betragszeilen MIT S/H, ohne Saldo-Checkpoints.
    betraege = []
    for z in zeilen:
        m = _NUR_BETRAG_RE.match(z)
        if not m:
            continue
        sh = (m.group(3) or "").upper()
        if not sh:                       # ohne S/H (z.B. Kreditrahmen) -> keine Buchung
            continue
        wert = float((m.group(1) + "," + m.group(2)).replace(".", "").replace(",", "."))
        if round(wert, 2) in ausschluss:  # Uebertrag/Kontostand -> keine Buchung
            continue
        betraege.append(-abs(wert) if sh == "S" else abs(wert))

    # 3) Vorgangs-Textsegmente (fuer die Verwendungszwecke).
    segmente = _vorgang_segmente(zeilen)

    # 4) Zip: Buchungsbetraege sind die Leitgroesse (jeder Betrag = eine Buchung).
    buchungen: List[Buchung] = []
    for i, betrag in enumerate(betraege):
        di = daten[i] if i < len(daten) else (daten[-1] if daten else None)
        zweck = segmente[i] if i < len(segmente) else ""
        if di:
            tag, monat, jahr, _ende = di
        else:
            tag = monat = jahr = None
        b = _bau_buchung(auszug, tag, monat, jahr, zweck, betrag, True, zweck or "delaminiert")
        buchungen.append(b)
    return buchungen


# ---------------------------------------------------------------------------
# CHECKPOINT-KORREKTUR: die Bank-Uebertragsrechnung je Seite als Grundwahrheit
# ---------------------------------------------------------------------------
_UEBERTRAG_LINE_RE = re.compile(r"[uü]e?bertrag", re.IGNORECASE)


def _ziffer_loesch_varianten(betrag: float):
    """Alle Betraege, die durch Loeschen GENAU EINER Ziffer entstehen.

    Deckt den haeufigsten OCR-Magnitudenfehler ab: eine faelschlich
    eingefuegte Ziffer (z.B. "709,85" statt "70,85"). Vorzeichen bleibt.
    """
    cent = str(int(round(abs(betrag) * 100)))
    sign = -1.0 if betrag < 0 else 1.0
    varianten = set()
    for i in range(len(cent)):
        rest = cent[:i] + cent[i + 1:]
        if rest:
            varianten.add(round(sign * int(rest) / 100.0, 2))
    return varianten


def _korrigiere_seite_per_checkpoint(seite: str, buchungen: List[Buchung]) -> None:
    """Korrigiert EINEN OCR-Betragsfehler je Seite anhand der Uebertragsrechnung.

    Grundwahrheit ist die Running-Balance der Bank: Start-Checkpoint
    (alter Kontostand / Uebertrag von) + Summe der Seiten-Buchungen =
    End-Checkpoint (Uebertrag auf / neuer Kontostand). Geht die Rechnung nicht
    auf und schliesst GENAU EINE Einzelziffer-Loeschung EINER Buchung die
    Luecke exakt, wird sie angewandt (mit Vermerk). Sonst bleibt alles
    unveraendert -> die Kontrollschicht meldet PRUEFEN.

    Nur bei echter Uebertrags-Struktur (mehrseitige Auszuege). Einseitige
    Auszuege ohne "Uebertrag"-Zeilen werden NIE automatisch korrigiert.
    """
    if not _UEBERTRAG_LINE_RE.search(seite):
        return
    carry = []
    for z in seite.splitlines():
        if _ist_carry_zeile(z):
            betr = finde_alle_betraege(z)
            if betr:
                carry.append(round(betr[-1][0], 2))
    if len(carry) < 2:
        return

    delta = round(carry[-1] - carry[0], 2)                 # End - Start
    summe = round(sum(b.betrag for b in buchungen if b.betrag is not None), 2)
    luecke = round(delta - summe, 2)
    if abs(luecke) <= 0.005:
        return

    kandidaten = []
    for b in buchungen:
        if b.betrag is None:
            continue
        for v in _ziffer_loesch_varianten(b.betrag):
            if round(v - b.betrag, 2) == luecke:
                kandidaten.append((b, v))
    if len(kandidaten) == 1:                                # nur bei EINDEUTIGKEIT
        b, v = kandidaten[0]
        b.vermerk = (f"OCR-Korrektur via Saldo-Checkpoint: "
                     f"{b.betrag:.2f} -> {v:.2f}")
        b.betrag = v


def _vorgang_segmente(zeilen: List[str]) -> List[str]:
    """Teilt die Vorgangs-Zeilen einer delaminierten Seite in Buchungs-Bloecke.

    Jeder Block beginnt an einer Vorgangs-Start-Zeile (LASTSCHRIFT, EURO-
    UEBERWEISUNG, Kartenzahlung, ...). Saldo-/Kopf-/Datum-/Betragszeilen werden
    dabei uebersprungen.
    """
    segmente: List[str] = []
    aktuell: Optional[List[str]] = None
    for z in zeilen:
        if _VORGANG_START_RE.match(z) and not _ist_carry_zeile(z):
            if aktuell is not None:
                segmente.append(" ".join(aktuell).strip())
            aktuell = [z.strip()]
        elif aktuell is not None:
            if (_NUR_KURZDATUM_RE.match(z) or _NUR_BETRAG_RE.match(z)
                    or _ist_carry_zeile(z) or _ist_kopf_zeile(z)):
                continue
            aktuell.append(z.strip())
    if aktuell is not None:
        segmente.append(" ".join(aktuell).strip())
    return segmente


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
