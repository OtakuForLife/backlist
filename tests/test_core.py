import zipfile
from pathlib import Path

import pytest
from PIL import Image

from backlist.chapter import natural_key
from backlist.profiles import DEFAULT_FORMATS, DEFAULT_PROFILES, output_from_template, parse_path, render_template
from backlist.library import add_series, commit_structure, cover_file, series_browse, series_image, series_pages
from backlist.service import Service


def jpeg(path: Path, color=(20, 40, 60)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, "JPEG")


def make_service(tmp_path: Path, **overrides):
    inbox = tmp_path / "input"
    out = tmp_path / "output"
    inbox.mkdir()
    out.mkdir()
    service = Service(tmp_path / "appdata")
    settings = {
        "input_dir": str(inbox),
        "output_dir": str(out),
        "watcher_enabled": True,
        "stability_seconds": 10,
        "worker_count": 1,
        "existing_behaviour": "skip",
        "delete_source": False,
        "chapter_padding": 4,
    }
    settings.update(overrides)
    service.store.save_settings(settings)
    return service, inbox, out


def watch(service, *names, profile="Manga"):
    settings = service.store.settings()
    for name in names:
        settings["works"][name] = {"profile": profile, "series": name}
    settings["default_profile"] = profile
    service.store.save_settings(settings)


def manga_cbz(out: Path, series: str, chapter: str, volume: str = "1") -> Path:
    return out / "Manga" / series / f"{series} Volume {volume} Chapter {chapter}.cbz"


@pytest.mark.parametrize(
    ("name", "volume", "chapter"),
    [
        ("Chapter 1", "1", "0001"),
        ("Chapter 001", "1", "0001"),
        ("Chapter 01", "1", "0001"),
        ("Ch 1", "1", "0001"),
        ("Ch. 1", "1", "0001"),
        ("c1", "1", "0001"),
        ("c001", "1", "0001"),
        ("chapter_001", "1", "0001"),
        ("Chapter-001", "1", "0001"),
        ("Chapter 12", "1", "0012"),
        ("Chapter 012", "1", "0012"),
        ("c12", "1", "0012"),
        ("Ch.12", "1", "0012"),
        ("Chapter 100", "1", "0100"),
        ("Chapter 1.5", "1", "0001.5"),
        ("Chapter 1.1", "1", "0001.1"),
        ("Chapter 100.5", "1", "0100.5"),
        ("Vol 2 Chapter 3", "2", "0003"),
    ],
)
def test_profile_names_the_chapter(name, volume, chapter):
    info = parse_path([name], DEFAULT_FORMATS)
    assert info is not None
    info["series"] = "One Piece"
    rel = render_template(DEFAULT_PROFILES[0]["template"], DEFAULT_PROFILES[0], info)
    assert rel == f"Manga/One Piece/One Piece Volume {volume} Chapter {chapter}.cbz"


@pytest.mark.parametrize("name", ["Extras", "Chapter 1 Chapter 2"])
def test_ambiguous_folder_names_are_not_guessed(name):
    assert parse_path([name], DEFAULT_FORMATS) is None or parse_path([name], DEFAULT_FORMATS)["chapter"] is None


def test_natural_sort_orders_numeric_pages():
    assert sorted(["10.jpg", "2.jpg", "1.jpg"], key=natural_key) == ["1.jpg", "2.jpg", "10.jpg"]
    assert sorted(["001.jpg", "003.jpg", "002.jpg"], key=natural_key) == ["001.jpg", "002.jpg", "003.jpg"]


def test_output_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="unsafe path"):
        output_from_template(tmp_path, "../secret.cbz")
    with pytest.raises(ValueError, match="unsafe path"):
        output_from_template(tmp_path, "foo/../../etc.cbz")
    dest = output_from_template(tmp_path, "Manga/One Piece/One Piece Volume 1 Chapter 0001.cbz")
    assert dest == (tmp_path / "Manga" / "One Piece" / "One Piece Volume 1 Chapter 0001.cbz").resolve()


def test_output_rejects_a_symlinked_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.cbz").write_bytes(b"x")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(ValueError, match="unsafe path"):
        output_from_template(tmp_path, "link/x.cbz")


