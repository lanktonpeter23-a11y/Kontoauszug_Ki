#!/usr/bin/env python3
"""Unit-Tests fuer die deterministische Parsing-/Kontroll-Logik.

Bewusst OHNE pytest/Pillow/openpyxl -- laeuft ueberall (auch in Termux) via:

    python test_parser.py

Deckt insbesondere die Haertungs-Fixes ab:
  * FIX 1  Soll/Haben (S/H) als primaere Vorzeichenquelle
  * FIX 2  Uebertrags-/Kontostand-Zeilen sind KEINE Buchungen
  * FIX 3  deterministische Bank-Erkennung (LIGA BANK) + korrektes Jahr
"""

import os
import re
import sys

import config
from control import pruefe_auszug
from models import STATUS_OK, TYP_AUSGABE, TYP_EINNAHME
from statement_parser import parse_auszug
from statement_parser import _sammle_balance_werte
from textutils import finde_letzten_betrag, parse_german_amount

_PROFILE = config.lade_bank_profile()
_HIER = os.path.dirname(os.path.abspath(__file__))
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
# NACHZUG 2 -- Bu-Tag ist Primaerkriterium: Uebertrags-/SPARRATE-Zeilen ohne
# fuehrendes TT.MM. sind KEINE Buchungen und ihr Betrag darf keiner Buchung
# zugeordnet werden.
# ---------------------------------------------------------------------------
def test_bu_tag_primaer_und_kein_fremdbetrag():
    print("\n== NACHZUG 2: Bu-Tag-Regel + kein Fremdbetrag (Uebertrag/SPARRATE) ==")
    # Genau der problematische Zeilenblock aus echtem LIGA-OCR:
    ocr = (
        "LIGA BANK eG\nKontoauszug 1/2025\n"
        "IBAN DE79 7509 0500 0000 1234 56\n"
        "alter Kontostand vom 30.12.2024        5.712,04 H\n"
        "02.01. 02.01. LASTSCHRIFT PN:931        52,65 S\n"
        "Uebertrag auf Blatt 2                   5.659,39 H\n"
        "Uebertrag von Blatt 1                   5.659,39 H\n"
        "05.01. 05.01. SPARRATE /*DA-2*                    \n"   # Buchung OHNE eigenen Betrag
        "        IBAN: DE02 1000 0000 0000 0000 00\n"           # Folgezeile (kein Betrag)
        "        Referenz 30.12 11.18\n"                         # Folgezeile (kein Betrag)
        "07.01. 07.01. GEHALT Arbeitgeber       500,00 H\n"
        "neuer Kontostand vom 31.01.2025        6.159,39 H\n"
    )
    # Gueltige Buchungen mit eigenem Betrag: -52,65 und +500,00 = 447,35
    # 5.712,04 + 447,35 = 6.159,39 == neuer Kontostand -> OK
    a = parse_auszug(ocr, "liga8.pdf", _PROFILE)
    pruefe_auszug(a)
    betraege = sorted(round(b.betrag, 2) for b in a.buchungen)

    check(betraege == [-52.65, 500.0], f"nur eigene Betraege [-52.65, 500] (ist {betraege})")
    check(all(abs(b.betrag) not in (5659.39, 5712.04, 6159.39) for b in a.buchungen),
          "kein Uebertrags-/Kontostand-Betrag als Buchung")
    # SPARRATE ohne Bu-Tag-Betrag darf NICHT den Uebertrag (5.659,39) bekommen
    check(all(abs(b.betrag) != 5659.39 for b in a.buchungen),
          "SPARRATE hat NICHT den Uebertragsbetrag uebernommen")
    check(any("SPARRATE" in u for u in a.unvollstaendige),
          "SPARRATE als betrag_fehlt gemeldet")
    check(a.status == STATUS_OK, f"Saldo-Kontrolle OK (Status={a.status}, Diff={a.saldo_differenz})")


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


