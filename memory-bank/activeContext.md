# Active context

The product name is Backlist. The container is `backlist` (`docker logs backlist`). The Python package is `backlist`.

Series browse, page and cover reads, and structure commit live in `backlist/library.py`. `service.py` watches the inbox, queues jobs, and imports. The web page and image routes call the library functions under other names so they do not call themselves.

The first implementation is in place: watcher, queue, WebUI, Dockerfile, compose, and README.

Checked and rejected as a full solution: Bindery (bundles chapters into one volume), cbzit (CLI only), CLU (archive conversion, not loose chapter folders), Manga Manager (metadata editor).

The page paints the side menu immediately. `GET /api/state` reads settings and the last inbox snapshot; it does not walk the inbox. The scanner fills that snapshot. Import says it is still checking until the first snapshot exists.

The WebUI is a React app built into the image and served by FastAPI. Library, Import, Activity, and Settings are separate views. Import is grouped by inbox folder: one series choice, then the files. An imported cover leaves the import list and shows on the series card. Opening a series lists its files. A CBZ row expands to the pages inside it. Edit opens a dialog for the type, volume and chapter, and the resulting paths. Apply is available only after those full paths are shown. A successful import removes the source from the inbox once the output is valid. Chapters that share one destination, such as every chapter of a Novel volume, are merged into that CBZ in chapter order. The cover moves with the type. A structure rename copies the file and deletes the old name. If the old name will not go, the new copy is removed so a commit cannot leave both. A library entry is a saved series (name, profile, cover) and does not import files. Import assigns inbox files to one of those series, or creates the entry first. Settings holds paths, the default profile, the folder-to-series map, profiles, and detection patterns.

The first inbox folder is the work (`One Piece/`). `config.json` stores `default_profile` and `works` (`One Piece` → profile, output series name, optional cover path). There is no chapter catalog. A known work's clean chapter or volume imports after the stability delay. The first chapter of a new work, an ambiguous folder, and a single file wait on an import row. `cover.jpg`, `folder.jpg`, and `poster.jpg` copy to `{profile}/{series}/cover.ext` and show on the library card. A later chapter import keeps that cover.
