"""EBENE 2 -- OCR.

tesseract mit deutschem Sprachpaket ('deu') extrahiert den Text pro Seite.
Das ist die deterministische Textquelle fuer das regelbasierte Parsing.
"""

from __future__ import annotations

import subprocess

import config


def bild_zu_text(bild_pfad: str) -> str:
    """Fuehrt tesseract auf einem Seitenbild aus und liefert den erkannten Text.

    Ruft:  tesseract <bild> stdout -l deu --psm 6 --oem 1
    'stdout' laesst tesseract den Text direkt ausgeben (keine Zwischendatei).
    Wirft bei Fehler eine Exception (Aufrufer faengt sie ab).
    """
    cmd = [
        config.TESSERACT_BIN,
        bild_pfad,
        "stdout",
        "-l",
        config.TESSERACT_LANG,
    ]
    # --psm/--oem als einzelne Tokens anhaengen (aus der Config-Zeichenkette).
    cmd.extend(config.TESSERACT_CONFIG.split())

    ergebnis = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return ergebnis.stdout


def pruefe_tesseract_sprache() -> bool:
    """Prueft, ob das deutsche Sprachpaket installiert ist.

    Gibt True zurueck, wenn 'deu' in `tesseract --list-langs` auftaucht.
    Bei Fehler (tesseract fehlt) False -- der Aufrufer gibt dann einen
    klaren Hinweis auf die README-Setup-Schritte.
    """
    try:
        ergebnis = subprocess.run(
            [config.TESSERACT_BIN, "--list-langs"],
            capture_output=True,
            text=True,
            check=True,
        )
        sprachen = ergebnis.stdout.split() + ergebnis.stderr.split()
        return config.TESSERACT_LANG in sprachen
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
