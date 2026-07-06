"""EBENE 1 -- Rendern.

Jede PDF-Seite wird mit poppler (pdftoppm) bei 300 DPI in ein PNG gerendert
und anschliessend mit Pillow fuer besseren OCR-Input vorverarbeitet:
Graustufen, Kontrast/Schwellwert, leichtes Denoising.
"""

from __future__ import annotations

import glob
import os
import subprocess
from typing import List

from PIL import Image, ImageFilter, ImageOps

import config


def pdf_zu_bildern(pdf_pfad: str, ziel_ordner: str) -> List[str]:
    """Rendert alle Seiten einer PDF zu PNG-Dateien (300 DPI).

    Nutzt pdftoppm aus dem poppler-Paket. Gibt die Liste der erzeugten
    Bildpfade (sortiert nach Seitennummer) zurueck. Wirft bei Fehler eine
    Exception, die der Aufrufer faengt (kaputte PDF -> ueberspringen).
    """
    os.makedirs(ziel_ordner, exist_ok=True)
    basis = os.path.splitext(os.path.basename(pdf_pfad))[0]
    prefix = os.path.join(ziel_ordner, basis)

    # pdftoppm -r 300 -png input.pdf /ziel/basis  ->  basis-1.png, basis-2.png ...
    cmd = [config.PDFTOPPM_BIN, "-r", str(config.DPI), "-png", pdf_pfad, prefix]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    bilder = sorted(
        glob.glob(prefix + "-*.png"),
        key=_seiten_sortierschluessel,
    )
    return bilder


def _seiten_sortierschluessel(pfad: str) -> int:
    """Extrahiert die Seitennummer aus 'basis-12.png' fuer korrekte Reihenfolge."""
    name = os.path.splitext(os.path.basename(pfad))[0]
    ziffern = name.rsplit("-", 1)[-1]
    return int(ziffern) if ziffern.isdigit() else 0


def vorverarbeiten(bild_pfad: str) -> str:
    """Bereitet ein Seitenbild fuer OCR auf.

    Schritte:
      1. Graustufen
      2. Autokontrast (haucht blassen Scans mehr Kontrast ein)
      3. leichtes Denoising (MedianFilter gegen Scan-Rauschen)
      4. Schwellwert -> nahezu schwarz/weiss (bessere Zeichenkanten)
    Ueberschreibt das Bild und gibt den Pfad zurueck. Bei Problemen wird
    das Originalbild unveraendert zurueckgegeben (robust).
    """
    try:
        img = Image.open(bild_pfad)
        img = ImageOps.grayscale(img)                 # 1. Graustufen
        img = ImageOps.autocontrast(img, cutoff=1)    # 2. Kontrast
        img = img.filter(ImageFilter.MedianFilter(size=3))  # 3. Denoising

        # 4. Schwellwert: Punkte unter ~150 -> schwarz, darueber -> weiss.
        #    point() mit einer Lookup-Tabelle ist schnell und ohne numpy.
        schwelle = 150
        img = img.point(lambda p: 0 if p < schwelle else 255)

        img.save(bild_pfad)
        return bild_pfad
    except Exception as exc:  # noqa: BLE001 - OCR soll trotzdem versucht werden
        print(f"    [WARN] Vorverarbeitung fehlgeschlagen ({exc}) -> Originalbild.")
        return bild_pfad