def _chapter(inbox: Path) -> Path:
    chapter = inbox / "One Piece" / "Chapter 001"
    jpeg(chapter / "10.jpg", (1, 1, 1))
    jpeg(chapter / "1.jpg", (2, 2, 2))
    jpeg(chapter / "2.jpg", (3, 3, 3))
    (chapter / "notes.txt").write_text("ignore me", encoding="utf-8")
    jpeg(chapter / ".secret.jpg")
    return chapter


def test_cbz_contains_pages_in_natural_order(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "One Piece")
    _chapter(inbox)
    service.scan(now=1)
    service.process_pending()
    cbz = manga_cbz(out, "One Piece", "0001")
    assert cbz.is_file()
    with zipfile.ZipFile(cbz) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["1.jpg", "2.jpg", "10.jpg"]
        assert all("/" not in name and not name.endswith("/") for name in archive.namelist())
    assert not (inbox / "One Piece" / "Chapter 001").exists()


def test_second_scan_does_not_create_another_file(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=10)
    watch(service, "One Piece")
    _chapter(inbox)
    service.scan(now=0)
    service.process_pending()
    assert list(out.rglob("*.cbz")) == []
    service.scan(now=20)
    service.process_pending()
    service.scan(now=40)
    service.process_pending()
    assert [path.name for path in out.rglob("*.cbz")] == ["One Piece Volume 1 Chapter 0001.cbz"]


def test_unstable_copy_does_not_create_cbz(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=10)
    watch(service, "Naruto")
    chapter = inbox / "Naruto" / "Chapter 12"
    jpeg(chapter / "1.jpg")
    service.scan(now=100)
    service.process_pending()
    jpeg(chapter / "2.jpg")
    service.scan(now=105)
    service.process_pending()
    service.scan(now=114)
    service.process_pending()
    assert list(out.rglob("*.cbz")) == []
    service.scan(now=116)
    service.process_pending()
    assert [path.name for path in out.rglob("*.cbz")] == ["Naruto Volume 1 Chapter 0012.cbz"]


def test_invalid_image_does_not_delete_source(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0, delete_source=True)
    watch(service, "Bleach")
    chapter = inbox / "Bleach" / "Chapter 004"
    chapter.mkdir(parents=True)
    (chapter / "001.jpg").write_bytes(b"this is not a jpeg")
    service.scan(now=1)
    service.process_pending()
    assert (chapter / "001.jpg").is_file()
    assert list(out.rglob("*.cbz")) == []
    job = service.store.recent_jobs()[0]
    assert job["status"] == "FAILED"
    assert "damaged image" in job["error"]
    service.scan(now=2)
    service.process_pending()
    assert len(service.store.recent_jobs()) == 1
    jpeg(chapter / "001.jpg")
    assert service.retry(job["id"])
    service.process_pending()
    assert service.store.recent_jobs()[0]["status"] == "SUCCESS"
    assert not chapter.exists()


def test_replaced_image_imports_on_the_next_scan(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0, delete_source=True)
    watch(service, "Bleach")
    chapter = inbox / "Bleach" / "Chapter 004"
    chapter.mkdir(parents=True)
    (chapter / "001.jpg").write_bytes(b"this is not a jpeg")
    service.scan(now=1)
    service.process_pending()
    service.scan(now=2)
    service.process_pending()
    assert len(service.store.recent_jobs()) == 1
    assert service.store.recent_jobs()[0]["status"] == "FAILED"
    jpeg(chapter / "001.jpg")
    service.scan(now=3)
    service.process_pending()
    assert service.store.recent_jobs()[0]["status"] == "SUCCESS"
    assert not chapter.exists()
    assert list(out.rglob("*.cbz"))


