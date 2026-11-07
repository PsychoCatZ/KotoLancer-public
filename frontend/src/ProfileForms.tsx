import { useState, type FormEvent } from "react";
import {
  api,
  type Capability,
  type Project,
  type ProfileResponse,
} from "./api";

type Run = (work: () => Promise<void>, success?: string) => Promise<void>;
export type ProfileEntryEdit = {
  kind: "capability" | "project";
  item: Capability | Project;
  base: ProfileResponse;
  creating: boolean;
};
const categories = {
  A: "Практический опыт",
  B: "Смежный / осваиваемый",
  C: "Нужна экспертиза",
};

export function ProfileEntryForm({
  editor,
  busy,
  run,
  onClose,
}: {
  editor: ProfileEntryEdit;
  busy: boolean;
  run: Run;
  onClose: () => void;
}) {
  const [item, setItem] = useState(editor.item);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const capability = editor.kind === "capability";
  const name = "label" in item ? item.label : item.name;
  async function save(e: FormEvent) {
    e.preventDefault();
    setError("");
    await run(async () => {
      const evidenceId = `owner-edit-${crypto.randomUUID()}`;
      const profile = structuredClone(editor.base.profile);
      const changed = {
        ...item,
        evidence_ids: [...item.evidence_ids, evidenceId],
      };
      if (editor.kind === "capability") {
        profile.capabilities = editor.creating
          ? [...profile.capabilities, changed as Capability]
          : profile.capabilities.map((c) =>
              c.id === changed.id ? (changed as Capability) : c,
            );
      } else {
        profile.projects = editor.creating
          ? [...profile.projects, changed as Project]
          : profile.projects.map((p) =>
              p.id === changed.id ? (changed as Project) : p,
            );
      }
      profile.evidence.push({
        id: evidenceId,
        source: "owner",
        statement: `${name}\nОснование владельца: ${reason}`,
        reference: `Ручная корректировка владельца ${new Date().toISOString()}`,
      });
      try {
        await api("/profile", "PUT", {
          expected_revision: editor.base.revision,
          reason,
          profile,
        });
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Не удалось сохранить профиль",
        );
        throw e;
      }
      onClose();
    }, "Новая редакция профиля сохранена");
  }
  return (
    <form onSubmit={(e) => void save(e)}>
      <label>
        {capability ? "Область" : "Название проекта"}
        <input
          required
          maxLength={capability ? 300 : 200}
          value={name}
          onChange={(e) =>
            setItem(
              "label" in item
                ? { ...item, label: e.target.value }
                : { ...item, name: e.target.value },
            )
          }
        />
      </label>
      {"category" in item && (
        <label>
          Характер опыта
          <select
            value={item.category}
            onChange={(e) =>
              setItem({
                ...item,
                category: e.target.value as Capability["category"],
              })
            }
          >
            {Object.entries(categories).map(([key, label]) => (
              <option key={key} value={key}>
                {key} — {label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        {capability
          ? "Что именно умеем и где границы"
          : "Что реализовано и где границы"}
        <textarea
          required
          rows={5}
          maxLength={5000}
          value={item.scope}
          onChange={(e) => setItem({ ...item, scope: e.target.value })}
        />
      </label>
      <label>
        На чём основано изменение
        <textarea
          required
          minLength={3}
          maxLength={1000}
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Что фактически сделали или какое прежнее утверждение нужно исправить"
        />
      </label>
      <p className="form-note">
        Это твои сведения: они сохранятся как основание владельца. Прежние
        редакции остаются в истории. Добавление проекта само по себе не повышает
        категории опыта.
      </p>
      {error && (
        <p role="alert">
          {error} Черновик сохранён в форме. При конфликте закрой её и обнови
          мастерскую перед новой правкой.
        </p>
      )}
      <div className="form-actions">
        <button
          className="primary"
          disabled={
            busy ||
            !name.trim() ||
            !item.scope.trim() ||
            reason.trim().length < 3
          }
        >
          Сохранить редакцию
        </button>
      </div>
    </form>
  );
}

export function RevokeEvidenceForm({
  base,
  evidenceId,
  busy,
  run,
  onClose,
}: {
  base: ProfileResponse;
  evidenceId: string;
  busy: boolean;
  run: Run;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const evidence = base.profile.evidence.find((e) => e.id === evidenceId)!;
  const soleA = base.profile.capabilities.filter(
    (c) =>
      c.category === "A" &&
      c.evidence_ids.includes(evidenceId) &&
      c.evidence_ids.length === 1,
  );
  async function save(e: FormEvent) {
    e.preventDefault();
    setError("");
    await run(async () => {
      try {
        await api(
          `/profile/evidence/${encodeURIComponent(evidenceId)}/revoke`,
          "POST",
          { expected_revision: base.revision, reason },
        );
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Не удалось отозвать свидетельство",
        );
        throw e;
      }
      onClose();
    }, "Свидетельство отозвано новой редакцией профиля");
  }
  return (
    <form onSubmit={(e) => void save(e)}>
      <p>{evidence.statement}</p>
      <p className="form-note">
        Основание будет снято со всех связанных компетенций и проектов. Прежняя
        редакция и цитаты останутся в истории; категории автоматически не
        меняются.
      </p>
      {!!soleA.length && (
        <p role="alert">
          Это единственное основание категории A:{" "}
          {soleA.map((c) => c.label).join(", ")}. Сначала явно уточни эти
          компетенции и их основания.
        </p>
      )}
      <label>
        Почему отзываем
        <textarea
          required
          minLength={3}
          maxLength={800}
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || !!soleA.length || reason.trim().length < 3}
        >
          Отозвать свидетельство
        </button>
      </div>
    </form>
  );
}
