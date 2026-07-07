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

from anonymisierung import (
    anonymisiere_buchungen,
    anonymisiere_text,
    anonymisiere_wiederkehrer,
)
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
    print("\n== d) Wiederkehrer: >=3 monatlich -> SICHER; 2x faellt raus ==")
    drei = [Buchung(konto="X", datum=d, verwendungszweck="LASTSCHRIFT PN:931 Netflix Europe",
                    betrag=-12.99)
            for d in (date(2025, 1, 5), date(2025, 2, 4), date(2025, 3, 6))]
    zwei = [Buchung(konto="X", datum=d, verwendungszweck="LASTSCHRIFT PN:931 Fitness Studio",
                    betrag=-29.99)
            for d in (date(2025, 1, 2), date(2025, 2, 2))]
    res = finde_wiederkehrer(drei + zwei)
    net = next((g for g in res if "netflix" in g.schluessel), None)
    check(net is not None and net.klassifikation == "SICHER" and net.turnus == "monatlich",
          f"Netflix 3x -> SICHER/monatlich ({net.klassifikation if net else '-'})")
    check(not any("fitness" in g.schluessel for g in res),
          "2-Vorkommen-Gruppe faellt raus (>=3 noetig)")


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
    excel_export.schreibe_excel(pfad, buch, wiederkehrer_je_konto={}, mit_daten=True)
    wb = load_workbook(pfad)
    ws = wb[[n for n in wb.sheetnames if n.startswith("Journal")][0]]
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


def _b(datum, betrag, empf, mref="", zweck=""):
    return Buchung(konto="X", datum=datum, betrag=betrag, empfaenger=empf,
                   mandatsref=mref, verwendungszweck=zweck or empf)


def test_i_gruppierung_kaskade():
    print("\n== i) Gruppierung: Referenz-Kaskade, Betrag nie harter Schluessel ==")
    # AXA: gleiche MREF, wechselnder Betrag, monatlich (>=3) -> EINE Gruppe
    axa = [_b(d, x, "AXA Versicherung Aktiengesellschaft", "21052299391")
           for d, x in ((date(2025, 1, 2), -6.84), (date(2025, 2, 2), -7.00),
                        (date(2025, 3, 2), -6.84))]
    # Vattenfall: variabler Betrag, gleiche MREF, monatlich -> EINE Gruppe
    vat = [_b(d, x, "Vattenfall Europe Sales", "M002000003885551")
           for d, x in ((date(2025, 1, 8), -86.0), (date(2025, 2, 8), -92.5), (date(2025, 3, 8), -79.9))]
    # Kartenzahlung ohne Referenz, variabler Betrag, unregelmaessig -> KEINE Gruppe
    karte = [_b(d, x, "REWE Markt", "", "Kartenzahlung girocard PN:931 REWE")
             for d, x in ((date(2025, 1, 3), -45.6), (date(2025, 1, 17), -12.3), (date(2025, 1, 28), -88.9))]
    res = finde_wiederkehrer(axa + vat + karte)
    axa_g = [g for g in res if "AXA" in g.empfaenger]
    vat_g = [g for g in res if "Vattenfall" in g.empfaenger]
    check(len(axa_g) == 1, f"AXA (gleiche MREF, 6,84 vs 7,00) = EINE Gruppe ({len(axa_g)})")
    check(len(vat_g) == 1 and vat_g[0].turnus == "monatlich",
          f"Vattenfall (variabel, MREF) = EINE Gruppe monatlich ({len(vat_g)})")
    check(vat_g and vat_g[0].schwankung, "Vattenfall: Betragsschwankung=ja (Preishinweis)")
    check(not any("REWE" in g.empfaenger for g in res),
          "Kartenzahlung (variabel/unregelmaessig, keine Ref) -> KEINE Gruppe")


def test_j_firma_ungespalten():
    print("\n== j) Keine Ueberschwaerzung in Firmennamen ==")
    for e in ["AMAZON PAYMENTS EUROPE S.C.A.", "PayPal Europe S.a.r.l. et Cie S.C.A",
              "Autoh.Nachtmann OHG", "DUSL GMBH", "Swiss Life Lebensversicherung SE"]:
        anon = anonymisiere_text(e, feld=True)
        check("[NAME]" not in anon, f"Firma ungespalten: {e!r} -> {anon!r}")


