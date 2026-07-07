# Kontoauszug-KI — lokale Analyse eingescannter Kontoauszüge (Termux)

Ein **vollständig lokal** in **Termux** (Android, ARM64/aarch64, kein Root)
laufendes Python-Tool. Es verarbeitet **eingescannte deutsche
Bank-Kontoauszüge** (PDF, mehrere Banken/Konten, verschiedene Layouts) und
erzeugt eine **Excel-Auswertung**.

- **Keine Cloud, keine Datenübertragung.** OCR, Parsing und Kontrolle laufen
  auf dem Gerät. Die (optionale) KI läuft ebenfalls lokal über einen
  OpenAI-kompatiblen Server (z. B. Ollama, llama.cpp, MLC).
- **Beträge, Daten und Salden gehen NIE durch die KI** — nur die reinen
  Verwendungszweck-Texte werden (falls Ollama läuft) zur Kategorisierung
  genutzt.

Getestet als Ziel: **Samsung Galaxy S26 Ultra** (Snapdragon, 16 GB RAM,
Android, aarch64, kein Root).

---

## Bedienkonzept (ein Wurf-Ordner)

```
/data/data/com.termux/files/home/downloads/Kontoauszuege/   <- HIER PDFs hineinwerfen
        ├── Auswertung/
        │      ├── Finanzanalyse_VOLL.xlsx         <- vollständig, Klarnamen (Append-Quelle)
        │      ├── Finanzanalyse_ANONYM.xlsx       <- Personennamen → [NAME], IBAN/BIC maskiert
        │      └── ocr/<pdfname>.txt               <- OCR-Dump je PDF (Parsing-Input, ===SEITENENDE===)
        └── verarbeitet/                           <- fertige PDFs (verschoben, nie gelöscht)
```

> Dieser Termux-HOME-Pfad ist zuverlässig les- **und** schreibbar. Wer die
> PDFs lieber im geteilten Speicher ablegt, setzt einfach
> `export KONTOAUSZUEGE_DIR=/storage/emulated/0/Documents/Kontoauszuege`
> vor dem Aufruf.

1. Beliebig viele Scan-PDFs (mehrere Monate, mehrere Konten, verschiedene
   Banken gemischt) in den Ordner `Kontoauszuege/` legen.
2. `python analyse.py` aufrufen — **ein Aufruf verarbeitet ALLES**.
3. Ergebnis: **zwei** Dateien in `Auswertung/` — `Finanzanalyse_VOLL.xlsx`
   (Klarnamen) und `Finanzanalyse_ANONYM.xlsx` (Personennamen → `[NAME]`,
   IBAN/BIC maskiert).
4. Verarbeitete PDFs wandern automatisch nach `Kontoauszuege/verarbeitet/`.

Bei erneutem Aufruf wird die bestehende `Finanzanalyse_VOLL.xlsx` geladen und um
**neue** Buchungen ergänzt (Duplikate werden erkannt und übersprungen —
**Append-Modus**).

---

## Setup in Termux — Schritt für Schritt

> Termux aus **F-Droid** oder dem **GitHub-Release** installieren (die
> Play-Store-Version ist veraltet).

### 1. Systempakete aktualisieren

```bash
pkg update && pkg upgrade
```

### 2. Systempakete installieren (via `pkg`)

```bash
pkg install python tesseract poppler
```

- `python`   — Python 3 (bringt `pip` mit)
- `tesseract` — OCR-Engine. **Enthält nur Englisch (`eng`)**, das deutsche
  Sprachpaket wird in Schritt 4 separat installiert.
- `poppler`  — liefert `pdftoppm` (rendert PDF-Seiten in Bilder).

Prüfen, dass alles da ist:

```bash
tesseract --version
pdftoppm -v
```

### 3. Speicherzugriff freigeben

```bash
termux-setup-storage
```

Danach ist `/storage/emulated/0/...` (der interne Speicher) aus Termux
erreichbar. Die Berechtigungsabfrage von Android muss bestätigt werden.

### 4. Deutsches Tesseract-Sprachpaket (`deu`) installieren  ← WICHTIG

Termux liefert **kein** separates `pkg`-Paket für deutsche OCR-Daten. Die
Datei `deu.traineddata` wird direkt in das tessdata-Verzeichnis von Termux
geladen. Dieses liegt unter `$PREFIX/share/tessdata` (also
`/data/data/com.termux/files/usr/share/tessdata`).

```bash
curl -L -o "$PREFIX/share/tessdata/deu.traineddata" \
  https://github.com/tesseract-ocr/tessdata_fast/raw/main/deu.traineddata
```

