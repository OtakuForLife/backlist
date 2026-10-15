# Tech context

Python 3.12 in Docker, FastAPI, Uvicorn, watchdog, Pillow, SQLite, the stdlib `zipfile`. The UI is React, built with Vite in the image; the container still runs one Python process. Tests run with pytest on the host (Python 3.11 is fine).

```text
docker compose up -d --build
```

WebUI: port 8080. Volumes: `/input`, `/output`, `/appdata`. `PUID`/`PGID` default to 99/100 for Unraid. No privileged mode and no Docker socket.

Tests: `.venv\Scripts\pytest` from the repo root.
