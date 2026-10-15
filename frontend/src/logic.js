export function padNumber(value, width) {
  const text = String(Number(value));
  const size = Number(width);
  return size ? text.padStart(size, "0") : text;
}

export function previewDest(profile, series, volume, chapter) {
  if (!profile || !series.trim()) return "";
  let vol = String(volume || "").trim();
  if (!vol) vol = profile.default_volume || "";
  else if (/^\d+$/.test(vol)) vol = padNumber(vol, profile.volume_padding);
  else return "";
  let chap = String(chapter || "").trim();
  const match = chap.match(/^(\d+)(?:\.(\d+))?$/);
  if (match) chap = padNumber(match[1], profile.chapter_padding) + (match[2] ? `.${match[2]}` : "");
  else if (chap) return "";
  const fields = { profile: profile.name, series: series.trim(), volume: vol, chapter: chap };
  if ([...profile.template.matchAll(/\{([a-z_]+)\}/g)].some((item) => !fields[item[1]])) return "";
  return profile.template.replace(/\{([a-z_]+)\}/g, (_, token) => fields[token]);
}

export function fileExt(path) {
  return (String(path).match(/\.[^.]+$/) || [".jpg"])[0].toLowerCase();
}

export function rowDest(profile, series, row, volume, chapter) {
  if (row.is_cover) return profile && series.trim() ? `${profile.name}/${series.trim()}/cover${fileExt(row.path)}` : "";
  return previewDest(profile, series, volume, chapter);
}

export function libraryMatch(library, name) {
  const key = String(name || "").trim().toLowerCase();
  if (!key) return null;
  return (library || []).find((entry) => entry.series.toLowerCase() === key) || null;
}

export function groupImports(rows) {
  const groups = new Map();
  for (const row of rows || []) {
    const key = row.work || row.path;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.entries()];
}

export const BLANK_PROFILE = {
  name: "",
  template: "{profile}/{series}/{series} Volume {volume} Chapter {chapter}.cbz",
  volume_padding: 1,
  chapter_padding: 4,
  default_volume: "1",
};

export const STATUS = {
  WAITING: "Waiting",
  PROCESSING: "Processing",
  SUCCESS: "Success",
  FAILED: "Failed",
  SKIPPED: "Skipped",
};
