import { useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  Copy,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  Send,
  ShieldCheck,
} from "lucide-react";
import { api } from "./api";

type Ethics = {
  verdict: "clear" | "clarify" | "reject";
  reason: string;
  source_ids: string[];
};
type Claim = { text: string; source_ids: string[] };
type Result = {
  summary: string;
  recommendation: "discuss" | "clarify" | "decline";
  technical_verdict:
    | "capable"
    | "needs_check"
    | "not_suitable"
    | "insufficient_data";
  technical_reason: string;
  ethics: Ethics;
  requirements: Claim[];
  components: {
    name: string;
    category: "A" | "B" | "C" | "unknown";
    required: boolean;
    explanation: string;
    source_ids: string[];
    verification: string | null;
  }[];
  scope: string;
  questions: { text: string; why_it_matters: string }[];
  client_message?: string | null;
  risks: Claim[];
  proposed_stack: { technology: string; purpose: string }[];
  effort: string | null;
  changes_from_previous: string | null;
};
type Job = {
  id: string;
  kind: "analysis" | "chat" | "draft";
  status: string;
  created_at: string;
  error_code: string | null;
  error_message: string | null;
  outdated: boolean;
  outdated_reason: string | null;
  model: string;
  usage: { input_tokens?: number; output_tokens?: number } | null;
  rules_version: string;
  versions: {
    order: number;
    materials: number;
    conversation: number;
    profile: number;
  };
  context_size: {
    text_chars: number;
    binary_bytes: number;
    attachments: number;
  };
  sources: Record<string, { label: string; kind: string }>;
  coverage: {
    source_id: string;
    title: string;
    mode: string;
    warnings: string[];
  }[];
};
type Analysis = {
  id: string;
  result: Result;
  created_at: string;
  request: Job;
};
type Draft = { id: string; content: string; version: number; request: Job };
type State = {
  configured: boolean;
  provider: string;
  model: string;
  worker_seen: boolean;
  consulting_allowed: boolean;
  jobs: Job[];
  analyses: Analysis[];
  messages: { id: number; author: string; content: string; job_id: string }[];
  drafts: Draft[];
  daily_request_limit: number;
  budget: { limit_nusd: number; reserved_nusd: number; remaining_nusd: number };
};
type Mode = "analysis" | "chat" | "draft";
const technical = {
  capable: "Предварительно посильно",
  needs_check: "Нужна проверка",
  not_suitable: "Не можем надёжно выполнить",
  insufficient_data: "Недостаточно данных",
};
const ethics = {
  clear: "Признаков нарушения не найдено",
  clarify: "Нужно выяснить назначение",
  reject: "Не берём по этическим причинам",
};
const recommendations = {
  discuss: "Обсуждать дальше",
  clarify: "Сначала уточнить",
  decline: "Не брать",
};
const actions = { analysis: "Анализ", chat: "Обсуждение", draft: "Отклик" };
const statuses: Record<string, string> = {
  queued: "Ожидает обработки",
  running: "Ассистент разбирается",
  completed: "Готово",
  failed: "Не завершено",
  stale: "Данные изменились",
  suppressed: "Консультирование завершено",
};
const active = (job: Job) =>
  job.status === "queued" || job.status === "running";
const date = (value: string) =>
  new Intl.DateTimeFormat("ru", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Krasnoyarsk",
  }).format(new Date(value));

