import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Cat,
  Check,
  ChevronRight,
  Clock3,
  Download,
  FileText,
  FolderKanban,
  Inbox,
  LayoutGrid,
  Link2,
  List,
  LoaderCircle,
  Paperclip,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import {
  api,
  ApiError,
  browserAuth,
  download,
  upload,
  type Capability,
  type Material,
  type Order,
  type OrderEvent,
  type OrderStatus,
  type ProfileResponse,
  type Revision,
  type Workspace,
} from "./api";
import ConsultantPanel from "./ConsultantPanel";
import WorkPanel from "./WorkPanel";
import IntegrationsPanel from "./IntegrationsPanel";
import Appearance from "./Appearance";
import ClipboardImage from "./ClipboardImage";
import Modal from "./Modal";
import {
  ProfileEntryForm,
  RevokeEvidenceForm,
  type ProfileEntryEdit,
} from "./ProfileForms";
import BrowserLogin from "./BrowserLogin";
import { useTelegramBack } from "./useTelegramBack";
import { orderFromUrl, urlWithOrder } from "./orderLink";
import { citationUrl } from "./GitHubSources";

const statuses: Record<OrderStatus, string> = {
  new: "Новый",
  discussion: "Обсуждение",
  declined: "Не берём",
  accepted: "Принят",
  in_progress: "В работе",
  completed: "Выполнен",
  closed: "Закрыт",
  cancelled: "Отменён",
};
const categories = {
  A: "Практический опыт",
  B: "Смежный / осваиваемый",
  C: "Нужна экспертиза",
};
const recommendationLabels = {
  discuss: "Обсуждать дальше",
  clarify: "Сначала уточнить",
  decline: "Не брать",
  unassessed: "Ещё нет анализа",
  outdated: "Оценка по прежним данным",
};
const recommendationBucket = (
  order: Order,
): keyof typeof recommendationLabels =>
  !order.recommendation
    ? "unassessed"
    : order.recommendation.outdated
      ? "outdated"
      : order.recommendation.value;
const date = (value: string) =>
  new Intl.DateTimeFormat("ru", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Krasnoyarsk",
  }).format(new Date(value));
type Page = "orders" | "inbox" | "profile" | "integrations";
type Fields = Pick<
  Order,
  "title" | "source" | "source_url" | "budget" | "description" | "notes"
>;
const blank: Fields = {
  title: "",
  source: "",
  source_url: "",
  budget: "",
  description: "",
  notes: "",
};

