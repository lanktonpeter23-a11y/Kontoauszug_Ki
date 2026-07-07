#!/usr/bin/env python3
"""Kontoauszug-KI -- lokale Analyse eingescannter deutscher Kontoauszuege.

Ein Aufruf verarbeitet ALLE PDFs im Wurf-Ordner (Standard:
    /data/data/com.termux/files/home/downloads/Kontoauszuege/ ,
zentral in config.BASIS_ORDNER, per KONTOAUSZUEGE_DIR umstellbar)
und erzeugt eine Excel-Auswertung in .../Auswertung/Finanzanalyse.xlsx.
Verarbeitete PDFs werden nach .../verarbeitet/ verschoben (nie geloescht).

Pipeline (deterministisch vor KI):
    EBENE 1  Rendern (poppler/pdftoppm, 300 DPI, Pillow-Vorverarbeitung)
    EBENE 2  OCR (tesseract -l deu)
    EBENE 3  Parsing (regelbasiert: Konto, Jahr, Buchungen, Salden)
    EBENE 4  Kontrollschicht (alt + Summe == neu ?)
    KI       nur Kategorisierung der Verwendungszwecke (Ollama, optional)

Aufruf:
    python analyse.py            # kompletter Lauf inkl. Excel
    python analyse.py --dry-run  # nur parsen + Kontroll-Report, kein Excel
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import traceback
from typing import List

import config
import excel_export
from anonymisierung import (
    anonymisiere_buchungen,
    anonymisiere_wiederkehrer,
    ist_identifizierbar,
)
from buchungsart import extrahiere_art
from categorize import LLMClient
from control import kontroll_report, pruefe_auszug
from journalfelder import extrahiere_felder
from models import (
    KAT_UMBUCHUNG,
    KAT_UNKATEGORISIERT,
    TYP_AUSGABE,
    TYP_EINNAHME,
    TYP_UMBUCHUNG,
    Auszug,
    Buchung,
)
from ocr import bild_zu_text, pruefe_tesseract_sprache
from render import pdf_zu_bildern, vorverarbeiten
from statement_parser import parse_auszug
from turnus import berechne_turnus_und_preise
from wiederkehrer import finde_wiederkehrer


# ===========================================================================
# Pipeline pro PDF
# ===========================================================================
def verarbeite_pdf(pdf_pfad: str, profile) -> Auszug:
    """Fuehrt EBENE 1-4 fuer eine PDF aus und liefert den geprueften Auszug."""
    name = os.path.basename(pdf_pfad)
    print(f"\n=== {name} ===")

    # EBENE 1 -- Rendern
    print("  [1/4] Rendern (pdftoppm, 300 DPI) ...")
    bilder = pdf_zu_bildern(pdf_pfad, config.TEMP_ORDNER)
    if not bilder:
        raise RuntimeError("Keine Seiten gerendert (leere/kaputte PDF?).")

    # EBENE 2 -- OCR (Seite fuer Seite, mit Fortschritt)
    print(f"  [2/4] OCR (tesseract -l {config.TESSERACT_LANG}) ueber {len(bilder)} Seite(n) ...")
    text_teile: List[str] = []
    for i, bild in enumerate(bilder, start=1):
        print(f"        Seite {i}/{len(bilder)} ...")
        vorverarbeiten(bild)
        text_teile.append(bild_zu_text(bild))
    # Seiten mit dem Trenner zusammenfuegen -> dieser String geht 1:1 ins
    # Parsing UND in den OCR-Dump (Seiten getrennt durch ===SEITENENDE===).
    ocr_text = f"\n{config.SEITEN_TRENNER}\n".join(text_teile)

    # OCR-DUMP: den intern erzeugten OCR-Text (nach Pillow-Vorverarbeitung)
    # je PDF wegschreiben -- exakt der String, der ins Parsing geht. Immer an.
    _schreibe_ocr_dump(name, ocr_text)

    # EBENE 3 -- Parsing
    print("  [3/4] Parsing (regelbasiert) ...")
    auszug = parse_auszug(ocr_text, name, profile)
    for b in auszug.buchungen:
        b.quelle_pdf = name

    # EBENE 4 -- Kontrollschicht
    print("  [4/4] Kontrollschicht (Saldo-Pruefung) ...")
    pruefe_auszug(auszug)
    print(kontroll_report(auszug))

    # Aufraeumen der Seitenbilder dieses PDFs.
    for bild in bilder:
        _still_entfernen(bild)
    return auszug


# ===========================================================================
# KI-Kategorisierung + Nachbearbeitung (Typ, Empfaenger, Turnus)
# ===========================================================================
def kategorisiere_und_bereite_auf(buchungen: List[Buchung]) -> None:
    """Setzt Kategorie (KI), Typ, Empfaenger und (via Python) Turnus/Preise."""
    # Nur die noch nicht kategorisierten, EINDEUTIGEN Zwecke an die KI geben.
    offen = [b for b in buchungen if b.kategorie in ("", KAT_UNKATEGORISIERT)]
    eindeutige = sorted({b.verwendungszweck for b in offen if b.verwendungszweck.strip()})
    # KI-Backend ist Konfiguration (config.json) -> austauschbarer LLMClient.
    zuordnung = LLMClient.from_config().categorize(eindeutige)

    for b in offen:
        b.kategorie = zuordnung.get(b.verwendungszweck, KAT_UNKATEGORISIERT)

    # Art + Referenzfelder + Klar-Empfaenger (deterministisch) + Typ ableiten.
    for b in buchungen:
        if not b.art:
            b.art = extrahiere_art(b.verwendungszweck)
        felder = extrahiere_felder(b.verwendungszweck)
        b.empfaenger = felder["empfaenger"]
        b.referenz = felder["referenz"]
        b.mandatsref = felder["mandatsref"]
        b.glaeubiger_id = felder["glaeubiger_id"]
        b.vertragsnr = felder["vertragsnr"]
        if b.kategorie == KAT_UMBUCHUNG:
            b.typ = TYP_UMBUCHUNG
        elif b.betrag >= 0:
            b.typ = TYP_EINNAHME
        else:
            b.typ = TYP_AUSGABE

    # Turnus + Preiserhoehung berechnet Python selbst (nicht die KI).
    berechne_turnus_und_preise(buchungen)


# ===========================================================================
# Hauptablauf
# ===========================================================================
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Lokale Kontoauszug-Analyse (Termux).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur parsen + Kontroll-Report; kein Excel, kein Verschieben.")
    parser.add_argument("--excel", metavar="PFAD",
                        help="Bestehendes (VOLLSTAENDIGES) Journal-Excel einlesen statt PDFs "
                             "zu OCRn und erneut auswerten (Wiederkehrer + Anonymisierung).")
    args = parser.parse_args(argv)

    print("=" * 64)
    print(" Kontoauszug-KI  --  lokale Analyse (Termux)")
    print("=" * 64)

    # --- FEATURE 4: Excel als Input (kein OCR) ---
    if args.excel:
        return _excel_input_lauf(args.excel)

    # Vorpruefung: deutsches Sprachpaket vorhanden?
    if not pruefe_tesseract_sprache():
        print(f"[FEHLER] tesseract-Sprachpaket '{config.TESSERACT_LANG}' nicht gefunden.")
        print("         Bitte README.md -> Setup (deu.traineddata) befolgen.")
        return 2

    os.makedirs(config.BASIS_ORDNER, exist_ok=True)
    pdfs = _finde_pdfs(config.BASIS_ORDNER)
    if not pdfs:
        print(f"\nKeine PDFs in {config.BASIS_ORDNER} gefunden. Nichts zu tun.")
        return 0

    print(f"\n{len(pdfs)} PDF(s) gefunden.\n")
    profile = config.lade_bank_profile()

    alle_neuen: List[Buchung] = []
    verarbeitete_pdfs: List[str] = []
    fehler: List[str] = []
    auszuege: List[Auszug] = []

    # --- EBENE 1-4 pro PDF (robust: eine kaputte Datei bricht nicht ab) ---
    for pdf in pdfs:
        try:
            auszug = verarbeite_pdf(pdf, profile)
            auszuege.append(auszug)
            alle_neuen.extend(auszug.buchungen)
            verarbeitete_pdfs.append(pdf)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"{os.path.basename(pdf)}: {exc}")
            print(f"  [FEHLER] {os.path.basename(pdf)} uebersprungen: {exc}")
            if os.environ.get("KONTO_DEBUG"):
                traceback.print_exc()

    _tmp_aufraeumen()

    if not alle_neuen:
        print("\nKeine Buchungen extrahiert.")
        _abschlussbericht(auszuege, fehler)
        return 0 if not fehler else 1

    # --- DRY-RUN: hier stoppen (kein Excel, kein Verschieben) ---
    if args.dry_run:
        print("\n" + "=" * 64)
        print(" DRY-RUN: Kontrollschicht-Report (kein Excel geschrieben)")
        print("=" * 64)
        for a in auszuege:
            print("\n" + kontroll_report(a))
        _abschlussbericht(auszuege, fehler)
        return 0

    # --- Append: bestehende Buchungen laden + zusammenfuehren ---
    bestehend = excel_export.lade_bestehende_buchungen(config.EXCEL_DATEI)
    if bestehend:
        print(f"\n  [Excel] {len(bestehend)} bestehende Buchungen geladen (Append-Modus).")
    gesamt = excel_export.merge_buchungen(bestehend, alle_neuen)

    # --- KI-Kategorisierung + Referenztrennung + Turnus/Preise ---
    print("\n--- Kategorisierung + Referenzfelder + Turnus ---")
    kategorisiere_und_bereite_auf(gesamt)

    # --- Wiederkehrer gruppieren (am ECHTEN Empfaenger) + Excel schreiben ---
    _schreibe_ausgaben(gesamt)

    # --- verarbeitete PDFs verschieben (nie loeschen) ---
    _verschiebe_verarbeitet(verarbeitete_pdfs)

    _abschlussbericht(auszuege, fehler)
    return 0 if not fehler else 1


# ===========================================================================
# Ausgabe: Wiederkehrer + ZWEI Excel-Dateien (VOLL + ANONYM)
# ===========================================================================
def _schreibe_ausgaben(gesamt: List[Buchung]) -> None:
    """Reihenfolge zwingend: ERST gruppieren (echte Empfaenger), DANN
    anonymisieren -> VOLL + ANONYM schreiben."""
    print("\n--- Wiederkehrer erkennen + Excel schreiben (VOLL + ANONYM) ---")
    wiederkehrer = finde_wiederkehrer(gesamt)               # auf Klarnamen!
    # Nur klar identifizierbare Verpflichtungen ins Sheet -- keine reinen
    # [NAME]-Gruppen (Personen-Empfaenger ohne Firma/Institution).
    wiederkehrer = [g for g in wiederkehrer if ist_identifizierbar(g.empfaenger)]
    print(f"  [Wiederkehrer] {len(wiederkehrer)} identifizierbare Gruppe(n).")

    excel_export.schreibe_excel(config.EXCEL_VOLL, gesamt, wiederkehrer, mit_daten=True)
    print(f"  [Excel] VOLL  : {config.EXCEL_VOLL}")

    # Anonymisierung ist der LETZTE Schritt (nach der Gruppierung).
    anon_buch = anonymisiere_buchungen(gesamt)
    anon_wied = anonymisiere_wiederkehrer(wiederkehrer)
    excel_export.schreibe_excel(config.EXCEL_ANONYM, anon_buch, anon_wied, mit_daten=False)
    print(f"  [Excel] ANONYM: {config.EXCEL_ANONYM}")


def _wirkt_anonymisiert(buchungen: List[Buchung]) -> bool:
    treffer = sum(1 for b in buchungen
                  if "[NAME]" in b.verwendungszweck or "DE**" in b.verwendungszweck)
    return treffer >= max(1, len(buchungen) // 5)


def _excel_input_lauf(pfad: str) -> int:
    """FEATURE 4: bestehendes VOLLSTAENDIGES Journal-Excel erneut auswerten."""
    print("\n--- Excel-Input-Modus ---")
    print("  [Hinweis] Es muss eine VOLLSTAENDIGE (nicht-anonymisierte) Excel sein.")
    print("            Eine bereits anonymisierte Excel kann NICHT gruppiert werden")
    print("            (Empfaenger fehlen) -- rueckwaerts geht nicht.")
    if not os.path.exists(pfad):
        print(f"  [FEHLER] Excel nicht gefunden: {pfad}")
        return 2
    try:
        buchungen = excel_export.lese_journal_excel(pfad)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FEHLER] Excel nicht lesbar: {exc}")
        return 2
    if not buchungen:
        print("  Keine Buchungen im Journal-Sheet gefunden.")
        return 1
    if _wirkt_anonymisiert(buchungen):
        print("  [WARN] Diese Excel wirkt bereits anonymisiert ([NAME]/DE**...). "
              "Die Wiederkehrer-Gruppierung wird dadurch unbrauchbar.")
    print(f"  {len(buchungen)} Buchungen aus dem Journal gelesen.")

    kategorisiere_und_bereite_auf(buchungen)
    _schreibe_ausgaben(buchungen)
    print("\nFertig (Excel-Input).")
    return 0


# ===========================================================================
# Hilfsfunktionen
# ===========================================================================
def _finde_pdfs(ordner: str) -> List[str]:
    """Alle PDFs direkt im Wurf-Ordner (Unterordner werden ausgelassen)."""
    treffer = []
    for muster in ("*.pdf", "*.PDF"):
        treffer.extend(glob.glob(os.path.join(ordner, muster)))
    return sorted(set(treffer))


def _verschiebe_verarbeitet(pdfs: List[str]) -> None:
    os.makedirs(config.VERARBEITET_ORDNER, exist_ok=True)
    for pdf in pdfs:
        ziel = os.path.join(config.VERARBEITET_ORDNER, os.path.basename(pdf))
        ziel = _eindeutiges_ziel(ziel)
        try:
            shutil.move(pdf, ziel)
        except OSError as exc:
            print(f"  [WARN] Konnte {os.path.basename(pdf)} nicht verschieben: {exc}")
    print(f"  [OK] {len(pdfs)} PDF(s) nach {config.VERARBEITET_ORDNER} verschoben.")


def _eindeutiges_ziel(pfad: str) -> str:
    """Verhindert Ueberschreiben, falls gleichnamige PDF schon verarbeitet wurde."""
    if not os.path.exists(pfad):
        return pfad
    basis, ext = os.path.splitext(pfad)
    i = 2
    while os.path.exists(f"{basis}_{i}{ext}"):
        i += 1
    return f"{basis}_{i}{ext}"


def _schreibe_ocr_dump(pdf_name: str, ocr_text: str) -> None:
    """Schreibt den intern erzeugten OCR-Text (Parsing-Input) nach
    Auswertung/ocr/<pdfname>.txt. Immer aktiv; Fehler brechen den Lauf nicht ab.

    So laesst sich der Golden Master an den TATSAECHLICH intern erzeugten OCR-
    Text angleichen (die extern erzeugte Fixture kann abweichen, weil hier mit
    Pillow-Vorverarbeitung geOCRt wird).
    """
    try:
        os.makedirs(config.OCR_ORDNER, exist_ok=True)
        basis = os.path.splitext(os.path.basename(pdf_name))[0]
        ziel = os.path.join(config.OCR_ORDNER, basis + ".txt")
        with open(ziel, "w", encoding="utf-8") as fh:
            fh.write(ocr_text)
        print(f"        OCR-Dump: {ziel}")
    except OSError as exc:
        print(f"        [WARN] OCR-Dump fehlgeschlagen: {exc}")


def _still_entfernen(pfad: str) -> None:
    try:
        os.remove(pfad)
    except OSError:
        pass


def _tmp_aufraeumen() -> None:
    if os.path.isdir(config.TEMP_ORDNER):
        shutil.rmtree(config.TEMP_ORDNER, ignore_errors=True)


def _abschlussbericht(auszuege: List[Auszug], fehler: List[str]) -> None:
    print("\n" + "=" * 64)
    print(" ABSCHLUSSBERICHT")
    print("=" * 64)
    ok = sum(1 for a in auszuege if a.status == "OK")
    pruef = sum(1 for a in auszuege if a.status == "PRUEFEN")
    ohne = sum(1 for a in auszuege if a.status == "OHNE_SALDO_PRUEFUNG")
    print(f"  Auszuege verarbeitet : {len(auszuege)}")
    print(f"    davon OK           : {ok}")
    print(f"    davon PRUEFEN      : {pruef}")
    print(f"    ohne Saldo-Pruefung: {ohne}")
    if pruef:
        print("  >> Bitte Sheet 'Pruefen' kontrollieren (Saldo ging nicht auf).")
    betrag_fehlt = sum(len(a.unvollstaendige) for a in auszuege)
    if betrag_fehlt:
        print(f"    betrag_fehlt Zeilen: {betrag_fehlt} (Buchungszeilen ohne eigenen "
              f"Betrag -- siehe Report je Auszug oben)")
    if fehler:
        print(f"\n  Uebersprungene Dateien ({len(fehler)}):")
        for f in fehler:
            print(f"    - {f}")
    print("=" * 64)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(130)