export default function ConsultantPanel({
  orderId,
  mode,
  tick,
  onOrderChanged,
}: {
  orderId: string;
  mode: Mode;
  tick: number;
  onOrderChanged: () => void;
}) {
  const [state, setState] = useState<State | null>(null),
    [error, setError] = useState(""),
    [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState(""),
    [draftInstruction, setDraftInstruction] = useState(""),
    [analysisId, setAnalysisId] = useState("");
  const mounted = useRef(true),
    sequence = useRef(0),
    sending = useRef(false);
  const priorStatuses = useRef(new Map<string, string>()),
    change = useRef(onOrderChanged);
  change.current = onOrderChanged;
  const pendingRequest = useRef<{
    request_id: string;
    kind: Mode;
    message: string;
  } | null>(null);
  const load = useCallback(async () => {
    const seq = ++sequence.current;
    try {
      const data = await api<State>(`/orders/${orderId}/consultant`);
      if (!mounted.current || seq !== sequence.current) return;
      const completed = data.jobs.some(
        (job) =>
          ["queued", "running"].includes(
            priorStatuses.current.get(job.id) || "",
          ) && !active(job),
      );
      priorStatuses.current = new Map(
        data.jobs.map((job) => [job.id, job.status]),
      );
      setState(data);
      if (completed) change.current();
    } catch (e) {
      if (mounted.current && seq === sequence.current)
        setError(
          e instanceof Error ? e.message : "Не удалось загрузить консультанта.",
        );
    }
  }, [orderId]);
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      sequence.current++;
    };
  }, [load, tick]);
  useEffect(() => {
    if (state?.jobs.some(active)) {
      const timer = setTimeout(() => void load(), 1500);
      return () => clearTimeout(timer);
    }
  }, [state, load]);

  async function submit(kind: Mode, text = "") {
    if (sending.current) return;
    sending.current = true;
    setSubmitting(true);
    setError("");
    const prior = pendingRequest.current;
    const body =
      prior?.kind === kind && prior.message === text
        ? prior
        : { request_id: crypto.randomUUID(), kind, message: text };
    pendingRequest.current = body;
    try {
      await api(`/orders/${orderId}/consultant`, "POST", body);
      pendingRequest.current = null;
      if (kind === "chat") setMessage("");
      await load();
      change.current();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Не удалось отправить запрос. Обнови состояние перед повторением.",
      );
    } finally {
      sending.current = false;
      setSubmitting(false);
    }
  }

  if (!state)
    return (
      <div className="panel">
        <p>{error || "Открываем консультанта…"}</p>
        <button className="secondary" onClick={() => void load()}>
          <RefreshCw size={15} />
          Обновить
        </button>
      </div>
    );
  const pending = state.jobs.find(active),
    latest = state.analyses[0];
  const current = state.analyses.find((a) => a.id === analysisId) || latest;
  const enabled =
    state.configured && state.consulting_allowed && !submitting && !pending;
  const draftAllowed =
    enabled &&
    latest &&
    !latest.request.outdated &&
    latest.result.ethics.verdict === "clear" &&
    latest.result.technical_verdict !== "not_suitable";

  return (
    <div className="consultant">
      {state.provider === "test-fixture" && (
        <div className="consultant-warning">
          <strong>Проверка интерфейса</strong>
          <p>
            Ответы заранее заданы для теста. OpenAI не вызывается; это не оценка
            реальной модели.
          </p>
        </div>
      )}
      {!state.consulting_allowed && (
        <div className="consultant-banner">
          <ShieldCheck size={20} />
          <p>
            Заказ принят. Техническое консультирование завершено; прежние
            материалы доступны для истории.
          </p>
        </div>
      )}
      {!state.configured && (
        <div className="consultant-banner">
          <BookOpen size={21} />
          <div>
            <strong>Кот, осталось подключить OpenAI</strong>
            <p>
              Анализ, обсуждение и отклики подготовлены. Пока доступ не
              настроен, запросы не отправляются и оценки не создаются.
            </p>
          </div>
        </div>
      )}
      {state.budget?.limit_nusd > 0 && (
        <div className="consultant-banner">
          <div>
            <strong>
              Расчётный резерв ${(state.budget.reserved_nusd / 1e9).toFixed(4)}{" "}
              из ${(state.budget.limit_nusd / 1e9).toFixed(2)}
            </strong>
            <p>
              Считаем с запасом; это не фактическое списание OpenAI. Общий
              предел сохраняется между днями и перезапусками. До{" "}
              {state.daily_request_limit} запусков в сутки.
            </p>
          </div>
        </div>
      )}
      {state.configured && !state.worker_seen && (
        <div className="consultant-warning">
          Обработчик запросов пока не отвечает. Запрос сохранится в очереди;
          проверь состояние обработчика.
        </div>
      )}
      {error && (
        <div className="alert error" role="alert">
          {error}
          <button
            className="icon-button"
            aria-label="Скрыть ошибку консультанта"
            onClick={() => setError("")}
          >
            ×
          </button>
        </div>
      )}
      {pending && (
        <div className="job-progress" role="status">
          <LoaderCircle size={17} className="spin" />
          <span>
            {actions[pending.kind]} · {statuses[pending.status]}
            <small>
              Можно перейти в другую карточку. Результат сохранится здесь.
            </small>
          </span>
          <button
            className="icon-button"
            aria-label="Обновить состояние запроса"
            onClick={() => void load()}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      )}

      {mode === "analysis" && (
        <>
          <div className="consultant-toolbar">
            <div>
              <h2>Оценка заказа</h2>
              <p>
                Возможности, этика и неизвестные — отдельно. Решение остаётся за
                тобой.
              </p>
            </div>
            <button
              className="primary"
              disabled={!enabled}
              onClick={() => void submit("analysis")}
            >
              <RefreshCw size={16} />
              {latest ? "Обновить анализ" : "Разобрать заказ"}
            </button>
          </div>
          {state.analyses.length > 1 && (
            <label className="analysis-selector">
              История оценок
              <select
                aria-label="Редакция анализа"
                value={current?.id || ""}
                onChange={(e) => setAnalysisId(e.target.value)}
              >
                {state.analyses.map((a, i) => (
                  <option key={a.id} value={a.id}>
                    {i === 0 ? "Последняя оценка" : "Прежняя оценка"} ·{" "}
                    {date(a.created_at)}
                    {a.request.outdated ? " · прежние данные" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {current ? (
            <AnalysisView analysis={current} />
          ) : (
            <div className="empty consultant-empty">
              <BookOpen size={30} />
              <h3>У этого заказа ещё нет оценки</h3>
              <p>
                Разбор опирается на объявление, материалы, переписку и
                действующий профиль наших возможностей.
              </p>
            </div>
          )}
        </>
      )}

      {mode === "chat" && (
        <>
          <div className="consultant-toolbar">
            <div>
              <h2>Обсудить этот заказ</h2>
              <p>
                Здесь сохраняется разговор с ассистентом. Ответ заказчика можно
                добавить отдельным материалом.
              </p>
            </div>
          </div>
          <div className="chat-thread" aria-label="Обсуждение заказа">
            {!state.messages.length && (
              <div className="empty">
                <MessageSquare size={28} />
                <h3>Отдельный разговор об этом заказе</h3>
                <p>
                  Уточни оценку, обсуди границы работ или спроси, какие вопросы
                  действительно нужны заказчику.
                </p>
              </div>
            )}
            {state.messages.map((m) => (
              <article className={`chat-message ${m.author}`} key={m.id}>
                <strong>{m.author === "owner" ? "Кот" : "КотоПиздюк"}</strong>
                <p>{m.content}</p>
                {m.author === "assistant" &&
                  state.jobs.find((j) => j.id === m.job_id)?.outdated && (
                    <small>Ответ относится к прежней версии сведений.</small>
                  )}
              </article>
            ))}
          </div>
          <form
            className="chat-composer panel"
            onSubmit={(e) => {
              e.preventDefault();
              void submit("chat", message.trim());
            }}
          >
            <label>
              Сообщение ассистенту
              <textarea
                rows={4}
                maxLength={8000}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Например: какой минимальный объём здесь стоит предложить?"
                disabled={!state.consulting_allowed}
              />
            </label>
            <div className="form-actions">
              <button
                className="primary"
                disabled={!enabled || !message.trim()}
              >
                <Send size={16} />
                Отправить
              </button>
            </div>
          </form>
        </>
      )}

      {mode === "draft" && (
        <>
          <div className="consultant-toolbar">
            <div>
              <h2>Черновик отклика</h2>
              <p>
                Проверяешь и отправляешь заказчику сам. Подтверждённый опыт не
                подменяется обещаниями.
              </p>
            </div>
          </div>
          <form
            className="panel"
            onSubmit={(e) => {
              e.preventDefault();
              void submit("draft", draftInstruction.trim());
            }}
          >
            <label>
              Пожелания к тексту
              <textarea
                rows={2}
                maxLength={8000}
                placeholder="Например: коротко, без приветствия, с вопросом об исходном коде"
                value={draftInstruction}
                onChange={(e) => setDraftInstruction(e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button className="primary" disabled={!draftAllowed}>
                Подготовить отклик
              </button>
            </div>
            {!latest && (
              <p className="form-note">Сначала нужен анализ заказа.</p>
            )}
            {latest?.request.outdated && (
              <p className="form-note">
                Перед откликом обнови анализ по новым сведениям.
              </p>
            )}
            {latest &&
              (latest.result.ethics.verdict !== "clear" ||
                latest.result.technical_verdict === "not_suitable") && (
                <p className="form-note">
                  Сейчас есть причины отказа или неразрешённые этические
                  вопросы. Отклик с предложением выполнить работу не готовим.
                </p>
              )}
          </form>
          {state.drafts.map((draft) => (
            <DraftEditor
              key={draft.id}
              draft={draft}
              orderId={orderId}
              editable={state.consulting_allowed}
              onSaved={load}
            />
          ))}
        </>
      )}

      {state.jobs.length > 0 && (
        <details className="request-journal panel">
          <summary>История запросов · {state.jobs.length}</summary>
          {state.jobs.map((job) => (
            <div key={job.id} className="request-row">
              <div>
                <strong>
                  {actions[job.kind]} · {statuses[job.status]}
                </strong>
                <small>
                  {date(job.created_at)} · {job.model || "Модель не выбрана"}
                </small>
                {job.error_message && <p>{job.error_message}</p>}
              </div>
              <span>
                {job.usage
                  ? `${job.usage.input_tokens ?? "?"} / ${job.usage.output_tokens ?? "?"} токенов`
                  : "Расход не подтверждён"}
              </span>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function Sources({ ids, job }: { ids: string[]; job: Job }) {
  if (!ids.length) return null;
  return (
    <details className="claim-sources">
      <summary>Основания · {ids.length}</summary>
      {ids.map((id) => (
        <p key={id}>{job.sources[id]?.label || id}</p>
      ))}
    </details>
  );
}

function AnalysisView({ analysis }: { analysis: Analysis }) {
  const { result: r, request: job } = analysis;
  return (
    <>
      {job.outdated && (
        <div className="consultant-warning">
          <strong>Оценка по прежним данным</strong>
          <p>{job.outdated_reason} Прежний результат сохранён для истории.</p>
        </div>
      )}
      <section className="panel analysis-verdict">
        <div className="eyebrow">ПРЕДВАРИТЕЛЬНЫЙ ВЫВОД</div>
        <span className={`recommendation ${r.recommendation}`}>
          {recommendations[r.recommendation]}
        </span>
        <p>{r.summary}</p>
        <div className="verdict-grid">
          <div>
            <small>Технически</small>
            <strong>{technical[r.technical_verdict]}</strong>
            <p>{r.technical_reason}</p>
          </div>
          <div>
            <small>По нашим принципам</small>
            <strong>{ethics[r.ethics.verdict]}</strong>
            <p>{r.ethics.reason}</p>
            <Sources ids={r.ethics.source_ids} job={job} />
          </div>
        </div>
      </section>
      {r.client_message && (
        <ClientQuestions
          key={analysis.id}
          message={r.client_message}
          outdated={job.outdated}
        />
      )}
      {r.changes_from_previous && (
        <section className="note-panel">
          <strong>Что изменилось в оценке</strong>
          <p>{r.changes_from_previous}</p>
        </section>
      )}
      {r.requirements.length > 0 && (
        <section className="panel">
          <h2>Что требуется</h2>
          {r.requirements.map((item, i) => (
            <div className="analysis-item" key={i}>
              <p>{item.text}</p>
              <Sources ids={item.source_ids} job={job} />
            </div>
          ))}
        </section>
      )}
      {r.components.length > 0 && (
        <section className="panel">
          <h2>На что опираемся</h2>
          {r.components.map((c, i) => (
            <div className="component-assessment" key={i}>
              <span className={`category ${c.category}`}>
                {c.category === "unknown" ? "?" : c.category}
              </span>
              <div>
                <strong>{c.name}</strong>
                <p>{c.explanation}</p>
                {c.verification && (
                  <p className="verification">
                    <b>Проверить:</b> {c.verification}
                  </p>
                )}
                <Sources ids={c.source_ids} job={job} />
              </div>
            </div>
          ))}
        </section>
      )}
      <section className="panel">
        <h2>Предварительная граница работ</h2>
        <p className="preserve-lines">{r.scope}</p>
        {r.effort && (
          <>
            <h3>Оценка трудоёмкости</h3>
            <p>{r.effort}</p>
          </>
        )}
      </section>
      {r.questions.length > 0 && (
        <section className="panel">
          <h2>Вопросы, влияющие на решение</h2>
          {r.questions.map((q, i) => (
            <div className="analysis-item" key={i}>
              <strong>{q.text}</strong>
              <p className="muted">{q.why_it_matters}</p>
            </div>
          ))}
        </section>
      )}
      {r.risks.length > 0 && (
        <section className="panel">
          <h2>Значимые риски</h2>
          {r.risks.map((risk, i) => (
            <div className="analysis-item" key={i}>
              <p>{risk.text}</p>
              <Sources ids={risk.source_ids} job={job} />
            </div>
          ))}
        </section>
      )}
      {r.proposed_stack.length > 0 && (
        <section className="panel">
          <h2>Предлагаемый стек</h2>
          {r.proposed_stack.map((item, i) => (
            <div className="analysis-item" key={i}>
              <strong>{item.technology}</strong>
              <p>{item.purpose}</p>
            </div>
          ))}
        </section>
      )}
      <details className="panel provenance">
        <summary>Материалы и версия оценки</summary>
        <p>
          {job.model} · {job.rules_version} · профиль {job.versions.profile} ·
          материалы {job.versions.materials}
        </p>
        {job.coverage.map((item) => (
          <div className="analysis-item" key={item.source_id}>
            <strong>{item.title}</strong>
            <small>
              {{
                text: "Текст",
                image: "Изображение передано модели",
                pdf_with_page_images: "PDF передан модели",
                docx_text_and_images:
                  "Извлечённый текст и доступные изображения DOCX",
              }[item.mode] || item.mode}
            </small>
            {item.warnings.map((w, i) => (
              <p key={i}>{w}</p>
            ))}
          </div>
        ))}
        <small>
          {job.context_size.text_chars.toLocaleString("ru")} символов контекста
          · {job.context_size.attachments} визуальных вложений
        </small>
      </details>
    </>
  );
}

function ClientQuestions({
  message,
  outdated,
}: {
  message: string;
  outdated: boolean;
}) {
  const [text, setText] = useState(message);
  const [notice, setNotice] = useState("");
  async function copyMessage() {
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Сообщение скопировано целиком");
    } catch {
      setNotice("Не удалось скопировать. Выдели текст вручную.");
    }
  }
  return (
    <section className="panel draft-editor">
      <h2>Сообщение заказчику</h2>
      <p>Вопросы одним сообщением. Проверь текст перед отправкой.</p>
      <label>
        Текст с вопросами
        <textarea
          rows={10}
          maxLength={10000}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setNotice("");
          }}
        />
      </label>
      <button
        type="button"
        className="primary"
        disabled={outdated || !text.trim()}
        onClick={() => void copyMessage()}
      >
        <Copy size={15} /> Копировать сообщение
      </button>
      {notice && <p role="status">{notice}</p>}
      <p className="form-note">
        {outdated
          ? "Анализ относится к прежним сведениям. Проверь актуальные условия."
          : "Ничего не отправляется автоматически. Правки в этом поле действуют до ухода со страницы."}
      </p>
    </section>
  );
}

function DraftEditor({
  draft,
  orderId,
  editable,
  onSaved,
}: {
  draft: Draft;
  orderId: string;
  editable: boolean;
  onSaved: () => Promise<void>;
}) {
  const [content, setContent] = useState(draft.content),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  useEffect(() => {
    setContent(draft.content);
  }, [draft.version, draft.content]);
  async function save() {
    setBusy(true);
    setNotice("");
    try {
      await api(`/orders/${orderId}/drafts/${draft.id}`, "PUT", {
        expected_version: draft.version,
        content,
      });
      await onSaved();
      setNotice("Правки сохранены");
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel draft-editor">
      <h3>Текст для заказчика</h3>
      {draft.request.outdated && (
        <div className="consultant-warning">
          Черновик относится к прежним сведениям. Проверь условия перед
          использованием.
        </div>
      )}
      <label>
        Редактировать отклик
        <textarea
          rows={10}
          maxLength={20000}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          disabled={!editable}
        />
      </label>
      <div className="form-actions">
        <button
          className="secondary"
          disabled={
            busy || !editable || !content.trim() || content === draft.content
          }
          onClick={() => void save()}
        >
          <Check size={15} />
          Сохранить правки
        </button>
        <button
          className="primary"
          disabled={draft.request.outdated || !content.trim()}
          onClick={() =>
            void navigator.clipboard.writeText(content).then(
              () => setNotice("Текст скопирован"),
              () => setNotice("Не удалось скопировать. Выдели текст вручную."),
            )
          }
        >
          <Copy size={15} />
          Копировать
        </button>
      </div>
      {notice && (
        <p className="form-note" role="status">
          {notice}
        </p>
      )}
      <p className="form-note">
        Черновик не отправляется автоматически. {draft.request.model}
      </p>
    </section>
  );
}
