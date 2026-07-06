"""LOKALE KI (nur Kategorisierung) -- austauschbarer Adapter.

Architektur-Prinzip: **Das Backend ist Konfiguration, nicht Code.** Alle
LLM-Aufrufe sind in EINER Klasse ``LLMClient`` gekapselt. Der Client spricht
ausschliesslich die **OpenAI-kompatible Chat-API**
(``POST {base_url}/v1/chat/completions``). Dieses Format bedienen Ollama,
llama.cpp (``--server``) und MLC (``mlc_llm serve``) nativ -- ein Backend-
Wechsel braucht nur ``base_url`` + ``model`` in config.json. KEINE
anbieter-spezifischen Endpunkte (z.B. Ollamas /api/generate).

Strikte Grenzen:
  * Die KI bekommt NUR die Liste der EINDEUTIGEN Verwendungszwecke und
    liefert eine Kategorie je Zweck. Betraege, Daten, Salden gehen NIE durch
    die KI.
  * ``llm_enabled=false`` ODER Server nicht erreichbar ODER kaputtes JSON
    -> Kategorie "unkategorisiert"; das Tool laeuft trotzdem komplett durch
    (graceful degradation).
  * Turnus/Preiserhoehung berechnet Python selbst (siehe turnus.py).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import config
from models import ERLAUBTE_KATEGORIEN, KAT_UNKATEGORISIERT

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


class LLMClient:
    """Kapselt ALLE LLM-Aufrufe hinter einer OpenAI-kompatiblen Chat-API.

    Einzige oeffentliche Methode: :meth:`categorize`.
    """

    def __init__(self, base_url: str, model: str, enabled: bool, timeout: int,
                 retries: int = 1):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = bool(enabled)
        self.timeout = int(timeout)
        self.retries = max(0, int(retries))     # Wiederholungen pro Batch

    # -- Fabrik aus config.json ---------------------------------------------
    @classmethod
    def from_config(cls) -> "LLMClient":
        cfg = config.lade_llm_config()
        return cls(
            base_url=cfg["llm_base_url"],
            model=cfg["llm_model"],
            enabled=cfg["llm_enabled"],
            timeout=cfg["llm_timeout"],
            retries=cfg.get("llm_retries", 1),
        )

    # -- oeffentliche API ---------------------------------------------------
    def categorize(self, verwendungszwecke: List[str]) -> Dict[str, str]:
        """Kategorisiert eine Liste EINDEUTIGER Verwendungszwecke.

        Rueckgabe: dict {verwendungszweck -> kategorie}. Bei jedem Problem
        (deaktiviert, Server aus, Timeout, kaputtes JSON) faellt der
        betroffene Zweck auf "unkategorisiert" -- das Tool laeuft immer durch.
        """
        ergebnis: Dict[str, str] = {z: KAT_UNKATEGORISIERT for z in verwendungszwecke}
        if not verwendungszwecke:
            return ergebnis

        if not self.enabled:
            print("  [KI] llm_enabled=false -> alle Buchungen 'unkategorisiert'.")
            return ergebnis
        if not _HAT_REQUESTS:
            print("  [KI] 'requests' nicht installiert -> alle 'unkategorisiert'.")
            return ergebnis
        if not self._erreichbar():
            print(f"  [KI] LLM-Server nicht erreichbar ({self.base_url}) "
                  f"-> alle 'unkategorisiert'.")
            return ergebnis

        # FIX 4: in kleine Batches stueckeln und SEQUENTIELL senden. Scheitert
        # ein Batch (auch nach Retries), bleiben nur DESSEN Zwecke
        # "unkategorisiert" -- kein Gesamt-Abbruch.
        batches = [
            verwendungszwecke[i:i + config.LLM_BATCH]
            for i in range(0, len(verwendungszwecke), config.LLM_BATCH)
        ]
        anzahl = len(batches)
        print(f"  [KI] Kategorisiere {len(verwendungszwecke)} eindeutige "
              f"Verwendungszwecke in {anzahl} Batch(es) "
              f"({self.model} @ {self.base_url}) ...")

        for nr, batch in enumerate(batches, start=1):
            try:
                ergebnis.update(self._frage_batch_mit_retry(batch))
                print(f"    [KI] Batch {nr}/{anzahl} ok")
            except Exception as exc:  # noqa: BLE001 - Batch faellt auf unkategorisiert
                print(f"    [KI] Batch {nr}/{anzahl} fehlgeschlagen ({exc}) "
                      f"-> {len(batch)}x unkategorisiert.")
        return ergebnis

    def _frage_batch_mit_retry(self, batch: List[str]) -> Dict[str, str]:
        """Ein Batch mit bis zu (1 + retries) Versuchen; Timeout aus config."""
        letzter_fehler: Exception = RuntimeError("unbekannt")
        for versuch in range(self.retries + 1):
            try:
                return self._frage_batch(batch)
            except Exception as exc:  # noqa: BLE001
                letzter_fehler = exc
                if versuch < self.retries:
                    print(f"      [KI] Wiederhole Batch (Versuch {versuch + 2}) ...")
        raise letzter_fehler

    # -- interne Helfer -----------------------------------------------------
    def _erreichbar(self) -> bool:
        """Schnelltest ueber den OpenAI-kompatiblen Endpunkt /v1/models."""
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _frage_batch(self, batch: List[str]) -> Dict[str, str]:
        """Ein Batch ueber POST {base_url}/v1/chat/completions."""
        nummeriert = "\n".join(f"{i}: {z}" for i, z in enumerate(batch))
        user_prompt = "Liste:\n" + nummeriert + "\n\nAntworte jetzt mit dem JSON-Objekt."

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "stream": False,
            # OpenAI-kompatibel: erzwingt (wo unterstuetzt) striktes JSON.
            "response_format": {"type": "json_object"},
        }
        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        inhalt = r.json()["choices"][0]["message"]["content"]
        return _mappe_antwort(inhalt, batch)


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
        if kat not in ERLAUBTE_KATEGORIEN:      # auf erlaubte Kategorien normalisieren
            kat = KAT_UNKATEGORISIERT
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[batch[idx]] = kat
    return out
