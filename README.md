# Backlist

Self-hosted Converter: lose Manga-Seiten in einer Inbox werden automatisch zu **einem CBZ pro Chapter**. Die Ausgabe ist für [Kavita](https://www.kavitareader.com/) gedacht. Container starten, WebUI öffnen, Watcher laufen lassen. Eine Shell ist dafür nicht nötig.

Beispiel:

```text
/input/One Piece/Chapter 001/*.jpg
```

wird zu

```text
/output/Manga/One Piece/One Piece Volume 1 Chapter 0001.cbz
```

## Features

- Docker-Image, ein Prozess, WebUI auf Port 8080
- Watchfolder mit Stabilitätsprüfung, damit unfertige Kopien nicht gepackt werden
- Manueller Scan. Bekannte Werke importieren saubere Chapter von allein, der Rest wartet auf eine Zuordnung
- Ein CBZ pro Chapter-Ordner, Seiten in Natural Sort, flach im Archiv
- Chapter-Ordner wie `Chapter 1`, `Ch.12`, `c001`, `Chapter 1.5` werden über das Profil benannt
- Skip, Overwrite oder Rename, wenn die Zieldatei schon existiert (Standard: Skip)
- Originale bleiben liegen, optional erst nach gültigem CBZ löschen
- Job-Historie, Fehler und Logs in der WebUI und auf stdout
- Konfiguration bleibt in `/appdata` über Container-Neustarts erhalten

Nicht enthalten: Downloader, Scraper, Metadaten, Kavita-API, Benutzerkonten, Reader.

## Docker Compose Beispiel

Lokale Entwicklung im Projektordner:

```yaml
services:
  backlist:
    build: .
    ports:
      - "8080:8080"
    environment:
      PUID: "99"
      PGID: "100"
      INPUT_DIR: /input
      OUTPUT_DIR: /output
      APPDATA_DIR: /appdata
    volumes:
      - ./data/input:/input
      - ./data/output:/output
      - ./data/appdata:/appdata
    restart: unless-stopped
```

```bash
docker compose up -d --build
```

WebUI: `http://<host>:8080`

Es gibt keine Anmeldung. Den Port nicht ins Internet stellen, ohne einen Reverse Proxy mit Zugriffsschutz davor.

## Unraid Setup

1. Image bauen oder auf dem Server bereitstellen und einen Container anlegen.
2. Netzwerk: Bridge, Port `8080` auf den Host legen.
3. Die drei Volumes unten eintragen.
4. `PUID` / `PGID` auf den Benutzer setzen, der die Shares schreiben darf. Auf Unraid ist das oft `99` / `100` (`nobody` / `users`).
5. Container starten und `http://<unraid-ip>:8080` öffnen.
6. In Kavita eine Manga-Bibliothek auf `/mnt/user/Books/Manga` anlegen, nicht auf die Inbox.

Kein privileged Modus, kein Docker-Socket, keine Extra-Rechte.

```bash
docker run -d \
  --name backlist \
  -p 8080:8080 \
  -e PUID=99 \
  -e PGID=100 \
  -v /mnt/user/Books/Inbox/Manga:/input \
  -v /mnt/user/Books/Manga:/output \
  -v /mnt/user/appdata/manga-cbz-converter:/appdata \
  --restart unless-stopped \
  backlist
```

Unraid-User-Shares liefern keine verlässlichen inotify-Events. Der Container prüft den Ordner deshalb per Polling. Das funktioniert auf `/mnt/user`.

## Volume Mappings

| Host | Container | Inhalt |
| --- | --- | --- |
| `/mnt/user/Books/Inbox/Manga` | `/input` | Lose Seiten, Serie/Chapter |
| `/mnt/user/Books/Manga` | `/output` | Fertige CBZ, nur dieser Pfad gehört in Kavita |
| `/mnt/user/appdata/manga-cbz-converter` | `/appdata` | `config.json`, SQLite, nichts davon liegt nur im Container-Dateisystem |

`PUID` muss in `/output` und `/appdata` schreiben dürfen. Die Inbox reicht als Lesen, außer „Quelle löschen“ ist an.

## WebUI Verwendung

- **Running / Paused**, plus **Pause** and **Scan**
- Input, Output, and the counts waiting, active, done, failed
- Queue and the last few jobs, with status, target file, error text, and **Retry**
- **Library**: saved series, as cards. Add one by name and profile. That does not import files
- **Import**: one block per inbox folder. Choose a library series or add one, then import the files in that folder. `cover.jpg`, `folder.jpg`, and `poster.jpg` import as the cover
- **Activity**: queue, recent jobs, and the log
- Settings, including **Add profile** for a folder name and template
- Log, derselbe Text geht nach stdout

Ein Scan läuft auch, wenn der Watcher pausiert ist.

## Input-Struktur

Der erste Ordner unter der Inbox ist das Werk, zum Beispiel `One Piece/`. Darunter liegen Chapter- oder Volume-Ordner. Eine Datei direkt im Werkordner oder direkt in der Inbox wird nicht von allein gepackt.

```text
/input/
└── One Piece/
    ├── Chapter 001/
    │   ├── 001.jpg
    │   ├── 002.jpg
    │   └── 003.jpg
    └── Chapter 002/
        └── ...
```

Seiten: JPG/JPEG, PNG, WEBP, GIF, BMP, TIFF. Andere Dateien und versteckte Dateien (`.…`, `._…`) werden nicht ins CBZ gelegt. `Chapter 12`, `Ch.12`, `c12`, `chapter_001` und `Chapter 1.5` werden erkannt. `Vol 2 Chapter 3` ist Volume 2, Chapter 3. Ein Ordner ohne Nummer wartet auf eine Zuordnung und wird nicht geraten. Die Kapitelbreite steht am Profil, nicht an einer globalen Einstellung.

## Profile und Templates

Alle Ausgabeformate stehen in `/appdata/config.json` und in der WebUI unter **Profiles**. Das Profil gehört zum Werk, nicht zum Ordnernamen. Ein neues Werk startet mit dem Default-Profil und bleibt dabei, sobald es einmal importiert wurde. Erkennungsmuster für `Volume`/`Chapter` stehen unter **Detection patterns**.

| Profil | Vorlage | Beispiel |
| --- | --- | --- |
| `Manga` | `{profile}/{series}/{series} Volume {volume} Chapter {chapter}.cbz` | `Manga/One Piece/One Piece Volume 1 Chapter 0001.cbz` |
| `Novel` | `{profile}/{series} Volume {volume}.cbz` | `Novel/Dune Volume 3.cbz` |

Die Struktur unter dem Werkordner wird gelesen: `Chapter 001`, `Volume 2/Chapter 12`. Fehlt das Volume bei Manga, gilt Volume 1. Ein Novel ohne `{chapter}` in der Vorlage wird pro Volume gepackt, auch wenn darin Chapter-Ordner liegen. Ein einzelnes Bild direkt im Werkordner wartet, bis Volume und Chapter gesetzt sind.

## Output-Struktur

```text
/output/
└── Manga/
    └── One Piece/
        ├── One Piece Volume 1 Chapter 0001.cbz
        └── One Piece Volume 1 Chapter 0002.cbz
```

Der Dateiname kommt aus der Profil-Vorlage. `Chapter 1.5` wird bei Manga `One Piece Volume 1 Chapter 0001.5.cbz`. Im Archiv liegen nur die Seiten, ohne zusätzlichen Ordner, in natürlicher Reihenfolge (`1.jpg`, `2.jpg`, `10.jpg`).

## Watchfolder

Der Watcher ist beim ersten Start an. Ein Chapter wird erst gepackt, wenn Dateiliste, Größe und Änderungszeit über die eingestellte Stabilität unverändert bleiben. Während des Kopierens entsteht kein CBZ. Die Frist startet nach der letzten Änderung neu.

**Jetzt scannen** geht den ganzen Baum sofort durch und unterliegt derselben Frist. **Import** packt die gewählten Dateien so, wie sie in dem Moment da sind.

## Fehlerbehandlung

Fehlgeschlagene Chapter bleiben im Input. In der WebUI steht der Grund, zum Beispiel:

- keine Bilder gefunden
- Chapter-Nummer nicht eindeutig erkannt
- beschädigtes Bild
- Schreibfehler oder fehlender Output-Pfad
- CBZ konnte nicht validiert werden

**Nochmal** stellt den Job zurück auf Wartend. Ein beschädigtes Bild löscht die Quelle nie, auch wenn „Quelle löschen“ an ist. Gelöscht wird nur nach geschriebenem und erneut geöffnetem, gültigem CBZ.

## Konfiguration

Über die WebUI oder, solange `/appdata/config.json` noch nicht existiert, über Umgebungsvariablen:

| Einstellung | Variable | Standard |
| --- | --- | --- |
| Input | `INPUT_DIR` | `/input` |
| Output | `OUTPUT_DIR` | `/output` |
| Watcher | `WATCHER_ENABLED` | `true` |
| Stabilität in Sekunden | `STABILITY_SECONDS` | `10` |
| Worker | `WORKER_COUNT` | `1` (max. 8) |
| Vorhandene Datei | `EXISTING_FILE_BEHAVIOUR` | `skip` (`overwrite`, `rename`) |
| Quelle löschen | `DELETE_SOURCE` | `false` |
| Benutzer / Gruppe | `PUID`, `PGID` | `99`, `100` |

Nach dem ersten Speichern in der WebUI gilt `config.json`. Ein Neustart liest diese Datei. Eine kaputte `config.json` wird nicht still durch die Umgebungsvariablen ersetzt. Wer die Variablen wieder ziehen will, löscht `config.json` im Appdata-Volume.

Skip lässt eine vorhandene CBZ in Ruhe. Overwrite ersetzt sie, wenn sich der Chapter-Ordner geändert hat. Rename legt `One Piece Volume 1 Chapter 0001 (2).cbz` daneben.

## Troubleshooting

- **Nichts passiert:** Der erste Ordner muss das Werk sein, `One Piece/Chapter 001/seiten.jpg`. Ein neues Werk wartet unter **Import**, bis das Profil bestätigt ist. Danach laufen saubere Chapter von allein. Die Stabilität abwarten.
- **Permission denied / Schreibfehler:** `PUID`/`PGID` müssen zum Besitzer von Output und Appdata passen. Auf Unraid meist 99/100.
- **Unraid sieht neue Dateien nicht:** Polling ist eingebaut. Trotzdem Input auf den Share mappen, nicht auf einen Pfad, den der Container nicht sieht.
- **Falsches oder kein Chapter:** `Vol 2 Chapter 3` ist Volume 2, Chapter 3. Ein Ordner ohne Nummer bleibt unter **Import**, bis Volume und Chapter gesetzt sind. Die Stellenbreite steht am Profil.
- **Kavita zeigt das Chapter nicht:** Bibliothek muss auf den Output-Share zeigen. Danach in Kavita scannen. Der Dateiname kommt aus der Profil-Vorlage, zum Beispiel `One Piece Volume 1 Chapter 0001.cbz`.
- **Geänderte Env-Variable wirkt nicht:** `config.json` löschen oder den Wert in der WebUI setzen.
- **Doppelte CBZ:** Standard ist Skip. Rename erzeugt absichtlich ` (2)`.

Logs: WebUI und `docker logs backlist`.

## Beispielworkflow mit Kavita

1. Container mit den drei Volumes starten.
2. WebUI öffnen, Input `/input`, Output `/output`, Watcher an. Das ist der Startzustand, wenn die Volumes wie oben gemappt sind.
3. Seiten nach `/input/One Piece/Chapter 001/` kopieren. Das erste Chapter eines neuen Werks unter **Import** bestätigen.
4. Danach liegt die Datei im Profilordner:

```text
/output/Manga/One Piece/One Piece Volume 1 Chapter 0001.cbz
```

5. Kavita-Bibliothek auf den Output zeigen lassen und scannen.

`Chapter 002` desselben Werks wird danach automatisch `One Piece Volume 1 Chapter 0002.cbz`, nicht ein gemeinsames Archiv.

## Tests

```bash
python -m venv .venv
.venv\Scripts\pip install --require-hashes -r requirements.lock
.venv\Scripts\pip install pytest
.venv\Scripts\pytest
cd frontend && npm test
```

Auf Linux/macOS heißen die Binaries `.venv/bin/pip` und `.venv/bin/pytest`.
