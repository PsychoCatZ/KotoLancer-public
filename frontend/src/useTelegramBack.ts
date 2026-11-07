import { useEffect, useRef } from "react";
import { bindTelegramBack } from "./telegramBack";

export function useTelegramBack(
  active: boolean,
  back: () => void,
  priority = 0,
) {
  const latest = useRef(back);
  latest.current = back;
  useEffect(() => {
    if (!active) return;
    return bindTelegramBack(() => latest.current(), priority);
  }, [active, priority]);
}
