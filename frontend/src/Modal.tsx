import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { useTelegramBack } from "./useTelegramBack";

export default function Modal({
  title,
  children,
  onClose,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useTelegramBack(
    true,
    () => {
      if (!busy) close.current();
    },
    100,
  );
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    container.current
      ?.querySelector<HTMLElement>("input, textarea, button")
      ?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) close.current();
      if (event.key === "Tab") {
        const nodes = container.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input, textarea, select, a[href]",
        );
        if (!nodes?.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = oldOverflow;
      previous?.focus();
    };
  }, [busy]);
  return (
    <div className="modal-shade">
      <div
        className="modal"
        ref={container}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-title">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Закрыть"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
