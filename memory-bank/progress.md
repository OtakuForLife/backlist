# Progress

Works:

- Profiles Manga and Novel, chosen by the first inbox folder, with templates and detection patterns in `config.json`

- Chapter parsing, natural sort, flat CBZ, skip/overwrite/rename
- Stability gate, failed jobs keep the source, optional delete only after a valid CBZ
- English WebUI: library cards with covers, import table, queue, recent jobs, settings, works, and add/remove profiles
- Series Structure: one type for the work, volume and chapter per file, commit renames output paths that differ
- Work folders import through a saved profile. New works and single files wait for a mapping. Cover images import onto the work
- The shelf no longer waits on an inbox walk. `/api/state` uses the scanner's cached snapshot.
- `pytest`: 50 passed, 2 skipped (symlink tests, when the OS refuses the link)
- Shelf browse and structure commit live in `backlist/library.py`. The watcher stays in `service.py`.
- Live watcher smoke test produced `One Piece c0001.cbz`, `One Piece c0002.cbz`, and waited out a mid-copy Naruto chapter
- Docker Compose end-to-end: copying `One Piece/Chapter 001` and `Chapter 002` produced `One Piece c0001.cbz` and `One Piece c0002.cbz`, each with exactly `001.jpg`, `002.jpg`, `003.jpg`. A page added mid-copy reset the stability timer; `notes.txt` stayed out of the archive. Sources were kept.

Known limits:

- A folder named `Vol 2 Chapter 3` is volume 2, chapter 3. Nested folders under a work are walked. The series screen reads pages out of the CBZ.
- A folder with no number waits for an import row.
- No login. Keep port 8080 on the LAN.
