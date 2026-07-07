#!/usr/bin/env python3
"""Unit-Tests fuer die vier Erweiterungen (deterministisch, ohne pytest):

    a) Anonymisierung (Personennamen -> [NAME], Firma bleibt, IBAN/BIC maskiert)
    b) Referenz-Extraktion (EREF/MREF/CRED in eigene Felder, Empfaenger rauschfrei)
    c) Buchungsart-Extraktion (LS/UEW/KA/GUT/LOHN/GA)
    d) Wiederkehrer (3x ~30 Tage -> SICHER/monatlich; 2x -> WAHRSCHEINLICH)
    e) Excel-Input (vollstaendiges Journal -> Wiederkehrend-Sheet)
    f) Kein Spaltenueberlauf (leere Betragszelle bleibt echt leer / None)

Aufruf:  python test_features.py
"""

import os
import re
import tempfile
from datetime import date, datetime

from anonymisierung import anonymisiere_buchungen, anonymisiere_text
from buchungsart import extrahiere_art
from journalfelder import extrahiere_felder
from models import Buchung
from wiederkehrer import finde_wiederkehrer, normalisiere_empfaenger

_fehler = 0


def check(bed, name):
    global _fehler
    if bed:
        print(f"  [OK ] {name}")
    else:
        _fehler += 1
        print(f"  [FEHLER] {name}")


def test_a_anonymisierung():
    print("\n== a) Anonymisierung ==")
    p = anonymisiere_text("EURO-UEBERWEISUNG PN:900 Veronika Weinzierl Pflegeversicherung "
                          "IBAN: DE66750905000990099716 BIC: GENODEF1S05")
    check("[NAME]" in p and "Weinzierl" not in p, "Person 'Veronika Weinzierl' -> [NAME]")
    check("DE**...9716" in p, "IBAN auf letzte 4 maskiert")
    check("GENODEF1S05" not in p, "BIC maskiert")

    f = anonymisiere_text("LASTSCHRIFT PN:931 AXA Versicherung AG "
                          "IBAN: DE04300500000000444166 BIC: WELADEDDXXX")
    check("AXA Versicherung AG" in f and "[NAME]" not in f, "Firma 'AXA Versicherung AG' bleibt")
    check("DE**...4166" in f, "IBAN maskiert (Firma)")


def test_b_referenzen():
    print("\n== b) Referenz-Extraktion ==")
    f = extrahiere_felder(
        "LASTSCHRIFT PN:931 AXA Versicherung Aktiengesellschaft BOXflex 56001383962 "
        "BTR. 01/25 EREF: 6600325 MREF: 21052299391 CRED: DE23G0100000066097 "
        "IBAN: DE04300500000000444166 BIC: WELADEDDXXX")
    check(f["referenz"] == "6600325", f"EREF -> Referenz ({f['referenz']})")
    check(f["mandatsref"] == "21052299391", f"MREF -> Mandatsref ({f['mandatsref']})")
    check(f["glaeubiger_id"] == "DE23G0100000066097", f"CRED -> Glaeubiger-ID ({f['glaeubiger_id']})")
    check("56001383962" in f["vertragsnr"], f"Vertragsnr erkannt ({f['vertragsnr']})")
    check(f["empfaenger"] == "AXA Versicherung Aktiengesellschaft",
          f"Empfaenger rauschfrei ({f['empfaenger']})")
    check("EREF" not in f["empfaenger"] and "56001383962" not in f["empfaenger"],
          "keine Referenznummern im Empfaenger")


def test_c_art():
    print("\n== c) Buchungsart ==")
    faelle = [
        ("LASTSCHRIFT PN:931 X", "LS"),
        ("EURO-UEBERWEISUNG PN:900 X", "UEW"),
        ("EURO-VUVEBERWEISUNG PN:900 X", "UEW"),
        ("Kartenzahlung girocard PN:931 REWE", "KA"),
        ("Kartenzahlung Maestro PN:931 DZ", "KA"),
        ("GUTSCHRIFT PN:1234 Finanzamt", "GUT"),
        ("LOHN/GEHALT PN:1234 Arbeitgeber", "LOHN"),
        ("Auszahlung girocard PN:931 GA", "GA"),
        ("SPARRATE etwas", "SONST"),
    ]
    for z, exp in faelle:
        check(extrahiere_art(z) == exp, f"{exp:5} <- {z[:32]}")


