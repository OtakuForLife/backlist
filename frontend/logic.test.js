import assert from "node:assert/strict";
import test from "node:test";

import { groupImports, libraryMatch, previewDest, rowDest } from "./src/logic.js";

const manga = {
  name: "Manga",
  template: "{profile}/{series}/{series} Volume {volume} Chapter {chapter}.cbz",
  volume_padding: 1,
  chapter_padding: 4,
  default_volume: "1",
};

test("manga chapter preview matches the profile template", () => {
  assert.equal(
    previewDest(manga, "One Piece", "", "1"),
    "Manga/One Piece/One Piece Volume 1 Chapter 0001.cbz",
  );
});

test("a fractional chapter keeps the fraction", () => {
  assert.equal(
    previewDest(manga, "One Piece", "2", "1.5"),
    "Manga/One Piece/One Piece Volume 2 Chapter 0001.5.cbz",
  );
});

test("leading zeros are dropped before padding", () => {
  assert.equal(
    previewDest(manga, "One Piece", "01", "001"),
    "Manga/One Piece/One Piece Volume 1 Chapter 0001.cbz",
  );
});

test("an empty chapter and a non-numeric volume do not build a path", () => {
  assert.equal(previewDest(manga, "One Piece", "1", ""), "");
  assert.equal(previewDest(manga, "One Piece", "vol", "1"), "");
});

test("library match ignores case and groups files by work", () => {
  assert.equal(libraryMatch([{ series: "one piece", profile: "Manga" }], "One Piece").series, "one piece");
  assert.equal(libraryMatch([{ series: "one piece", profile: "Manga" }], "Naruto"), null);
  assert.deepEqual(groupImports([{ work: "A", path: "A/1" }, { work: "B", path: "B/1" }, { work: "A", path: "A/2" }]).map(([work]) => work), ["A", "B"]);
});

test("a cover row uses the cover path", () => {
  assert.equal(
    rowDest(manga, "One Piece", { is_cover: true, path: "One Piece/cover.jpg" }, "", ""),
    "Manga/One Piece/cover.jpg",
  );
});
