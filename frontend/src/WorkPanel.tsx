import { useEffect, useState } from "react";
import { api, type Order } from "./api";
import { useTelegramBack } from "./useTelegramBack";
type Run = (work: () => Promise<void>, success?: string) => Promise<void>;
type Details = {
  agreed_scope: string;
  agreed_stack: string;
  terms: string;
  due_on: string | null;
  started_on: string | null;
};
type Outcome = {
  id: string;
  revision: number;
  result: string;
  delivered: string;
  validation: string;
  excluded: string;
  learned: string;
  capability_ids: string[];
  finished_on: string;
};
type Proposal = {
  id: string;
  outcome_id: string;
  statement: string;
  capability_ids: string[];
  status: string;
  base_revision: number;
  accepted_revision: number | null;
  stale: boolean;
};
type State = {
  work:
    | (Details & {
        acceptance_snapshot: Record<string, unknown>;
        outcome_id: string | null;
      })
    | null;
  outcomes: Outcome[];
  proposals: Proposal[];
  profile_revision: number;
  confirmed_evidence: boolean;
  capabilities: { id: string; label: string; category: string }[];
};
const empty: Details = {
  agreed_scope: "",
  agreed_stack: "",
  terms: "",
  due_on: null,
  started_on: null,
};
const today = () =>
  new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Krasnoyarsk" }).format(
    new Date(),
  );
const labels: Record<string, string> = {
  accepted: "Принят",
  in_progress: "В работе",
  completed: "Выполнен",
  closed: "Закрыт",
  cancelled: "Отменён",
};
const results: Record<string, string> = {
  succeeded: "Успешно выполнен",
  partial: "Выполнен частично",
  failed: "Не выполнен",
};

