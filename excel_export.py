"""AUSGABE -- EINE Excel-Datei (openpyxl) mit 4 Sheets.

  SHEET 1 "Journal"    -- lueckenlos jede Buchung, chronologisch
  SHEET 2 "Fixkosten"  -- gruppiert nach Empfaenger + Turnus + Preisvermerk
  SHEET 3 "Konsum"     -- Lebenshaltung/Tanken + Monatsdurchschnitt
  SHEET 4 "Pruefen"    -- alle PRUEFEN-Buchungen inkl. Saldo-Differenz

APPEND-MODUS: Existiert Finanzanalyse.xlsx bereits, werden die vorhandenen
Buchungen aus einem versteckten Datenblatt "_Daten" geladen, mit den neuen
zusammengefuehrt (Duplikate via Datum+Zweck+Betrag+Konto vermeiden), neu
sortiert und alle Sheets frisch geschrieben. Zahlen sind ECHTE Zahlen
(Excel-Zahlenformat), kein Text.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Dict, List

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import (
    KAT_FIXKOSTEN,
    KAT_LEBENSHALTUNG,
    KAT_TANKEN,
    STATUS_PRUEFEN,
    Buchung,
)

# Excel-Zahlenformat fuer Euro-Betraege.
GELD_FORMAT = "#,##0.00"
DATUM_FORMAT = "DD.MM.YYYY"

_KOPF_FILL = PatternFill("solid", fgColor="1F4E78")
_KOPF_FONT = Font(bold=True, color="FFFFFF")
_GRUPPE_FILL = PatternFill("solid", fgColor="D9E1F2")
_SUMME_FONT = Font(bold=True)
_PRUEF_FILL = PatternFill("solid", fgColor="FCE4D6")
_DUENN = Side(style="thin", color="BFBFBF")
_RAHMEN = Border(left=_DUENN, right=_DUENN, top=_DUENN, bottom=_DUENN)

# Reihenfolge der Felder im versteckten Persistenz-Blatt "_Daten".
_DATEN_SPALTEN = [
    "konto", "datum", "verwendungszweck", "betrag", "status", "kategorie",
    "typ", "art", "empfaenger", "referenz", "mandatsref", "glaeubiger_id",
    "vertragsnr", "turnus", "vermerk", "auszug_differenz",
    "vorzeichen_unsicher", "quelle_pdf",
]
_DATEN_BLATT = "_Daten"


# ===========================================================================
# Laden (Append-Modus)
# ===========================================================================
def lade_bestehende_buchungen(pfad: str) -> List[Buchung]:
    """Laedt alle frueher gespeicherten Buchungen aus dem Blatt "_Daten"."""
    if not os.path.exists(pfad):
        return []
    try:
        wb = load_workbook(pfad, data_only=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] Bestehende Excel nicht lesbar ({exc}) -> beginne neu.")
        return []
    if _DATEN_BLATT not in wb.sheetnames:
        return []

    ws = wb[_DATEN_BLATT]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    kopf = [str(c) if c is not None else "" for c in rows[0]]
    idx = {name: kopf.index(name) for name in _DATEN_SPALTEN if name in kopf}
    buchungen: List[Buchung] = []
    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        buchungen.append(_zeile_zu_buchung(row, idx))
    return buchungen


def _zeile_zu_buchung(row, idx: Dict[str, int]) -> Buchung:
    def g(name, default=""):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) and row[i] is not None else default

    return Buchung(
        konto=str(g("konto")),
        datum=_zu_datum(g("datum", None)),
        verwendungszweck=str(g("verwendungszweck")),
        betrag=float(g("betrag", 0) or 0),
        status=str(g("status")),
        kategorie=str(g("kategorie")),
        typ=str(g("typ")),
        art=str(g("art")),
        empfaenger=str(g("empfaenger")),
        referenz=str(g("referenz")),
        mandatsref=str(g("mandatsref")),
        glaeubiger_id=str(g("glaeubiger_id")),
        vertragsnr=str(g("vertragsnr")),
        turnus=str(g("turnus")),
        vermerk=str(g("vermerk")),
        auszug_differenz=float(g("auszug_differenz", 0) or 0),
        vorzeichen_unsicher=bool(g("vorzeichen_unsicher", False)),
        quelle_pdf=str(g("quelle_pdf")),
    )


def _zu_datum(wert):
    if wert in (None, ""):
        return None
    if isinstance(wert, datetime):
        return wert.date()
    if isinstance(wert, date):
        return wert
    try:
        return datetime.strptime(str(wert)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ===========================================================================
# Zusammenfuehren (Duplikate vermeiden)
# ===========================================================================
def merge_buchungen(bestehend: List[Buchung], neu: List[Buchung]) -> List[Buchung]:
    """Fuegt neue Buchungen hinzu, ohne Duplikate (Datum+Zweck+Betrag+Konto)."""
    gesehen = {b.dedup_key() for b in bestehend}
    ergebnis = list(bestehend)
    hinzugefuegt = 0
    for b in neu:
        if b.dedup_key() not in gesehen:
            ergebnis.append(b)
            gesehen.add(b.dedup_key())
            hinzugefuegt += 1
    print(f"  [Excel] {hinzugefuegt} neue Buchungen, {len(neu) - hinzugefuegt} Duplikate uebersprungen.")
    return ergebnis


def _sortiere(buchungen: List[Buchung]) -> List[Buchung]:
    """Chronologisch (Buchungen ohne Datum ans Ende)."""
    return sorted(
        buchungen,
        key=lambda b: (b.datum is None, b.datum or date.max, b.konto),
    )


# ===========================================================================
# Schreiben
# ===========================================================================
def schreibe_excel(pfad: str, buchungen: List[Buchung], wiederkehrer_je_konto=None,
                   mit_daten: bool = True, anonym: bool = False) -> None:
    """Erzeugt die komplette Excel-Datei -- KONTOGETRENNT (Feature 5A).

    ``wiederkehrer_je_konto`` = dict {konto -> Liste WiederkehrerGruppe}.
    Pro Konto entstehen eigene Sheets "Journal_<letzte4>" und
    "Wiederkehrend_<letzte4>"; ein Uebersichts-Sheet "Konten" steht vorne.
    ``mit_daten`` = False laesst das versteckte Persistenz-Blatt "_Daten" weg
    (ANONYM-Datei, nie Append-Quelle). ``anonym`` maskiert die Kontonummern
    in der Uebersicht.
    """
    buchungen = _sortiere(buchungen)
    wiederkehrer_je_konto = wiederkehrer_je_konto or {}
    wb = Workbook()
    wb.remove(wb.active)  # leeres Standardblatt entfernen

    konten = _gruppiere_nach_konto(buchungen)
    suffixe = _konto_suffixe(list(konten.keys()))

    # Uebersicht zuerst.
    _sheet_konten(wb, konten, suffixe, anonym)
    # Pro Konto: eigenes Journal + eigene Wiederkehrer (nie kontouebergreifend).
    for konto, kb in konten.items():
        suf = suffixe[konto]
        _sheet_journal(wb, kb, f"Journal_{suf}")
        _sheet_wiederkehrend(wb, wiederkehrer_je_konto.get(konto, []), f"Wiederkehrend_{suf}")
    # Analyse-Sheets ueber alle Konten (Gesamtsicht).
    _sheet_fixkosten(wb, buchungen)
    _sheet_konsum(wb, buchungen)
    _sheet_pruefen(wb, buchungen)
    if mit_daten:                       # _Daten nur in die VOLL-Datei (Append-Quelle)
        _sheet_daten(wb, buchungen)

    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    wb.save(pfad)


# --- Konto-Gruppierung / -Benennung ----------------------------------------
def _konto_last4(konto: str) -> str:
    ziffern = re.sub(r"\s", "", konto or "")
    return ziffern[-4:] if len(ziffern) >= 4 else (ziffern or "0000")


def _gruppiere_nach_konto(buchungen: List[Buchung]) -> "Dict[str, list]":
    konten: Dict[str, list] = {}
    for b in buchungen:
        konten.setdefault(b.konto or "UNBEKANNT", []).append(b)
    return konten


def _konto_suffixe(konten: List[str]) -> Dict[str, str]:
    """Eindeutige Sheet-Suffixe (letzte 4) -- Kollisionen werden durchnummeriert."""
    ergebnis: Dict[str, str] = {}
    belegt: Dict[str, int] = {}
    for konto in konten:
        basis = _konto_last4(konto)
        if basis in belegt:
            belegt[basis] += 1
            ergebnis[konto] = f"{basis}_{belegt[basis]}"
        else:
            belegt[basis] = 1
            ergebnis[konto] = basis
    return ergebnis


def _konto_label(konto: str, anonym: bool) -> str:
    if not anonym:
        return konto or "UNBEKANNT"
    return "****" + _konto_last4(konto)


def _sheet_konten(wb: Workbook, konten: "Dict[str, list]", suffixe: Dict[str, str],
                  anonym: bool) -> None:
    ws = wb.create_sheet("Konten")
    spalten = ["Konto", "Sheet", "Buchungen", "Erster", "Letzter", "Saldo-Status"]
    _schreibe_kopf(ws, spalten)
    r = 2
    for konto, kb in konten.items():
        mit_datum = [b.datum for b in kb if b.datum]
        stati = {b.status for b in kb}
        if STATUS_PRUEFEN in stati:
            status = STATUS_PRUEFEN
        elif "OK" in stati:
            status = "OK"
        else:
            status = "OHNE_SALDO_PRUEFUNG"
        _textzelle(ws, r, 1, _konto_label(konto, anonym))
        _textzelle(ws, r, 2, "Journal_" + suffixe[konto])
        ws.cell(row=r, column=3, value=len(kb))
        _datumzelle(ws, r, 4, min(mit_datum) if mit_datum else None)
        _datumzelle(ws, r, 5, max(mit_datum) if mit_datum else None)
        z = ws.cell(row=r, column=6, value=status)
        z.alignment = _LINKS
        if status == STATUS_PRUEFEN:
            z.font = Font(bold=True, color="C00000")
        r += 1
    _spaltenbreiten(ws, {1: 28, 2: 20, 3: 12, 4: 12, 5: 12, 6: 20})
    _finalisiere(ws, len(spalten))


# --- gemeinsame Helfer -----------------------------------------------------
# wrap_text=False ueberall -> Text laeuft nicht um; feste Spaltenbreiten +
# Autofilter halten die Darstellung sauber, ohne leere Zellen zu fuellen.
_LINKS = Alignment(horizontal="left", vertical="center", wrap_text=False)


def _schreibe_kopf(ws, spalten: List[str]) -> None:
    for c, titel in enumerate(spalten, start=1):
        zelle = ws.cell(row=1, column=c, value=titel)
        zelle.fill = _KOPF_FILL
        zelle.font = _KOPF_FONT
        zelle.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
    ws.freeze_panes = "A2"                       # erste Zeile fixieren


def _finalisiere(ws, n_spalten: int) -> None:
    """Autofilter ueber den benutzten Bereich (Kopfzeile bleibt fixiert)."""
    letzte = max(1, ws.max_row)
    ws.auto_filter.ref = f"A1:{get_column_letter(n_spalten)}{letzte}"


def _geldzelle(ws, row, col, wert) -> None:
    """Betrag als echte Zahl. None/'' -> Zelle bleibt ECHT leer (summierbar)."""
    if wert is None:
        return
    z = ws.cell(row=row, column=col, value=round(wert, 2))
    z.number_format = GELD_FORMAT


def _datumzelle(ws, row, col, d) -> None:
    if d is None:
        return                                   # echt leer lassen (kein "")
    z = ws.cell(row=row, column=col, value=datetime(d.year, d.month, d.day))
    z.number_format = DATUM_FORMAT


def _textzelle(ws, row, col, wert) -> None:
    """Text nur schreiben, wenn vorhanden -- sonst Zelle ECHT leer lassen."""
    if wert:
        z = ws.cell(row=row, column=col, value=str(wert))
        z.alignment = _LINKS


def _spaltenbreiten(ws, breiten: Dict[int, int]) -> None:
    for spalte, breite in breiten.items():
        ws.column_dimensions[get_column_letter(spalte)].width = breite


# --- SHEET 1: Journal ------------------------------------------------------
def _sheet_journal(wb: Workbook, buchungen: List[Buchung], name: str = "Journal") -> None:
    ws = wb.create_sheet(name)
    spalten = ["Datum", "Art", "Empfaenger", "Einnahme", "Ausgabe", "Typ",
               "Status", "Referenz", "Mandatsref", "Glaeubiger-ID",
               "Vertrags-/Kundennr", "Verwendungszweck_voll"]
    _schreibe_kopf(ws, spalten)

    r = 2
    for b in buchungen:
        _datumzelle(ws, r, 1, b.datum)
        _textzelle(ws, r, 2, b.art)
        _textzelle(ws, r, 3, b.empfaenger)
        # Einnahme als Pluswert, Ausgabe als Minuswert -- das jeweils andere
        # Feld bleibt ECHT leer (None), damit Summen/Autofilter sauber sind.
        if b.betrag >= 0:
            _geldzelle(ws, r, 4, b.betrag)
        else:
            _geldzelle(ws, r, 5, b.betrag)
        _textzelle(ws, r, 6, b.typ)
        z = ws.cell(row=r, column=7, value=b.status)
        z.alignment = _LINKS
        if b.status == STATUS_PRUEFEN:
            z.font = Font(bold=True, color="C00000")
        _textzelle(ws, r, 8, b.referenz)
        _textzelle(ws, r, 9, b.mandatsref)
        _textzelle(ws, r, 10, b.glaeubiger_id)
        _textzelle(ws, r, 11, b.vertragsnr)
        _textzelle(ws, r, 12, b.verwendungszweck)   # Originaltext als Beleg
        r += 1

    _spaltenbreiten(ws, {1: 12, 2: 7, 3: 30, 4: 13, 5: 13, 6: 11, 7: 20,
                         8: 20, 9: 18, 10: 20, 11: 22, 12: 70})
    _finalisiere(ws, len(spalten))


# --- SHEET 2: Fixkosten ----------------------------------------------------
def _sheet_fixkosten(wb: Workbook, buchungen: List[Buchung]) -> None:
    ws = wb.create_sheet("Fixkosten")
    spalten = ["Kontonummer", "Empfaenger", "Datum", "Verwendungszweck",
               "Betrag", "Turnus", "Erklaerung", "Vermerk"]
    _schreibe_kopf(ws, spalten)

    fix = [b for b in buchungen if b.kategorie == KAT_FIXKOSTEN]

    # Gruppieren nach Empfaenger, innerhalb chronologisch.
    gruppen: Dict[str, List[Buchung]] = {}
    for b in fix:
        schluessel = b.empfaenger or b.verwendungszweck[:30]
        gruppen.setdefault(schluessel, []).append(b)

    r = 2
    jahressumme = 0.0
    for empf in sorted(gruppen.keys(), key=str.lower):
        gruppe = sorted(gruppen[empf], key=lambda b: (b.datum is None, b.datum or date.max))
        for b in gruppe:
            ws.cell(row=r, column=1, value=b.konto)
            ws.cell(row=r, column=2, value=b.empfaenger or empf)
            _datumzelle(ws, r, 3, b.datum)
            ws.cell(row=r, column=4, value=b.verwendungszweck)
            _geldzelle(ws, r, 5, b.betrag)
            ws.cell(row=r, column=6, value=b.turnus)
            ws.cell(row=r, column=7, value=_turnus_erklaerung(b.turnus))
            ws.cell(row=r, column=8, value=b.vermerk)
            jahressumme += abs(b.betrag)
            r += 1
        # leichte Gruppentrennung
        for c in range(1, 9):
            ws.cell(row=r, column=c).fill = _GRUPPE_FILL
        r += 1

    # Jahressumme + monatlicher Durchschnitt (Jahressumme / 12).
    r += 1
    ws.cell(row=r, column=4, value="Jahressumme Fixkosten:").font = _SUMME_FONT
    _geldzelle(ws, r, 5, jahressumme)
    ws.cell(row=r, column=5).font = _SUMME_FONT
    r += 1
    ws.cell(row=r, column=4, value="Monatl. Durchschnitt (Summe/12):").font = _SUMME_FONT
    _geldzelle(ws, r, 5, jahressumme / 12.0)
    ws.cell(row=r, column=5).font = _SUMME_FONT

    _spaltenbreiten(ws, {1: 24, 2: 26, 3: 12, 4: 40, 5: 14, 6: 18, 7: 30, 8: 34})
    _finalisiere(ws, 8)


def _turnus_erklaerung(turnus: str) -> str:
    texte = {
        "woechentlich": "Zahlung ca. alle 7 Tage",
        "zweiwoechentlich": "Zahlung ca. alle 14 Tage",
        "monatlich": "Zahlung ca. jeden Monat",
        "zweimonatlich": "Zahlung ca. alle 2 Monate",
        "vierteljaehrlich": "Zahlung ca. alle 3 Monate",
        "halbjaehrlich": "Zahlung ca. alle 6 Monate",
        "jaehrlich": "Zahlung ca. 1x pro Jahr",
        "einmalig/unklar": "nur 1 Buchung -- Turnus nicht bestimmbar",
        "unregelmaessig": "kein klarer Rhythmus erkennbar",
    }
    return texte.get(turnus, "")


# --- SHEET 3: Konsum -------------------------------------------------------
def _sheet_konsum(wb: Workbook, buchungen: List[Buchung]) -> None:
    ws = wb.create_sheet("Konsum")
    spalten = ["Kontonummer", "Datum", "Kategorie", "Empfaenger", "Betrag"]
    _schreibe_kopf(ws, spalten)

    konsum = [b for b in buchungen if b.kategorie in (KAT_LEBENSHALTUNG, KAT_TANKEN)]

    r = 2
    summen: Dict[str, float] = {KAT_LEBENSHALTUNG: 0.0, KAT_TANKEN: 0.0}
    monate: Dict[str, set] = {KAT_LEBENSHALTUNG: set(), KAT_TANKEN: set()}
    for b in konsum:
        ws.cell(row=r, column=1, value=b.konto)
        _datumzelle(ws, r, 2, b.datum)
        ws.cell(row=r, column=3, value=b.kategorie)
        ws.cell(row=r, column=4, value=b.empfaenger or b.verwendungszweck[:30])
        _geldzelle(ws, r, 5, b.betrag)
        summen[b.kategorie] += abs(b.betrag)
        if b.datum:
            monate[b.kategorie].add((b.datum.year, b.datum.month))
        r += 1

    # Monatlicher Durchschnitt je Kategorie (Summe / Anzahl erfasster Monate).
    r += 1
    ws.cell(row=r, column=3, value="Monatlicher Durchschnitt je Kategorie:").font = _SUMME_FONT
    r += 1
    for kat in (KAT_LEBENSHALTUNG, KAT_TANKEN):
        anzahl_monate = max(1, len(monate[kat]))
        ws.cell(row=r, column=3, value=kat)
        ws.cell(row=r, column=4, value=f"{anzahl_monate} Monat(e)")
        _geldzelle(ws, r, 5, summen[kat] / anzahl_monate)
        ws.cell(row=r, column=5).font = _SUMME_FONT
        r += 1

    _spaltenbreiten(ws, {1: 24, 2: 12, 3: 16, 4: 34, 5: 14})
    _finalisiere(ws, 5)


# --- SHEET: Wiederkehrend --------------------------------------------------
def _sheet_wiederkehrend(wb: Workbook, wiederkehrer, name: str = "Wiederkehrend") -> None:
    ws = wb.create_sheet(name)
    spalten = ["Empfaenger", "Art", "Turnus", "Anzahl", "Erster", "Letzter",
               "Ø-Betrag", "Summe", "Klassifikation", "Betragsschwankung"]
    _schreibe_kopf(ws, spalten)

    r = 2
    for g in wiederkehrer:
        ws.cell(row=r, column=1, value=g.empfaenger)
        ws.cell(row=r, column=2, value=g.art)
        ws.cell(row=r, column=3, value=g.turnus)
        ws.cell(row=r, column=4, value=g.anzahl)
        _datumzelle(ws, r, 5, g.erster)
        _datumzelle(ws, r, 6, g.letzter)
        _geldzelle(ws, r, 7, g.schnitt)
        _geldzelle(ws, r, 8, g.summe)
        z = ws.cell(row=r, column=9, value=g.klassifikation)
        if g.klassifikation == "SICHER":
            z.font = Font(bold=True, color="1F7A1F")
        ws.cell(row=r, column=10, value="ja" if g.schwankung else "nein")
        r += 1

    if not wiederkehrer:
        ws.cell(row=2, column=1, value="Keine wiederkehrenden Zahlungen erkannt.")

    _spaltenbreiten(ws, {1: 34, 2: 8, 3: 18, 4: 8, 5: 12, 6: 12, 7: 14, 8: 14,
                         9: 16, 10: 16})
    _finalisiere(ws, 10)


# --- SHEET 4: Pruefen ------------------------------------------------------
def _sheet_pruefen(wb: Workbook, buchungen: List[Buchung]) -> None:
    ws = wb.create_sheet("Pruefen")
    spalten = ["Kontonummer", "Datum", "Verwendungszweck", "Betrag",
               "Quelle-PDF", "Saldo-Differenz Auszug"]
    _schreibe_kopf(ws, spalten)

    pruef = [b for b in buchungen if b.status == STATUS_PRUEFEN]
    r = 2
    for b in pruef:
        ws.cell(row=r, column=1, value=b.konto)
        _datumzelle(ws, r, 2, b.datum)
        ws.cell(row=r, column=3, value=b.verwendungszweck)
        _geldzelle(ws, r, 4, b.betrag)
        ws.cell(row=r, column=5, value=b.quelle_pdf)
        _geldzelle(ws, r, 6, b.auszug_differenz)
        for c in range(1, 7):
            ws.cell(row=r, column=c).fill = _PRUEF_FILL
        r += 1

    if not pruef:
        ws.cell(row=2, column=1, value="Keine Buchungen mit Status PRUEFEN -- alles kontrolliert. :)")

    _spaltenbreiten(ws, {1: 24, 2: 12, 3: 50, 4: 14, 5: 28, 6: 22})
    _finalisiere(ws, 6)


# --- verstecktes Persistenz-Blatt "_Daten" ---------------------------------
def _sheet_daten(wb: Workbook, buchungen: List[Buchung]) -> None:
    """Vollstaendiger Buchungssatz als Rohdaten -- Quelle fuer den Append-Modus."""
    ws = wb.create_sheet(_DATEN_BLATT)
    for c, name in enumerate(_DATEN_SPALTEN, start=1):
        ws.cell(row=1, column=c, value=name)

    r = 2
    for b in buchungen:
        werte = {
            "konto": b.konto,
            "datum": b.datum.isoformat() if b.datum else "",
            "verwendungszweck": b.verwendungszweck,
            "betrag": round(b.betrag, 2),
            "status": b.status,
            "kategorie": b.kategorie,
            "typ": b.typ,
            "art": b.art,
            "empfaenger": b.empfaenger,
            "referenz": b.referenz,
            "mandatsref": b.mandatsref,
            "glaeubiger_id": b.glaeubiger_id,
            "vertragsnr": b.vertragsnr,
            "turnus": b.turnus,
            "vermerk": b.vermerk,
            "auszug_differenz": round(b.auszug_differenz, 2),
            "vorzeichen_unsicher": b.vorzeichen_unsicher,
            "quelle_pdf": b.quelle_pdf,
        }
        for c, name in enumerate(_DATEN_SPALTEN, start=1):
            ws.cell(row=r, column=c, value=werte[name])
        r += 1

    ws.sheet_state = "hidden"


# ===========================================================================
# FEATURE 3 -- Excel als Input (bestehendes Journal statt PDFs)
# ===========================================================================
# Spaltenname (lowercased) -> Feld. Toleriert uebliche Varianten.
_JOURNAL_SPALTEN = {
    "kontonummer": "konto", "konto": "konto",
    "datum": "datum",
    "art": "art",
    "verwendungszweck_voll": "verwendungszweck",   # Header der VOLL-Ausgabe
    "verwendungszweck": "verwendungszweck", "zweck": "verwendungszweck",
    "empfaenger": "empfaenger", "empfänger": "empfaenger",
    "einnahme": "einnahme",
    "ausgabe": "ausgabe",
    "betrag": "betrag",
    "typ": "typ",
    "status": "status",
}


def lese_journal_excel(pfad: str) -> List[Buchung]:
    """Liest ein bestehendes (VOLLSTAENDIGES) Journal-Sheet zu Buchungen ein.

    Erwartet Spalten Datum, Verwendungszweck, Einnahme/Ausgabe (oder Betrag),
    optional Art/Typ/Kontonummer/Status. Aus Einnahme (positiv) bzw. Ausgabe
    (negativ) wird der vorzeichenbehaftete Betrag gebildet.
    """
    wb = load_workbook(pfad, data_only=True)
    # Beste Wiedergabe: unser eigenes Persistenz-Blatt (enthaelt Konto + alle
    # Felder) -- so bleibt beim Re-Import die Multi-Konto-Struktur erhalten.
    if _DATEN_BLATT in wb.sheetnames:
        return lade_bestehende_buchungen(pfad)
    # Sonst: alle "Journal*"-Sheets (kontogetrennt), sonst das erste Sheet.
    journal_sheets = [n for n in wb.sheetnames if n.lower().startswith("journal")]
    if not journal_sheets:
        journal_sheets = [wb.sheetnames[0]]

    buchungen: List[Buchung] = []
    for sheetname in journal_sheets:
        buchungen.extend(_lese_journal_sheet(wb[sheetname]))
    return buchungen


def _lese_journal_sheet(ws) -> List[Buchung]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    kopf = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    idx = {}
    for i, name in enumerate(kopf):
        feld = _JOURNAL_SPALTEN.get(name)
        if feld and feld not in idx:
            idx[feld] = i

    def zelle(row, feld):
        i = idx.get(feld)
        return row[i] if i is not None and i < len(row) else None

    buchungen: List[Buchung] = []
    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        einnahme = _zu_float(zelle(row, "einnahme"))
        ausgabe = _zu_float(zelle(row, "ausgabe"))
        betrag = _zu_float(zelle(row, "betrag"))
        if betrag is None:
            betrag = (einnahme or 0.0) + (ausgabe or 0.0)  # Ausgabe ist bereits negativ
        # Verwendungszweck bevorzugt; sonst Empfaenger-Spalte als Ersatz
        # (fuer die Wiederkehrer-Gruppierung).
        zweck = str(zelle(row, "verwendungszweck") or "").strip()
        empf = str(zelle(row, "empfaenger") or "").strip()
        if not zweck:
            zweck = empf
        if not zweck and betrag == 0:
            continue
        b = Buchung(
            konto=str(zelle(row, "konto") or "").strip(),
            datum=_zu_datum(zelle(row, "datum")),
            verwendungszweck=zweck,
            empfaenger=empf,
            betrag=round(betrag, 2),
            status=str(zelle(row, "status") or "OK").strip() or "OK",
            typ=str(zelle(row, "typ") or "").strip(),
            art=str(zelle(row, "art") or "").strip(),
        )
        buchungen.append(b)
    return buchungen


def _zu_float(wert):
    if wert in (None, ""):
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    s = str(wert).strip().replace(".", "").replace(",", ".")  # deutsches Format
    try:
        return float(s)
    except ValueError:
        return None