def test_k_name_in_allen_feldern():
    print("\n== k) Name in ALLEN Feldern getilgt ==")
    b = Buchung(konto="X", datum=date(2025, 1, 2), betrag=-50.0, empfaenger="Sabine Neppl",
                verwendungszweck="EURO-UEBERWEISUNG PN:900 Sabine Neppl HuTa",
                referenz="Sabine Neppl 12345", mandatsref="M999")
    k = anonymisiere_buchungen([b])[0]
    blob = " | ".join([k.empfaenger, k.verwendungszweck, k.referenz, k.mandatsref])
    check("Neppl" not in blob and "Sabine" not in blob,
          f"kein Klarname in irgendeinem Feld ({blob})")


def test_l_personen_nicht_im_sheet():
    print("\n== l) Personen-Empfaenger -> nicht im Wiederkehrend-Sheet ==")
    from anonymisierung import ist_identifizierbar
    p1 = [_b(d, -100.0, "Anna Schmidt", "", "EURO-UEBERWEISUNG PN:801 Anna Schmidt Miete")
          for d in (date(2025, 1, 2), date(2025, 2, 2), date(2025, 3, 2))]
    p2 = [_b(d, -50.0, "Bernd Mueller", "", "EURO-UEBERWEISUNG PN:801 Bernd Mueller Sparen")
          for d in (date(2025, 1, 5), date(2025, 2, 5), date(2025, 3, 5))]
    axa = [_b(d, -6.84, "AXA Versicherung AG", "21052299391",
              "LASTSCHRIFT PN:931 AXA Versicherung AG")
           for d in (date(2025, 1, 2), date(2025, 2, 2), date(2025, 3, 2))]
    roh = finde_wiederkehrer(p1 + p2 + axa)
    sheet = [g for g in roh if ist_identifizierbar(g.empfaenger)]
    check(any("AXA" in g.empfaenger for g in sheet), "AXA (Firma) bleibt im Sheet")
    check(not any(("Schmidt" in g.empfaenger or "Mueller" in g.empfaenger) for g in sheet),
          "reine Personen-Gruppen ([NAME]) fallen aus dem Sheet")


def test_m_kein_namensrest_substring():
    print("\n== m) FINALE Blockliste: kein Namensrest als Substring (FEHLER 1) ==")
    b = Buchung(konto="X", datum=date(2025, 1, 2), betrag=-50.0,
                empfaenger="Ulrike Weinzierl Abrechnung",
                verwendungszweck="EURO-UEBERWEISUNG PN:900 Ulrike Weinzierl Abrechnung "
                                 "WEINZIERLGLUED von ULRIKE",
                referenz="Weinzierl-Ref-9", mandatsref="WEINZIERL99")
    k = anonymisiere_buchungen([b])[0]
    blob = " | ".join([k.empfaenger, k.verwendungszweck, k.referenz, k.mandatsref,
                        k.glaeubiger_id, k.vertragsnr]).lower()
    for teil in ("weinzierl", "ulrike"):
        check(teil not in blob, f"'{teil}' in KEINEM Feld (auch nicht als Substring)")


def test_n_wiederkehrer_nur_verpflichtungen():
    print("\n== n) Wiederkehrer = nur echte Verpflichtungen (FEHLER 2) ==")
    axa = [_b(d, x, "AXA Versicherung AG", "21052299391",
              "LASTSCHRIFT PN:931 AXA Versicherung AG")
           for d, x in ((date(2025, 1, 2), -6.84), (date(2025, 2, 2), -7.0), (date(2025, 3, 2), -7.0))]
    # Amazon: (evtl. gleiche MREF, aber) UNREGELMAESSIGE Kauf-Abstaende -> raus
    amz = [_b(d, x, "AMAZON PAYMENTS EUROPE", "vYv80KxdWOTY6U",
              "LASTSCHRIFT PN:931 AMAZON PAYMENTS EUROPE")
           for d, x in ((date(2025, 1, 2), -34.94), (date(2025, 1, 7), -1.99),
                        (date(2025, 1, 8), -6.42), (date(2025, 1, 17), -30.69),
                        (date(2025, 1, 21), -26.79))]
    # 2 zufaellige Tankzahlungen ohne Referenz -> raus (kein belegter Rhythmus)
    tank = [_b(d, x, "Tankstelle", "", "Kartenzahlung girocard PN:931 Tankstelle")
            for d, x in ((date(2025, 1, 23), -77.21), (date(2025, 1, 28), -74.01))]
    res = finde_wiederkehrer(axa + amz + tank)
    namen = [g.empfaenger for g in res]
    check(any("AXA" in n for n in namen), "AXA (stabile MREF, monatlich) = Wiederkehrer")
    check(not any("AMAZON" in n for n in namen),
          "Amazon (unregelmaessige Abstaende) = KEIN Wiederkehrer")
    check(not any("Tankstelle" in n for n in namen),
          "Tankstelle (2x, keine Referenz) = KEIN Wiederkehrer")


