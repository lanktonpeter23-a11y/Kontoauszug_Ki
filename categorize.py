"""LOKALE KI (nur Kategorisierung, strikt begrenzt).

Ollama-API unter http://127.0.0.1:11434. Die KI bekommt NUR die Liste der
EINDEUTIGEN Verwendungszwecke (dedupliziert, gebatcht) und liefert strikt
JSON zurueck:

    Kategorie in {Fixkosten, Lebenshaltung, Tanken, Sonstiges, Umbuchung}

WICHTIG:
  * Betraege, Daten, Salden gehen NIE durch die KI.
  * KI nicht erreichbar / JSON kaputt -> Kategorie "unkategorisiert",
    das Tool laeuft trotzdem komplett durch (graceful degradation).
  * Turnus/Preiserhoehung berechnet Python selbst (siehe turnus.py); die KI
    liefert nur die semantische Kategorie (fuer Fixkosten optional einen
    Turnus-Vorschlag, der aber nur informativ ist).
"""

from __future__ import annotations

import json
from typing import Dict, List

import config
from models import (
    ERLAUBTE_KATEGORIEN,
    KAT_UNKATEGORISIERT,
)

# requests ist optional -- ohne es laeuft das Tool ohne KI weiter.
try:
    import requests  # type: ignore
    _HAT_REQUESTS = True
except ImportError:
    _HAT_REQUESTS = False


_SYSTEM_PROMPT = (
    "Du bist ein deutscher Finanz-Kategorisierer. Du bekommst eine nummerierte "
    "Liste von Verwendungszwecken aus Kontoauszuegen. Ordne jedem GENAU EINE "
    "Kategorie zu:\n"
    "- Fixkosten: regelmaessige Vertraege (Miete, Strom, Gas, Wasser, "
    "Versicherung, Telekom/Internet, Streaming, Abos, Rundfunk, Kredit).\n"
    "- Lebenshaltung: Supermarkt, Drogerie, Baecker, Restaurant, taeglicher Bedarf.\n"
    "- Tanken: Tankstellen, Kraftstoff (Aral, Shell, Esso, JET, Total, HEM).\n"
    "- Umbuchung: eigene Uebertraege zwischen eigenen Konten, Sparen, "
    "Kreditkarten-Ausgleich, Bargeldabhebung/-einzahlung.\n"
    "- Sonstiges: alles andere.\n"
    "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt der Form "
    '{\"ergebnis\": [{\"i\": <nummer>, \"kategorie\": \"<Kategorie>\"}, ...]}. '
    "Keine Erklaerungen, kein Text ausserhalb des JSON."
)


def ki_verfuegbar() -> bool:
    """Schnelltest, ob die Ollama-API erreichbar ist."""
    if not _HAT_REQUESTS:
        return False
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def kategorisiere(zwecke: List[str]) -> Dict[str, str]:
    """Kategorisiert eine Liste EINDEUTIGER Verwendungszwecke.

    Rueckgabe: dict {verwendungszweck -> kategorie}. Bei jedem Problem
    (KI aus, Timeout, kaputtes JSON) faellt der betroffene Zweck auf
    "unkategorisiert" -- das Tool laeuft immer durch.
    """
    ergebnis: Dict[str, str] = {z: KAT_UNKATEGORISIERT for z in zwecke}

    if not zwecke:
        return ergebnis
    if not ki_verfuegbar():
        print("  [KI] Ollama nicht erreichbar -> alle Buchungen 'unkategorisiert'.")
        return ergebnis

    print(f"  [KI] Kategorisiere {len(zwecke)} eindeutige Verwendungszwecke "
          f"({config.OLLAMA_MODEL}) ...")

    for start in range(0, len(zwecke), config.OLLAMA_BATCH):
        batch = zwecke[start:start + config.OLLAMA_BATCH]
        try:
            teil = _frage_batch(batch)
            ergebnis.update(teil)
        except Exception as exc:  # noqa: BLE001 - Batch faellt auf unkategorisiert
            print(f"    [KI] Batch ab #{start} fehlgeschlagen ({exc}) "
                  f"-> unkategorisiert.")
    return ergebnis


def _frage_batch(batch: List[str]) -> Dict[str, str]:
    """Schickt einen Batch an Ollama und liefert {zweck -> kategorie}."""
    nummeriert = "\n".join(f"{i}: {z}" for i, z in enumerate(batch))
    prompt = (
        _SYSTEM_PROMPT
        + "\n\nListe:\n" + nummeriert
        + "\n\nAntworte jetzt mit dem JSON-Objekt."
    )

    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",          # zwingt Ollama zu striktem JSON
        "options": {"temperature": 0},
    }
    r = requests.post(
        f"{config.OLLAMA_URL}/api/generate",
        json=payload,
        timeout=config.OLLAMA_TIMEOUT,
    )
    r.raise_for_status()
    roh = r.json().get("response", "")
    return _mappe_antwort(roh, batch)


def _mappe_antwort(roh: str, batch: List[str]) -> Dict[str, str]:
    """Parst die JSON-Antwort und mappt Index -> Kategorie -> Zweck."""
    out: Dict[str, str] = {}
    daten = json.loads(roh)                 # wirft bei kaputtem JSON -> Batch faellt
    liste = daten.get("ergebnis", daten) if isinstance(daten, dict) else daten
    if not isinstance(liste, list):
        return out

    for eintrag in liste:
        if not isinstance(eintrag, dict):
            continue
        idx = eintrag.get("i", eintrag.get("index"))
        kat = str(eintrag.get("kategorie", "")).strip().capitalize()
        # Normalisieren auf erlaubte Kategorien.
        if kat not in ERLAUBTE_KATEGORIEN:
            kat = KAT_UNKATEGORISIERT
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[batch[idx]] = kat
    return out