def test_paused_loop_scans_only_when_asked(tmp_path, monkeypatch):
    from watchdog.observers.polling import PollingObserver

    service, inbox, out = make_service(tmp_path, watcher_enabled=False, stability_seconds=0)
    watch(service, "Naruto")
    jpeg(inbox / "Naruto" / "Chapter 1" / "1.jpg")

    def stop_after_wait(_timeout=None):
        service._stop.set()

    monkeypatch.setattr(service._wake, "wait", stop_after_wait)
    service._scan_loop()
    assert service._observer is None
    assert list(out.rglob("*.cbz")) == []
    service._stop.clear()
    service.request_scan()
    service._scan_loop()
    service.process_pending()
    assert [path.name for path in out.rglob("*.cbz")] == ["Naruto Volume 1 Chapter 0001.cbz"]
    jpeg(inbox / "Naruto" / "Chapter 2" / "1.jpg")
    service._drop_units()
    service._stop.clear()
    settings = service.store.settings()
    settings["watcher_enabled"] = True
    service.store.save_settings(settings)
    service._scan_loop()
    assert isinstance(service._observer, PollingObserver)
    service._stop_observer()
    service.process_pending()
    assert sorted(path.name for path in out.rglob("*.cbz")) == [
        "Naruto Volume 1 Chapter 0001.cbz",
        "Naruto Volume 1 Chapter 0002.cbz",
    ]


def test_unknown_chapter_name_keeps_files(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0, delete_source=True)
    watch(service, "One Piece")
    chapter = inbox / "One Piece" / "Extras"
    jpeg(chapter / "1.jpg")
    service.scan(now=1)
    service.process_pending()
    assert chapter.is_dir()
    assert list(out.rglob("*.cbz")) == []
    assert service.store.recent_jobs() == []
    assert any("ambiguous" in (row["error"] or "") for row in service.list_imports())


def test_delete_source_only_after_valid_cbz(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0, delete_source=True)
    watch(service, "One Piece")
    chapter = inbox / "One Piece" / "Chapter 002"
    jpeg(chapter / "001.jpg")
    jpeg(chapter / "002.jpg")
    service.scan(now=1)
    service.process_pending()
    cbz = manga_cbz(out, "One Piece", "0002")
    with zipfile.ZipFile(cbz) as archive:
        assert archive.namelist() == ["001.jpg", "002.jpg"]
    assert not chapter.exists()


def test_skip_rename_and_overwrite(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0, existing_behaviour="skip")
    watch(service, "One Piece")
    jpeg(inbox / "One Piece" / "Chapter 001" / "1.jpg")
    existing = manga_cbz(out, "One Piece", "0001")
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"original")
    service.scan(now=1)
    service.process_pending()
    assert existing.read_bytes() == b"original"
    assert list(out.rglob("*.cbz")) == [existing]

    service.store.save_settings({**service.store.settings(), "existing_behaviour": "rename"})
    service.scan(now=2)
    service.process_pending()
    renamed = manga_cbz(out, "One Piece", "0001").with_name("One Piece Volume 1 Chapter 0001 (2).cbz")
    assert existing.read_bytes() == b"original"
    with zipfile.ZipFile(renamed) as archive:
        assert archive.namelist() == ["1.jpg"]

    service.store.save_settings({**service.store.settings(), "existing_behaviour": "overwrite"})
    jpeg(inbox / "One Piece" / "Chapter 001" / "1.jpg")
    jpeg(inbox / "One Piece" / "Chapter 001" / "2.jpg", (9, 9, 9))
    service.scan(now=3)
    service.process_pending()
    with zipfile.ZipFile(existing) as archive:
        assert archive.namelist() == ["1.jpg", "2.jpg"]


def test_png_webp_and_gif_are_packed_text_is_not(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "One Piece")
    chapter = inbox / "One Piece" / "Chapter 010"
    chapter.mkdir(parents=True)
    Image.new("RGB", (4, 4), (1, 2, 3)).save(chapter / "a.png", "PNG")
    Image.new("RGB", (4, 4), (4, 5, 6)).save(chapter / "b.webp", "WEBP")
    Image.new("RGB", (4, 4), (7, 8, 9)).save(chapter / "c.gif", "GIF")
    (chapter / "readme.txt").write_text("nope", encoding="utf-8")
    service.scan(now=1)
    service.process_pending()
    with zipfile.ZipFile(manga_cbz(out, "One Piece", "0010")) as archive:
        assert archive.namelist() == ["a.png", "b.webp", "c.gif"]


