export type ThemeChoice = "auto" | "light" | "dark";
const key = "koto-theme";

export function readTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(key);
    if (value === "light" || value === "dark") return value;
  } catch {
    // The current choice still works when WebView storage is unavailable.
  }
  return "auto";
}

export function saveTheme(choice: ThemeChoice) {
  try {
    localStorage.setItem(key, choice);
  } catch {
    // Storage can be disabled independently of the Telegram session.
  }
}

export function applyTheme(choice: ThemeChoice) {
  const app = window.Telegram?.WebApp;
  const theme =
    choice === "auto"
      ? (app?.colorScheme ??
        (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"))
      : choice;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  const background = theme === "dark" ? "#131e19" : "#f6f7f4";
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", background);
  try {
    if (app?.isVersionAtLeast?.("6.1")) {
      app.setHeaderColor?.(
        app.isVersionAtLeast("6.9") ? background : "bg_color",
      );
      app.setBackgroundColor?.(background);
    }
    if (app?.isVersionAtLeast?.("7.10")) app.setBottomBarColor?.("#132119");
  } catch {
    // Native chrome support must not prevent the web interface from changing theme.
  }
  return theme;
}
