#!/usr/bin/env python3
"""Unit-Tests fuer die deterministische Parsing-/Kontroll-Logik.

Bewusst OHNE pytest/Pillow/openpyxl -- laeuft ueberall (auch in Termux) via:

    python test_parser.py

Deckt insbesondere die Haertungs-Fixes ab:
  * FIX 1  Soll/Haben (S/H) als primaere Vorzeichenquelle
  * FIX 2  Uebertrags-/Kontostand-Zeilen sind KEINE Buchungen
  * FIX 3  deterministische Bank-Erkennung (LIGA BANK) + korrektes Jahr
"""

import sys

import config
from control import pruefe_auszug
from models import STATUS_OK, TYP_AUSGABE, TYP_EINNAHME
from statement_parser import parse_auszug
from textutils import finde_letzten_betrag, parse_german_amount

_PROFILE = config.lade_bank_profile()
_fehler = 0


def check(bedingung, name):
    global _fehler
    if bedingung:
        print(f"  [OK ] {name}")
    else:
        _fehler += 1
        print(f"  [FEHLER] {name}")


# ---------------------------------------------------------------------------
# FIX 1 -- S/H-Vorzeichenlogik
# ---------------------------------------------------------------------------
def test_soll_haben_vorzeichen():
    print("\n== FIX 1: Soll/Haben (S/H) als Vorzeichenquelle ==")
    # S = Soll = Ausgabe -> negativ ; H = Haben = Einnahme -> positiv
    check(parse_german_amount("6,84 S") == -6.84, "S -> Ausgabe (negativ)")
    check(parse_german_amount("2.500,00 H") == 2500.00, "H -> Einnahme (positiv)")

    # finde_letzten_betrag liefert (wert, start, ende, vorzeichen_explizit)
    wert_s, _, _, expl_s = finde_letzten_betrag("LASTSCHRIFT Netflix 6,84 S")
    check(wert_s == -6.84 and expl_s is True, "S: negativ + vorzeichen_explizit=True")

    wert_h, _, _, expl_h = finde_letzten_betrag("GEHALT Arbeitgeber 2.500,00 H")
    check(wert_h == 2500.00 and expl_h is True, "H: positiv + vorzeichen_explizit=True")

    # Ohne S/H bzw. +/- -> Vorzeichen NICHT explizit (Fallback, unsicher)
    wert_o, _, _, expl_o = finde_letzten_betrag("KARTENZAHLUNG 45,60")
    check(expl_o is False, "kein S/H -> vorzeichen_explizit=False")

    # Integration: S -> Ausgabe-Typ, H -> Einnahme-Typ, no-sign -> unsicher
    ocr = (
        "Sparkasse\nIBAN DE12 3456 7890 1234 5678 90\n"
        "alter Kontostand 100,00 H\n"
        "05.07. LASTSCHRIFT Strom 30,00 S\n"
        "06.07. GEHALT Lohn 200,00 H\n"
        "07.07. KARTENZAHLUNG Baecker 5,00\n"      # ohne S/H -> unsicher
        "neuer Kontostand 265,00 H\n"
    )
    a = parse_auszug(ocr, "t.pdf", _PROFILE)
    pruefe_auszug(a)
    by = {round(b.betrag, 2): b for b in a.buchungen}
    check(-30.0 in by and by[-30.0].vorzeichen_unsicher is False, "S-Buchung negativ & sicher")
    check(200.0 in by and by[200.0].vorzeichen_unsicher is False, "H-Buchung positiv & sicher")
    check(5.0 in by and by[5.0].vorzeichen_unsicher is True, "Buchung ohne S/H -> vorzeichen_unsicher")


