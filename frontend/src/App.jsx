import { Fragment, useEffect, useState } from "react";
import { BLANK_PROFILE, STATUS, fileExt, groupImports, libraryMatch, previewDest, rowDest } from "./logic.js";

async function readError(response) {
  const payload = await response.json().catch(() => ({}));
  return payload.detail || "Request failed";
}

export function App() {
  const [data, setData] = useState(null);
  const [view, setView] = useState("library");
  const [seriesName, setSeriesName] = useState(null);

  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const response = await fetch("/api/state");
        if (response.ok && !stop) setData(await response.json());
      } catch {
        /* the next poll retries */
      }
    }
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);

  async function refresh() {
    const response = await fetch("/api/state");
    if (response.ok) setData(await response.json());
  }

  const settings = data?.settings;
  const waiting = (data?.imports || []).length;
  const active = data ? (data.counts.WAITING || 0) + (data.counts.PROCESSING || 0) : 0;
  const [note, setNote] = useState("");
  const running = data?.watcher === "RUNNING";

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) setNote(await readError(response));
    else setNote("");
  }

  return (
    <div className="shell">
      <aside>
        <h1>Back<span>list</span></h1>
        <nav>
          <button type="button" className={view === "library" ? "on" : ""} onClick={() => { setSeriesName(null); setView("library"); }}>Library</button>
          <button type="button" className={view === "import" ? "on" : ""} onClick={() => setView("import")}>
            Import{waiting ? <em>{waiting}</em> : null}
          </button>
          <button type="button" className={view === "activity" ? "on" : ""} onClick={() => setView("activity")}>
            Activity{active ? <em>{active}</em> : null}
          </button>
          <button type="button" className={view === "settings" ? "on" : ""} onClick={() => setView("settings")}>Settings</button>
        </nav>
        <div className="aside-foot">
          <div className={running ? "stamp" : "stamp pause"}>{running ? "Running" : "Paused"}</div>
          <button type="button" className="secondary" disabled={!data} onClick={() => postJson("/api/watcher", { enabled: !running })}
          >{running ? "Pause" : "Resume"}</button>
          {note && <p className="err">{note}</p>}
          <button type="button" disabled={!data} onClick={() => fetch("/api/scan", { method: "POST" })}>Scan</button>
        </div>
      </aside>
      <main>
        {!data && <section className="card library"><h2>Library</h2><p className="hint">Loading the library…</p></section>}
        {data && view === "library" && !seriesName && <Library settings={settings} onAdded={refresh} onOpen={setSeriesName} />}
        {data && view === "library" && seriesName && (
          <SeriesDetail
            name={seriesName}
            profiles={settings.profiles || []}
            cover={Boolean((settings.library || []).find((item) => item.series === seriesName)?.cover)}
            outputDir={settings.output_dir || ""}
            onBack={() => setSeriesName(null)}
          />
        )}
        {data && view === "import" && <Import data={data} onChanged={refresh} />}
        {data && view === "activity" && <Activity data={data} onError={setNote} />}
        {data && view === "settings" && <Settings data={data} onSaved={refresh} />}
      </main>
    </div>
  );
}