export default function WorkPanel({
  order,
  tick,
  run,
  busy,
}: {
  order: Order;
  tick: number;
  run: Run;
  busy: boolean;
}) {
  const [state, setState] = useState<State | null>(null),
    [error, setError] = useState("");
  const [mode, setMode] = useState<"accept" | "details" | "outcome" | null>(
    null,
  );
  const [fields, setFields] = useState<Details>(empty),
    [confirm, setConfirm] = useState(false);
  const [next, setNext] = useState(order.status),
    [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState({
    result: "succeeded",
    delivered: "",
    validation: "",
    excluded: "",
    learned: "",
    capability_ids: [] as string[],
    finished_on: today(),
  });
  useTelegramBack(
    mode !== null,
    () => {
      if (!busy) setMode(null);
    },
    80,
  );
  useEffect(() => {
    let active = true;
    api<State>(`/orders/${order.id}/work`)
      .then((s) => {
        if (active) {
          setState(s);
          setNext(order.status);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [order.id, order.version, tick]);
  function open(which: "accept" | "details" | "outcome") {
    if (!state) return;
    setFields(
      state.work
        ? {
            agreed_scope: state.work.agreed_scope,
            agreed_stack: state.work.agreed_stack,
            terms: state.work.terms,
            due_on: state.work.due_on,
            started_on: state.work.started_on,
          }
        : empty,
    );
    const current = state.outcomes.find((o) => o.id === state.work?.outcome_id);
    setOutcome(
      current
        ? {
            result: current.result,
            delivered: current.delivered,
            validation: current.validation,
            excluded: current.excluded,
            learned: current.learned,
            capability_ids: current.capability_ids,
            finished_on: current.finished_on,
          }
        : {
            result: "succeeded",
            delivered: "",
            validation: "",
            excluded: "",
            learned: "",
            capability_ids: [],
            finished_on: today(),
          },
    );
    setConfirm(false);
    setMode(which);
  }
  if (error)
    return (
      <div role="alert" className="alert error">
        {error}
      </div>
    );
  if (!state) return <p>Открываем учёт заказа…</p>;
  const current = state.outcomes.find((o) => o.id === state.work?.outcome_id);
  return (
    <section className="work-panel">
      <div className="panel">
        <h2>{order.consulting_allowed ? "Принять заказ" : "Работа и итог"}</h2>
        {order.consulting_allowed ? (
          <>
            <p>
              Прими заказ только после своего решения. Сохраним договорённости
              отдельным снимком. Неизвестные условия можно оставить пустыми.
            </p>
            <p>
              После принятия технические консультации прекращаются, в том числе
              после отмены заказа.
            </p>
            <button
              className="primary"
              disabled={busy || order.status === "declined" || mode !== null}
              onClick={() => open("accept")}
            >
              Заказ принят
            </button>
            {order.status === "declined" && (
              <p className="form-note">Сначала верни заказ в обсуждение.</p>
            )}
          </>
        ) : (
          <>
            <p>
              Заказ принят. Консультации завершены; материалы и прежние ответы
              остаются доступны.
            </p>
            {state.work && (
              <dl className="work-facts">
                <dt>Согласованный объём</dt>
                <dd>{state.work.agreed_scope || "Пока не зафиксирован"}</dd>
                <dt>Согласованный стек</dt>
                <dd>{state.work.agreed_stack || "Пока не зафиксирован"}</dd>
                <dt>Условия</dt>
                <dd>{state.work.terms || "Пока не зафиксированы"}</dd>
                <dt>Срок</dt>
                <dd>{state.work.due_on || "Не указан"}</dd>
                <dt>Фактическое начало</dt>
                <dd>{state.work.started_on || "Не указано"}</dd>
              </dl>
            )}
            <div className="form-actions">
              <button
                className="secondary"
                disabled={busy || mode !== null}
                onClick={() => open("details")}
              >
                Изменить условия и даты
              </button>
              <button
                className="primary"
                disabled={
                  busy ||
                  mode !== null ||
                  order.status === "closed" ||
                  state.confirmed_evidence
                }
                onClick={() => open("outcome")}
              >
                {current
                  ? "Исправить итог новой редакцией"
                  : "Зафиксировать итог"}
              </button>
            </div>
            {state.confirmed_evidence && (
              <p className="form-note">
                Итог уже подтверждён в профиле. Чтобы исправить его или открыть
                работу снова, сначала явно отзови свидетельство ниже.
              </p>
            )}
            {state.work && (
              <details>
                <summary>Снимок договорённостей при первом принятии</summary>
                <p className="work-text">
                  {String(
                    state.work.acceptance_snapshot.agreed_scope ||
                      "Объём не был уточнён при принятии",
                  )}
                </p>
                <p className="work-text">
                  {String(
                    state.work.acceptance_snapshot.agreed_stack ||
                      "Стек не был уточнён при принятии",
                  )}
                </p>
                <p className="work-text">
                  {String(
                    state.work.acceptance_snapshot.terms ||
                      "Условия не были уточнены при принятии",
                  )}
                </p>
              </details>
            )}
          </>
        )}
      </div>
      {mode && (mode === "accept" || mode === "details") && (
        <form
          className="panel"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              async () => {
                await api(
                  `/orders/${order.id}/${mode === "accept" ? "accept" : "work"}`,
                  mode === "accept" ? "POST" : "PUT",
                  {
                    ...fields,
                    expected_version: order.version,
                    ...(mode === "accept" ? { confirm } : {}),
                  },
                );
                setMode(null);
              },
              mode === "accept"
                ? "Заказ принят. Консультации завершены"
                : "Условия сохранены",
            );
          }}
        >
          <h3>{mode === "accept" ? "Подтверди принятие" : "Условия и даты"}</h3>
          <label>
            Согласованный объём
            <textarea
              maxLength={8000}
              rows={4}
              value={fields.agreed_scope}
              onChange={(e) =>
                setFields({ ...fields, agreed_scope: e.target.value })
              }
            />
          </label>
          <label>
            Согласованный стек
            <textarea
              maxLength={3000}
              rows={2}
              value={fields.agreed_stack}
              onChange={(e) =>
                setFields({ ...fields, agreed_stack: e.target.value })
              }
            />
          </label>
          <label>
            Условия и договорённости
            <textarea
              maxLength={5000}
              rows={3}
              value={fields.terms}
              onChange={(e) => setFields({ ...fields, terms: e.target.value })}
            />
          </label>
          <div className="work-dates">
            <label>
              Срок
              <input
                type="date"
                value={fields.due_on || ""}
                onChange={(e) =>
                  setFields({ ...fields, due_on: e.target.value || null })
                }
              />
            </label>
            <label>
              Фактическое начало
              <input
                type="date"
                max={today()}
                value={fields.started_on || ""}
                onChange={(e) =>
                  setFields({ ...fields, started_on: e.target.value || null })
                }
              />
            </label>
          </div>
          {mode === "accept" && (
            <label className="work-check">
              <input
                type="checkbox"
                required
                checked={confirm}
                onChange={(e) => setConfirm(e.target.checked)}
              />
              Я принимаю заказ. Технические консультации по нему прекращаются.
            </label>
          )}
          <div className="form-actions">
            <button
              className="primary"
              disabled={busy || (mode === "accept" && !confirm)}
            >
              {mode === "accept" ? "Подтвердить принятие" : "Сохранить условия"}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => setMode(null)}
            >
              Отмена
            </button>
          </div>
        </form>
      )}
      {!order.consulting_allowed && (
        <form
          className="panel work-status"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api(`/orders/${order.id}/work/status`, "POST", {
                expected_version: order.version,
                status: next,
                reason,
              });
              setReason("");
            }, "Состояние сохранено");
          }}
        >
          <h3>Состояние работы</h3>
          <label>
            Новое состояние
            <select
              aria-label="Новое состояние"
              value={next}
              onChange={(e) => setNext(e.target.value as Order["status"])}
            >
              {Object.entries(labels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Причина изменения
            <input
              required
              minLength={3}
              maxLength={1000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <p className="form-note">
            Для «Выполнен» и «Закрыт» нужен сохранённый итог. Возврат к учёту
            работы не возвращает консультации.
          </p>
          <button
            className="secondary"
            disabled={busy || reason.trim().length < 3 || next === order.status}
          >
            Изменить состояние
          </button>
        </form>
      )}
      {mode === "outcome" && (
        <form
          className="panel"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api(`/orders/${order.id}/outcomes`, "POST", {
                ...outcome,
                expected_version: order.version,
              });
              setMode(null);
            }, "Итог сохранён; профиль пока не изменён");
          }}
        >
          <h3>{current ? "Новая редакция итога" : "Фактический итог"}</h3>
          <label>
            Результат
            <select
              aria-label="Результат"
              value={outcome.result}
              onChange={(e) =>
                setOutcome({ ...outcome, result: e.target.value })
              }
            >
              {Object.entries(results).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Что действительно сделали
            <textarea
              required
              minLength={3}
              maxLength={1500}
              rows={4}
              value={outcome.delivered}
              onChange={(e) =>
                setOutcome({ ...outcome, delivered: e.target.value })
              }
            />
          </label>
          <label>
            Чем подтверждается результат
            <textarea
              required
              minLength={3}
              maxLength={1000}
              rows={3}
              placeholder="Репозиторий и коммит, проверка результата, переданные файлы…"
              value={outcome.validation}
              onChange={(e) =>
                setOutcome({ ...outcome, validation: e.target.value })
              }
            />
          </label>
          <label>
            Что осталось за пределами работы
            <textarea
              maxLength={1000}
              rows={2}
              value={outcome.excluded}
              onChange={(e) =>
                setOutcome({ ...outcome, excluded: e.target.value })
              }
            />
          </label>
          <label>
            Что освоили на практике
            <textarea
              maxLength={1000}
              rows={2}
              value={outcome.learned}
              onChange={(e) =>
                setOutcome({ ...outcome, learned: e.target.value })
              }
            />
          </label>
          <label>
            Фактическое завершение
            <input
              type="date"
              required
              max={today()}
              value={outcome.finished_on}
              onChange={(e) =>
                setOutcome({ ...outcome, finished_on: e.target.value })
              }
            />
          </label>
          <fieldset>
            <legend>Какой опыт подтверждает именно эта работа</legend>
            <p className="form-note">
              До 20 компетенций. Пустой список допустим. Категории A/B/C
              автоматически не меняются.
            </p>
            <div className="work-capabilities">
              {state.capabilities.map((c) => (
                <label className="work-check" key={c.id}>
                  <input
                    type="checkbox"
                    checked={outcome.capability_ids.includes(c.id)}
                    disabled={
                      !outcome.capability_ids.includes(c.id) &&
                      outcome.capability_ids.length >= 20
                    }
                    onChange={(e) =>
                      setOutcome({
                        ...outcome,
                        capability_ids: e.target.checked
                          ? [...outcome.capability_ids, c.id]
                          : outcome.capability_ids.filter((id) => id !== c.id),
                      })
                    }
                  />
                  {c.label} · {c.category}
                </label>
              ))}
            </div>
          </fieldset>
          <p className="form-note">
            Успешный и частичный итог переведут заказ в «Выполнен», неуспешный —
            в «Отменён». Свидетельство для профиля предложим только для
            успешного итога и выбранных компетенций.
          </p>
          <div className="form-actions">
            <button className="primary" disabled={busy}>
              Сохранить итог и состояние
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => setMode(null)}
            >
              Отмена
            </button>
          </div>
        </form>
      )}
      {state.outcomes.length > 0 && (
        <section className="panel">
          <h3>История итогов</h3>
          {state.outcomes.map((o) => (
            <details key={o.id} open={o.id === state.work?.outcome_id}>
              <summary>
                Редакция {o.revision} · {results[o.result]} · {o.finished_on}
                {o.id === state.work?.outcome_id
                  ? " · текущий итог"
                  : " · архив"}
              </summary>
              <h4>Сделано</h4>
              <p className="work-text">{o.delivered}</p>
              <h4>Подтверждение</h4>
              <p className="work-text">{o.validation}</p>
              {o.excluded && (
                <p className="work-text">За пределами: {o.excluded}</p>
              )}
              {o.learned && <p className="work-text">Освоили: {o.learned}</p>}
            </details>
          ))}
        </section>
      )}
      {state.proposals.map((p) => (
        <ProposalCard
          key={p.id}
          proposal={p}
          order={order}
          state={state}
          run={run}
          busy={busy}
        />
      ))}
    </section>
  );
}