def test_o_multi_konto():
    print("\n== o) Multi-Konto-Trennung (Feature 5A) ==")
    import excel_export
    from anonymisierung import anonymisiere_buchungen, anonymisiere_wiederkehrer, ist_identifizierbar
    from openpyxl import load_workbook
    # Zwei Konten, je eigene monatliche Verpflichtung + eigener Saldo-Status.
    k1 = "DE79750903000001320424"
    k2 = "DE11220000000000009999"
    buch = []
    for d in (date(2025, 1, 5), date(2025, 2, 5), date(2025, 3, 5)):
        buch.append(Buchung(konto=k1, datum=d, betrag=-12.99, status="OK",
                            empfaenger="Netflix", mandatsref="NFLX-1",
                            verwendungszweck="LASTSCHRIFT PN:931 Netflix Europe"))
    for d in (date(2025, 1, 8), date(2025, 2, 8), date(2025, 3, 8)):
        buch.append(Buchung(konto=k2, datum=d, betrag=-25.0, status="PRUEFEN",
                            empfaenger="congstar", mandatsref="CS-9",
                            verwendungszweck="LASTSCHRIFT PN:931 congstar"))

    # Wiederkehrer je Konto (wie in analyse._schreibe_ausgaben).
    konten = {}
    for b in buch:
        konten.setdefault(b.konto, []).append(b)
    wied = {k: [g for g in finde_wiederkehrer(kb) if ist_identifizierbar(g.empfaenger)]
            for k, kb in konten.items()}

    pfad = os.path.join(tempfile.mkdtemp(), "voll.xlsx")
    excel_export.schreibe_excel(pfad, buch, wied, mit_daten=True, anonym=False)
    wb = load_workbook(pfad)
    check(wb.sheetnames[0] == "Konten", "Konten-Uebersicht ist das erste Sheet")
    check("Journal_0424" in wb.sheetnames and "Journal_9999" in wb.sheetnames,
          f"2 getrennte Journal-Sheets ({[n for n in wb.sheetnames if n.startswith('Journal')]})")
    check("Wiederkehrend_0424" in wb.sheetnames and "Wiederkehrend_9999" in wb.sheetnames,
          "2 getrennte Wiederkehrend-Sheets")
    # Journal_0424 enthaelt nur Konto 1 (3 Zeilen)
    j1 = wb["Journal_0424"]
    check(j1.max_row == 4, f"Journal_0424 hat 3 Buchungen ({j1.max_row - 1})")
    # Konten-Sheet: Status je Konto (OK vs PRUEFEN) getrennt
    ku = wb["Konten"]
    zeilen = {ku.cell(row=r, column=1).value: ku.cell(row=r, column=6).value
              for r in range(2, ku.max_row + 1)}
    check(any(v == "PRUEFEN" for v in zeilen.values()) and any(v == "OK" for v in zeilen.values()),
          f"Saldo-Status je Konto getrennt ({zeilen})")

    # ANONYM: Kontonummern maskiert, Sheet-Struktur identisch
    ap = os.path.join(tempfile.mkdtemp(), "anon.xlsx")
    aw = {k: anonymisiere_wiederkehrer(v) for k, v in wied.items()}
    excel_export.schreibe_excel(ap, anonymisiere_buchungen(buch), aw, mit_daten=False, anonym=True)
    wba = load_workbook(ap)
    kua = wba["Konten"]
    labels = [kua.cell(row=r, column=1).value for r in range(2, kua.max_row + 1)]
    check(all(str(l).startswith("****") for l in labels), f"ANONYM: Konten maskiert ({labels})")
    check("_Daten" not in wba.sheetnames, "ANONYM ohne _Daten")


if __name__ == "__main__":
    test_a_anonymisierung()
    test_b_referenzen()
    test_c_art()
    test_d_wiederkehrer()
    test_e_excel_input()
    test_f_kein_ueberlauf()
    test_g_anonymisierung_hart()
    test_h_normalisierung_konsistent()
    test_i_gruppierung_kaskade()
    test_j_firma_ungespalten()
    test_k_name_in_allen_feldern()
    test_l_personen_nicht_im_sheet()
    test_m_kein_namensrest_substring()
    test_n_wiederkehrer_nur_verpflichtungen()
    test_o_multi_konto()
    print("\n" + ("Alle Feature-Tests bestanden." if _fehler == 0 else f"{_fehler} FEHLER!"))
    raise SystemExit(1 if _fehler else 0)