Kontrolle — `deu` muss in der Liste auftauchen:

```bash
tesseract --list-langs
```

> **Variante mit höchster Genauigkeit** (größer, langsamer) — statt
> `tessdata_fast` das Repo `tessdata_best` verwenden:
> ```bash
> curl -L -o "$PREFIX/share/tessdata/deu.traineddata" \
>   https://github.com/tesseract-ocr/tessdata_best/raw/main/deu.traineddata
> ```

### 5. Python-Pakete installieren (via `pip`)

```bash
pip install Pillow openpyxl requests
```

- `Pillow`   — Bildvorverarbeitung (Graustufen/Kontrast/Denoising)
- `openpyxl` — Excel-Ausgabe (`.xlsx`)
- `requests` — Kommunikation mit dem optionalen KI-Server (OpenAI-kompatibel)

> **`pandas` wird NICHT benötigt.** Das Tool kommt komplett mit `openpyxl`
> aus. Das ist Absicht, damit auf aarch64 keine Kompilierprobleme
> entstehen.

### 6. Dieses Repo holen und starten

```bash
git clone <REPO-URL> Kontoauszug_Ki
cd Kontoauszug_Ki
python analyse.py
```

---

## Optional: lokale KI-Kategorisierung

Die KI ordnet den Buchungen nur eine **Kategorie** zu
(Fixkosten / Lebenshaltung / Tanken / Sonstiges / Umbuchung). **Das Tool
läuft auch komplett OHNE KI** — dann bleiben die Buchungen
`unkategorisiert`, alle anderen Ergebnisse (Journal, Salden-Kontrolle,
Prüfen-Sheet) sind vollständig vorhanden. Beträge, Daten und Salden gehen
**nie** durch die KI.

Beispiel mit **Ollama** (Standard-Backend):

```bash
pkg install ollama
ollama serve &                 # Server im Hintergrund starten
ollama pull phi4-mini          # Standard-Modell laden
python analyse.py
```

Die KI wird ausschließlich über **`config.json`** (neben dem Skript)
gesteuert:

```json
{
  "llm_base_url": "http://127.0.0.1:11434",
  "llm_model": "phi4-mini",
  "llm_enabled": true,
  "llm_timeout": 180
}
```

- `llm_base_url` — Adresse des lokalen Servers (Ollama-Standard `:11434`).
- `llm_model` — Modellname (Alternative z. B. `qwen3.5:4b`).
- `llm_enabled` — `false` schaltet die KI komplett ab (alles bleibt
  `unkategorisiert`).
- `llm_timeout` — Sekunden pro Anfrage.

> Ist der Server nicht erreichbar, `llm_enabled=false` gesetzt oder liefert er
> kaputtes JSON, fällt das Tool automatisch auf `unkategorisiert` zurück und
> läuft trotzdem vollständig durch (*graceful degradation*).

### KI-Backend wechseln

Das KI-Backend ist **Konfiguration, nicht Code**. Alle LLM-Aufrufe stecken in
einer einzigen Klasse `LLMClient`, die ausschließlich die **OpenAI-kompatible
Chat-API** (`POST {llm_base_url}/v1/chat/completions`) spricht — dasselbe
Format, das **Ollama**, **llama.cpp** (`llama-server`) und **MLC**
(`mlc_llm serve`) nativ bedienen. Um das Backend zu tauschen, genügt es,
`llm_base_url` und `llm_model` in `config.json` anzupassen — es ist **keine
Codeänderung** nötig. Beispiele: `http://127.0.0.1:11434` (Ollama),
`http://127.0.0.1:8080` (llama.cpp `--server`), oder der Port des
MLC-Servers. Der Client prüft die Erreichbarkeit über `/v1/models` und
nutzt keine anbieter-spezifischen Endpunkte.

---

## Aufruf

```bash
python analyse.py                     # kompletter Lauf inkl. Excel + Verschieben
python analyse.py --dry-run           # NUR parsen + Kontrollschicht-Report auf der
                                      # Konsole; kein Excel, kein Verschieben
python analyse.py --excel DATEI.xlsx  # bestehendes VOLLSTÄNDIGES Journal erneut
                                      # auswerten (statt PDFs zu OCRn)
```

> **`--excel`** liest ein bestehendes Journal-Sheet (Spalten *Datum*,
> *Verwendungszweck/Empfaenger*, *Einnahme*, *Ausgabe*, *Art/Typ*) statt PDFs
> zu OCRn und durchläuft dieselbe Nachverarbeitung (Referenztrennung →
> Wiederkehrer → Anonymisierung → zwei Ausgabedateien). **Nur eine
> VOLLSTÄNDIGE (nicht-anonymisierte) Excel taugt als Input** — eine bereits
> anonymisierte Datei kann nicht gruppiert werden (Empfänger fehlen).
> Rückwärts geht nicht.