def test_d_wiederkehrer():
    print("\n== d) Wiederkehrer ==")
    drei = [Buchung(konto="X", datum=d, verwendungszweck="LASTSCHRIFT PN:931 Netflix Europe",
                    betrag=-12.99)
            for d in (date(2025, 1, 5), date(2025, 2, 4), date(2025, 3, 6))]
    zwei = [Buchung(konto="X", datum=d, verwendungszweck="EURO-UEBERWEISUNG PN:900 Miete Verwaltung",
                    betrag=-800.0)
            for d in (date(2025, 1, 2), date(2025, 2, 2))]
    res = {g.schluessel: g for g in finde_wiederkehrer(drei + zwei)}
    net = next((g for k, g in res.items() if "netflix" in k), None)
    mie = next((g for k, g in res.items() if "miete" in k), None)
    check(net is not None and net.klassifikation == "SICHER" and net.turnus == "monatlich",
          f"3x ~30 Tage -> SICHER/monatlich ({net.klassifikation}/{net.turnus if net else '-'})")
    check(mie is not None and mie.klassifikation == "WAHRSCHEINLICH",
          f"2x -> WAHRSCHEINLICH ({mie.klassifikation if mie else '-'})")


def test_e_excel_input():
    print("\n== e) Excel-Input ==")
    import excel_export
    from openpyxl import Workbook
    pfad = os.path.join(tempfile.mkdtemp(), "journal_in.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Journal"
    ws.append(["Datum", "Art", "Empfaenger", "Einnahme", "Ausgabe", "Typ",
               "Status", "Verwendungszweck_voll"])
    zeilen = [
        (datetime(2025, 1, 5), "LS", "Netflix", None, -12.99, "Ausgabe", "OK",
         "LASTSCHRIFT PN:931 Netflix Europe"),
        (datetime(2025, 2, 4), "LS", "Netflix", None, -12.99, "Ausgabe", "OK",
         "LASTSCHRIFT PN:931 Netflix Europe"),
        (datetime(2025, 3, 6), "LS", "Netflix", None, -12.99, "Ausgabe", "OK",
         "LASTSCHRIFT PN:931 Netflix Europe"),
    ]
    for z in zeilen:
        ws.append(z)
    wb.save(pfad)

    buch = excel_export.lese_journal_excel(pfad)
    check(len(buch) == 3, f"3 Buchungen aus Journal gelesen ({len(buch)})")
    check(all(round(b.betrag, 2) == -12.99 for b in buch), "Betraege aus Ausgabe-Spalte (negativ)")
    gr = finde_wiederkehrer(buch)
    check(len(gr) == 1 and gr[0].klassifikation == "SICHER",
          f"Wiederkehrer-Gruppe SICHER erzeugt ({len(gr)} Gruppe(n))")


def test_f_kein_ueberlauf():
    print("\n== f) Kein Spaltenueberlauf / echte Leerzellen ==")
    import excel_export
    from openpyxl import load_workbook
    pfad = os.path.join(tempfile.mkdtemp(), "voll.xlsx")
    buch = [
        Buchung(konto="X", datum=date(2025, 1, 2), verwendungszweck="GUTSCHRIFT Lohn",
                betrag=1400.0, art="GUT", empfaenger="Arbeitgeber", typ="Einnahme", status="OK"),
        Buchung(konto="X", datum=date(2025, 1, 3), verwendungszweck="LASTSCHRIFT Strom",
                betrag=-80.0, art="LS", empfaenger="Stadtwerke", typ="Ausgabe", status="OK"),
    ]
    excel_export.schreibe_excel(pfad, buch, wiederkehrer=[], mit_daten=True)
    wb = load_workbook(pfad)
    ws = wb["Journal"]
    # Spalte 4 = Einnahme, 5 = Ausgabe
    einn = [ws.cell(row=r, column=4).value for r in (2, 3)]
    ausg = [ws.cell(row=r, column=5).value for r in (2, 3)]
    check(einn[0] == 1400 and einn[1] is None, "leere Einnahme-Zelle ist echt None")
    check(ausg[0] is None and ausg[1] == -80, "leere Ausgabe-Zelle ist echt None")
    # Summierbar (Python-seitig, weil keine Strings/Leerzeichen in den Zellen)
    summe = sum(v for v in einn + ausg if isinstance(v, (int, float)))
    check(round(summe, 2) == 1320.0, f"Betragsspalten summierbar ({summe})")
    check(ws.auto_filter.ref is not None, "Autofilter gesetzt")
    check(ws.freeze_panes == "A2", "erste Zeile fixiert")


def test_g_anonymisierung_hart():
    print("\n== g) Anonymisierung gehärtet: Groß-/Kleinschreibung, keine Leaks ==")
    # Gleiche Person in ALL-CAPS / lower / Title -> alle [NAME].
    for v in ("ULRIKE WEINZIERL", "ulrike weinzierl", "Ulrike Weinzierl"):
        t = anonymisiere_text(f"EURO-UEBERWEISUNG PN:900 {v} IBAN: DE66750905000990099716")
        check("[NAME]" in t and "WEINZIERL" not in t.upper(), f"'{v}' -> [NAME]")
    # ALL-CAPS ist KEIN Firmenmarker mehr.
    t2 = anonymisiere_text("GUTSCHRIFT PN:1234 ALEXANDER NEUMANN")
    check("[NAME]" in t2 and "NEUMANN" not in t2.upper(), "'ALEXANDER NEUMANN' (all-caps) -> [NAME]")
    # Echte Firma bleibt.
    f = anonymisiere_text("LASTSCHRIFT PN:931 AXA Versicherung AG IBAN: DE04300500000000444166")
    check("AXA Versicherung AG" in f, "Firma 'AXA Versicherung AG' bleibt")

    # Blockliste: nach Anonymisierung darf KEIN Klarname mehr vorkommen.
    namen = ["WEINZIERL", "NEUMANN", "NEPPL", "POLEDNIK", "PERNSTECHER",
             "HUBER", "DUSL", "WAMSLER"]
    proben = [
        Buchung(konto="X", datum=date(2025, 1, 2), betrag=-40.0,
                empfaenger="ULRIKE WEINZIERL",
                verwendungszweck="EURO-UEBERWEISUNG PN:900 ULRIKE WEINZIERL IBAN: DE9075062026"),
        Buchung(konto="X", datum=date(2025, 1, 2), betrag=-10.0,
                empfaenger="Veronika Weinzierl Pflegeversicherung",
                verwendungszweck="EURO-UEBERWEISUNG PN:900 Veronika Weinzierl Pflegeversicherung"),
        Buchung(konto="X", datum=date(2025, 1, 2), betrag=-180.0, empfaenger="Sabine Neppl",
                verwendungszweck="EURO-UEBERWEISUNG PN:900 Sabine Neppl HuTa"),
        Buchung(konto="X", datum=date(2025, 1, 7), betrag=-1713.6, empfaenger="Marc Polednik",
                verwendungszweck="EURO-UEBERWEISUNG PN:801 Marc Polednik RE 2024"),
        Buchung(konto="X", datum=date(2025, 1, 9), betrag=-30.0, empfaenger="Kristina Huber",
                verwendungszweck="EURO-UEBERWEISUNG PN:801 Kristina Huber Prowin"),
        Buchung(konto="X", datum=date(2025, 1, 23), betrag=-17.9, empfaenger="Wamsler",
                verwendungszweck="EURO-UEBERWEISUNG PN:801 Wamsler RE"),
        Buchung(konto="X", datum=date(2025, 1, 29), betrag=-110.0, empfaenger="vhs Rottenburg",
                verwendungszweck="EURO-UEBERWEISUNG PN:801 vhs Rottenburg R24 Alexander Neumann"),
    ]
    anon = anonymisiere_buchungen(proben)
    text_all = " || ".join(k.verwendungszweck + " | " + k.empfaenger for k in anon)
    leaks = [n for n in namen if re.search(rf"\b{n}\b", text_all, re.IGNORECASE)]
    check(not leaks, f"ANONYM-Ausgabe enthält keinen Klarnamen (Leaks: {leaks})")


def test_h_normalisierung_konsistent():
    print("\n== h) Wiederkehrer-Normalisierung case-insensitiv (FEHLER 3) ==")
    a = normalisiere_empfaenger("EURO-UEBERWEISUNG PN:900 ULRIKE WEINZIERL Miete")
    b = normalisiere_empfaenger("EURO-UEBERWEISUNG PN:900 Ulrike Weinzierl Miete")
    c = normalisiere_empfaenger("euro-ueberweisung pn:900   ulrike   weinzierl   miete")
    check(a == b == c and a != "", f"gleiche Person -> gleicher Schluessel ('{a}')")


if __name__ == "__main__":
    test_a_anonymisierung()
    test_b_referenzen()
    test_c_art()
    test_d_wiederkehrer()
    test_e_excel_input()
    test_f_kein_ueberlauf()
    test_g_anonymisierung_hart()
    test_h_normalisierung_konsistent()
    print("\n" + ("Alle Feature-Tests bestanden." if _fehler == 0 else f"{_fehler} FEHLER!"))
    raise SystemExit(1 if _fehler else 0)