# ---------------------------------------------------------------------------
# FIX 2 -- Uebertrags-/Kontostand-Zeilen ausschliessen
# ---------------------------------------------------------------------------
def test_uebertrag_zeilen_ausgeschlossen():
    print("\n== FIX 2: Uebertrags-/Kontostand-Zeilen sind keine Buchungen ==")
    ocr = (
        "LIGA BANK eG\nIBAN DE79 7509 0500 0000 1234 56\n"
        "alter Kontostand vom 30.12.2024        1.000,00 H\n"
        "02.01. LASTSCHRIFT Netflix               10,00 S\n"
        "Uebertrag auf Blatt 2                    990,00 H\n"
        "Uebertrag von Blatt 1                    990,00 H\n"
        "03.01. GEHALT Arbeitgeber               500,00 H\n"
        "neuer Kontostand vom 31.01.2025        1.490,00 H\n"
    )
    a = parse_auszug(ocr, "liga.pdf", _PROFILE)
    pruefe_auszug(a)
    betraege = sorted(round(b.betrag, 2) for b in a.buchungen)

    check(len(a.buchungen) == 2, f"nur 2 echte Buchungen (nicht {len(a.buchungen)})")
    check(betraege == [-10.0, 500.0], f"Betraege == [-10, 500] (ist {betraege})")
    check(all(abs(b.betrag) not in (990.0, 1000.0, 1490.0) for b in a.buchungen),
          "kein Uebertrags-/Kontostand-Betrag als Buchung")
    # Kontrollschicht muss aufgehen: 1000 - 10 + 500 = 1490
    check(a.status == STATUS_OK, f"Saldo-Kontrolle OK (Status={a.status})")
    check(all("bertrag" not in b.verwendungszweck.lower() for b in a.buchungen),
          "kein 'Uebertrag' im Verwendungszweck haengengeblieben")


# ---------------------------------------------------------------------------
# FIX 3 -- Bank-Erkennung + Jahr
# ---------------------------------------------------------------------------
def test_liga_erkennung_und_jahr():
    print("\n== FIX 3: LIGA-BANK-Erkennung + korrektes Jahr ==")
    ocr = (
        "LIGA BANK eG Regensburg\nKontoauszug 1/2025  Blatt 1\n"
        "IBAN DE79 7509 0500 0000 1234 56   BLZ 75090500\n"
        "alter Kontostand vom 30.12.2024        5.712,04 H\n"
        "02.01. LASTSCHRIFT Netflix               6,84 S\n"
        "neuer Kontostand vom 31.01.2025        5.705,20 H\n"
    )
    a = parse_auszug(ocr, "liga.pdf", _PROFILE)
    check(a.bank_profil == "LIGA BANK", f"Profil == 'LIGA BANK' (ist '{a.bank_profil}')")
    # Buchung im Januar 2025 (NICHT 2024 aus der 'alter Kontostand vom'-Zeile)
    check(a.buchungen and a.buchungen[0].datum.year == 2025,
          f"Buchungsjahr 2025 (ist {a.buchungen[0].datum.year if a.buchungen else '—'})")


# ---------------------------------------------------------------------------
# Regression: Dez -> Jan Rollover bleibt korrekt
# ---------------------------------------------------------------------------
def test_rollover_regression():
    print("\n== Regression: Smart-Year Dez -> Jan ==")
    ocr = (
        "Sparkasse\nIBAN DE12 3456 7890 1234 5678 90\n"
        "Zeitraum vom 15.12.2023 bis 15.01.2024\n"
        "alter Kontostand 200,00 H\n"
        "18.12. LASTSCHRIFT Netflix 12,99 S\n"
        "05.01. GEHALT Lohn 100,00 H\n"
        "neuer Kontostand 287,01 H\n"
    )
    a = parse_auszug(ocr, "roll.pdf", _PROFILE)
    pruefe_auszug(a)
    check(a.buchungen[0].datum.year == 2023, "Dez-Buchung -> 2023")
    check(a.buchungen[1].datum.year == 2024, "Jan-Buchung -> 2024 (Rollover)")
    check(a.status == STATUS_OK, f"Saldo-Kontrolle OK (Status={a.status})")


if __name__ == "__main__":
    test_soll_haben_vorzeichen()
    test_uebertrag_zeilen_ausgeschlossen()
    test_liga_erkennung_und_jahr()
    test_rollover_regression()
    print("\n" + ("Alle Tests bestanden." if _fehler == 0 else f"{_fehler} FEHLER!"))
    sys.exit(1 if _fehler else 0)
