import type { TelegramWebApp } from "./telegram";

const handlers = new Map<symbol, { back: () => void; priority: number }>();
let button: TelegramWebApp["BackButton"];

function dispatch() {
  // A dialog takes precedence over the card or section underneath it.
  const current = [...handlers.values()]
    .sort((a, b) => a.priority - b.priority)
    .at(-1);
  current?.back();
}

function sync() {
  const next = handlers.size ? window.Telegram?.WebApp?.BackButton : undefined;
  if (button !== next) {
    button?.offClick(dispatch);
    button?.hide();
    button = next;
    button?.onClick(dispatch);
  }
  if (handlers.size) button?.show();
}

export function bindTelegramBack(back: () => void, priority = 0) {
  const key = Symbol();
  handlers.set(key, { back, priority });
  sync();
  return () => {
    handlers.delete(key);
    sync();
  };
}