# ---------------------------------------------------------------------------
# GOLDEN MASTER -- echter (anonymisierter) LIGA-Auszug, 7 Seiten.
# PFLICHT-Test fuer JEDE kuenftige Parser-Aenderung: der komplette Auszug
# MUSS cent-genau auf OK gehen. Seite 1 ist im OCR delaminiert (Spalten
# getrennt), Seiten 2-7 sind saubere Zeilen mit Kartenzahlungs-Folgezeilen.
# ---------------------------------------------------------------------------
def test_golden_master_liga():
    print("\n== GOLDEN MASTER: echter LIGA-Auszug (Fixture) ==")
    pfad = os.path.join(_HIER, "tests", "fixtures", "liga_ocr_anonymized.txt")
    if not os.path.exists(pfad):
        check(False, f"Fixture fehlt: {pfad}")
        return
    with open(pfad, encoding="utf-8") as fh:
        ocr = fh.read()

    a = parse_auszug(ocr, "liga_golden.pdf", _PROFILE)
    pruefe_auszug(a)
    summe = round(sum(b.betrag for b in a.buchungen), 2)

    # (a) CENT-GENAU: alter Saldo + Summe = neuer Saldo
    check(a.bank_profil == "LIGA BANK", f"Bank = LIGA BANK (ist {a.bank_profil})")
    check(a.jahr == 2025, f"Jahr = 2025 (ist {a.jahr})")
    check(a.saldo_alt == 5712.04, f"saldo_alt = 5712.04 (ist {a.saldo_alt})")
    check(a.saldo_neu == 11681.18, f"saldo_neu = 11681.18 (ist {a.saldo_neu})")
    check(summe == 5969.14, f"Summe Buchungen = 5969.14 (ist {summe})")
    check(a.status == STATUS_OK, f"Status OK, Diff {a.saldo_differenz} (ist {a.status})")
    check(round((a.saldo_alt or 0) + summe, 2) == a.saldo_neu, "alt + Summe == neu (cent-genau)")

    # (b) KEINE betrag_fehlt-Zeilen (Kartenzahlungs-Folgezeilen mit Lang-Datum
    #     duerfen NIE eigene Buchung sein).
    check(len(a.unvollstaendige) == 0, f"0 betrag_fehlt (ist {len(a.unvollstaendige)})")
    langdatum_start = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{4}")
    check(not any(langdatum_start.match(b.roh_zeile) for b in a.buchungen if b.roh_zeile),
          "keine Buchung startet mit einem Lang-Datum TT.MM.JJJJ")

    # (c) Uebertrag-/Kontostand-Werte tauchen NIE als Buchungsbetrag auf.
    balance = _sammle_balance_werte(ocr) | {5712.04, 11681.18}
    treffer = [b for b in a.buchungen if round(abs(b.betrag), 2) in balance]
    check(not treffer, f"kein Uebertrags-/Kontostand-Betrag als Buchung ({len(treffer)} Treffer)")

    # Regressions-Anker: exakte Buchungszahl (die zwei 'Geb.Uebernahme LIGA'-
    # Zeilen sind Gebuehren-UEBERNAHMEN der Bank, keine eigenen Buchungen).
    check(len(a.buchungen) == 61, f"exakt 61 Buchungen (ist {len(a.buchungen)})")

    print(f"     -> {len(a.buchungen)} Buchungen, Summe {summe}, "
          f"alt {a.saldo_alt} + Summe = {a.saldo_neu}, Status {a.status}")


if __name__ == "__main__":
    test_soll_haben_vorzeichen()
    test_uebertrag_zeilen_ausgeschlossen()
    test_bu_tag_primaer_und_kein_fremdbetrag()
    test_liga_erkennung_und_jahr()
    test_rollover_regression()
    test_golden_master_liga()
    print("\n" + ("Alle Tests bestanden." if _fehler == 0 else f"{_fehler} FEHLER!"))
    sys.exit(1 if _fehler else 0)