function Library({ settings, onAdded, onOpen }) {
  const [name, setName] = useState("");
  const [profile, setProfile] = useState(settings.default_profile || "");
  const [error, setError] = useState("");
  const entries = settings.library || [];

  async function add(event) {
    event.preventDefault();
    setError("");
    const response = await fetch("/api/library", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series: name.trim(), profile }),
    });
    if (!response.ok) {
      setError(await readError(response));
      return;
    }
    setName("");
    onAdded();
  }

  return (
    <section className="card library">
      <div className="split">
        <h2>Library</h2>
        <span className="muted">{entries.length} series</span>
      </div>
      <form className="add-series" onSubmit={add}>
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Series name" required />
        <select value={profile} onChange={(event) => setProfile(event.target.value)}>
          {(settings.profiles || []).map((item) => <option key={item.name}>{item.name}</option>)}
        </select>
        <button type="submit">Add</button>
      </form>
      {error && <p className="err">{error}</p>}
      {entries.length === 0 ? <p className="hint">No series yet.</p> : (
        <div className="grid">
          {entries.map((entry, index) => (
            <button type="button" className="poster" key={entry.series} style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }} onClick={() => onOpen(entry.series)}>
              <div className="poster-art">
                {entry.cover
                  ? <img alt="" src={`/api/cover?series=${encodeURIComponent(entry.series)}`} />
                  : <div className="poster-fallback">{entry.series.slice(0, 1)}</div>}
              </div>
              <b title={entry.series}>{entry.series}</b>
              <span className="muted">{entry.profile}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function formatSize(size) {
  if (size >= 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

function fullPath(root, rel) {
  const base = String(root || "").replace(/\\/g, "/").replace(/\/$/, "");
  return base ? `${base}/${rel}` : rel;
}

function SeriesBar({ name, coverSrc, onBack, status, onEdit }) {
  return (
    <div className={coverSrc ? "series-bar" : "series-bar bare"}>
      <button type="button" className="back" onClick={onBack}>Library</button>
      {coverSrc && <img className="series-cover" alt="" src={coverSrc} />}
      <div className="series-id">
        <h2>{name}</h2>
        {status && <p className="status-line">{status}</p>}
      </div>
      {onEdit && <button type="button" className="commit" onClick={onEdit}>Edit</button>}
    </div>
  );
}

function SeriesDetail({ name, profiles, cover, outputDir, onBack }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    setDetail(null);
    fetch(`/api/series?series=${encodeURIComponent(name)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json();
      })
      .then((payload) => { if (!stop) setDetail(payload); })
      .catch((reason) => { if (!stop) setError(reason.message); });
    return () => { stop = true; };
  }, [name]);

  if (error) return <section className="card"><p className="err">{error}</p></section>;
  if (!detail) {
    return (
      <section className="series">
        <SeriesBar name={name} coverSrc={cover ? `/api/cover?series=${encodeURIComponent(name)}` : ""} onBack={onBack} status="Loading files…" />
      </section>
    );
  }
  return <Structure name={name} detail={detail} profiles={profiles} outputDir={outputDir} onBack={onBack} onDone={setDetail} />;
}

function otherTarget(profile, savedProfile, series, file) {
  if (file.is_cover) {
    return `${profile.name}/${series.trim()}/cover${fileExt(file.path)}`;
  }
  if (profile.name === savedProfile) return file.path;
  const parts = file.path.split("/");
  if (parts[0] !== savedProfile) return file.path;
  return [profile.name, ...parts.slice(1)].join("/");
}

function Structure({ name, detail, profiles, outputDir, onBack, onDone }) {
  const [open, setOpen] = useState("");
  const [pages, setPages] = useState(null);
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");
  const coverFile = detail.files.find((file) => file.is_cover);
  const coverSrc = detail.has_cover
    ? `/api/cover?series=${encodeURIComponent(name)}`
    : coverFile
      ? `/api/series/image?series=${encodeURIComponent(name)}&path=${encodeURIComponent(coverFile.path)}`
      : "";

  useEffect(() => {
    if (!open) {
      setPages(null);
      return undefined;
    }
    let stop = false;
    setPages(null);
    fetch(`/api/series/pages?series=${encodeURIComponent(name)}&path=${encodeURIComponent(open)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json();
      })
      .then((payload) => { if (!stop) setPages(payload.pages || []); })
      .catch((reason) => { if (!stop) { setPages([]); setNote(reason.message); } });
    return () => { stop = true; };
  }, [name, open]);

  return (
    <section className="series">
      <SeriesBar name={name} coverSrc={coverSrc} onBack={onBack} onEdit={() => setEditing(true)} />
      {note && <p className="err">{note}</p>}
      <div className="series-table">
        <table>
          <thead>
            <tr>
              <th />
              <th>File</th>
              <th>Volume</th>
              <th>Chapter</th>
              <th>Size</th>
            </tr>
          </thead>
          <tbody>
            {detail.files.map((file) => (
              <Fragment key={file.path}>
                <tr>
                  <td>
                    {file.kind === "cbz" && (
                      <button type="button" className="icon" aria-expanded={open === file.path} aria-label={`Contents of ${file.name}`} onClick={() => setOpen(open === file.path ? "" : file.path)}>
                        {open === file.path ? "▾" : "▸"}
                      </button>
                    )}
                  </td>
                  <td className="file" title={file.path}>{file.name}</td>
                  <td>{file.kind === "cbz" ? file.volume : ""}</td>
                  <td>{file.kind === "cbz" ? file.chapter : ""}</td>
                  <td>{formatSize(file.size || 0)}</td>
                </tr>
                {open === file.path && (
                  pages === null ? (
                    <tr className="inside"><td /><td colSpan={4}>Opening…</td></tr>
                  ) : pages.length ? pages.map((page) => (
                    <tr className="inside" key={page}>
                      <td />
                      <td className="file" colSpan={3}>{page}</td>
                      <td />
                    </tr>
                  )) : (
                    <tr className="inside"><td /><td colSpan={4}>No pages in this file.</td></tr>
                  )
                )}
              </Fragment>
            ))}
            {!detail.files.length && (
              <tr><td colSpan={5}>Nothing imported for this series yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {editing && (
        <EditSeries
          name={name}
          detail={detail}
          profiles={profiles}
          outputDir={outputDir}
          onClose={() => setEditing(false)}
          onDone={(payload) => { setEditing(false); setOpen(""); onDone(payload); }}
        />
      )}
    </section>
  );
}

function EditSeries({ name, detail, profiles, outputDir, onClose, onDone }) {
  const [profileName, setProfileName] = useState(detail.profile);
  const [cells, setCells] = useState({});
  const [picked, setPicked] = useState(() => new Set());
  const [bulkVol, setBulkVol] = useState("");
  const [bulkCh, setBulkCh] = useState("");
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const profile = profiles.find((item) => item.name === profileName);
  const needsVolume = !!profile?.template.includes("{volume}");
  const needsChapter = !!profile?.template.includes("{chapter}");

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape" && !busy) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  function value(file, key) {
    return cells[file.path]?.[key] ?? file[key] ?? "";
  }

  function target(file) {
    if (!profile) return "";
    if (file.kind === "cbz") return previewDest(profile, name, value(file, "volume"), value(file, "chapter"));
    return otherTarget(profile, detail.profile, name, file);
  }

  const chapters = detail.files.filter((file) => file.kind === "cbz");
  const planned = detail.files.map((file) => ({ file, dest: target(file) }));
  const incomplete = chapters.some((file) => !target(file));
  const dirty = profileName !== detail.profile || planned.some((row) => row.dest && row.dest !== row.file.path);
  const moves = new Map();
  for (const row of planned) {
    if (!row.dest || row.dest === row.file.path) continue;
    const group = moves.get(row.dest) || [];
    group.push(row.file.path);
    moves.set(row.dest, group);
  }

  function setField(file, key, next) {
    setCells((current) => ({
      ...current,
      [file.path]: {
        volume: key === "volume" ? next : (current[file.path]?.volume ?? file.volume ?? ""),
        chapter: key === "chapter" ? next : (current[file.path]?.chapter ?? file.chapter ?? ""),
      },
    }));
    setReview(false);
  }

  function toggle(path) {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function applyBulk() {
    setCells((current) => {
      const next = { ...current };
      for (const file of chapters) {
        if (!picked.has(file.path)) continue;
        const saved = current[file.path];
        next[file.path] = {
          volume: bulkVol !== "" ? bulkVol : (saved?.volume ?? file.volume ?? ""),
          chapter: bulkCh !== "" ? bulkCh : (saved?.chapter ?? file.chapter ?? ""),
        };
      }
      return next;
    });
    setReview(false);
  }

  async function apply() {
    setBusy(true);
    setNote("");
    try {
      const response = await fetch("/api/series/structure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          series: name,
          profile: profileName,
          files: chapters.map((file) => ({
            path: file.path,
            volume: value(file, "volume"),
            chapter: value(file, "chapter"),
          })),
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      onDone(await response.json());
    } catch (error) {
      setNote(error.message);
      setBusy(false);
    }
  }

  const allOn = chapters.length > 0 && chapters.every((file) => picked.has(file.path));

  return (
    <div className="modal-back">
      <div className="modal" role="dialog" aria-modal="true" aria-label={`Edit ${name}`}>
        <h2>Edit {name}</h2>
        {review ? (
          <>
            <p className="hint">Approve the paths below. Nothing is written until you apply.</p>
            {profileName !== detail.profile && <p>Type {detail.profile} → {profileName}</p>}
            {[...moves.entries()].map(([dest, sources]) => (
              <div className="path-change" key={dest}>
                {sources.map((path) => <div key={path}>{fullPath(outputDir, path)}</div>)}
                <div className="after">→ {fullPath(outputDir, dest)}</div>
              </div>
            ))}
            {!moves.size && <p className="hint">No file paths change.</p>}
          </>
        ) : (
          <>
            <label><span>Type</span>
              <select aria-label="Type" value={profileName} onChange={(event) => { setProfileName(event.target.value); setReview(false); }}>
                {profiles.map((item) => <option key={item.name}>{item.name}</option>)}
              </select>
            </label>
            <h3>Metadata</h3>
            <div className="bulk">
              <span>{picked.size} selected</span>
              {needsVolume && <input aria-label="Bulk volume" placeholder="Volume" value={bulkVol} onChange={(event) => setBulkVol(event.target.value)} />}
              {needsChapter && <input aria-label="Bulk chapter" placeholder="Chapter" value={bulkCh} onChange={(event) => setBulkCh(event.target.value)} />}
              <button type="button" className="tiny" disabled={!picked.size || (bulkVol === "" && bulkCh === "")} onClick={applyBulk}>Apply</button>
            </div>
            <table className="edit-table">
              <thead>
                <tr>
                  <th><input className="tick" type="checkbox" aria-label="Select all chapters" checked={allOn} onChange={() => setPicked(allOn ? new Set() : new Set(chapters.map((file) => file.path)))} /></th>
                  <th>File</th>
                  {needsVolume && <th>Volume</th>}
                  {needsChapter && <th>Chapter</th>}
                </tr>
              </thead>
              <tbody>
                {chapters.map((file) => (
                  <tr key={file.path}>
                    <td><input className="tick" type="checkbox" aria-label={`Select ${file.name}`} checked={picked.has(file.path)} onChange={() => toggle(file.path)} /></td>
                    <td className="file" title={file.path}>{file.name}</td>
                    {needsVolume && <td><input aria-label={`Volume for ${file.name}`} value={value(file, "volume")} onChange={(event) => setField(file, "volume", event.target.value)} /></td>}
                    {needsChapter && <td><input aria-label={`Chapter for ${file.name}`} value={value(file, "chapter")} onChange={(event) => setField(file, "chapter", event.target.value)} /></td>}
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>File structure</h3>
            <div className="path-list">
              {planned.filter((row) => row.file.kind === "cbz" || row.dest !== row.file.path).map((row) => (
                <div className="path-change" key={row.file.path}>
                  <div>{fullPath(outputDir, row.file.path)}</div>
                  <div className={row.dest && row.dest !== row.file.path ? "after" : ""}>{row.dest ? fullPath(outputDir, row.dest) : "Needs numbers"}</div>
                </div>
              ))}
            </div>
          </>
        )}
        {note && <p className="err">{note}</p>}
        {incomplete && <p className="err">Each file needs the numbers this type uses.</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={busy} onClick={() => (review ? setReview(false) : onClose())}>{review ? "Back" : "Cancel"}</button>
          {review ? (
            <button type="button" disabled={busy || incomplete || !dirty} onClick={apply}>{busy ? "Applying…" : "Apply"}</button>
          ) : (
            <button type="button" disabled={incomplete || !dirty} onClick={() => setReview(true)}>Review changes</button>
          )}
        </div>
      </div>
    </div>
  );
}

function Import({ data, onChanged }) {
  const [choices, setChoices] = useState({});
  const [cells, setCells] = useState({});
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const settings = data.settings;
  const library = settings.library || [];
  const profiles = settings.profiles || [];
  const groups = groupImports(data.imports);

  function choiceFor(work, rows) {
    const saved = choices[work];
    if (saved) return saved;
    const suggested = rows[0]?.series || work;
    const match = libraryMatch(library, suggested);
    return {
      mode: match ? "pick" : "new",
      series: match ? match.series : suggested,
      profile: match ? match.profile : (rows[0]?.profile || settings.default_profile || ""),
    };
  }

  function setChoice(work, rows, patch) {
    setChoices((current) => {
      const saved = current[work];
      const suggested = rows[0]?.series || work;
      const match = libraryMatch(library, saved?.series || suggested);
      const base = saved || {
        mode: match ? "pick" : "new",
        series: match ? match.series : suggested,
        profile: match ? match.profile : (rows[0]?.profile || settings.default_profile || ""),
      };
      return { ...current, [work]: { ...base, ...patch } };
    });
  }

  function cell(row) {
    return {
      volume: cells[row.path]?.volume ?? row.volume,
      chapter: cells[row.path]?.chapter ?? row.chapter,
    };
  }

  function profileNamed(name) {
    return profiles.find((item) => item.name === name);
  }

  async function addSeries(work, rows) {
    const choice = choiceFor(work, rows);
    setNote("");
    const response = await fetch("/api/library", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series: choice.series.trim(), profile: choice.profile }),
    });
    if (!response.ok) {
      setNote(await readError(response));
      return;
    }
    setChoices((current) => ({
      ...current,
      [work]: { mode: "pick", series: choice.series.trim(), profile: choice.profile },
    }));
    onChanged();
  }

  async function importRow(row, choice) {
    const edited = cell(row);
    const response = await fetch("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: row.path,
        series: choice.series.trim(),
        profile: choice.profile,
        volume: row.is_cover ? "" : edited.volume,
        chapter: row.is_cover ? "" : edited.chapter,
      }),
    });
    if (!response.ok) throw new Error(await readError(response));
  }

  async function importMany(work, rows) {
    const choice = choiceFor(work, rows);
    const ready = rows.filter((row) => rowDest(profileNamed(choice.profile), choice.series, row, cell(row).volume, cell(row).chapter));
    setBusy(work);
    setNote("");
    try {
      for (const row of ready) await importRow(row, choice);
      onChanged();
    } catch (error) {
      setNote(error.message);
    } finally {
      setBusy("");
    }
  }

  if (data.imports_ready === false) {
    return <section className="card"><h2>Import</h2><p className="hint">Checking the inbox…</p></section>;
  }
  if (!groups.length) {
    return <section className="card"><h2>Import</h2><p className="hint">Nothing is waiting for a series.</p></section>;
  }

  return (
    <section className="stack-list">
      <div className="card">
        <h2>Import</h2>
        <p className="hint">Each folder is one series. Add it to the library, then import the files. A cover image is not a chapter.</p>
        {note && <p className="err">{note}</p>}
      </div>
      {groups.map(([work, rows]) => {
        const choice = choiceFor(work, rows);
        const known = libraryMatch(library, choice.series);
        const profile = profileNamed(choice.profile);
        const ready = rows.filter((row) => known && rowDest(profile, choice.series, row, cell(row).volume, cell(row).chapter));
        return (
          <article className="card group" key={work}>
            <div className="split">
              <h3>{work}</h3>
              <span className="muted">{rows.length} files</span>
            </div>
            <div className="group-bar">
              {choice.mode === "pick" ? (
                <select value={known ? known.series : ""} onChange={(event) => {
                  if (event.target.value === "__new__") {
                    setChoice(work, rows, { mode: "new", series: choice.series, profile: choice.profile });
                    return;
                  }
                  const picked = libraryMatch(library, event.target.value);
                  setChoice(work, rows, { mode: "pick", series: picked.series, profile: picked.profile });
                }}
                >
                  {!known && <option value="">Choose a series</option>}
                  {library.map((entry) => <option key={entry.series}>{entry.series}</option>)}
                  <option value="__new__">New series…</option>
                </select>
              ) : (
                <input
                  value={choice.series}
                  placeholder="Series name"
                  onChange={(event) => setChoice(work, rows, { mode: "new", series: event.target.value })}
                />
              )}
              <select value={choice.profile} onChange={(event) => setChoice(work, rows, { profile: event.target.value })}>
                {profiles.map((item) => <option key={item.name}>{item.name}</option>)}
              </select>
              {known ? (
                <button type="button" disabled={!ready.length || busy === work} onClick={() => importMany(work, rows)}>
                  {busy === work ? "Importing…" : `Import ${ready.length}`}
                </button>
              ) : (
                <button type="button" disabled={!choice.series.trim()} onClick={() => addSeries(work, rows)}>Add to library</button>
              )}
            </div>
            <div className="file-list">
              {rows.map((row) => {
                const edited = cell(row);
                const dest = rowDest(profile, choice.series, row, edited.volume, edited.chapter);
                const label = row.path.startsWith(`${work}/`) ? row.path.slice(work.length + 1) : row.path;
                return (
                  <div className="file-row" key={row.path} title={dest || row.error || row.path}>
                    <span className="file">{label}</span>
                    {row.is_cover ? <span className="chip">Cover</span> : (
                      <>
                        <input className="vol" aria-label="Volume" value={edited.volume} onChange={(event) => setCells((current) => ({ ...current, [row.path]: { ...edited, volume: event.target.value } }))} />
                        <input className="ch" aria-label="Chapter" value={edited.chapter} onChange={(event) => setCells((current) => ({ ...current, [row.path]: { ...edited, chapter: event.target.value } }))} />
                      </>
                    )}
                    <button type="button" className="tiny" disabled={!known || !dest || busy === work} onClick={() => importMany(work, [row])}>Import</button>
                  </div>
                );
              })}
            </div>
          </article>
        );
      })}
    </section>
  );
}

function Activity({ data, onError }) {
  const [showAll, setShowAll] = useState(false);
  const jobs = data.jobs || [];
  const active = jobs.filter((job) => job.status === "WAITING" || job.status === "PROCESSING");
  const done = jobs.filter((job) => job.status !== "WAITING" && job.status !== "PROCESSING");
  const counts = data.counts || {};
  return (
    <section className="card">
      <div className="split">
        <h2>Activity</h2>
        <span className="muted">{counts.WAITING || 0} waiting · {counts.PROCESSING || 0} active · {counts.SUCCESS || 0} done · {counts.FAILED || 0} failed</span>
      </div>
      <h3>Queue</h3>
      {active.length ? active.map((job) => <Job key={job.id} job={job} onError={onError} />) : <p className="hint">Nothing in the queue.</p>}
      <div className="split">
        <h3>Recent</h3>
        {done.length > 5 && <button type="button" className="tiny" onClick={() => setShowAll((value) => !value)}>{showAll ? "Show less" : "Show all"}</button>}
      </div>
      <div className="scroll">
        {done.length ? done.slice(0, showAll ? 40 : 5).map((job) => <Job key={job.id} job={job} onError={onError} />) : <p className="hint">Nothing processed yet.</p>}
      </div>
      <details>
        <summary>Log</summary>
        <pre>{(data.logs || []).slice(-40).map((line) => `${line.ts.slice(11)} ${line.level} ${line.message}`).join("\n")}</pre>
      </details>
    </section>
  );
}

function Job({ job, onError }) {
  const cls = job.status === "SUCCESS" ? "ok" : job.status === "FAILED" ? "bad" : "";
  async function retry() {
    const response = await fetch(`/api/jobs/${job.id}/retry`, { method: "POST" });
    if (!response.ok) onError(await readError(response));
    else onError("");
  }
  return (
    <div className="rowline">
      <div>
        <b className={cls}>{STATUS[job.status] || job.status}</b> {job.output_name || job.source_rel}
        {job.error && <span className="err">{job.error}</span>}
      </div>
      {(job.status === "FAILED" || job.status === "SKIPPED") && (
        <button type="button" className="tiny" onClick={retry}>Retry</button>
      )}
    </div>
  );
}

function Settings({ data, onSaved }) {
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [note, setNote] = useState("");
  const stamp = JSON.stringify(data.settings);

  useEffect(() => {
    if (!dirty) setDraft(structuredClone(data.settings));
  }, [stamp, dirty, data.settings]);

  if (!draft) return null;

  function patch(partial) {
    setDirty(true);
    setDraft((current) => ({ ...current, ...partial }));
  }

  function editProfile(index, fields) {
    const profiles = draft.profiles.slice();
    profiles[index] = { ...profiles[index], ...fields };
    patch({ profiles });
  }

  async function save(event) {
    event.preventDefault();
    const body = {
      ...draft,
      stability_seconds: Number(draft.stability_seconds),
      worker_count: Number(draft.worker_count),
      delete_source: !!draft.delete_source,
      watcher_enabled: data.watcher === "RUNNING",
      library: data.settings.library || [],
      formats: {
        ...draft.formats,
        chapter_patterns: (draft.formats?.chapter_patterns || []).map((line) => line.trim()).filter(Boolean),
      },
      profiles: (draft.profiles || []).map((profile) => ({
        ...profile,
        volume_padding: Number(profile.volume_padding),
        chapter_padding: Number(profile.chapter_padding),
      })),
    };
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      setNote(await readError(response));
      return;
    }
    setDirty(false);
    setNote("Saved");
    onSaved();
  }

  const works = Object.entries(draft.works || {});
  return (
    <section className="card">
      <form onSubmit={save}>
        <div className="form-grid">
          <label className="wide"><span>Input</span><input required value={draft.input_dir} onChange={(event) => patch({ input_dir: event.target.value })} /></label>
          <label className="wide"><span>Output</span><input required value={draft.output_dir} onChange={(event) => patch({ output_dir: event.target.value })} /></label>
          <label><span>Stability (seconds)</span><input type="number" min="0" max="3600" required value={draft.stability_seconds} onChange={(event) => patch({ stability_seconds: event.target.value })} /></label>
          <label><span>Workers</span><input type="number" min="1" max="8" required value={draft.worker_count} onChange={(event) => patch({ worker_count: event.target.value })} /></label>
          <label><span>Existing file</span>
            <select value={draft.existing_behaviour} onChange={(event) => patch({ existing_behaviour: event.target.value })}>
              <option value="skip">Skip</option>
              <option value="overwrite">Overwrite</option>
              <option value="rename">Rename</option>
            </select>
          </label>
          <label className="wide"><span>Default profile</span>
            <select value={draft.default_profile} onChange={(event) => patch({ default_profile: event.target.value })}>
              {(draft.profiles || []).map((profile) => <option key={profile.name}>{profile.name}</option>)}
            </select>
          </label>
        </div>
        <h2>Folder links</h2>
        <p className="hint">Created when a folder is imported. Removing a link makes the next chapter wait.</p>
        {works.map(([folder, work]) => (
          <div className="profile" key={folder}>
            <div className="split"><b>{folder}</b><button type="button" className="tiny" onClick={() => {
              const next = { ...draft.works };
              delete next[folder];
              patch({ works: next });
            }}>Remove</button></div>
            <div className="form-grid">
              <label><span>Series</span><input value={work.series} onChange={(event) => patch({ works: { ...draft.works, [folder]: { ...work, series: event.target.value } } })} /></label>
              <label><span>Profile</span>
                <select value={work.profile} onChange={(event) => patch({ works: { ...draft.works, [folder]: { ...work, profile: event.target.value } } })}>
                  {(draft.profiles || []).map((profile) => <option key={profile.name}>{profile.name}</option>)}
                </select>
              </label>
            </div>
          </div>
        ))}
        <h2>Profiles</h2>
        <p className="hint">A profile is the output layout. Tokens: {"{profile} {series} {volume} {chapter}"}.</p>
        {(draft.profiles || []).map((profile, index) => (
          <div className="profile" key={index}>
            <div className="split">
              <label style={{ flex: 1, margin: 0 }}><span>Folder name</span><input required value={profile.name} onChange={(event) => editProfile(index, { name: event.target.value })} /></label>
              <button type="button" className="tiny" onClick={() => patch({ profiles: draft.profiles.filter((_, item) => item !== index) })}>Remove</button>
            </div>
            <label><span>Template</span><input required value={profile.template} onChange={(event) => editProfile(index, { template: event.target.value })} /></label>
            <details>
              <summary>Number format</summary>
              <div className="form-grid">
                <label><span>Volume padding</span><input type="number" min="0" max="8" value={profile.volume_padding} onChange={(event) => editProfile(index, { volume_padding: event.target.value })} /></label>
                <label><span>Chapter padding</span><input type="number" min="1" max="8" value={profile.chapter_padding} onChange={(event) => editProfile(index, { chapter_padding: event.target.value })} /></label>
                <label className="wide"><span>Volume when none is detected</span><input placeholder="empty = error" value={profile.default_volume || ""} onChange={(event) => editProfile(index, { default_volume: event.target.value })} /></label>
              </div>
            </details>
          </div>
        ))}
        <button type="button" className="tiny" onClick={() => patch({ profiles: [...(draft.profiles || []), { ...BLANK_PROFILE }] })}>Add profile</button>
        <details>
          <summary>Detection patterns</summary>
          <label><span>Volume</span><input value={draft.formats?.volume_pattern || ""} onChange={(event) => patch({ formats: { ...draft.formats, volume_pattern: event.target.value } })} /></label>
          <label><span>Chapter, one pattern per line</span><textarea rows="3" value={(draft.formats?.chapter_patterns || []).join("\n")} onChange={(event) => patch({ formats: { ...draft.formats, chapter_patterns: event.target.value.split(/\r?\n/) } })} /></label>
        </details>
        <button type="submit">Save</button>
        <p className="note">{note}</p>
      </form>
    </section>
  );
}