function ProposalCard({
  proposal: p,
  order,
  state,
  run,
  busy,
}: {
  proposal: Proposal;
  order: Order;
  state: State;
  run: Run;
  busy: boolean;
}) {
  const [reason, setReason] = useState(""),
    [confirm, setConfirm] = useState(false);
  const current =
    p.outcome_id === state.work?.outcome_id &&
    ["completed", "closed"].includes(order.status);
  async function review(action: string) {
    await run(
      async () => {
        await api(`/orders/${order.id}/experience/${p.id}/${action}`, "POST", {
          expected_version: order.version,
          expected_profile_revision: state.profile_revision,
          reason,
        });
        setConfirm(false);
        setReason("");
      },
      action === "accept"
        ? "Свидетельство добавлено в профиль"
        : action === "revoke"
          ? "Свидетельство отозвано"
          : "Решение сохранено",
    );
  }
  return (
    <section className="panel">
      <h3>Свидетельство опыта</h3>
      <p className="form-note">
        {
          (
            {
              pending: "На подтверждении",
              accepted: "Подтверждено",
              rejected: "Отклонено",
              superseded: "Заменено новым итогом",
              revoked: "Отозвано",
            } as Record<string, string>
          )[p.status]
        }
      </p>
      <p className="work-text">{p.statement}</p>
      <p>
        Для:{" "}
        {p.capability_ids
          .map((id) => state.capabilities.find((c) => c.id === id)?.label || id)
          .join(", ")}
      </p>
      <p className="form-note">
        Добавится свидетельство с этим объёмом, а не весь исходный заказ.
        Категории A/B/C сохранятся.
      </p>
      {((p.status === "pending" && current) ||
        p.status === "accepted" ||
        (["rejected", "revoked"].includes(p.status) && current)) && (
        <>
          <label>
            Почему подтверждаю или меняю решение
            <input
              minLength={3}
              maxLength={1000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {((p.status === "pending" && !p.stale) ||
            p.status === "accepted") && (
            <label className="work-check">
              <input
                type="checkbox"
                checked={confirm}
                onChange={(e) => setConfirm(e.target.checked)}
              />
              {p.status === "accepted"
                ? "Подтверждаю отзыв этого свидетельства из текущего профиля. История сохранится."
                : "Проверил итог и подтверждаю добавление этого свидетельства в профиль."}
            </label>
          )}
          <div className="form-actions">
            {p.status === "pending" && !p.stale && (
              <button
                className="primary"
                disabled={busy || !confirm || reason.trim().length < 3}
                onClick={() => void review("accept")}
              >
                Подтвердить опыт
              </button>
            )}
            {p.status === "pending" && (
              <button
                className="secondary"
                disabled={busy || reason.trim().length < 3}
                onClick={() => void review("reject")}
              >
                Отклонить
              </button>
            )}
            {current &&
              p.status !== "accepted" &&
              (p.stale || ["rejected", "revoked"].includes(p.status)) && (
                <button
                  className="secondary"
                  disabled={busy || reason.trim().length < 3}
                  onClick={() => void review("refresh")}
                >
                  Обновить предложение
                </button>
              )}
            {p.status === "accepted" && (
              <button
                className="secondary"
                disabled={busy || !confirm || reason.trim().length < 3}
                onClick={() => void review("revoke")}
              >
                Отозвать свидетельство
              </button>
            )}
          </div>
        </>
      )}
      {p.status === "pending" && p.stale && (
        <p className="form-note">
          Профиль или итог изменился. Сначала обнови предложение, затем проверь
          его ещё раз.
        </p>
      )}
    </section>
  );
}
