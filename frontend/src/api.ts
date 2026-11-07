export type OrderStatus =
  | "new"
  | "discussion"
  | "declined"
  | "accepted"
  | "in_progress"
  | "completed"
  | "closed"
  | "cancelled";
export type Order = {
  id: string;
  title: string;
  source: string;
  source_url: string;
  budget: string;
  description: string;
  notes: string;
  status: OrderStatus;
  version: number;
  materials_revision: number;
  material_count: number;
  created_at: string;
  updated_at: string;
  accepted_at: string | null;
  archived_at: string | null;
  deleted_at: string | null;
  consulting_allowed: boolean;
  recommendation?: {
    value: "discuss" | "clarify" | "decline";
    outdated: boolean;
    job_id: string;
  } | null;
};
export type Material = {
  id: string;
  order_id: string | null;
  title: string;
  kind: string;
  role: string;
  text: string;
  size: number;
  mime: string | null;
  sha256: string | null;
  extraction_status: string;
  has_file: boolean;
  created_at: string;
  blocks: { location: string; text: string }[];
  warnings: string[];
};
export type Evidence = {
  id: string;
  source: string;
  statement: string;
  reference: string;
  citations?: {
    repository: string;
    commit_sha: string;
    path: string;
    blob_sha: string;
    line_start: number;
    line_end: number;
  }[];
};
export type Capability = {
  id: string;
  label: string;
  category: "A" | "B" | "C";
  scope: string;
  evidence_ids: string[];
};
export type Project = {
  id: string;
  name: string;
  scope: string;
  evidence_ids: string[];
};
export type Profile = {
  capabilities: Capability[];
  projects: Project[];
  evidence: Evidence[];
  risk_areas: string[];
  rules: string[];
};
export type Revision = {
  revision: number;
  reason: string;
  author: string;
  created_at: string;
};
export type ProfileResponse = Revision & { profile: Profile };
export type Workspace = {
  stage: string;
  auth_mode: string;
  browser_session: boolean;
  database: string;
  max_upload_bytes: number;
  ai_configured: boolean;
  telegram_configured: boolean;
  telegram_connected: boolean;
};
export type OrderEvent = {
  id: number;
  kind: string;
  detail: Record<string, unknown>;
  created_at: string;
};

function headers(): Record<string, string> {
  const token = sessionStorage.getItem("koto-access-token");
  const initData = window.Telegram?.WebApp?.initData;
  return {
    "X-Koto-Client": "workspace",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(initData ? { "X-Telegram-Init-Data": initData } : {}),
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request(path: string, options?: RequestInit): Promise<Response> {
  try {
    return await fetch(path, options);
  } catch {
    throw new ApiError(
      "Нет связи с мастерской. Проверь, что её сервер запущен, и повтори подключение.",
    );
  }
}

async function checked(response: Response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((item: { msg: string }) => item.msg).join("; ")
          : `Ошибка запроса (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return response;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await checked(
    await request(`/api${path}`, {
      method,
      headers: {
        ...headers(),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  return response.json();
}

export async function upload(file: File, orderId?: string): Promise<Material> {
  if (file.size > 20 * 1024 * 1024)
    throw new Error("Файл больше 20 МБ. Раздели его на части.");
  const body = new FormData();
  body.append("file", file);
  const response = await checked(
    await request(
      `/api/materials/file${orderId ? `?order_id=${orderId}` : ""}`,
      {
        method: "POST",
        headers: headers(),
        body,
      },
    ),
  );
  return response.json();
}

export async function download(material: Material) {
  const response = await checked(
    await request(`/api/materials/${material.id}/file`, { headers: headers() }),
  );
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = material.title;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function browserAuth<T>(action: string): Promise<T> {
  const response = await checked(
    await request(`/auth/browser/${action}`, {
      method: action === "info" ? "GET" : "POST",
      headers: { "X-Koto-Client": "workspace" },
      credentials: "same-origin",
    }),
  );
  return response.json();
}
