import { useEffect, useRef, useState, type ReactNode } from "react";
import { browserAuth } from "./api";

type Challenge = { code: string; telegram_url: string; expires_in: number };
export default function BrowserLogin({
  onLogin,
  fallback,
}: {
  onLogin: () => void;
  fallback: ReactNode;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deadline, setDeadline] = useState(0);
  const login = useRef(onLogin);
  login.current = onLogin;
  useEffect(() => {
    let active = true;
    browserAuth<{ enabled: boolean }>("info")
      .then((r) => {
        if (active) setEnabled(r.enabled);
      })
      .catch(() => {
        if (active) setError("Не удалось проверить вход. Обнови страницу.");
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!challenge) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (Date.now() > deadline) {
        setStatus("expired");
        setChallenge(null);
        return;
      }
      try {
        const result = await browserAuth<{ status: string }>("complete");
        if (stopped) return;
        if (result.status === "authenticated") {
          login.current();
          return;
        }
        if (result.status !== "pending" && result.status !== "approved") {
          setStatus(result.status);
          setChallenge(null);
          return;
        }
        setError("");
      } catch {
        if (!stopped) setError("Связь прервалась. Проверяем вход повторно…");
      }
      if (!stopped) timer = setTimeout(() => void poll(), 2500);
    }
    timer = setTimeout(() => void poll(), 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [challenge, deadline]);
  async function start() {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const result = await browserAuth<Challenge>("start");
      setDeadline(Date.now() + result.expires_in * 1000);
      setChallenge(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось начать вход.");
    } finally {
      setBusy(false);
    }
  }
  if (enabled === false) return <>{fallback}</>;
  return (
    <div className="browser-login">
      <p>
        Мастерская только для Кота. Подтверди вход в личном чате с ботом — и
        возвращайся сюда.
      </p>
      {error && <p role="alert">{error}</p>}
      {status && (
        <p role="status">
          {status === "rejected"
            ? "Вход отклонён. Доступ браузеру не выдан."
            : "Запрос закрыт или истёк. Можно начать заново."}
        </p>
      )}
      {challenge ? (
        <>
          <p>Код этого входа</p>
          <strong className="login-code">{challenge.code}</strong>
          <p>
            Открой бота, при необходимости нажми «Старт», сверь код и выбери
            «Это я, войти». Запрос действует пять минут.
          </p>
          <a
            className="primary login-link"
            href={challenge.telegram_url}
            target="_blank"
            rel="noreferrer"
          >
            Подтвердить в Telegram
          </a>
          <p role="status">
            Жду подтверждения. Эта страница откроет мастерскую автоматически.
          </p>
        </>
      ) : (
        <button
          className="primary"
          disabled={busy || enabled === null}
          onClick={() => void start()}
        >
          {busy ? "Готовлю вход…" : "Войти через Telegram"}
        </button>
      )}
    </div>
  );
}