function Empty({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("orders");
  const [selected, setSelected] = useState<string | null>(() =>
    orderFromUrl(window.location.href),
  );
  useEffect(() => {
    window.history.replaceState(
      window.history.state,
      "",
      urlWithOrder(window.location.href, selected),
    );
  }, [selected]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [inbox, setInbox] = useState<Material[]>([]);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [fromMaterial, setFromMaterial] = useState<Material | null>(null);
  const [tick, setTick] = useState(0);
  const [token, setToken] = useState("");
  const [accessRequired, setAccessRequired] = useState(false);
  useTelegramBack(selected !== null || page !== "orders", () => {
    if (busy) return;
    setSelected(null);
    setPage("orders");
    setError("");
  });

  const reload = useCallback(async () => {
    const [os, ins, pro, ws] = await Promise.all([
      api<Order[]>("/orders"),
      api<Material[]>("/materials"),
      api<ProfileResponse>("/profile"),
      api<Workspace>("/workspace"),
    ]);
    setOrders(os);
    setInbox(ins);
    setProfile(pro);
    setWorkspace(ws);
  }, []);

  useEffect(() => {
    reload()
      .catch((e) => {
        setError(e instanceof ApiError && e.status === 401 ? "" : e.message);
        setAccessRequired(e instanceof ApiError && e.status === 401);
      })
      .finally(() => setLoading(false));
  }, [reload]);
  useEffect(() => {
    if (page !== "inbox") return;
    const refresh = () =>
      void api<Material[]>("/materials")
        .then(setInbox)
        .catch((e) => setError(e.message));
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [page]);
  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(""), 4500);
      return () => clearTimeout(timer);
    }
  }, [notice]);

  async function run(work: () => Promise<void>, success = "") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await work();
      await reload();
      setTick((x) => x + 1);
      if (success) setNotice(success);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setWorkspace(null);
        setOrders([]);
        setInbox([]);
        setProfile(null);
        setSelected(null);
        setAccessRequired(true);
      }
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие",
      );
    } finally {
      setBusy(false);
    }
  }

  function navigate(next: Page) {
    setPage(next);
    setSelected(null);
    setError("");
  }
  const openOrder = (id: string) => {
    setPage("orders");
    setSelected(id);
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <a
          href="#"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            navigate("orders");
          }}
        >
          <span className="brand-mark">
            <Cat size={26} />
          </span>
          <span>
            KotoLancer<small>Личная мастерская Кота</small>
          </span>
        </a>
        <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav aria-label="Основное меню">
          <button
            className={page === "orders" ? "nav-item active" : "nav-item"}
            onClick={() => navigate("orders")}
          >
            <FolderKanban size={19} />
            <span>Заказы</span>
            <b>{orders.length}</b>
          </button>
          <button
            className={page === "inbox" ? "nav-item active" : "nav-item"}
            onClick={() => navigate("inbox")}
          >
            <Inbox size={19} />
            <span>Входящие</span>
            {inbox.length > 0 && <b>{inbox.length}</b>}
          </button>
          <button
            className={page === "profile" ? "nav-item active" : "nav-item"}
            onClick={() => navigate("profile")}
          >
            <BookOpen size={19} />
            <span>Наш опыт</span>
          </button>
          <button
            className={page === "integrations" ? "nav-item active" : "nav-item"}
            onClick={() => navigate("integrations")}
          >
            <Link2 size={19} />
            <span>Подключения</span>
          </button>
        </nav>
        <div className="sidebar-note">
          <ShieldCheck size={19} />
          <strong>Честно о возможностях</strong>
          <p>Опыт, новые задачи и границы — всё на своих местах.</p>
        </div>
        <div className="sidebar-owner">
          <span className="avatar">К</span>
          <div>
            Кот<small>Единственный и неповторимый</small>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <span>
            НИИ ИИ <ChevronRight size={14} /> <b>KotoLancer</b>
          </span>
          {workspace?.browser_session && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void browserAuth("logout")
                  .then(() => {
                    setWorkspace(null);
                    setOrders([]);
                    setInbox([]);
                    setProfile(null);
                    setSelected(null);
                    setPage("orders");
                    setAccessRequired(true);
                    setError("");
                    sessionStorage.removeItem("koto-access-token");
                  })
                  .catch((e) => setError(e.message))
                  .finally(() => setBusy(false));
              }}
            >
              Выйти
            </button>
          )}
          <span className="local-badge">
            <i />
            Мастерская Кота
          </span>
        </header>
        <Appearance />
        <div className="content">
          {error && (
            <div className="alert error app-error" role="alert">
              <span>{error}</span>
              <button
                aria-label="Скрыть ошибку"
                className="icon-button"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="toast" role="status">
              <Check size={16} />
              {notice}
            </div>
          )}
          {loading ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              Открываем мастерскую…
            </div>
          ) : !workspace ? (
            <div className="panel connection">
              <h2>
                {accessRequired
                  ? "Вход в мастерскую"
                  : "Нет подключения к мастерской"}
              </h2>
              {!accessRequired ? (
                <>
                  <p>
                    Сервер мастерской сейчас недоступен. Повтори подключение,
                    когда он будет запущен.
                  </p>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => void run(async () => {}, "Подключено")}
                  >
                    Повторить подключение
                  </button>
                </>
              ) : window.Telegram?.WebApp?.initData ? (
                <p>
                  Не удалось подтвердить вход через Telegram. Закрой мастерскую
                  и открой её заново кнопкой в личном чате с ботом KotoLancer.
                  Доступ есть только у Кота.
                </p>
              ) : (
                <BrowserLogin
                  onLogin={() =>
                    void run(async () => {}, "С возвращением, Кот!")
                  }
                  fallback={
                    <>
                      <p>
                        Мастерская запросила авторизацию. Для доступа по ключу
                        введи отдельный ключ мастерской. Если открываешь её
                        через Telegram, воспользуйся кнопкой в личном чате с
                        ботом.
                      </p>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          sessionStorage.setItem("koto-access-token", token);
                          void run(async () => {}, "Подключено");
                        }}
                      >
                        <label>
                          Ключ мастерской
                          <input
                            type="password"
                            value={token}
                            onChange={(e) => setToken(e.target.value)}
                          />
                        </label>
                        <button className="primary" disabled={busy}>
                          Подключиться
                        </button>
                      </form>
                    </>
                  }
                />
              )}
            </div>
          ) : (
            <>
              {page === "orders" && !selected && (
                <Orders
                  run={run}
                  busy={busy}
                  orders={orders}
                  onOpen={openOrder}
                  onCreate={() => {
                    setFromMaterial(null);
                    setCreating(true);
                  }}
                />
              )}
              {page === "orders" && selected && (
                <OrderDetail
                  key={selected}
                  id={selected}
                  tick={tick}
                  busy={busy}
                  run={run}
                  onBack={() => setSelected(null)}
                />
              )}
              {page === "inbox" && (
                <>
                  <div className="page-heading">
                    <div>
                      <div className="eyebrow">НИЧЕГО НЕ ПОТЕРЯЕТСЯ</div>
                      <h1>Входящие</h1>
                      <p>
                        Сначала сохрани материал. Заказ выберешь, когда будет
                        удобно.
                      </p>
                    </div>
                  </div>
                  <MaterialComposer busy={busy} run={run} />
                  <div className="section-heading">
                    <h2>
                      Без привязки к заказу <span>{inbox.length}</span>
                    </h2>
                  </div>
                  {!inbox.length ? (
                    <Empty
                      icon={<Inbox size={30} />}
                      title="Во входящих порядок"
                    >
                      Здесь будут тексты и файлы, для которых ещё не выбрана
                      карточка заказа.
                    </Empty>
                  ) : (
                    <div className="material-list">
                      {inbox.map((m) => (
                        <MaterialCard
                          key={m.id}
                          material={m}
                          busy={busy}
                          run={run}
                          orders={orders}
                          onCreate={() => {
                            setFromMaterial(m);
                            setCreating(true);
                          }}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}
              {page === "profile" && profile && (
                <ProfileView value={profile} run={run} busy={busy} />
              )}
              {page === "integrations" && (
                <IntegrationsPanel onUpdated={reload} />
              )}
              <footer>
                <Cat size={15} />
                <span>Всё важное — в карточках. Всё решение — за Котом.</span>
                <span className="footer-state">
                  {workspace.telegram_connected
                    ? "Telegram принимает материалы"
                    : workspace.telegram_configured
                      ? "Telegram настроен"
                      : "Telegram ещё не подключён"}
                </span>
              </footer>
            </>
          )}
        </div>
      </main>
      {creating && (
        <OrderForm
          initial={
            fromMaterial
              ? {
                  ...blank,
                  title: fromMaterial.title.replace(/\.[^.]+$/, ""),
                  description:
                    fromMaterial.kind === "text" ? fromMaterial.text : "",
                }
              : blank
          }
          busy={busy}
          onClose={() => setCreating(false)}
          onSave={(values) =>
            run(async () => {
              const created = await api<Order>("/orders", "POST", values);
              setCreating(false);
              openOrder(created.id);
              if (fromMaterial)
                await api(`/materials/${fromMaterial.id}/attach`, "POST", {
                  order_id: created.id,
                });
            }, "Карточка создана")
          }
        />
      )}
    </div>
  );
}

function Orders({
  run,
  busy,
  orders,
  onOpen,
  onCreate,
}: {
  orders: Order[];
  run: Run;
  busy: boolean;
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  const [query, setQuery] = useState(""),
    [status, setStatus] = useState(""),
    [source, setSource] = useState(""),
    [recommendation, setRecommendation] = useState("");
  const [mode, setMode] = useState<"list" | "board">("list");
  const [view, setView] = useState("active");
  const [stored, setStored] = useState<Order[]>([]);
  const [listError, setListError] = useState("");
  const [listLoading, setListLoading] = useState(false);
  useEffect(() => {
    let alive = true;
    setListError("");
    if (view === "active") {
      setListLoading(false);
      return;
    }
    setListLoading(true);
    setStored([]);
    void api<Order[]>(`/orders?view=${view}`)
      .then(
        (rows) => {
          if (alive) setStored(rows);
        },
        (e) => {
          if (alive) setListError(e.message);
        },
      )
      .finally(() => {
        if (alive) setListLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [view, orders]);
  const shown = view === "active" ? orders : stored;
  const filtered = shown.filter(
    (o) =>
      (!status || o.status === status) &&
      (!source || o.source === source) &&
      (!recommendation || recommendationBucket(o) === recommendation) &&
      `${o.title} ${o.description}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const sources = [...new Set(shown.map((o) => o.source).filter(Boolean))];
  const card = (order: Order) =>
    order.deleted_at ? (
      <article className="panel" key={order.id}>
        <h3>{order.title}</h3>
        <p>В корзине. История и материалы сохранены.</p>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api(`/orders/${order.id}/visibility`, "POST", {
                action: "restore",
                expected_version: order.version,
              });
            }, "Заказ восстановлен")
          }
        >
          Восстановить заказ
        </button>
      </article>
    ) : (
      <button
        key={order.id}
        className="order-card"
        onClick={() => onOpen(order.id)}
      >
        <div className="card-top">
          <span className="source-label">
            {order.source || "Источник не указан"}
          </span>
          <span className={`status ${order.status}`}>
            {statuses[order.status]}
          </span>
        </div>
        <h3>{order.title}</h3>
        <p>{order.description || "Добавь объявление или описание задачи."}</p>
        <p className="order-recommendation">
          {recommendationLabels[recommendationBucket(order)]}
        </p>
        <div className="card-bottom">
          <span>{order.budget || "Бюджет не указан"}</span>
          <span>
            <Paperclip size={14} />
            {order.material_count}
            <ArrowUpRight size={17} />
          </span>
        </div>
      </button>
    );
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">ОТ ОБЪЯВЛЕНИЯ ДО РЕЗУЛЬТАТА</div>
          <h1>
            Заказы<span className="heading-dot">.</span>
          </h1>
          <p>Каждой задаче — своя история. Каждому решению — основания.</p>
        </div>
        <button className="primary" onClick={onCreate}>
          <Plus size={18} />
          Новый заказ
        </button>
      </div>
      <div className="stats">
        <div>
          <span>Всего заказов</span>
          <strong>
            {orders.length}
            <FolderKanban size={22} />
          </strong>
        </div>
        <div>
          <span>На рассмотрении</span>
          <strong>
            {
              orders.filter(
                (o) => o.consulting_allowed && o.status !== "declined",
              ).length
            }
            <Clock3 size={22} />
          </strong>
        </div>
        <div className="stat-note">
          <span className="tiny-cat">
            <Cat size={27} />
          </span>
          <p>
            <b>Берём по силам и по совести.</b>
            <br />
            Бюджет не меняет наши возможности.
          </p>
        </div>
      </div>
      <div className="toolbar">
        <label className="search">
          <Search size={18} />
          <input
            aria-label="Поиск заказов"
            placeholder="Найти заказ…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label="Статус"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">Все статусы</option>
          {Object.entries(statuses).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="Источник"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="">Все источники</option>
          {sources.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select
          aria-label="Рекомендация"
          value={recommendation}
          onChange={(e) => setRecommendation(e.target.value)}
        >
          <option value="">Все рекомендации</option>
          {Object.entries(recommendationLabels).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <div className="view-toggle">
          <button
            aria-label="Список"
            aria-pressed={mode === "list"}
            onClick={() => setMode("list")}
          >
            <List size={18} />
          </button>
          <button
            aria-label="Доска"
            aria-pressed={mode === "board"}
            onClick={() => setMode("board")}
          >
            <LayoutGrid size={18} />
          </button>
        </div>
      </div>
      <label className="integration-picker">
        Список заказов
        <select
          aria-label="Список заказов"
          value={view}
          onChange={(e) => setView(e.target.value)}
        >
          <option value="active">Основные</option>
          <option value="archived">Архив</option>
          <option value="deleted">Корзина</option>
        </select>
      </label>
      {listError && <p role="alert">{listError}</p>}
      {listLoading ? (
        <p role="status">Загружаем заказы…</p>
      ) : !shown.length && view !== "active" ? (
        <Empty
          icon={<FolderKanban size={28} />}
          title={view === "archived" ? "Архив пуст" : "Корзина пуста"}
        >
          Здесь появятся убранные с главной заказы.
        </Empty>
      ) : !orders.length && view === "active" ? (
        <Empty
          icon={<FolderKanban size={32} />}
          title="Первый заказ ждёт своей карточки"
        >
          Создай заказ и добавь объявление, скриншот или ТЗ. Профиль наших
          возможностей уже на месте.
          <button className="secondary empty-action" onClick={onCreate}>
            <Plus size={16} />
            Создать первый заказ
          </button>
        </Empty>
      ) : !filtered.length ? (
        <Empty icon={<Search size={28} />} title="Ничего не нашлось">
          Попробуй другой запрос или убери фильтры.
        </Empty>
      ) : mode === "list" ? (
        <div className="order-grid">{filtered.map(card)}</div>
      ) : (
        <div className="board">
          {Object.entries(statuses).map(([key, label]) => (
            <section key={key} className="board-column">
              <h2>
                <i className={key} />
                {label}
                <span>{filtered.filter((o) => o.status === key).length}</span>
              </h2>
              {filtered.filter((o) => o.status === key).map(card)}
              {!filtered.some((o) => o.status === key) && (
                <p className="column-empty">Пока нет заказов</p>
              )}
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function OrderForm({
  initial,
  busy,
  onClose,
  onSave,
}: {
  initial: Fields;
  busy: boolean;
  onClose: () => void;
  onSave: (values: Fields) => Promise<void>;
}) {
  const [values, setValues] = useState<Fields>(
    () =>
      Object.fromEntries(
        Object.keys(blank).map((key) => [key, initial[key as keyof Fields]]),
      ) as Fields,
  );
  const field = (
    key: keyof Fields,
    label: string,
    placeholder: string,
    multiline = false,
  ) => (
    <label>
      {label}
      {multiline ? (
        <textarea
          rows={key === "description" ? 6 : 3}
          value={values[key]}
          onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          placeholder={placeholder}
          maxLength={key === "description" ? 100000 : 50000}
        />
      ) : (
        <input
          required={key === "title"}
          maxLength={
            key === "title"
              ? 200
              : key === "source"
                ? 100
                : key === "source_url"
                  ? 2000
                  : 200
          }
          type={key === "source_url" ? "url" : "text"}
          value={values[key]}
          onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          placeholder={placeholder}
        />
      )}
    </label>
  );
  return (
    <Modal
      title={
        initial === blank || !("id" in initial)
          ? "Карточка заказа"
          : "Изменить заказ"
      }
      onClose={onClose}
      busy={busy}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void onSave(values);
        }}
      >
        {field("title", "Название", "Например, Telegram-бот для записи")}
        <div className="form-grid">
          {field("source", "Источник", "Kwork, FL или другое")}
          {field("budget", "Бюджет как в объявлении", "Например, до 15 000 ₽")}
        </div>
        {field("source_url", "Ссылка", "https://…")}
        {field(
          "description",
          "Исходное объявление",
          "Вставь текст. Файлы можно добавить после создания.",
          true,
        )}
        {field(
          "notes",
          "Мои заметки",
          "Что важно помнить об этом заказе",
          true,
        )}
        <div className="form-actions">
          <button
            className="secondary"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Отмена
          </button>
          <button className="primary" disabled={busy || !values.title.trim()}>
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Check size={16} />
            )}
            Сохранить
          </button>
        </div>
      </form>
    </Modal>
  );
}

type Run = (work: () => Promise<void>, success?: string) => Promise<void>;

function OrderDetail({
  id,
  tick,
  run,
  busy,
  onBack,
}: {
  id: string;
  tick: number;
  run: Run;
  busy: boolean;
  onBack: () => void;
}) {
  const [order, setOrder] = useState<Order | null>(null),
    [materials, setMaterials] = useState<Material[]>([]),
    [events, setEvents] = useState<OrderEvent[]>([]);
  const [tab, setTab] = useState("materials"),
    [editing, setEditing] = useState(false),
    [loadError, setLoadError] = useState("");
  const [aiTick, setAiTick] = useState(0);
  useEffect(() => {
    let active = true;
    Promise.all([
      api<Order>(`/orders/${id}`),
      api<Material[]>(`/materials?order_id=${id}`),
      api<OrderEvent[]>(`/orders/${id}/events`),
    ])
      .then(([o, m, e]) => {
        if (active) {
          setOrder(o);
          setMaterials(m);
          setEvents(e);
          setLoadError("");
        }
      })
      .catch((e) => {
        if (active) setLoadError(e.message);
      });
    return () => {
      active = false;
    };
  }, [id, tick, aiTick]);
  if (loadError)
    return (
      <div className="alert error" role="alert">
        {loadError}
      </div>
    );
  if (!order)
    return (
      <div className="loading">
        <LoaderCircle className="spin" />
        Открываем карточку…
      </div>
    );
  const workLabels: Record<string, string> = {
    telegram_card: "Отправлена карточка для ответов в Telegram",
    order_accepted: "Заказ явно принят. Консультации прекращены",
    work_updated: "Условия или даты обновлены",
    outcome_saved: "Сохранена новая редакция итога",
    experience_accept: "Свидетельство добавлено в профиль",
    experience_revoke: "Свидетельство отозвано из профиля",
    experience_rejected: "Предложение опыта отклонено",
    experience_refreshed: "Предложение опыта обновлено",
  };
  const eventLabel = (event: OrderEvent) =>
    workLabels[event.kind] ??
    (event.kind === "created"
      ? "Карточка создана"
      : event.kind === "edited"
        ? `Изменены поля: ${Object.keys(event.detail)
            .map(
              (k) =>
                ({
                  title: "название",
                  description: "объявление",
                  notes: "заметки",
                  budget: "бюджет",
                  source: "источник",
                  source_url: "ссылка",
                })[k] || k,
            )
            .join(", ")}`
        : event.kind === "ai_queued"
          ? "Запрос ассистенту сохранён"
          : event.kind === "ai_completed"
            ? "Ответ ассистента сохранён"
            : event.kind === "ai_stale"
              ? "Оценка сохранена по прежним данным"
              : event.kind === "draft_edited"
                ? "Черновик отклика отредактирован"
                : event.kind === "status_changed"
                  ? `Статус: ${statuses[event.detail.before as OrderStatus]} → ${statuses[event.detail.after as OrderStatus]}`
                  : event.kind === "visibility_changed"
                    ? `Список заказов: ${event.detail.action}`
                    : `Добавлен материал: ${event.detail.title}`);
  return (
    <>
      <button className="back" onClick={onBack}>
        <ArrowLeft size={16} />
        Все заказы
      </button>
      <div className="page-heading detail-heading">
        <div>
          <div className="eyebrow">{order.source || "КАРТОЧКА ЗАКАЗА"}</div>
          <h1>{order.title}</h1>
          <p>
            Создан {date(order.created_at)} ·{" "}
            {order.budget || "Бюджет не указан"}
          </p>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => setEditing(true)}
        >
          <Pencil size={16} />
          Изменить
        </button>
      </div>
      <div className="form-actions order-storage-actions">
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(
              async () => {
                await api(`/orders/${id}/visibility`, "POST", {
                  action: order.archived_at ? "restore" : "archive",
                  expected_version: order.version,
                });
                onBack();
              },
              order.archived_at
                ? "Заказ возвращён на главную"
                : "Заказ убран в архив",
            )
          }
        >
          {order.archived_at ? "Вернуть из архива" : "В архив"}
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => {
            if (
              !window.confirm(
                `Переместить «${order.title}» в корзину? Его можно будет восстановить.`,
              )
            )
              return;
            void run(async () => {
              await api(`/orders/${id}/visibility`, "POST", {
                action: "delete",
                expected_version: order.version,
              });
              onBack();
            }, "Заказ перемещён в корзину");
          }}
        >
          Удалить заказ
        </button>
        {order.archived_at && <span>Заказ в архиве</span>}
      </div>
      <div className="detail-meta">
        <label>
          Состояние
          <select
            value={order.status}
            disabled={busy || !order.consulting_allowed}
            onChange={(e) =>
              void run(async () => {
                await api(`/orders/${id}/status`, "POST", {
                  expected_version: order.version,
                  status: e.target.value,
                });
              }, "Статус обновлён")
            }
          >
            {Object.entries(statuses)
              .filter(([key]) =>
                ["new", "discussion", "declined", order.status].includes(key),
              )
              .map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
          </select>
        </label>
        {order.source_url && (
          <a
            className="external-link"
            href={order.source_url}
            target="_blank"
            rel="noreferrer"
          >
            Открыть объявление
            <ArrowUpRight size={16} />
          </a>
        )}
        <button className="secondary" onClick={() => setTab("work")}>
          {order.consulting_allowed ? "Принять заказ" : "Работа и итог"}
        </button>
        {!order.consulting_allowed && (
          <span className="muted">
            Принят {date(order.accepted_at!)} · консультации завершены
          </span>
        )}
      </div>
      {order.description && (
        <section className="panel original">
          <h2>Исходное объявление</h2>
          <p>{order.description}</p>
        </section>
      )}
      {order.notes && (
        <section className="note-panel">
          <strong>Мои заметки</strong>
          <p>{order.notes}</p>
        </section>
      )}
      <div className="tabs" role="tablist" aria-label="Раздел карточки">
        {[
          ["materials", `Материалы · ${materials.length}`],
          ["work", "Работа и итог"],
          ["history", "История"],
          ["analysis", "Анализ"],
          ["chat", "Обсуждение"],
          ["draft", "Отклик"],
        ].map(([key, label]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            key={key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "materials" && (
        <>
          <MaterialComposer orderId={id} run={run} busy={busy} />
          {!materials.length ? (
            <Empty
              icon={<Paperclip size={28} />}
              title="Материалы этого заказа"
            >
              Добавь текст, скриншоты или документы. Они останутся в этой
              карточке.
            </Empty>
          ) : (
            <div className="material-list">
              {materials.map((m) => (
                <MaterialCard key={m.id} material={m} run={run} busy={busy} />
              ))}
            </div>
          )}
        </>
      )}
      {tab === "work" && (
        <WorkPanel order={order} tick={tick} run={run} busy={busy} />
      )}
      {tab === "history" && (
        <div className="panel timeline">
          {events.map((e) => (
            <div key={e.id}>
              <i />
              <section>
                <strong>{eventLabel(e)}</strong>
                <small>{date(e.created_at)}</small>
                {[
                  "edited",
                  "order_accepted",
                  "work_updated",
                  "outcome_saved",
                  "status_changed",
                  "experience_accept",
                  "experience_revoke",
                  "experience_rejected",
                  "experience_refreshed",
                ].includes(e.kind) && (
                  <details>
                    <summary>Посмотреть изменения</summary>
                    <pre>{JSON.stringify(e.detail, null, 2)}</pre>
                  </details>
                )}
              </section>
            </div>
          ))}
        </div>
      )}
      {(["analysis", "chat", "draft"] as string[]).includes(tab) && (
        <ConsultantPanel
          orderId={id}
          mode={tab as "analysis" | "chat" | "draft"}
          tick={tick}
          onOrderChanged={() => setAiTick((value) => value + 1)}
        />
      )}
      {editing && (
        <OrderForm
          initial={order}
          busy={busy}
          onClose={() => setEditing(false)}
          onSave={(values) =>
            run(async () => {
              await api(`/orders/${id}`, "PUT", {
                ...values,
                expected_version: order.version,
              });
              setEditing(false);
            }, "Изменения сохранены")
          }
        />
      )}
    </>
  );
}

function MaterialComposer({
  orderId,
  busy,
  run,
}: {
  orderId?: string;
  busy: boolean;
  run: Run;
}) {
  const [showText, setShowText] = useState(false),
    [title, setTitle] = useState(""),
    [text, setText] = useState(""),
    [role, setRole] = useState("brief");
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="panel composer">
      <ClipboardImage
        busy={busy}
        onSave={(file, done) =>
          run(async () => {
            await upload(file, orderId);
            done();
          }, "Скриншот сохранён")
        }
      />
      <div className="composer-heading">
        <div>
          <h3>Добавить материал</h3>
          <p>Текст, скриншоты, PDF или DOCX · до 20 МБ на файл</p>
        </div>
        <div className="composer-actions">
          <button
            className="secondary"
            disabled={busy}
            onClick={() => setShowText(!showText)}
          >
            <FileText size={16} />
            Текст
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            {busy ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Upload size={16} />
            )}
            Файл
          </button>
        </div>
      </div>
      <input
        className="sr-only"
        ref={input}
        aria-label="Загрузить материал"
        type="file"
        accept=".pdf,.docx,.png,.jpg,.jpeg,.webp"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file)
            void run(async () => {
              await upload(file, orderId);
            }, "Материал сохранён");
          e.target.value = "";
        }}
      />
      {showText && (
        <form
          className="text-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api("/materials/text", "POST", {
                title,
                text,
                role,
                order_id: orderId ?? null,
              });
              setTitle("");
              setText("");
              setShowText(false);
            }, "Текст сохранён");
          }}
        >
          <div className="form-grid">
            <label>
              Название
              <input
                required
                maxLength={255}
                placeholder="Например, ответ заказчика"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              Тип материала
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="brief">Объявление / ТЗ</option>
                <option value="correspondence">Переписка заказчика</option>
                <option value="note">Моя заметка</option>
              </select>
            </label>
          </div>
          <label>
            Текст
            <textarea
              required
              rows={5}
              maxLength={100000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <div className="form-actions">
            <button
              className="primary"
              disabled={busy || !text.trim() || !title.trim()}
            >
              Сохранить материал
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function MaterialCard({
  material: m,
  busy,
  run,
  orders,
  onCreate,
}: {
  material: Material;
  busy: boolean;
  run: Run;
  orders?: Order[];
  onCreate?: () => void;
}) {
  const [orderId, setOrderId] = useState("");
  const status =
    {
      ready: "Текст сохранён",
      partial: "Частичный разбор",
      needs_visual: "Нужен визуальный разбор",
      failed: "Ошибка разбора",
    }[m.extraction_status] || m.extraction_status;
  return (
    <article className="panel material">
      <div className="material-heading">
        <span className="file-icon">
          <FileText size={21} />
        </span>
        <div>
          <h3>{m.title}</h3>
          <small>
            {m.kind.toUpperCase()}
            {m.size > 0 ? ` · ${(m.size / 1024).toFixed(1)} КБ` : ""} ·{" "}
            {date(m.created_at)}
          </small>
        </div>
        {m.has_file && (
          <button
            className="icon-button"
            disabled={busy}
            aria-label={`Скачать ${m.title}`}
            onClick={() => void run(() => download(m))}
          >
            <Download size={18} />
          </button>
        )}
      </div>
      <span className={`extract-state ${m.extraction_status}`}>{status}</span>
      {m.text && <p className="material-text">{m.text}</p>}
      {m.warnings.length > 0 && (
        <div className="extract-warnings">
          {m.warnings.map((w, i) => (
            <p key={i}>{w}</p>
          ))}
        </div>
      )}
      {m.blocks.length > 0 && (
        <details>
          <summary>Извлечённый текст</summary>
          {m.blocks.map((b, i) => (
            <div className="extract-block" key={i}>
              <small>{b.location}</small>
              <p>{b.text || "Текст не найден"}</p>
            </div>
          ))}
        </details>
      )}
      {!m.order_id && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => {
            if (
              !window.confirm(
                `Удалить «${m.title}» из Входящих? Материал и файл будут удалены без восстановления.`,
              )
            )
              return;
            void run(async () => {
              await api(`/materials/${m.id}`, "DELETE");
            }, "Материал удалён");
          }}
        >
          Удалить материал
        </button>
      )}
      {!m.order_id && orders && (
        <div className="attach-row">
          <select
            aria-label={`Заказ для ${m.title}`}
            value={orderId}
            onChange={(e) => setOrderId(e.target.value)}
          >
            <option value="">Выбери заказ…</option>
            {orders.map((o) => (
              <option key={o.id} value={o.id}>
                {o.title}
              </option>
            ))}
          </select>
          <button
            className="secondary"
            disabled={busy || !orderId}
            onClick={() =>
              void run(async () => {
                await api(`/materials/${m.id}/attach`, "POST", {
                  order_id: orderId,
                });
              }, "Материал прикреплён")
            }
          >
            Прикрепить
          </button>
          <button className="text-button" disabled={busy} onClick={onCreate}>
            <Plus size={15} />
            Новый заказ
          </button>
        </div>
      )}
    </article>
  );
}

function ProfileView({
  value,
  run,
  busy,
}: {
  value: ProfileResponse;
  run: Run;
  busy: boolean;
}) {
  const [tab, setTab] = useState("capabilities"),
    [query, setQuery] = useState(""),
    [edit, setEdit] = useState<ProfileEntryEdit | null>(null);
  const [withdraw, setWithdraw] = useState<{
    evidenceId: string;
    base: ProfileResponse;
  } | null>(null);
  const [history, setHistory] = useState<Revision[]>([]),
    [old, setOld] = useState<ProfileResponse | null>(null);
  const p = value.profile;
  const chooseTab = (next: string) => {
    setTab(next);
    if (next === "history")
      void run(async () => setHistory(await api("/profile/revisions")));
  };
  function openEntry(
    kind: "capability" | "project",
    item?: Capability | import("./api").Project,
  ) {
    const fresh =
      kind === "capability"
        ? {
            id: `owner-capability-${crypto.randomUUID()}`,
            label: "",
            category: "B" as const,
            scope: "",
            evidence_ids: [],
          }
        : {
            id: `owner-project-${crypto.randomUUID()}`,
            name: "",
            scope: "",
            evidence_ids: [],
          };
    setEdit({
      kind,
      creating: !item,
      item: structuredClone(item || fresh),
      base: structuredClone(value),
    });
  }
  function openWithdraw(evidenceId: string) {
    setWithdraw({ evidenceId, base: structuredClone(value) });
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">ОСНОВАНИЯ, А НЕ ОБЕЩАНИЯ</div>
          <h1>
            Наш опыт<span className="heading-dot">.</span>
          </h1>
          <p>
            Чем уже владеем, что можем освоить и где нужна отдельная экспертиза.
          </p>
        </div>
        <span className="revision-pill">Редакция {value.revision}</span>
      </div>
      <div className="profile-banner">
        <ShieldCheck size={24} />
        <div>
          <strong>Исходный профиль задан Котом</strong>
          <p>
            Он действует уже сейчас. GitHub уточняет основания, а новые
            технологии не отсеиваются автоматически.
          </p>
        </div>
      </div>
      <div className="profile-stats">
        {(["A", "B", "C"] as const).map((c) => (
          <div key={c}>
            <span className={`category ${c}`}>{c}</span>
            <strong>
              {p.capabilities.filter((x) => x.category === c).length}
            </strong>
            <span>{categories[c]}</span>
          </div>
        ))}
      </div>
      <div className="tabs" role="tablist" aria-label="Раздел профиля">
        {[
          ["capabilities", "Возможности"],
          ["projects", `Проекты · ${p.projects.length}`],
          ["evidence", `Основания · ${p.evidence.length}`],
          ["limits", "Границы"],
          ["history", "История"],
        ].map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => chooseTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "capabilities" && (
        <>
          <div className="form-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => openEntry("capability")}
            >
              <Plus size={16} />
              Добавить возможность
            </button>
          </div>
          <label className="search profile-search">
            <Search size={18} />
            <input
              aria-label="Поиск возможностей"
              placeholder="Технология или область…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <div className="capability-list">
            {p.capabilities
              .filter((c) =>
                `${c.label} ${c.scope}`
                  .toLocaleLowerCase()
                  .includes(query.toLocaleLowerCase()),
              )
              .map((c) => (
                <article className="panel capability" key={c.id}>
                  <span className={`category ${c.category}`}>{c.category}</span>
                  <div>
                    <h3>{c.label}</h3>
                    <p>{c.scope}</p>
                    <details>
                      <summary>На чём основано</summary>
                      {c.evidence_ids
                        .map((id) => p.evidence.find((e) => e.id === id))
                        .filter(Boolean)
                        .map((e) => (
                          <div className="evidence" key={e!.id}>
                            <p>{e!.statement}</p>
                            <small>
                              {e!.source === "owner"
                                ? "Со слов владельца"
                                : e!.source}{" "}
                              · {e!.reference}
                            </small>
                            {e!.citations?.map((c, i) => (
                              <p key={i}>
                                <a
                                  href={citationUrl(c)}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {c.path}:{c.line_start}–{c.line_end} ·{" "}
                                  {c.commit_sha.slice(0, 8)}
                                </a>
                              </p>
                            ))}
                            {e!.source === "github" && (
                              <button
                                className="secondary"
                                disabled={busy}
                                onClick={() => openWithdraw(e!.id)}
                              >
                                Отозвать свидетельство
                              </button>
                            )}
                          </div>
                        ))}
                    </details>
                  </div>
                  <button
                    className="icon-button"
                    aria-label={`Уточнить ${c.label}`}
                    disabled={busy}
                    onClick={() => {
                      openEntry("capability", c);
                    }}
                  >
                    <Pencil size={16} />
                  </button>
                </article>
              ))}
          </div>
        </>
      )}
      {tab === "projects" && (
        <>
          <div className="form-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => openEntry("project")}
            >
              <Plus size={16} />
              Добавить проект
            </button>
          </div>
          <div className="project-grid">
            {p.projects.map((project) => (
              <article className="panel project" key={project.id}>
                <span className="project-symbol">
                  <Cat size={23} />
                </span>
                <h2>{project.name}</h2>
                <p>{project.scope}</p>
                <span className="source-label">
                  Собственный проект · сведения Кота
                </span>
                <details>
                  <summary>На чём основано</summary>
                  {project.evidence_ids
                    .map((id) => p.evidence.find((e) => e.id === id))
                    .filter(Boolean)
                    .map((e) => (
                      <div className="evidence" key={e!.id}>
                        <p>{e!.statement}</p>
                        {e!.citations?.map((c, i) => (
                          <p key={i}>
                            <a
                              href={citationUrl(c)}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {c.path}:{c.line_start}–{c.line_end} ·{" "}
                              {c.commit_sha.slice(0, 8)}
                            </a>
                          </p>
                        ))}
                        {e!.source === "github" && (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => openWithdraw(e!.id)}
                          >
                            Отозвать свидетельство
                          </button>
                        )}
                      </div>
                    ))}
                </details>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => openEntry("project", project)}
                >
                  <Pencil size={16} />
                  Изменить проект
                </button>
              </article>
            ))}
          </div>
        </>
      )}
      {tab === "evidence" && (
        <div className="capability-list">
          {p.evidence.map((e) => (
            <article className="panel" key={e.id}>
              <strong>
                {e.source === "owner"
                  ? "Со слов владельца"
                  : e.source === "github"
                    ? "GitHub"
                    : "Завершённый заказ"}
              </strong>
              <p>{e.statement}</p>
              {e.citations?.map((c, i) => (
                <p key={i}>
                  <a
                    href={citationUrl(c)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {c.path}:{c.line_start}–{c.line_end} ·{" "}
                    {c.commit_sha.slice(0, 8)}
                  </a>
                </p>
              ))}
              {e.source === "github" && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => openWithdraw(e.id)}
                >
                  Отозвать свидетельство
                </button>
              )}
            </article>
          ))}
        </div>
      )}
      {tab === "limits" && (
        <>
          <section className="panel">
            <h2>Области повышенного риска</h2>
            <p className="muted">
              Отдельная оценка, без автоматического запрета.
            </p>
            <ul className="risk-list">
              {p.risk_areas.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </section>
          <section className="panel rules">
            <h2>Как читаем профиль</h2>
            {p.rules.map((r, i) => (
              <p key={i}>{r}</p>
            ))}
          </section>
        </>
      )}
      {tab === "history" && (
        <div className="panel timeline">
          {history.map((r) => (
            <div key={r.revision}>
              <i />
              <section>
                <strong>Редакция {r.revision}</strong>
                <p>{r.reason}</p>
                <small>{date(r.created_at)}</small>
                <button
                  className="text-button"
                  onClick={() =>
                    void run(async () =>
                      setOld(await api(`/profile/revisions/${r.revision}`)),
                    )
                  }
                >
                  Открыть снимок
                  <ChevronRight size={14} />
                </button>
              </section>
            </div>
          ))}
        </div>
      )}
      {edit && (
        <Modal
          title={
            edit.kind === "project"
              ? edit.creating
                ? "Добавить проект"
                : "Изменить проект"
              : edit.creating
                ? "Добавить возможность"
                : "Уточнить возможность"
          }
          busy={busy}
          onClose={() => setEdit(null)}
        >
          <ProfileEntryForm
            editor={edit}
            busy={busy}
            run={run}
            onClose={() => setEdit(null)}
          />
        </Modal>
      )}
      {withdraw && (
        <Modal
          title="Отозвать свидетельство GitHub"
          busy={busy}
          onClose={() => setWithdraw(null)}
        >
          <RevokeEvidenceForm
            base={withdraw.base}
            evidenceId={withdraw.evidenceId}
            busy={busy}
            run={run}
            onClose={() => setWithdraw(null)}
          />
        </Modal>
      )}
      {old && (
        <Modal
          title={`Снимок профиля · редакция ${old.revision}`}
          onClose={() => setOld(null)}
        >
          <p>{old.reason}</p>
          <div className="revision-snapshot">
            {old.profile.capabilities.map((c) => (
              <div key={c.id}>
                <span className={`category ${c.category}`}>{c.category}</span>
                <section>
                  <strong>{c.label}</strong>
                  <p>{c.scope}</p>
                </section>
              </div>
            ))}
            <h3>Проекты</h3>
            {old.profile.projects.map((project) => (
              <section key={project.id}>
                <strong>{project.name}</strong>
                <p>{project.scope}</p>
              </section>
            ))}
            <h3>Основания</h3>
            {old.profile.evidence.map((e) => (
              <section key={e.id}>
                <strong>
                  {e.source === "owner" ? "Со слов владельца" : e.source}
                </strong>
                <p>{e.statement}</p>
                {e.citations?.map((c, i) => (
                  <p key={i}>
                    <a
                      href={citationUrl(c)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {c.path}:{c.line_start}–{c.line_end} ·{" "}
                      {c.commit_sha.slice(0, 8)}
                    </a>
                  </p>
                ))}
              </section>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
