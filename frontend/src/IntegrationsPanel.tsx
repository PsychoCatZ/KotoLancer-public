import { useEffect, useState } from "react";
import { api, type ProfileResponse } from "./api";
import RepositoryPicker from "./RepositoryPicker";
import GitHubReview from "./GitHubReview";
import {
  SourceChoices,
  CitationPicker,
  citationUrl,
  type SourceSnapshot,
  type Citation,
  type SavedCitation,
} from "./GitHubSources";

type Status = {
  telegram_configured: boolean;
  telegram_polling: boolean;
  telegram_last_poll: string | null;
  mini_app_url: string;
  github_configured: boolean;
  github_owner: string;
  telegram_failures: { update_id: number; error: string }[];
};
type Snapshot = SourceSnapshot;
type ReadJob = {
  id: string;
  repository: string;
  status: string;
  error: string;
  snapshot_id: string | null;
  progress: { done?: number; total?: number };
};
type Proposal = {
  id: string;
  statement: string;
  capability_ids: string[];
  project_id: string | null;
  base_revision: number;
  status: string;
  accepted_revision: number | null;
  citations: SavedCitation[];
};

export default function IntegrationsPanel({
  onUpdated,
}: {
  onUpdated: () => Promise<void>;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [repository, setRepository] = useState("");
  const [selected, setSelected] = useState("");
  const [capability, setCapability] = useState("");
  const [project, setProject] = useState("");
  const [statement, setStatement] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reads, setReads] = useState<ReadJob[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  useEffect(() => setCitations([]), [selected]);
  const snapshot = snapshots.find((s) => s.id === selected);

  async function load() {
    const [state, reads, pending, current, jobs] = await Promise.all([
      api<Status>("/integrations"),
      api<Snapshot[]>("/github/snapshots"),
      api<Proposal[]>("/github/proposals"),
      api<ProfileResponse>("/profile"),
      api<ReadJob[]>("/github/reads"),
    ]);
    setStatus(state);
    setSnapshots((previous) =>
      [
        ...reads,
        ...previous.filter((p) => !reads.some((s) => s.id === p.id)),
      ].slice(0, 100),
    );
    setProposals(pending);
    setProfile(current);
    setReads(jobs);
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, []);
  const active = reads.some((j) => ["queued", "running"].includes(j.status));
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(
      () => void load().catch((e) => setError(e.message)),
      2000,
    );
    return () => window.clearInterval(timer);
  }, [active]);
  async function work(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await load();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">НА СВЯЗИ С МАСТЕРСКОЙ</div>
          <h1>Подключения</h1>
          <p>Пересылки из Telegram и проверяемые свидетельства из GitHub.</p>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void work(async () => {})}
        >
          Обновить
        </button>
      </div>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      <section className="panel integration-panel">
        <h2>Telegram</h2>
        <p>
          {status?.telegram_polling
            ? "Принимаю твои сообщения и файлы во Входящие."
            : status?.telegram_configured
              ? "Бот настроен; активного приёма сейчас не видно."
              : "Бот ещё не подключён."}
        </p>
        <p>
          {status?.mini_app_url
            ? "Кнопка мастерской доступна в ответах бота."
            : "Пока материалы можно смотреть в локальной мастерской. Открытие Mini App с телефона появится после настройки HTTPS-адреса."}
        </p>
        {status?.telegram_failures.map((failure) => (
          <p className="error-text" key={failure.update_id}>
            Сообщение {failure.update_id}: {failure.error}
          </p>
        ))}
      </section>
      <section className="panel integration-panel">
        <h2>GitHub · только чтение</h2>
        <p>
          Сначала сохраним коммит и список файлов, затем выберем исходники и
          точные строки. Чтение идёт в фоне, код не запускается. Свидетельство
          меняет профиль только после твоего подтверждения; категории опыта
          сохраняются.
        </p>
        <RepositoryPicker
          configured={!!status?.github_configured}
          selected={repository}
          onSelect={setRepository}
        />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void work(async () => {
              await api<ReadJob>("/github/inspect", "POST", {
                repository,
                request_key: crypto.randomUUID(),
              });
              setNotice(
                "Чтение поставлено в очередь. Мастерской можно пользоваться дальше.",
              );
            });
          }}
          className="integration-form"
        >
          <label>
            Репозиторий {status?.github_owner}/
            <input
              required
              maxLength={100}
              value={repository}
              onChange={(e) => setRepository(e.target.value)}
              placeholder="KotoLancer"
            />
          </label>
          <button
            className="primary"
            disabled={
              busy ||
              !status?.github_configured ||
              reads.some(
                (j) =>
                  j.repository === repository &&
                  ["queued", "running"].includes(j.status),
              )
            }
          >
            Прочитать репозиторий
          </button>
        </form>
        {status && !status.github_configured && (
          <p>Для чтения нужен отдельный ключ GitHub.</p>
        )}
        {!!reads.length && (
          <section className="github-sources">
            <h3>Чтения репозиториев</h3>
            {reads.map((j) => (
              <article key={j.id} className="integration-proposal">
                <strong>
                  {j.repository} ·{" "}
                  {(
                    {
                      queued: "В очереди",
                      running: "Читаем",
                      succeeded: "Готово",
                      failed: "Не завершено",
                      cancelled: "Отменено",
                    } as Record<string, string>
                  )[j.status] || j.status}
                </strong>
                {j.progress.total !== undefined && (
                  <p>
                    Обработано файлов: {j.progress.done} из {j.progress.total}
                  </p>
                )}
                {j.error && <p role="alert">{j.error}</p>}
                {j.snapshot_id && (
                  <button
                    className="secondary"
                    type="button"
                    onClick={() =>
                      void work(async () => {
                        const saved = await api<Snapshot>(
                          `/github/snapshots/${j.snapshot_id}`,
                        );
                        setSnapshots((prev) => [
                          saved,
                          ...prev.filter((s) => s.id !== saved.id),
                        ]);
                        setSelected(saved.id);
                      })
                    }
                  >
                    Открыть результат
                  </button>
                )}
                {["queued", "running"].includes(j.status) && (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      void work(async () => {
                        await api(`/github/reads/${j.id}/cancel`, "POST");
                      })
                    }
                  >
                    Отменить чтение
                  </button>
                )}
              </article>
            ))}
          </section>
        )}
        {!!snapshots.length && (
          <label className="integration-picker">
            Сохранённый просмотр
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Выбери снимок</option>
              {snapshots.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.repository} · {s.commit_sha.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        )}
        {snapshot && (
          <>
            <SourceChoices
              snapshot={snapshot}
              busy={
                busy ||
                reads.some(
                  (j) =>
                    j.repository === snapshot.repository.split("/")[1] &&
                    ["queued", "running"].includes(j.status),
                )
              }
              onRead={(paths) =>
                void work(async () => {
                  await api("/github/inspect", "POST", {
                    repository: snapshot.repository.split("/")[1],
                    request_key: crypto.randomUUID(),
                    source_snapshot_id: snapshot.id,
                    paths,
                  });
                  setNotice(
                    "Выбранные файлы поставлены в очередь на том же коммите.",
                  );
                })
              }
            />
            <p>
              <a
                target="_blank"
                rel="noopener noreferrer"
                href={`https://github.com/${snapshot.repository}/tree/${snapshot.commit_sha}`}
              >
                Открыть коммит {snapshot.commit_sha.slice(0, 8)}
              </a>
            </p>
            {snapshot.facts.warnings.map((warning) => (
              <p className="muted" key={warning}>
                {warning}
              </p>
            ))}
            {snapshot.facts.files.map((file) => (
              <details key={file.path} className="integration-file">
                <summary>
                  {file.path} · {file.bytes} байт
                </summary>
                <pre>
                  {file.text
                    .split("\n")
                    .map((line, i) => `${i + 1} | ${line}`)
                    .join("\n")}
                </pre>
              </details>
            ))}
            {profile &&
              snapshot.facts.files.some((f) => f.kind === "implementation") && (
                <GitHubReview
                  key={snapshot.id}
                  snapshotId={snapshot.id}
                  revision={profile.revision}
                  capabilities={profile.profile.capabilities}
                  onPrepared={load}
                />
              )}
            <h3>Подготовить свидетельство вручную</h3>
            <form
              className="integration-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (!profile) return;
                void work(async () => {
                  await api("/github/proposals", "POST", {
                    snapshot_id: snapshot.id,
                    expected_revision: profile.revision,
                    statement,
                    capability_ids: [capability],
                    project_id: project || null,
                    citations,
                  });
                  setStatement("");
                  setNotice(
                    "Предложение подготовлено. Проверь его ниже перед подтверждением.",
                  );
                });
              }}
            >
              <CitationPicker
                snapshot={snapshot}
                value={citations}
                onChange={setCitations}
              />
              <label>
                Что конкретно подтверждается
                <textarea
                  minLength={10}
                  maxLength={3000}
                  rows={4}
                  value={statement}
                  onChange={(e) => setStatement(e.target.value)}
                  placeholder="Опиши конкретное наблюдение и границы. Если оставить пустым, подготовим нейтральную запись о выбранных строках без вывода об опыте."
                />
              </label>
              <label>
                Компетенция
                <select
                  required
                  value={capability}
                  onChange={(e) => setCapability(e.target.value)}
                >
                  <option value="">Выбери компетенцию</option>
                  {profile?.profile.capabilities.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label} · {c.category}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Связанный собственный проект
                <select
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                >
                  <option value="">Без привязки к проекту</option>
                  {profile?.profile.projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="secondary"
                disabled={busy || !citations.length}
              >
                Подготовить предложение
              </button>
            </form>
          </>
        )}
      </section>
      {!!proposals.length && (
        <section className="panel integration-panel">
          <h2 id="github-proposals">Свидетельства для профиля</h2>
          {proposals.map((p) => (
            <article className="integration-proposal" key={p.id}>
              <p>{p.statement}</p>
              {p.citations.map((c, i) => (
                <details className="integration-file" key={i}>
                  <summary>
                    {c.path}:{c.line_start}–{c.line_end} ·{" "}
                    {c.kind === "implementation"
                      ? "исходный код"
                      : "декларация"}
                  </summary>
                  <a
                    href={citationUrl(c)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Открыть точные строки на GitHub
                  </a>
                  <pre>{c.excerpt}</pre>
                </details>
              ))}
              <p className="muted">
                {p.capability_ids
                  .map(
                    (id) =>
                      profile?.profile.capabilities.find((c) => c.id === id)
                        ?.label || id,
                  )
                  .join(", ")}{" "}
                · редакция профиля {p.base_revision}
              </p>
              {p.status === "accepted" ? (
                <strong>Подтверждено · редакция {p.accepted_revision}</strong>
              ) : p.status === "rejected" ? (
                <strong>Отклонено · профиль не изменён</strong>
              ) : p.status === "revoked" ? (
                <strong>Отозвано · прежняя редакция сохранена в истории</strong>
              ) : (
                <>
                  <button
                    className="primary"
                    disabled={busy || p.base_revision !== profile?.revision}
                    onClick={() =>
                      void work(async () => {
                        await api(`/github/proposals/${p.id}/accept`, "POST");
                        await onUpdated();
                        setNotice(
                          "Свидетельство добавлено в новую редакцию профиля.",
                        );
                      })
                    }
                  >
                    {p.base_revision !== profile?.revision
                      ? "Профиль изменился — подготовь заново"
                      : "Подтвердить свидетельство"}
                  </button>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      void work(async () => {
                        await api(`/github/proposals/${p.id}/reject`, "POST");
                      })
                    }
                  >
                    Отклонить предложение
                  </button>
                </>
              )}
            </article>
          ))}
        </section>
      )}
    </>
  );
}
