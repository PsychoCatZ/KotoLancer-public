export type HomeScreenStatus = "unsupported" | "unknown" | "added" | "missed";
export type TelegramWebApp = {
  initData: string;
  colorScheme?: "light" | "dark";
  ready: () => void;
  expand: () => void;
  BackButton?: {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => void;
    offClick: (callback: () => void) => void;
  };
  isVersionAtLeast?: (version: string) => boolean;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  setBottomBarColor?: (color: string) => void;
  onEvent?: (event: string, handler: () => void) => void;
  offEvent?: (event: string, handler: () => void) => void;
  addToHomeScreen?: () => void;
  checkHomeScreenStatus?: (
    callback: (status: HomeScreenStatus) => void,
  ) => void;
};

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

export async function prepareTelegram() {
  if (!location.hash.includes("tgWebAppData") && !window.Telegram?.WebApp)
    return;
  if (!window.Telegram?.WebApp) {
    await new Promise<void>((resolve) => {
      const script = document.createElement("script");
      script.src = "https://telegram.org/js/telegram-web-app.js";
      script.onload = () => resolve();
      script.onerror = () => resolve();
      document.head.appendChild(script);
    });
  }
  window.Telegram?.WebApp?.ready();
  window.Telegram?.WebApp?.expand();
}
