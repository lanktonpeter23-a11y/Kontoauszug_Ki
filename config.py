"""Zentrale Konfiguration + Laden der Bank-Layout-Profile.

Hier stehen alle Pfade und Konstanten an EINER Stelle, damit sie leicht
angepasst werden koennen (z.B. wenn der Wurf-Ordner woanders liegt).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# BEDIENKONZEPT: ein einziger Wurf-Ordner (zentral, nur HIER definiert)
# ---------------------------------------------------------------------------
# FIX 5: Standard ist ein Termux-HOME-Pfad, der zuverlaessig les- UND
# schreibbar ist. /storage/emulated/0/... ist unter Termux (ohne Root und je
# nach Android-Version) nicht immer beschreibbar. Ueber die Umgebungsvariable
# KONTOAUSZUEGE_DIR laesst sich der Ordner umbiegen (z.B. auf den geteilten
# Speicher, wenn gewuenscht).
BASIS_ORDNER = os.environ.get(
    "KONTOAUSZUEGE_DIR",
    "/data/data/com.termux/files/home/downloads/Kontoauszuege",
)

# Ergebnisse landen hier ...
AUSWERTUNG_ORDNER = os.path.join(BASIS_ORDNER, "Auswertung")
# ... verarbeitete PDFs werden hierhin VERSCHOBEN (nie geloescht) ...
VERARBEITET_ORDNER = os.path.join(BASIS_ORDNER, "verarbeitet")

# ZWEI Ausgabedateien:
#   VOLL   -- vollstaendig mit Klarnamen (auch Append-Quelle, enthaelt _Daten)
#   ANONYM -- identisch, aber Personennamen -> [NAME], IBAN/BIC maskiert
EXCEL_VOLL = os.path.join(AUSWERTUNG_ORDNER, "Finanzanalyse_VOLL.xlsx")
EXCEL_ANONYM = os.path.join(AUSWERTUNG_ORDNER, "Finanzanalyse_ANONYM.xlsx")
# Append/Persistenz laeuft ueber die VOLL-Datei.
EXCEL_DATEI = EXCEL_VOLL

# Temporaerer Ordner fuer gerenderte Seitenbilder (wird pro Lauf geleert).
TEMP_ORDNER = os.path.join(AUSWERTUNG_ORDNER, ".tmp_render")
# OCR-Dump: der intern erzeugte OCR-Text je PDF (exakt der Parsing-Input).
OCR_ORDNER = os.path.join(AUSWERTUNG_ORDNER, "ocr")

# Seiten-Trenner im internen OCR-Text (auch im Dump verwendet).
SEITEN_TRENNER = "===SEITENENDE==="

# ---------------------------------------------------------------------------
# EBENE 1 -- Rendern
# ---------------------------------------------------------------------------
DPI = 300                       # Aufloesung fuer pdftoppm (guter OCR-Input)
PDFTOPPM_BIN = os.environ.get("PDFTOPPM_BIN", "pdftoppm")

# ---------------------------------------------------------------------------
# EBENE 2 -- OCR (tesseract)
# ---------------------------------------------------------------------------
TESSERACT_BIN = os.environ.get("TESSERACT_BIN", "tesseract")
TESSERACT_LANG = os.environ.get("TESSERACT_LANG", "deu")   # deutsches Sprachpaket
# --psm 6 = "Assume a single uniform block of text" -- passt gut fuer
# tabellarische Kontoauszuege. --oem 1 = LSTM (Standard in tesseract 5).
TESSERACT_CONFIG = os.environ.get("TESSERACT_CONFIG", "--psm 6 --oem 1")

# ---------------------------------------------------------------------------
# LOKALE KI (nur Kategorisierung) -- OpenAI-kompatibler Chat-Server
# ---------------------------------------------------------------------------
# Das KI-Backend ist KONFIGURATION, nicht Code: alle Einstellungen kommen aus
# config.json (neben diesem Skript). Der LLMClient spricht ausschliesslich die
# OpenAI-kompatible Chat-API ({base_url}/v1/chat/completions), die Ollama,
# llama.cpp --server und MLC serve nativ bedienen. So laesst sich das Backend
# ohne Codeaenderung tauschen -- nur base_url + model in config.json aendern.
_CONFIG_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Standardwerte (greifen, falls config.json fehlt oder Schluessel fehlen).
_LLM_DEFAULTS: Dict[str, Any] = {
    "llm_base_url": "http://127.0.0.1:11434",   # Ollama-Standard; llama.cpp: :8080
    "llm_model": "phi4-mini",                    # Alternative: "qwen3.5:4b"
    "llm_enabled": True,
    "llm_timeout": 180,                           # Sekunden pro Batch (phi4-mini auf Handy-CPU)
    "llm_retries": 1,                             # Wiederholungen pro Batch bei Fehler/Timeout
}


def lade_llm_config() -> Dict[str, Any]:
    """Laedt die KI-Konfiguration aus config.json (mit Defaults als Fallback)."""
    werte = dict(_LLM_DEFAULTS)
    try:
        with open(_CONFIG_DATEI, "r", encoding="utf-8") as fh:
            daten = json.load(fh)
        if isinstance(daten, dict):
            for key in _LLM_DEFAULTS:
                if key in daten:
                    werte[key] = daten[key]
    except FileNotFoundError:
        print("  [WARN] config.json nicht gefunden -> LLM-Standardwerte.")
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [WARN] config.json fehlerhaft ({exc}) -> LLM-Standardwerte.")
    return werte


# So viele Verwendungszwecke pro KI-Anfrage (Batch-Groesse). FIX 4: klein
# halten -- grosse Batches (z.B. 61 Zwecke auf einmal) lassen kleine lokale
# Modelle mit 500 antworten. Sequentiell viele kleine Batches sind robuster.
LLM_BATCH = 10

# ---------------------------------------------------------------------------
# Bank-Layout-Profile
# ---------------------------------------------------------------------------
_PROFIL_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bank_profiles.json")


def lade_bank_profile() -> List[Dict[str, Any]]:
    """Laedt die konfigurierbaren Bank-Muster-Profile aus bank_profiles.json.

    Neue Banken koennen dort OHNE Codeaenderung ergaenzt werden.
    Faellt bei Fehler auf eine leere Liste zurueck -> generischer Parser
    uebernimmt dann alles.
    """
    try:
        with open(_PROFIL_DATEI, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "profile" in data:
            return data["profile"]
        if isinstance(data, list):
            return data
    except FileNotFoundError:
        print("  [WARN] bank_profiles.json nicht gefunden -> nur generischer Parser.")
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [WARN] bank_profiles.json fehlerhaft ({exc}) -> nur generischer Parser.")
    return []
