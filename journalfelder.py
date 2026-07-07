"""FEATURE 4 -- Referenzfelder aus dem Verwendungszweck ziehen (Regex, kein KI).

Aus dem freien Verwendungszweck werden strukturierte Felder in EIGENE Spalten
extrahiert und aus dem Empfaenger-Text entfernt, sodass "Empfaenger" nur den
Klar-/Firmennamen enthaelt:

    Referenz           -> EREF: ...
    Mandatsref         -> MREF: ...
    Glaeubiger-ID      -> CRED: ...
    Vertrags-/Kundennr -> konservativ erkannte Vertrags-/Objektnummern
                          (z.B. "BOXflex 56001383962", "KR-1133-5628",
                           "VS 9168037-8", "Kundennummer 2208397945")
"""

from __future__ import annotations

import re
from typing import Dict

from wiederkehrer import _schneide_kern

# Marker, an denen der Referenzteil beginnt (Empfaenger steht davor).
_MARKERS = r"(?:EREF|MREF|CRED|SVWZ|ABWA|IBAN|BIC)"
_MARKER_START_RE = re.compile(_MARKERS + r"[:\s]", re.IGNORECASE)

# Konservative Muster fuer Vertrags-/Kunden-/Objektnummern.
_VERTRAG_PATTERNS = [
    re.compile(r"\bVS\s+[0-9][\w./-]*", re.IGNORECASE),                 # VS 9168037-8
    re.compile(r"\bKR-[\w-]*\d[\w-]*", re.IGNORECASE),                  # KR-1133-5628
    re.compile(r"\b(?:Kundennummer|Kundennr|Vertragskonto|Vertragsnummer|Kd)"
               r"\.?\s*:?\s*\d[\w./-]*", re.IGNORECASE),
    re.compile(r"\b[A-Za-zÄÖÜäöü]{3,}\s+\d{7,}\b"),                     # BOXflex 56001383962
]


def _capture(zweck: str, key: str) -> str:
    """Wert nach 'KEY:' bis zum naechsten Marker (oder Ende)."""
    m = re.search(rf"\b{key}[:\s]+(.*?)(?=\s+{_MARKERS}[:\s]|$)", zweck,
                  re.IGNORECASE | re.DOTALL)
    return " ".join(m.group(1).split()) if m else ""


def _vertragsnummern(pre: str) -> str:
    treffer = []
    for pat in _VERTRAG_PATTERNS:
        for m in pat.finditer(pre):
            treffer.append(" ".join(m.group(0).split()))
    # Reihenfolge erhalten, Duplikate raus.
    return "; ".join(dict.fromkeys(treffer))


def extrahiere_felder(zweck: str) -> Dict[str, str]:
    """Zerlegt einen Verwendungszweck in (empfaenger, referenz, mandatsref,
    glaeubiger_id, vertragsnr). Der Originaltext bleibt als Beleg erhalten."""
    zweck = zweck or ""
    referenz = _capture(zweck, "EREF")
    mandatsref = _capture(zweck, "MREF")
    glaeubiger = _capture(zweck, "CRED")

    m = _MARKER_START_RE.search(zweck)
    pre = zweck[:m.start()] if m else zweck        # Teil VOR den Referenzmarkern
    vertragsnr = _vertragsnummern(pre)

    # Vertragsnummern (inkl. fuehrendem Wort) aus dem Empfaenger-Teil entfernen.
    rein = pre
    for vn in vertragsnr.split("; "):
        if vn:
            rein = rein.replace(vn, " ")
    empfaenger = _schneide_kern(rein) or "?"

    return {
        "empfaenger": empfaenger,
        "referenz": referenz,
        "mandatsref": mandatsref,
        "glaeubiger_id": glaeubiger,
        "vertragsnr": vertragsnr,
    }
