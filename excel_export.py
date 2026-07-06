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
    "typ", "empfaenger", "turnus", "vermerk", "auszug_differenz", "quelle_pdf",
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
        empfaenger=str(g("empfaenger")),
        turnus=str(g("turnus")),
        vermerk=str(g("vermerk")),
        auszug_differenz=float(g("auszug_differenz", 0) or 0),
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
def schreibe_excel(pfad: str, buchungen: List[Buchung]) -> None:
    """Erzeugt die komplette Excel-Datei aus dem vollstaendigen Buchungssatz."""
    buchungen = _sortiere(buchungen)
    wb = Workbook()
    wb.remove(wb.active)  # leeres Standardblatt entfernen

    _sheet_journal(wb, buchungen)
    _sheet_fixkosten(wb, buchungen)
    _sheet_konsum(wb, buchungen)
    _sheet_pruefen(wb, buchungen)
    _sheet_daten(wb, buchungen)

    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    wb.save(pfad)


# --- gemeinsame Helfer -----------------------------------------------------
def _schreibe_kopf(ws, spalten: List[str]) -> None:
    for c, titel in enumerate(spalten, start=1):
        zelle = ws.cell(row=1, column=c, value=titel)
        zelle.fill = _KOPF_FILL
        zelle.font = _KOPF_FONT
        zelle.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"


def _geldzelle(ws, row, col, wert) -> None:
    z = ws.cell(row=row, column=col, value=round(wert, 2))
    z.number_format = GELD_FORMAT


def _datumzelle(ws, row, col, d) -> None:
    if d is None:
        ws.cell(row=row, column=col, value="")
        return
    z = ws.cell(row=row, column=col, value=datetime(d.year, d.month, d.day))
    z.number_format = DATUM_FORMAT


def _spaltenbreiten(ws, breiten: Dict[int, int]) -> None:
    for spalte, breite in breiten.items():
        ws.column_dimensions[get_column_letter(spalte)].width = breite


# --- SHEET 1: Journal ------------------------------------------------------
def _sheet_journal(wb: Workbook, buchungen: List[Buchung]) -> None:
    ws = wb.create_sheet("Journal")
    spalten = ["Kontonummer", "Datum", "Verwendungszweck", "Einnahme",
               "Ausgabe", "Typ", "Status"]
    _schreibe_kopf(ws, spalten)

    r = 2
    for b in buchungen:
        ws.cell(row=r, column=1, value=b.konto)
        _datumzelle(ws, r, 2, b.datum)
        ws.cell(row=r, column=3, value=b.verwendungszweck)
        # Einnahme als Pluswert, Ausgabe als Minuswert -- anderes Feld leer.
        if b.betrag >= 0:
            _geldzelle(ws, r, 4, b.betrag)
        else:
            _geldzelle(ws, r, 5, b.betrag)
        ws.cell(row=r, column=6, value=b.typ)
        z = ws.cell(row=r, column=7, value=b.status)
        if b.status == STATUS_PRUEFEN:
            z.font = Font(bold=True, color="C00000")
        r += 1

    _spaltenbreiten(ws, {1: 24, 2: 12, 3: 50, 4: 14, 5: 14, 6: 12, 7: 20})


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
            "empfaenger": b.empfaenger,
            "turnus": b.turnus,
            "vermerk": b.vermerk,
            "auszug_differenz": round(b.auszug_differenz, 2),
            "quelle_pdf": b.quelle_pdf,
        }
        for c, name in enumerate(_DATEN_SPALTEN, start=1):
            ws.cell(row=r, column=c, value=werte[name])
        r += 1

    ws.sheet_state = "hidden"
