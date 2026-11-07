import { useEffect, useState } from "react";
import { api } from "./api";

type Catalog = {
  owner: string;
  fetched_at: string;
  possibly_incomplete: boolean;
  repositories: {
    name: string;
    description: string;
    language: string;
    private: boolean;
    archived: boolean;
    fork: boolean;
  }[];
};
type CatalogState = { catalog: Catalog | null; refreshing: boolean };

export default function RepositoryPicker({
  configured,
  selected,
  onSelect,
}: {
  configured: boolean;
  selected: string;
  onSelect: (name: string) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void api<CatalogState>("/github/repositories")
      .then((s) => {
        if (active) setCatalog(s.catalog);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  const needle = query.trim().toLocaleLowerCase();
  const matches =
    catalog?.repositories.filter((r) =>
      `${r.name} ${r.description} ${r.language}`
        .toLocaleLowerCase()
        .includes(needle),
    ) ?? [];
  async function refresh() {
    setBusy(true);
    setError("");
    try {
      const state = await api<CatalogState>(
        "/github/repositories/refresh",
        "POST",
        {},
      );
      setCatalog(state.catalog);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось обновить список");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="github-sources repository-picker"
      aria-label="Каталог проектов GitHub"
    >
      <div className="repository-picker-heading">
        <h3>Выбрать проект</h3>
        <button
          type="button"
          className="secondary"
          disabled={!configured || busy}
          onClick={() => void refresh()}
        >
          {busy ? "Обновляем список…" : "Обновить список проектов"}
        </button>
      </div>
      <p>Выбери проект. Затем нажми «Прочитать репозиторий» ниже.</p>
      {error && (
        <p role="alert">
          {error} {catalog && "Показан прежний список."}
        </p>
      )}
      {!catalog && (
        <p>
          Список ещё не загружен. Нажми «Обновить список проектов» или введи имя
          вручную ниже.
        </p>
      )}
      {catalog && (
        <>
          <p>
            Владелец: {catalog.owner}. Получено:{" "}
            {new Date(catalog.fetched_at).toLocaleString("ru-RU")}.
          </p>
          <p>
            Здесь проекты, которые видны настроенному ключу GitHub. Если нужного
            нет, обнови список или введи имя вручную ниже.
          </p>
          {catalog.possibly_incomplete && (
            <p role="status">
              Достигнут предел 500 записей: список может быть неполным. Поиск
              работает только по загруженным проектам.
            </p>
          )}
          <label>
            Найти проект
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Название, описание или язык"
            />
          </label>
          <p>
            Найдено: {matches.length} из {catalog.repositories.length}.{" "}
            {selected && `Выбран: ${selected}.`}
          </p>
          {!matches.length && (
            <p>
              {catalog.repositories.length
                ? "В загруженном списке совпадений нет. Можно ввести имя вручную ниже."
                : "Для настроенного владельца проектов не найдено. Проверь выбор репозиториев у ключа или введи имя вручную."}
            </p>
          )}
          <div className="repository-list">
            {matches.map((r) => (
              <button
                type="button"
                className="secondary repository-choice"
                key={r.name}
                aria-pressed={selected === r.name}
                onClick={() => onSelect(r.name)}
              >
                <strong>{r.name}</strong>
                <span>
                  {[
                    r.private ? "Приватный" : "Публичный",
                    r.language,
                    r.archived && "Архив",
                    r.fork && "Форк",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {r.description && <span>{r.description}</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
