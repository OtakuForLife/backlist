# System patterns

One process: FastAPI (`backlist/web.py`) plus a scan thread and a small worker pool (`backlist/service.py`).

- Name patterns and path pieces live in `backlist/chapter.py`. Output paths come from `profiles.parse_path`, `render_template`, and `output_from_template`. `discover` walks the inbox.
- Series browse, page and cover reads, and structure commit live in `backlist/library.py`. That commit is the only caller of the CBZ merge. `convert.write_cbz` packs the inbox.
- CBZ writing and image checks live in `backlist/convert.py`. Pillow verifies pages. ZIP entries are stored flat, in natural order, via a `.partial` file that is replaced only after `testzip`.
- SQLite jobs and `config.json` live in `/appdata` (`backlist/store.py`). A saved config overrides environment variables.
- The inbox is polled. `PollingObserver` is only a wake-up, because Unraid `/mnt/user` does not emit inotify.
- A chapter is queued only after its file fingerprint stays unchanged for `stability_seconds`.
- Existing output defaults to skip. Source delete runs only after a second successful CBZ validation.