def test_manga_profile_uses_volume_chapter_template(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "One Piece")
    jpeg(inbox / "One Piece" / "Chapter 001" / "1.jpg")
    jpeg(inbox / "One Piece" / "Volume 2" / "Chapter 12" / "1.jpg")
    service.scan(now=1)
    service.process_pending()
    first = out / "Manga" / "One Piece" / "One Piece Volume 1 Chapter 0001.cbz"
    second = out / "Manga" / "One Piece" / "One Piece Volume 2 Chapter 0012.cbz"
    assert first.is_file() and second.is_file()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["1.jpg"]


def test_novel_profile_packs_one_volume(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "Dune", profile="Novel")
    jpeg(inbox / "Dune" / "Volume 3" / "1.jpg")
    jpeg(inbox / "Dune" / "Volume 3" / "2.jpg")
    jpeg(inbox / "Dune" / "Volume 1" / "Chapter 1" / "1.jpg")
    jpeg(inbox / "Dune" / "Volume 1" / "Chapter 2" / "1.jpg")
    service.scan(now=1)
    service.process_pending()
    names = sorted(path.name for path in (out / "Novel").glob("*.cbz"))
    assert names == ["Dune Volume 1.cbz", "Dune Volume 3.cbz"]
    with zipfile.ZipFile(out / "Novel" / "Dune Volume 1.cbz") as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == 2
    assert not (out / "Novel" / "Dune").exists()


