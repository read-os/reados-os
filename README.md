# ReadOS 📚

**An open-source e-reader system designed for freedom.
No ads. No lock-in. No ecosystem control.                                                                                                                   Runs on Kobo, Kindle, and more.**

---

ReadOS is a full-featured, self-hosted e-reader application. It runs a lightweight Python web server on the device and exposes a polished reading interface through the device's browser.
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-active-blue)
![Platform](https://img.shields.io/badge/platform-Kobo%20%7C%20Kindle%20%7C%20Boox-orange)

---

## Features

- 📖 **Multi-format support** — EPUB (via ebooklib), PDF (via PyMuPDF), TXT
- 🗂️ **Library management** — grid view with covers, search, filter by format
- 📡 **Cloud sync** — progress, bookmarks and highlights sync across devices
- 🔍 **Anna's Archive** — search and download books directly to your library
- 📥 **Multi-source import** — local files, Google Drive, email attachments
- 🌍 **Multi-language UI** — English + Portuguese (easily extensible)
- 🌙 **Reader themes** — Light, Sepia, Dark with adjustable font size
- 👤 **User accounts** — register/login, per-user progress and bookmarks
- 🔖 **Bookmarks & Highlights** — per-book, synced to cloud

---
## Why ReadOS?

Most e-readers are locked ecosystems:
- Ads
- DRM
- Limited control

ReadOS changes that.

You own your device.
You control your library.

---
## Quick Start

### 1. Install

```bash
git clone https://github.com/youruser/ReadOS.git
cd ReadOS
bash install.sh
```

### 2. Add books

Drop `.epub`, `.pdf`, or `.txt` files into the `books/` folder.

### 3. Start

```bash
./start.sh
```

Open your browser at **http://localhost:8080**

---

## Installation on Kobo

### Requirements
- Kobo with network access (Wi-Fi)
- Kobo's built-in browser, or NickelMenu + KFMon for launching
- Python 3.7+ (install via [KoboRoot.tgz packages](https://www.mobileread.com/forums/forumdisplay.php?f=247))

### Steps

1. Copy the ReadOS folder to `/mnt/onboard/.adds/reados/`
2. Install dependencies: `pip3 install -r requirements.txt --target ./deps`
3. Set `READOS_BOOKS_DIR=/mnt/onboard/books` in your environment
4. Run `./kobo_launch.sh` or add to NickelMenu:
   ```
   menu_item :main :ReadOS :cmd_spawn :quiet:/mnt/onboard/.adds/reados/kobo_launch.sh
   ```
5. Open Kobo's browser → `http://localhost:8080`

---

## Configuration

Edit `config.yaml`:

```yaml
# Books location
books_dir: "./books"

# Themes: light | dark | sepia
default_theme: "light"

# Language: en | pt | es | fr | de | it
default_language: "en"

# Cloud sync (optional)
cloud:
  enabled: false
  provider: "rest"  # rest | firebase | supabase
  base_url: "https://your-cloud-api.example.com"
  api_key: "your-api-key"

# Anna's Archive mirror sources
# Update these if any domain goes down — no code changes needed
annas_archive:
  sources:
    - "https://annas-archive.gl"
    - "https://annas-archive.pk"
    - "https://annas-archive.gd"
```

All config values can also be set via environment variables:

| Variable | Description |
|---|---|
| `READOS_SECRET_KEY` | Session secret key |
| `READOS_BOOKS_DIR` | Path to books directory |
| `READOS_DB_PATH` | SQLite database path |
| `READOS_CLOUD_URL` | Cloud API base URL |
| `READOS_CLOUD_KEY` | Cloud API key |
| `READOS_PORT` | Port to listen on (default: 8080) |

---

## Cloud Sync Setup

ReadOS supports multiple cloud backends:

### Local (default — no sync)
```yaml
cloud:
  enabled: false
```

### REST API
Point to any REST server implementing:
- `POST /sync/progress`
- `GET /sync/progress/{user_id}`
- `POST /sync/bookmark`
- `DELETE /sync/bookmark/{id}`
- `POST /books/upload`
- `GET /books/{user_id}`

### Supabase (recommended free tier)
```yaml
cloud:
  enabled: true
  provider: supabase
  base_url: "https://xxxx.supabase.co"
  api_key: "your-anon-key"
```

### Firebase
```yaml
cloud:
  enabled: true
  provider: firebase
  firebase_project_id: "your-project-id"
  api_key: "your-web-api-key"
```

---

## Anna's Archive — Volunteer Maintenance

The mirror source list is **fully config-driven**. If a domain is taken down:

1. **Option A — Edit config.yaml:**
   ```yaml
   annas_archive:
     sources:
       - "https://new-mirror-domain.example"
       - "https://annas-archive.pk"
   ```
   Then restart ReadOS.

2. **Option B — Live update via Settings UI:**
   Open Settings → Anna's Archive Sources → edit the list → Save.
   Changes take effect immediately, no restart needed.

3. **Option C — API call:**
   ```bash
   curl -X PUT http://localhost:8080/api/archive/sources \
     -H "Content-Type: application/json" \
     -d '{"sources": ["https://new-mirror.example"]}'
   ```

---

## Multi-Language Support

### Adding a new language

1. Copy `locales/en.json` to `locales/<lang_code>.json` (e.g. `locales/es.json`)
2. Translate all values
3. Add the language code to `SUPPORTED_LANGUAGES` in `i18n.py`
4. Restart ReadOS — the language will appear in Settings automatically

Current languages: English (`en`), Portuguese (`pt`)

---

## Project Structure

```
ReadOS/
├── app.py              # Flask app factory & entry point
├── config.py           # Configuration management
├── config.yaml         # User configuration file
├── database.py         # SQLite database layer
├── library.py          # Book scanning, parsing, cover extraction
├── reader.py           # Reading sessions, progress, bookmarks
├── auth.py             # User accounts & session management
├── cloud.py            # Cloud sync engine (pluggable backends)
├── annas_archive.py    # Anna's Archive search & download client
├── importer.py         # Import pipeline (local, GDrive, email)
├── i18n.py             # Internationalization engine
├── routes/
│   ├── auth_routes.py
│   ├── library_routes.py
│   ├── reader_routes.py
│   ├── cloud_routes.py
│   ├── archive_routes.py
│   ├── import_routes.py
│   └── settings_routes.py
├── locales/
│   ├── en.json
│   └── pt.json
├── static/
│   ├── css/app.css
│   └── js/app.js
├── templates/
│   └── index.html
├── books/              # Local book storage
├── cache/covers/       # Extracted cover images
├── install.sh          # Setup script
├── start.sh            # Production start script
├── kobo_launch.sh      # Kobo-specific launcher
└── requirements.txt
```

---

## API Reference

### Auth
| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create account |
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/logout` | Logout |
| GET  | `/api/auth/me` | Current user |

### Library
| Method | Path | Description |
|---|---|---|
| GET  | `/api/library/books` | List books |
| POST | `/api/library/scan` | Scan books directory |
| GET  | `/api/library/covers/<id>` | Book cover image |
| GET  | `/api/library/books/<id>/epub/chapter/<n>` | EPUB chapter |
| GET  | `/api/library/books/<id>/pdf/page/<n>` | PDF page |

### Reader
| Method | Path | Description |
|---|---|---|
| GET  | `/api/reader/open/<book_id>` | Open book session |
| POST | `/api/reader/progress` | Save progress |
| GET  | `/api/reader/progress/<book_id>` | Get progress |
| POST | `/api/reader/bookmarks` | Add bookmark |

### Archive
| Method | Path | Description |
|---|---|---|
| GET  | `/api/archive/search?q=...` | Search Anna's Archive |
| POST | `/api/archive/download` | Start download |
| GET  | `/api/archive/download/<job_id>` | Poll download status |
| GET  | `/api/archive/sources` | List mirror sources |
| PUT  | `/api/archive/sources` | Update mirror sources |

---

## License

MIT License — free to use, modify, and distribute.

---

## Contributing

ReadOS is designed to be maintained by volunteers. The most impactful areas:

- **Mirror maintenance** — Update Anna's Archive sources in `config.yaml`
- **Parser improvements** — Improve EPUB/PDF parsing in `library.py`
- **New cloud backends** — Add backends in `cloud.py`
- **New languages** — Add locale files in `locales/`
- **Kobo compatibility** — Test and report issues on specific Kobo models

---
  
## Join the project

ReadOS is just getting started.

If you believe in open devices and free reading:
- ⭐ Star the repo
- 🛠️ Contribute
- 🐛 Report bugs