`--dry-run` ist ideal, um bei einer neuen Bank zu prüfen, ob Salden und
Buchungen korrekt erkannt werden, **bevor** in die Excel geschrieben wird.

---

## Die Pipeline (4 Ebenen — deterministisch vor KI)

| Ebene | Modul | Aufgabe |
|------:|-------|---------|
| **1 Rendern**   | `render.py` | `pdftoppm` rendert jede Seite mit **300 DPI** zu PNG; Pillow macht Graustufen, Autokontrast, Denoising, Schwellwert → besserer OCR-Input. |
| **2 OCR**       | `ocr.py` | `tesseract -l deu` extrahiert den Text pro Seite (deterministische Textquelle). |
| **3 Parsing**   | `statement_parser.py`, `textutils.py` | Regelbasiert (keine KI): IBAN/Kontonummer, Jahr, Buchungen (Datum, Zweck, Betrag), alter/neuer Saldo. **Vorzeichen aus Soll/Haben** (`S`=Ausgabe, `H`=Einnahme) bzw. `+`/`-`. Übertrags-/Kontostandszeilen werden ausgeschlossen. **Smart-Year** ergänzt `04.07.` mit dem PDF-Jahr und behandelt Dez→Jan korrekt. |
| **4 Kontrolle** | `control.py` | **Herzstück:** `alter Saldo + Summe aller Buchungen == neuer Saldo`? |

**Kontrollschicht-Status:**
- **OK** — Rechnung geht auf → Buchungen verifiziert.
- **PRUEFEN** — Rechnung geht *nicht* auf → **alle** Buchungen des Auszugs
  werden markiert und die Differenz wird ausgewiesen (nie still übernommen).
- **OHNE_SALDO_PRUEFUNG** — kein Saldo lesbar.

---

## Neue Banken ergänzen (ohne Code zu ändern)

Layouts werden in **`bank_profiles.json`** als Muster-Profile gepflegt. Ein
generischer Fallback-Parser greift, wenn kein Profil passt. Ein Profil:

```json
{
  "name": "Meine Bank",
  "erkennung": ["meine bank", "meinebank eg", "12345678", "de99 1234 5678"],
  "saldo_alt": ["alter kontostand", "saldovortrag"],
  "saldo_neu": ["neuer kontostand", "endsaldo"],
  "laufender_saldo": false
}
```

- `erkennung` — taucht eines dieser Stichwörter im OCR-Text auf, greift das
  Profil. **Die Bank-Erkennung ist rein deterministisch** (Header-Text,
  BLZ oder IBAN-Präfix) — nie über die KI. Die Reihenfolge in der Liste
  entscheidet: das erste passende Profil gewinnt (spezifische Profile wie
  `LIGA BANK` stehen daher oben).
- `saldo_alt` / `saldo_neu` — zusätzliche Bezeichnungen der Saldozeilen.
- `laufender_saldo` — `true`, wenn jede Buchungszeile zusätzlich eine
  laufende Kontostand-Spalte hat (dann ist der Umsatz der vorletzte Betrag
  der Zeile). Ohne Angabe wird es automatisch erkannt.

> **Wichtig:** Ein Profil regelt nur das **Layout** (wo Datum/Betrag/Zweck
> stehen). Das **Vorzeichen kommt immer aus dem Soll/Haben-Kennzeichen**
> (`S` = Ausgabe/negativ, `H` = Einnahme/positiv) bzw. `+`/`-` am Betrag —
> bankübergreifend, nie aus dem Buchungstext geraten und nie über die KI.
> Fehlt ein S/H-Kennzeichen, greift eine Heuristik und die Buchung wird als
> *Vorzeichen unsicher* markiert. Auch ohne passendes Profil parst der
> generische Parser über die S/H-Regel korrekt; Übertrags- und
> Kontostandszeilen werden immer von den Buchungen ausgeschlossen.

---

## Ausgabe: zwei Dateien, je 5 Sheets

Es werden **zwei** Dateien geschrieben: `Finanzanalyse_VOLL.xlsx` (Klarnamen)
und `Finanzanalyse_ANONYM.xlsx` (Personennamen → `[NAME]`, IBAN/BIC maskiert;
Firmen bleiben — auch im Wiederkehrend-Sheet). Beide enthalten dieselben Sheets:

1. **Journal** — lückenlos jede Buchung, chronologisch, mit getrennten Feldern:
   `Datum | Art | Empfaenger | Einnahme | Ausgabe | Typ | Status | Referenz |
   Mandatsref | Glaeubiger-ID | Vertrags-/Kundennr | Verwendungszweck_voll`.
   Einnahmen als Pluswert in *Einnahme*, Ausgaben als Minuswert in *Ausgabe*
   (das jeweils andere Feld bleibt **echt leer** → summierbar); interne
   Überträge als Typ `UMBUCHUNG`. **Art** = Kürzel per Prefix-Match
   (LS/UEW/KA/GUT/LOHN/GA/SONST). **Referenz/Mandatsref/Glaeubiger-ID/
   Vertrags-/Kundennr** werden per Regex aus dem Text gezogen, sodass
   *Empfaenger* nur den Klar-/Firmennamen enthält; der Originaltext steht
   als Beleg ganz rechts in *Verwendungszweck_voll*.
2. **Fixkosten** — gruppiert nach Zahlungsempfänger, chronologisch; mit
   **Turnus**, **Erklärung** und **Vermerk bei Preiserhöhung**. Unten:
   Jahressumme + monatlicher Durchschnitt (Jahressumme/12).
3. **Konsum** — Lebenshaltung/Tanken + monatlicher Durchschnitt je Kategorie.
4. **Wiederkehrend** — deterministisch erkannte wiederkehrende Zahlungen:
   `Empfaenger | Art | Turnus | Anzahl | Erster | Letzter | Ø-Betrag | Summe |
   Klassifikation | Betragsschwankung`. Klassifikation **SICHER** (≥3 Vorkommen
   in regelmäßigem Abstand) oder **WAHRSCHEINLICH** (2 Vorkommen bzw. ≥3
   unregelmäßig). *Betragsschwankung = ja* ist ein Preiserhöhungs-Hinweis.
5. **Pruefen** — alle Buchungen mit Status `PRUEFEN` inkl. Saldo-Differenz.

Zahlen sind **echte Zahlen** (Excel-Zahlenformat), Kopfzeile fett, **Autofilter
an**, erste Zeile fixiert, feste Spaltenbreiten und `wrap_text=False` (kein
Überlauf in Nachbarspalten). Ein verstecktes Blatt `_Daten` in der
**VOLL**-Datei speichert den vollständigen Buchungssatz für den Append-Modus
(die ANONYM-Datei enthält es bewusst **nicht** — sie ist nie Append-Quelle).

**Verarbeitungsreihenfolge (zwingend):** parsen → Referenzen/Empfänger trennen
→ Wiederkehrer am **echten** Empfänger gruppieren → **erst dann** anonymisieren
→ Ausgabe. Alle vier Erweiterungen sind rein deterministisch (Regex/Logik),
nie KI.

---

## Robustheit

- **Fortschrittsausgabe** pro PDF/Seite auf der Konsole.
- Eine kaputte/unlesbare PDF bricht den Lauf **nicht** ab — sie wird
  übersprungen und am Ende im Abschlussbericht gemeldet.
- Für Fehlersuche: `KONTO_DEBUG=1 python analyse.py` zeigt den vollen
  Stacktrace übersprungener Dateien.

## Fehlerbehebung

- **`tesseract: Error opening data file ... deu.traineddata`** → Schritt 4
  wurde nicht (korrekt) ausgeführt. `tesseract --list-langs` muss `deu`
  zeigen; die Datei muss unter `$PREFIX/share/tessdata/deu.traineddata`
  liegen.
- **`pdftoppm` meldet `cannot locate symbol ...`** → veraltete Pakete.
  `pkg update && pkg upgrade` ausführen; danach `poppler` ggf. neu
  installieren (`pkg reinstall poppler`).
- **Berechtigungsfehler beim Lesen von `/storage/emulated/0/...`** →
  `termux-setup-storage` ausführen und die Android-Abfrage bestätigen.
- **KI-Kategorien fehlen** → Ollama läuft nicht (`ollama serve &`) oder das
  Modell ist nicht geladen (`ollama pull phi4-mini`). Das Tool läuft trotzdem
  vollständig durch, nur die Spalte *Kategorie* bleibt `unkategorisiert`.

## Konfiguration (Umgebungsvariablen, optional)

| Variable | Standard | Zweck |
|----------|----------|-------|
| `KONTOAUSZUEGE_DIR` | `/data/data/com.termux/files/home/downloads/Kontoauszuege` | Wurf-Ordner |
| `TESSERACT_LANG` | `deu` | OCR-Sprache |

