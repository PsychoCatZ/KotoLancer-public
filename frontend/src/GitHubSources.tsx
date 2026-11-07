import { useEffect, useState } from "react";

export type Citation = { path: string; line_start: number; line_end: number };
export type SourceFile = {
  path: string;
  text: string;
  bytes: number;
  sha: string;
  lines?: number;
  kind?: string;
};
export type SourceSnapshot = {
  id: string;
  repository: string;
  commit_sha: string;
  facts: {
    files: SourceFile[];
    warnings: string[];
    mode?: string;
    available_files?: { path: string; size: number }[];
  };
};
export type SavedCitation = Citation & {
  repository: string;
  commit_sha: string;
  blob_sha: string;
  excerpt: string;
  kind: string;
};
export function citationUrl(
  c: Citation & { repository: string; commit_sha: string },
) {
  return `https://github.com/${c.repository}/blob/${c.commit_sha}/${c.path.split("/").map(encodeURIComponent).join("/")}#L${c.line_start}-L${c.line_end}`;
}

export function SourceChoices({
  snapshot,
  busy,
  onRead,
}: {
  snapshot: SourceSnapshot;
  busy: boolean;
  onRead: (paths: string[]) => void;
}) {
  const [query, setQuery] = useState(""),
    [paths, setPaths] = useState<string[]>([]);
  useEffect(() => {
    setPaths([]);
    setQuery("");
  }, [snapshot.id]);
  const entries = (snapshot.facts.available_files || []).filter((f) =>
    f.path.toLowerCase().includes(query.toLowerCase()),
  );
  if (!snapshot.facts.available_files)
    return (
      <p className="muted">
        У старого просмотра нет списка исходников. Запусти новый просмотр
        репозитория.
      </p>
    );
  return (
    <section className="github-sources">
      <h3>Выбрать файлы для чтения</h3>
      <p>
        Тот же коммит {snapshot.commit_sha.slice(0, 8)}. До 12 файлов, 64 КиБ на
        файл и 128 КиБ всего. Пропуски будут отмечены.
      </p>
      <label>
        Поиск по путям
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Например, telegram или src/"
        />
      </label>
      <div className="github-file-list">
        {entries.map((f) => (
          <label className="work-check" key={f.path}>
            <input
              type="checkbox"
              checked={paths.includes(f.path)}
              disabled={
                busy ||
                f.size > 65536 ||
                (!paths.includes(f.path) && paths.length >= 12)
              }
              onChange={(e) =>
                setPaths(
                  e.target.checked
                    ? [...paths, f.path]
                    : paths.filter((p) => p !== f.path),
                )
              }
            />
            <span>
              {f.path} · {f.size} байт
              {f.size > 65536 ? " · превышает предел" : ""}
            </span>
          </label>
        ))}
      </div>
      {!entries.length && (
        <p>
          В этом ограниченном списке совпадений нет. Это не вывод о наличии
          реализации.
        </p>
      )}
      <button
        type="button"
        className="secondary"
        disabled={busy || !paths.length}
        onClick={() => onRead(paths)}
      >
        Прочитать выбранные файлы · {paths.length}
      </button>
    </section>
  );
}

export function CitationPicker({
  snapshot,
  value,
  onChange,
}: {
  snapshot: SourceSnapshot;
  value: Citation[];
  onChange: (v: Citation[]) => void;
}) {
  const [path, setPath] = useState(""),
    [start, setStart] = useState(1),
    [end, setEnd] = useState(1);
  useEffect(() => {
    setPath(snapshot.facts.files[0]?.path || "");
    setStart(1);
    setEnd(1);
  }, [snapshot.id]);
  const file = snapshot.facts.files.find((f) => f.path === path);
  const lines =
    file?.text
      .split(/\r?\n/)
      .filter((_, i, a) => i !== a.length - 1 || a[i] !== "") || [];
  const valid =
    start >= 1 && end >= start && end <= lines.length && end - start < 80;
  return (
    <section className="github-sources">
      <h3>Точные основания</h3>
      <p>
        Выбери до пяти фрагментов. Зависимость или README подтверждают
        декларацию, а не работоспособность реализации.
      </p>
      <label>
        Прочитанный файл
        <select
          aria-label="Прочитанный файл"
          value={path}
          onChange={(e) => {
            setPath(e.target.value);
            setStart(1);
            setEnd(1);
          }}
        >
          {snapshot.facts.files.map((f) => (
            <option key={f.path}>{f.path}</option>
          ))}
        </select>
      </label>
      <div className="form-grid">
        <label>
          С первой строки
          <input
            type="number"
            min={1}
            value={start}
            onChange={(e) => setStart(Number(e.target.value))}
          />
        </label>
        <label>
          По строку
          <input
            type="number"
            min={1}
            value={end}
            onChange={(e) => setEnd(Number(e.target.value))}
          />
        </label>
      </div>
      <p className="muted">
        Всего строк: {lines.length}. Тип основания:{" "}
        {file?.kind === "implementation"
          ? "фрагмент исходного кода"
          : "декларация / описание"}
        .
      </p>
      {valid && (
        <pre className="github-excerpt">
          {lines
            .slice(start - 1, end)
            .map((s, i) => `${start + i} | ${s}`)
            .join("\n")}
        </pre>
      )}
      <button
        type="button"
        className="secondary"
        disabled={
          !valid ||
          value.length >= 5 ||
          value.some(
            (c) =>
              c.path === path && c.line_start === start && c.line_end === end,
          )
        }
        onClick={() =>
          onChange([...value, { path, line_start: start, line_end: end }])
        }
      >
        Добавить фрагмент в основание
      </button>
      {value.map((c, i) => (
        <p key={`${c.path}:${c.line_start}:${c.line_end}`}>
          {c.path}:{c.line_start}–{c.line_end}{" "}
          <button
            type="button"
            className="secondary"
            onClick={() => onChange(value.filter((_, n) => n !== i))}
          >
            Убрать фрагмент
          </button>
        </p>
      ))}
    </section>
  );
}