def test_state_shows_the_library_before_the_inbox_is_scanned(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    jpeg(inbox / "Bleach" / "Chapter 0001" / "001.jpg")
    add_series(service.store, {"series": "Bleach", "profile": "Manga"})
    payload = service.state()
    assert payload["imports_ready"] is False
    assert payload["imports"] == []
    assert payload["settings"]["library"][0]["series"] == "Bleach"
    assert service.list_imports()
    ready = service.state()
    assert ready["imports_ready"] is True
    assert ready["imports"][0]["path"] == "Bleach/Chapter 0001"


def test_library_entry_does_not_import(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    jpeg(inbox / "Demo" / "Chapter 0001" / "001.jpg")
    saved = add_series(service.store, {"series": "Demo", "profile": "Manga"})
    assert saved["library"] == [{"series": "Demo", "profile": "Manga", "cover": ""}]
    assert saved["works"] == {}
    service.scan(now=1)
    service.process_pending()
    assert service.store.recent_jobs() == []
    assert list(out.rglob("*.cbz")) == []


def test_cover_imports_onto_the_work(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    folder = "Hell's Paradise Jigokuraku"
    jpeg(inbox / folder / "cover.jpg")
    service.scan(now=1)
    service.process_pending()
    assert list(out.rglob("*.cbz")) == []
    cover = next(row for row in service.list_imports() if row["path"].endswith("cover.jpg"))
    assert cover["is_cover"] is True
    assert not cover["error"]
    assert service.import_files(
        {"path": f"{folder}/cover.jpg", "series": folder, "profile": "Manga"}
    ) == "cover"
    dest = out / "Manga" / folder / "cover.jpg"
    assert dest.is_file()
    assert not (inbox / folder / "cover.jpg").exists()
    assert cover_file(service.store, folder) == dest.resolve()
    assert service.store.settings()["works"][folder]["cover"].endswith("/cover.jpg")
    assert not any(row["path"].endswith("cover.jpg") for row in service.list_imports())
    jpeg(inbox / folder / "Chapter 0001" / "001.jpg")
    service.import_files(
        {"path": f"{folder}/Chapter 0001", "series": folder, "volume": "1", "chapter": "1", "profile": "Manga"}
    )
    assert service.store.settings()["works"][folder]["cover"].endswith("/cover.jpg")
    assert list(out.rglob("*.cbz")) == []


def test_rename_drops_a_source_the_mount_left_behind(tmp_path, monkeypatch):
    from backlist.library import _rename

    src = tmp_path / "a.cbz"
    dest = tmp_path / "b.cbz"
    src.write_bytes(b"page")
    real_unlink = Path.unlink
    seen = {"n": 0}

    def ignore_once(self, missing_ok=False):
        if self == src and seen["n"] == 0:
            seen["n"] += 1
            return
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", ignore_once)
    _rename(src, dest)
    assert dest.read_bytes() == b"page"
    assert not src.exists()


def test_rename_removes_the_copy_when_the_source_stays(tmp_path, monkeypatch):
    from backlist.library import _rename

    src = tmp_path / "a.cbz"
    dest = tmp_path / "b.cbz"
    src.write_bytes(b"page")
    real_unlink = Path.unlink

    def keep_source(self, missing_ok=False):
        if self == src:
            return
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", keep_source)
    with pytest.raises(OSError):
        _rename(src, dest)
    assert src.read_bytes() == b"page"
    assert not dest.exists()


def test_structure_commit_merges_chapters_into_one_volume(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    jpeg(inbox / "Bleach" / "Chapter 0001" / "001.jpg")
    jpeg(inbox / "Bleach" / "Chapter 0002" / "002.jpg", color=(1, 2, 3))
    jpeg(inbox / "Bleach" / "cover.jpg", color=(9, 9, 9))
    service.import_files({"path": "Bleach/cover.jpg", "series": "Bleach", "profile": "Manga"})
    service.import_files(
        {"path": "Bleach/Chapter 0001", "series": "Bleach", "volume": "1", "chapter": "1", "profile": "Manga"}
    )
    service.import_files(
        {"path": "Bleach/Chapter 0002", "series": "Bleach", "volume": "1", "chapter": "2", "profile": "Manga"}
    )
    first = (inbox / "Bleach" / "Chapter 0001" / "001.jpg").read_bytes()
    second = (inbox / "Bleach" / "Chapter 0002" / "002.jpg").read_bytes()
    service.process_pending()
    detail = series_browse(service.store, "Bleach")
    cbz = [item for item in detail["files"] if item["kind"] == "cbz"]
    commit_structure(service.store, 
        {
            "series": "Bleach",
            "profile": "Novel",
            "files": [
                {"path": cbz[0]["path"], "volume": "1", "chapter": cbz[0]["chapter"]},
                {"path": cbz[1]["path"], "volume": "1", "chapter": cbz[1]["chapter"]},
            ],
        }
    )
    dest = out / "Novel" / "Bleach Volume 1.cbz"
    assert dest.is_file()
    assert not manga_cbz(out, "Bleach", "0001").exists()
    assert not manga_cbz(out, "Bleach", "0002").exists()
    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["0001.jpg", "0002.jpg"]
        assert archive.read("0001.jpg") == first
        assert archive.read("0002.jpg") == second
    assert cover_file(service.store, "Bleach") == (out / "Novel" / "Bleach" / "cover.jpg").resolve()
    assert service.store.settings()["library"][0]["profile"] == "Novel"


def test_structure_commit_renames_when_the_path_differs(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    jpeg(inbox / "Bleach" / "Chapter 0001" / "001.jpg")
    jpeg(inbox / "Bleach" / "Chapter 0002" / "001.jpg", color=(1, 2, 3))
    jpeg(inbox / "Bleach" / "cover.jpg", color=(9, 9, 9))
    service.import_files({"path": "Bleach/cover.jpg", "series": "Bleach", "profile": "Manga"})
    service.import_files(
        {"path": "Bleach/Chapter 0001", "series": "Bleach", "volume": "1", "chapter": "1", "profile": "Manga"}
    )
    service.import_files(
        {"path": "Bleach/Chapter 0002", "series": "Bleach", "volume": "1", "chapter": "2", "profile": "Manga"}
    )
    first = (inbox / "Bleach" / "Chapter 0001" / "001.jpg").read_bytes()
    second = (inbox / "Bleach" / "Chapter 0002" / "001.jpg").read_bytes()
    service.process_pending()
    detail = series_browse(service.store, "Bleach")
    cbz = [item for item in detail["files"] if item["kind"] == "cbz"]
    assert [(item["volume"], item["chapter"]) for item in cbz] == [("1", "1"), ("1", "2")]
    commit_structure(service.store, 
        {
            "series": "Bleach",
            "profile": "Manga",
            "files": [
                {"path": cbz[0]["path"], "volume": "1", "chapter": "2"},
                {"path": cbz[1]["path"], "volume": "1", "chapter": "1"},
            ],
        }
    )
    with zipfile.ZipFile(manga_cbz(out, "Bleach", "0001")) as archive:
        assert archive.read("001.jpg") == second
    with zipfile.ZipFile(manga_cbz(out, "Bleach", "0002")) as archive:
        assert archive.read("001.jpg") == first
    assert cover_file(service.store, "Bleach") == (out / "Manga" / "Bleach" / "cover.jpg").resolve()

    commit_structure(service.store, 
        {
            "series": "Bleach",
            "profile": "Novel",
            "files": [
                {"path": manga_cbz(out, "Bleach", "0001").relative_to(out).as_posix(), "volume": "1", "chapter": "1"},
                {"path": manga_cbz(out, "Bleach", "0002").relative_to(out).as_posix(), "volume": "3", "chapter": "2"},
            ],
        }
    )
    assert (out / "Novel" / "Bleach Volume 1.cbz").is_file()
    assert (out / "Novel" / "Bleach Volume 3.cbz").is_file()
    assert cover_file(service.store, "Bleach") == (out / "Novel" / "Bleach" / "cover.jpg").resolve()
    assert service.store.settings()["library"][0]["profile"] == "Novel"
    assert service.store.settings()["works"]["Bleach"]["profile"] == "Novel"
    assert not manga_cbz(out, "Bleach", "0001").exists()


def test_series_browse_reads_pages_inside_the_cbz(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    jpeg(inbox / "Bleach" / "Chapter 0001" / "001.jpg", color=(10, 20, 30))
    service.import_files(
        {"path": "Bleach/Chapter 0001", "series": "Bleach", "volume": "1", "chapter": "1", "profile": "Manga"}
    )
    service.process_pending()
    detail = series_browse(service.store, "Bleach")
    cbz = next(item for item in detail["files"] if item["kind"] == "cbz")
    assert detail["tree"]["name"] == "Bleach"
    pages = series_pages(service.store, "Bleach", cbz["path"])
    assert pages == ["001.jpg"]
    data, media = series_image(service.store, "Bleach", cbz["path"], pages[0])
    assert data.startswith(b"\xff\xd8") and media == "image/jpeg"
    with pytest.raises(ValueError):
        series_pages(service.store, "Bleach", "../secret.cbz")


def test_loose_image_waits_until_volume_and_chapter_are_sent(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "Bleach")
    jpeg(inbox / "Bleach" / "page.jpg")
    service.scan(now=1)
    service.process_pending()
    assert list(out.rglob("*.cbz")) == []
    assert service.store.recent_jobs() == []
    service.import_files(
        {"path": "Bleach/page.jpg", "series": "Bleach", "volume": "1", "chapter": "4", "profile": "Manga"}
    )
    service.process_pending()
    assert manga_cbz(out, "Bleach", "0004").is_file()


def test_template_rejects_path_traversal(tmp_path):
    from backlist.store import coerce_settings

    raw = {
        "input_dir": str(tmp_path / "input"),
        "output_dir": str(tmp_path / "output"),
        "profiles": [
        {
            "name": "Manga",
            "template": "../{series}.cbz",
            "volume_padding": 1,
            "chapter_padding": 4,
            "default_volume": "1",
        }
        ],
    }
    with pytest.raises(ValueError):
        coerce_settings(raw)


def test_custom_profile_and_empty_list(tmp_path):
    service, inbox, out = make_service(
        tmp_path,
        stability_seconds=0,
        profiles=[
            {
                "name": "Manhwa",
                "template": "{profile}/{series} Chapter {chapter}.cbz",
                "volume_padding": 0,
                "chapter_padding": 2,
                "default_volume": "",
            }
        ],
    )
    jpeg(inbox / "Solo" / "Chapter 3" / "1.jpg")
    watch(service, "Solo", profile="Manhwa")
    service.scan(now=1)
    service.process_pending()
    assert (out / "Manhwa" / "Solo Chapter 03.cbz").is_file()
    other = tmp_path / "other"
    other.mkdir()
    cleared, _, _ = make_service(other, profiles=[])
    assert cleared.store.settings()["profiles"] == []


def test_unknown_work_waits_until_import(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    jpeg(inbox / "One Piece" / "Chapter 001" / "1.jpg")
    service.scan(now=1)
    service.process_pending()
    assert list(out.rglob("*.cbz")) == []
    row = service.list_imports()[0]
    assert row["path"] == "One Piece/Chapter 001"
    assert row["profile"] == "Manga"
    service.import_files(
        {
            "path": row["path"],
            "series": "One Piece",
            "volume": row["volume"],
            "chapter": row["chapter"],
            "profile": "Manga",
        }
    )
    service.process_pending()
    assert manga_cbz(out, "One Piece", "0001").is_file()
    assert service.store.settings()["works"]["One Piece"]["profile"] == "Manga"
    jpeg(inbox / "One Piece" / "Chapter 002" / "1.jpg")
    service.scan(now=2)
    service.process_pending()
    assert manga_cbz(out, "One Piece", "0002").is_file()


def test_saved_config_wins_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DELETE_SOURCE", "false")
    service, _inbox, _out = make_service(tmp_path, delete_source=True)
    assert service.store.settings()["delete_source"] is True


def test_broken_config_is_reported(tmp_path):
    service, _inbox, _out = make_service(tmp_path)
    (tmp_path / "appdata" / "config.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="config.json"):
        service.store.settings()


def test_symlink_page_stays_out_of_the_cbz(tmp_path):
    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "One Piece")
    chapter = inbox / "One Piece" / "Chapter 001"
    jpeg(chapter / "1.jpg")
    outside = tmp_path / "outside.jpg"
    jpeg(outside)
    link = chapter / "extra.jpg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted")
    service.scan(now=1)
    service.process_pending()
    with zipfile.ZipFile(manga_cbz(out, "One Piece", "0001")) as archive:
        assert archive.namelist() == ["1.jpg"]


def test_http_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backlist.web import create_app

    inbox = tmp_path / "input"
    out = tmp_path / "output"
    inbox.mkdir()
    out.mkdir()
    monkeypatch.setenv("INPUT_DIR", str(inbox.resolve()))
    monkeypatch.setenv("OUTPUT_DIR", str(out.resolve()))
    monkeypatch.delenv("APPDATA_DIR", raising=False)
    with TestClient(create_app(tmp_path / "appdata")) as client:
        assert client.get("/api/health").json() == {"ok": True}
        saved = client.put(
            "/api/settings",
            json={
                "input_dir": str(inbox.resolve()),
                "output_dir": str(out.resolve()),
                "profiles": [
                    {
                        "name": "Manga",
                        "template": "../{series}.cbz",
                        "volume_padding": 1,
                        "chapter_padding": 4,
                        "default_volume": "1",
                    }
                ],
            },
        )
        assert saved.status_code == 400
        assert client.post("/api/jobs/999/retry").status_code == 404
        assert client.get("/api/cover", params={"series": "Nope"}).status_code == 404
        assert client.get("/api/series", params={"series": "Nope"}).status_code == 404


def test_series_page_route_reads_the_cbz(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backlist.web import create_app

    service, inbox, out = make_service(tmp_path, stability_seconds=0)
    watch(service, "Bleach")
    add_series(service.store, {"series": "Bleach", "profile": "Manga"})
    jpeg(inbox / "Bleach" / "Chapter 1" / "1.jpg")
    service.scan(now=1)
    service.process_pending()
    cbz = next(out.rglob("*.cbz"))
    rel = cbz.relative_to(out).as_posix()
    monkeypatch.setenv("INPUT_DIR", str(inbox.resolve()))
    monkeypatch.setenv("OUTPUT_DIR", str(out.resolve()))
    monkeypatch.delenv("APPDATA_DIR", raising=False)
    with TestClient(create_app(tmp_path / "appdata")) as client:
        pages = client.get("/api/series/pages", params={"series": "Bleach", "path": rel})
        assert pages.status_code == 200
        name = pages.json()["pages"][0]
        image = client.get("/api/series/image", params={"series": "Bleach", "path": rel, "page": name})
        assert image.status_code == 200
        assert image.content


def test_whole_chapter_sorts_before_its_fraction():
    from backlist.library import _chapter_sort_key

    blank = _chapter_sort_key(("a.cbz", 1, ""))
    tenth = _chapter_sort_key(("b.cbz", 1, "1"))
    assert blank < tenth
