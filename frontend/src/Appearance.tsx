import { useEffect, useState } from "react";
import { Check, Moon, Smartphone } from "lucide-react";
import { applyTheme, readTheme, saveTheme, type ThemeChoice } from "./theme";
import type { HomeScreenStatus } from "./telegram";

export default function Appearance() {
  const [theme, setTheme] = useState<ThemeChoice>(readTheme);
  const app = window.Telegram?.WebApp;
  const supported = Boolean(
    app?.isVersionAtLeast?.("8.0") &&
    app.addToHomeScreen &&
    app.checkHomeScreenStatus,
  );
  const [home, setHome] = useState<HomeScreenStatus>(
    supported ? "unknown" : "unsupported",
  );
  const [message, setMessage] = useState("");

  useEffect(() => {
    const update = () => applyTheme(theme);
    update();
    const media = matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);
    app?.onEvent?.("themeChanged", update);
    return () => {
      media.removeEventListener("change", update);
      app?.offEvent?.("themeChanged", update);
    };
  }, [theme, app]);

  useEffect(() => {
    if (!supported) return;
    let active = true;
    const added = () => {
      if (!active) return;
      setHome("added");
      setMessage("Ярлык мастерской добавлен на главный экран.");
    };
    const check = () => {
      try {
        app?.checkHomeScreenStatus?.((status) => {
          if (active) setHome(status);
        });
      } catch {
        if (active) setHome("unsupported");
      }
    };
    app?.onEvent?.("homeScreenAdded", added);
    window.addEventListener("focus", check);
    check();
    return () => {
      active = false;
      app?.offEvent?.("homeScreenAdded", added);
      window.removeEventListener("focus", check);
    };
  }, [supported, app]);

  function addShortcut() {
    if (!supported || home === "unsupported") {
      setMessage(
        "Для ярлыка открой мастерскую в актуальном Telegram на телефоне. Этот клиент добавление не поддерживает.",
      );
      return;
    }
    try {
      setMessage(
        "Подтверди добавление ярлыка в окне Telegram. На некоторых устройствах Telegram не сообщает результат — проверь главный экран.",
      );
      app?.addToHomeScreen?.();
    } catch {
      setMessage(
        "Telegram не смог открыть добавление ярлыка. Попробуй обновить Telegram и открыть мастерскую заново.",
      );
    }
  }

  return (
    <div className="appearance">
      <div className="appearance-controls">
        <label className="theme-picker">
          <Moon size={16} aria-hidden="true" />
          <span className="sr-only">Тема оформления</span>
          <select
            aria-label="Тема оформления"
            value={theme}
            onChange={(event) => {
              const value = event.target.value as ThemeChoice;
              saveTheme(value);
              setTheme(value);
            }}
          >
            <option value="auto">Авто</option>
            <option value="light">Светлая</option>
            <option value="dark">Тёмная</option>
          </select>
        </label>
        <button
          className="secondary shortcut-button"
          onClick={addShortcut}
          disabled={home === "added"}
        >
          {home === "added" ? <Check size={16} /> : <Smartphone size={16} />}
          {home === "added" ? "Ярлык добавлен" : "Ярлык на экран"}
        </button>
      </div>
      {message && (
        <p className="appearance-message" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
