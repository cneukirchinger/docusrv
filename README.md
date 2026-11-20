# Docusrv

Diese Docker-Applikation hostet eine Website, die von einem administrativen Benutzer über einen Upload einer zip-Datei auf einer Weboberfläche ausgetauscht werden kann. Die Aufgabe des Webservers ist die Anzeige von Schulungsunterlagen (die zuvor durch Quarto generiert wurden), die als offline Alternative zu Schulungsorten ohne Internetverbindung angezeigt werden können. Die Schulungsunterlagen stehen direkt unter dem Hostname `docusrv` zur Verfügung.

## Betrieb

### Starten

```bash
export ADMIN_PASSWORD='sicheres-passwort'
docker compose up -d --build
```

Alternativ ohne Compose:

```bash
docker build -t docusrv .
docker run -d --name docusrv --hostname docusrv -p 80:8080 \
  -e ADMIN_PASSWORD='sicheres-passwort' -v docusrv-data:/data docusrv
```

Die Unterlagen sind danach unter `http://docusrv/` erreichbar. Damit der Hostname `docusrv` aufgelöst wird, muss im Schulungsnetz ein DNS-Eintrag oder auf den Clients ein Eintrag in der `hosts`-Datei auf die IP des Docker-Hosts zeigen.

### Unterlagen austauschen

1. Quarto-Projekt rendern (`quarto render`) und den Ausgabeordner (z. B. `_site/`) als ZIP packen.
2. `http://docusrv/_admin/` öffnen und mit `ADMIN_USER` / `ADMIN_PASSWORD` anmelden.
3. ZIP hochladen – die neue Version ist sofort aktiv.

Das ZIP muss eine `index.html` enthalten, entweder im Wurzelverzeichnis oder in einem einzelnen Unterordner. Die vorherige Version bleibt erhalten und kann in der Verwaltung per „Wiederherstellen“ wieder aktiviert werden.

### Konfiguration (Umgebungsvariablen)

| Variable           | Standard | Beschreibung                                                    |
|--------------------|----------|-----------------------------------------------------------------|
| `ADMIN_PASSWORD`   | –        | Pflicht; ohne Passwort ist die Verwaltung deaktiviert.          |
| `ADMIN_USER`       | `admin`  | Benutzername für die Verwaltung.                                |
| `MAX_UPLOAD_MB`    | `1024`   | Maximale Größe des ZIP-Uploads.                                 |
| `MAX_EXTRACTED_MB` | `4096`   | Maximale Größe des entpackten Inhalts.                          |
| `SECRET_KEY`       | auto     | Session-Schlüssel; wird sonst generiert und in `/data` gespeichert. |

Alle Daten liegen im Volume `/data`. Health-Check: `GET /_health`.
