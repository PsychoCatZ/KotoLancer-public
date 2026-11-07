import { useEffect, useState } from "react";
import { api } from "./api";
import { citationUrl, type SavedCitation } from "./GitHubSources";

type Review = {
  id: string;
  status: string;
  outdated: boolean;
  outdated_reason: string;
  error_message: string | null;
  model: string;
  rules_version: string;
  result: {
    summary: string;
    observations: {
      capability_id: string;
      observation: string;
      limitation: string;
      citations: SavedCitation[];
    }[];
  } | null;
};
type State = {
  configured: boolean;
  fixture: boolean;
  budget: { reserved_nusd: number; limit_nusd: number; remaining_nusd: number };
  jobs: Review[];
};

export default function GitHubReview({
  snapshotId,
  revision,
  capabilities,
  onPrepared,
}: {
  snapshotId: string;
  revision: number;
  capabilities: { id: string; label: string }[];
  onPrepared: () => Promise<void>;
}) {
  const [state, setState] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [generation, setGeneration] = useState(0);
  const active = state?.jobs.some((j) =>
    ["queued", "running"].includes(j.status),
  );
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const value = await api<State>(
          `/github/reviews?snapshot_id=${encodeURIComponent(snapshotId)}`,
        );
        if (alive) setState(value);
      } catch (e) {
        if (alive)
          setError(
            e instanceof Error ? e.message : "Не удалось загрузить разборы",
          );
      }
    }
    void load();
    const timer = active
      ? window.setInterval(() => void load(), 2000)
      : undefined;
    return () => {
      alive = false;
      if (timer) window.clearInterval(timer);
    };
  }, [snapshotId, revision, generation, active]);
  async function work(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setGeneration((n) => n + 1);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие",
      );
    } finally {
      setBusy(false);
    }
  }
  async function prepare(jobId: string, index: number) {
    await api(`/github/reviews/${jobId}/proposals`, "POST", {
      index,
      expected_revision: revision,
    });
    await onPrepared();
    setNotice(
      "Предложение подготовлено ниже. Проверь его перед отдельным подтверждением.",
    );
    window.requestAnimationFrame(() =>
      document
        .getElementById("github-proposals")
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }
  return (
    <section className="github-sources" aria-label="Наблюдения об опыте">
      <h3>Что подтверждается кодом</h3>
      <p>
        Котёнок предложит до трёх наблюдений с точными строками. Сначала
        проверим черновик, затем отдельно решим, добавлять ли его в профиль.
      </p>
      {state?.fixture && (
        <p role="status">Учебные ответы: модель не вызывается.</p>
      )}
      {state && (
        <p>
          Общий расчётный резерв $
          {(state.budget.reserved_nusd / 1e9).toFixed(4)} из $
          {(state.budget.limit_nusd / 1e9).toFixed(2)}. Лимит общий с анализом и
          обсуждением заказов.
        </p>
      )}
      <button
        type="button"
        className="primary"
        disabled={busy || active || !state?.configured}
        onClick={() =>
          void work(async () => {
            await api("/github/reviews", "POST", {
              snapshot_id: snapshotId,
              expected_revision: revision,
              request_id: crypto.randomUUID(),
            });
            setNotice("Разбор поставлен в очередь. Профиль не изменён.");
          })
        }
      >
        Предложить наблюдения
      </button>
      {state && !state.configured && (
        <p>
          AI пока не подключён. Можно подготовить свидетельство вручную ниже.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {state?.jobs.map((job) => (
        <article className="integration-proposal" key={job.id}>
          <strong>
            {(
              {
                queued: "В очереди",
                running: "Разбираем исходники",
                completed: "Черновик наблюдений",
                failed: "Разбор не завершён",
                stale: "Разбор по прежним данным",
                cancelled: "Разбор отменён",
              } as Record<string, string>
            )[job.status] || job.status}
          </strong>
          <p>
            {job.model} · {job.rules_version}
          </p>
          {job.error_message && <p>{job.error_message}</p>}
          {job.outdated && <p role="status">{job.outdated_reason}</p>}
          {["queued", "running"].includes(job.status) && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() =>
                void work(async () => {
                  await api(`/github/reviews/${job.id}/cancel`, "POST");
                })
              }
            >
              Отменить разбор
            </button>
          )}
          {job.result && (
            <>
              <p>{job.result.summary}</p>
              {job.result.observations.length > 1 && (
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || job.outdated || job.status !== "completed"}
                  onClick={() => void work(() => prepare(job.id, -1))}
                >
                  Подготовить все наблюдения одним свидетельством
                </button>
              )}
              {!job.result.observations.length && (
                <p>
                  Подтверждённых наблюдений в этих фрагментах не предложено. Это
                  не отказ в компетенции.
                </p>
              )}
              {job.result.observations.map((observation, index) => (
                <div className="integration-proposal" key={index}>
                  <strong>
                    {capabilities.find(
                      (c) => c.id === observation.capability_id,
                    )?.label || observation.capability_id}
                  </strong>
                  <p>{observation.observation}</p>
                  <p>Граница вывода: {observation.limitation}</p>
                  {observation.citations.map((c, i) => (
                    <details key={i} className="integration-file">
                      <summary>
                        {c.path}:{c.line_start}–{c.line_end}
                      </summary>
                      <a
                        href={citationUrl(c)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Открыть точные строки
                      </a>
                      <pre>{c.excerpt}</pre>
                    </details>
                  ))}
                  <button
                    type="button"
                    className="secondary"
                    disabled={
                      busy || job.outdated || job.status !== "completed"
                    }
                    onClick={() => void work(() => prepare(job.id, index))}
                  >
                    Подготовить свидетельство
                  </button>
                </div>
              ))}
            </>
          )}
        </article>
      ))}
    </section>
  );
}
