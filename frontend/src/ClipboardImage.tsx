import { useEffect, useState } from "react";

export default function ClipboardImage({
  busy,
  onSave,
}: {
  busy: boolean;
  onSave: (file: File, done: () => void) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [notice, setNotice] = useState("");
  const [reading, setReading] = useState(false);
  function choose(blob: Blob) {
    if (!["image/png", "image/jpeg", "image/webp"].includes(blob.type)) {
      setNotice("Вставь скриншот PNG, JPEG или WebP.");
      return;
    }
    if (blob.size > 20 * 1024 * 1024) {
      setNotice("Скриншот больше 20 МБ. Уменьши изображение.");
      return;
    }
    const extension =
      blob.type === "image/jpeg" ? "jpg" : blob.type.split("/")[1];
    setFile(
      new File([blob], `screenshot-${Date.now()}.${extension}`, {
        type: blob.type,
      }),
    );
    setNotice("");
  }
  useEffect(() => {
    if (!file) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => {
    function paste(event: ClipboardEvent) {
      const target = event.target;
      if (
        busy ||
        file ||
        event.defaultPrevented ||
        (target instanceof Element &&
          target.closest("input,textarea,[contenteditable=true]"))
      )
        return;
      const images = Array.from(event.clipboardData?.files || []).filter((f) =>
        f.type.startsWith("image/"),
      );
      if (!images.length) return;
      event.preventDefault();
      if (images.length > 1) {
        setNotice("Вставляй скриншоты по одному.");
        return;
      }
      choose(images[0]);
    }
    document.addEventListener("paste", paste);
    return () => document.removeEventListener("paste", paste);
  }, [busy, file]);
  async function read() {
    setReading(true);
    setNotice("");
    try {
      if (!navigator.clipboard?.read) throw new Error("unsupported");
      const items = await navigator.clipboard.read();
      const images = items.filter((item) =>
        item.types.some((type) =>
          ["image/png", "image/jpeg", "image/webp"].includes(type),
        ),
      );
      if (images.length !== 1) {
        setNotice(
          images.length
            ? "Вставляй скриншоты по одному."
            : "В буфере нет изображения. Скопируй скриншот и нажми Ctrl+V.",
        );
        return;
      }
      const type = images[0].types.find((t) =>
        ["image/png", "image/jpeg", "image/webp"].includes(t),
      )!;
      choose(await images[0].getType(type));
    } catch {
      setNotice(
        "Браузер не дал прочитать буфер. Скопируй скриншот и нажми Ctrl+V вне текстового поля. Можно также выбрать файл.",
      );
    } finally {
      setReading(false);
    }
  }
  return (
    <section className="clipboard-image" aria-label="Скриншот из буфера">
      <p>Скриншот можно вставить сюда через Ctrl+V, не сохраняя в файл.</p>
      <button
        type="button"
        className="secondary"
        disabled={busy || reading || !!file}
        onClick={() => void read()}
      >
        {reading ? "Читаем буфер…" : "Вставить скриншот"}
      </button>
      {notice && <p role="status">{notice}</p>}
      {file && (
        <div>
          <p>Предпросмотр · {(file.size / 1024).toFixed(1)} КБ</p>
          {preview && (
            <img
              className="clipboard-preview"
              src={preview}
              alt="Скриншот перед сохранением"
            />
          )}
          <div className="form-actions">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void onSave(file, () => setFile(null))}
            >
              Сохранить скриншот
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => setFile(null)}
            >
              Отменить
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