> Das **KI-Backend** wird nicht über Umgebungsvariablen, sondern über
> `config.json` konfiguriert (siehe Abschnitt *KI-Backend wechseln*).

---

## Schnelltest der Kernlogik (ohne PDFs)

```bash
python textutils.py     # prüft die deutsche Betrags-/Datumserkennung
python test_parser.py   # Unit-Tests + GOLDEN-MASTER gegen echten LIGA-Auszug
python test_features.py # Anonymisierung / Referenzen / Art / Wiederkehrer / Excel-Input
```

`test_parser.py` enthält einen **Golden-Master-Test** gegen den echten,
intern erzeugten OCR-Text (nach Pillow-Vorverarbeitung) eines 7-seitigen
LIGA-BANK-Auszugs (`tests/fixtures/liga_ocr_anonymized.txt`). Er ist
**Pflicht-Test für jede Parser-Änderung**: der komplette Auszug muss
cent-genau auf `OK` gehen (alter Saldo + Summe = neuer Saldo). Die Fixture
enthält bewusst die realen OCR-Verleser (am Bu-Tag klebende Rausch-Ziffern
wie `907.01.`, `S`→`5` in der Soll/Haben-Spalte, ein Magnitudenfehler
`709,85` statt `70,85`), die der Parser robust behandeln muss. Sie wurde mit
`tools/anonymize_ocr.py` von personenbezogenen Daten befreit (Namen → `NAME`,
IBANs/Referenzen maskiert), während Beträge, S/H-Kennzeichen, Datumsformate
und die komplette Zeilenstruktur **unverändert** bleiben.

**Robustheit gegen OCR-Fehler:** Der Parser toleriert am Bu-Tag klebende
Rausch-Ziffern (`907.01.` → `07.01.`), liest ein als `5`/`$`/`§` verlesenes
`S` als Soll (Ausgabe), und korrigiert einen einzelnen Magnitudenfehler je
Seite **deterministisch über die Übertrags-/Kontostand-Checkpoints der Bank**:
Geht die Seiten-Running-Balance (Start + Σ Buchungen = Ende) nicht auf und
schließt genau eine Einzelziffer-Korrektur einer Buchung die Lücke exakt, wird
sie mit Vermerk angewandt. Ist die Korrektur nicht eindeutig, bleibt es bei
`PRUEFEN` — nie wird still geraten. Einseitige Auszüge ohne Übertrags-Struktur
werden nie automatisch korrigiert.

## Dateien

| Datei | Inhalt |
|-------|--------|
| `analyse.py` | Hauptprogramm / Orchestrierung + CLI (`--dry-run`, `--excel`) |
| `config.py` | Pfade & Konstanten, Laden von Bank-Profilen und `config.json` |
| `config.json` | KI-Backend-Konfiguration (base_url, model, enabled, timeout) |
| `render.py` | Ebene 1 — Rendern + Bildvorverarbeitung |
| `ocr.py` | Ebene 2 — Tesseract-OCR |
| `statement_parser.py` | Ebene 3 — regelbasiertes Parsing + Smart-Year |
| `textutils.py` | deutsche Betrags-/Datumserkennung (+ Selbsttest) |
| `control.py` | Ebene 4 — Saldo-Kontrollschicht |
| `categorize.py` | `LLMClient` — austauschbares KI-Backend (OpenAI-kompatibel), nur Kategorien |
| `turnus.py` | Turnus- & Preiserhöhungs-Berechnung (Python) |
| `buchungsart.py` | Buchungsart-Kürzel (LS/UEW/KA/GUT/LOHN/GA/SONST) |
| `journalfelder.py` | Referenzfelder (EREF/MREF/CRED/Vertragsnr) aus dem Zweck ziehen |
| `wiederkehrer.py` | deterministische Wiederkehrer-Erkennung (Sheet „Wiederkehrend") |
| `anonymisierung.py` | Personennamen → `[NAME]`, IBAN/BIC maskieren (letzter Schritt) |
| `excel_export.py` | Excel-Ausgabe (5 Sheets, Append-Modus, Excel-Input) |
| `models.py` | Datenmodelle (`Buchung`, `Auszug`) |
| `test_features.py` | Unit-Tests für Anonymisierung/Referenzen/Art/Wiederkehrer/Excel-Input |
| `bank_profiles.json` | konfigurierbare Bank-Layout-Profile |
| `test_parser.py` | Unit-Tests + Golden-Master (echter LIGA-Auszug) |
| `tests/fixtures/liga_ocr_anonymized.txt` | anonymisierte Golden-Master-Fixture |
| `tools/anonymize_ocr.py` | Anonymisierer (OCR-Text → PII-freie Fixture) |
