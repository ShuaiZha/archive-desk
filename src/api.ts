const API_BASE = (import.meta.env.VITE_API_BASE ?? "/api/v1").replace(/\/$/, "");

export type DialogCategory = "private" | "group" | "channel";

export interface TelegramCredentials {
  configured: boolean;
  api_id: number | null;
  api_hash_masked: string | null;
}

export interface TelegramAccount {
  id: string;
  telegram_user_id: number | string;
  display_name: string;
  username: string | null;
  phone_masked: string | null;
}

export interface OutputRoot {
  id: string;
  path: string;
}

export interface BootstrapResponse {
  api_version: string;
  credentials_configured: boolean;
  accounts: TelegramAccount[];
  output_roots: OutputRoot[];
  capabilities?: {
    container_mode: boolean;
    open_local_folder: boolean;
  };
}

export type AuthFlowStatus = "code_required" | "password_required" | "authorized";

export interface AuthFlow {
  id: string;
  status: AuthFlowStatus;
  phone_masked?: string | null;
  account?: TelegramAccount | null;
}

export interface TelegramDialog {
  id: string;
  peer_id: number | string;
  title: string;
  category: DialogCategory;
  username: string | null;
  unread_count: number;
  message_count: number | null;
  subtitle?: string | null;
}

export interface DialogListResponse {
  items: TelegramDialog[];
  next_cursor?: string | null;
  next_offset: string | null;
}

export interface DialogBounds {
  dialog_id: string;
  earliest_message_at: string | null;
  latest_message_at: string | null;
}

export type ExportJobStatus =
  | "created"
  | "queued"
  | "running"
  | "waiting"
  | "pausing"
  | "paused"
  | "awaiting_confirmation"
  | "cancelling"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled";

export type ExportJobStage =
  | "preflight"
  | "takeout"
  | "messages"
  | "media"
  | "rendering"
  | "verifying"
  | "committing"
  | "finalizing"
  | string;

export interface ExportProgress {
  messages_seen: number;
  messages_saved: number;
  files_total: number;
  files_done: number;
  bytes_done: number;
  bytes_total: number;
  files_skipped?: number;
  bytes_remaining?: number;
  unknown_size_files?: number;
  enumeration_completed?: boolean;
  scan_before_message_id?: number | null;
  upper_message_id?: number | null;
  wait_until?: string | null;
  capacity_checked?: boolean;
  disk_free_bytes?: number;
  disk_required_bytes?: number;
  disk_reserve_bytes?: number;
  disk_shortfall_bytes?: number;
  capacity_sufficient?: boolean;
  download_confirmed?: boolean;
  photos_total?: number;
  videos_total?: number;
  regular_files_total?: number;
  photos_bytes_total?: number;
  videos_bytes_total?: number;
  regular_files_bytes_total?: number;
  photos_unknown_size?: number;
  videos_unknown_size?: number;
  regular_files_unknown_size?: number;
}

export interface ExportJobError {
  code?: string;
  message: string;
  retryable?: boolean;
}

export interface ExportJob {
  id: string;
  revision?: number;
  account_id?: string;
  dialog_id?: string;
  status: ExportJobStatus;
  stage: ExportJobStage;
  progress: ExportProgress;
  output_path: string | null;
  manifest_path?: string | null;
  result_path?: string | null;
  error: ExportJobError | string | null;
  created_at: string;
  updated_at: string;
  dialog_title?: string | null;
  account_display_name?: string | null;
  config?: {
    date_from?: string | null;
    date_to?: string | null;
    time_zone?: string;
    max_file_size_mb?: number | null;
    media_types?: Array<"photo" | "video" | "file">;
  };
}

export interface CreateExportJobInput {
  account_id: string;
  dialog_id: string;
  output_root_id: string;
  date_from?: string;
  date_to?: string;
  time_zone: string;
  max_file_size_mb: number | null;
  media_types: Array<"photo" | "video" | "file">;
}

type FastApiDetail =
  | string
  | Array<{ loc?: Array<string | number>; msg?: string; type?: string }>
  | { message?: string; code?: string };

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly category?: string;
  readonly retryable: boolean;
  readonly userAction?: string | null;
  readonly requestId?: string | null;

  constructor(
    message: string,
    status: number,
    code?: string,
    options?: {
      category?: string;
      retryable?: boolean;
      userAction?: string | null;
      requestId?: string | null;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.category = options?.category;
    this.retryable = options?.retryable ?? false;
    this.userAction = options?.userAction;
    this.requestId = options?.requestId;
  }
}

function detailMessage(detail: FastApiDetail | undefined, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item.msg).filter(Boolean);
    if (messages.length > 0) return messages.join("；");
  }
  if (detail && typeof detail === "object" && "message" in detail && detail.message) {
    return detail.message;
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let payload: {
      detail?: FastApiDetail;
      message?: string;
      code?: string;
      error?: {
        code?: string;
        category?: string;
        message?: string;
        retryable?: boolean;
        user_action?: string | null;
        request_id?: string | null;
      };
    } | undefined;
    try {
      payload = await response.json();
    } catch {
      payload = undefined;
    }
    const problem = payload?.error;
    const detail = payload?.detail;
    const code =
      problem?.code ??
      payload?.code ??
      (detail && !Array.isArray(detail) && typeof detail === "object" ? detail.code : undefined);
    throw new ApiError(
      problem?.message ?? detailMessage(detail, payload?.message ?? `请求失败（HTTP ${response.status}）`),
      response.status,
      code,
      {
        category: problem?.category,
        retryable: problem?.retryable,
        userAction: problem?.user_action,
        requestId: problem?.request_id,
      },
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) };
}

export const api = {
  bootstrap(signal?: AbortSignal) {
    return request<BootstrapResponse>("/bootstrap", { signal });
  },

  credentials(signal?: AbortSignal) {
    return request<TelegramCredentials>("/telegram/credentials", { signal });
  },

  configureCredentials(apiId: number, apiHash: string) {
    return request<TelegramCredentials>("/telegram/credentials", {
      method: "PUT",
      ...jsonBody({ api_id: apiId, api_hash: apiHash }),
    });
  },

  createAuthFlow(phone: string) {
    return request<AuthFlow>("/auth/flows", {
      method: "POST",
      ...jsonBody({ phone }),
    });
  },

  submitCode(flowId: string, code: string) {
    return request<AuthFlow>(`/auth/flows/${encodeURIComponent(flowId)}/code`, {
      method: "POST",
      ...jsonBody({ code }),
    });
  },

  submitPassword(flowId: string, password: string) {
    return request<AuthFlow>(`/auth/flows/${encodeURIComponent(flowId)}/password`, {
      method: "POST",
      ...jsonBody({ password }),
    });
  },

  resendAuthCode(flowId: string) {
    return request<AuthFlow>(`/auth/flows/${encodeURIComponent(flowId)}/resend`, {
      method: "POST",
    });
  },

  cancelAuthFlow(flowId: string) {
    return request<void>(`/auth/flows/${encodeURIComponent(flowId)}`, {
      method: "DELETE",
    });
  },

  accounts(signal?: AbortSignal) {
    return request<{ items: TelegramAccount[] }>("/accounts", { signal });
  },

  async dialogs(accountId: string, search = "", signal?: AbortSignal) {
    const items: TelegramDialog[] = [];
    let cursor: string | null = null;
    do {
      const query = new URLSearchParams({ limit: "100" });
      if (search.trim()) query.set("search", search.trim());
      if (cursor) query.set("cursor", cursor);
      const page = await request<DialogListResponse>(
        `/accounts/${encodeURIComponent(accountId)}/dialogs?${query.toString()}`,
        { signal },
      );
      items.push(...page.items);
      cursor = page.next_cursor ?? page.next_offset ?? null;
    } while (cursor);
    return { items, next_cursor: null, next_offset: null } satisfies DialogListResponse;
  },

  refreshDialogs(accountId: string) {
    return request<DialogListResponse>(
      `/accounts/${encodeURIComponent(accountId)}/dialogs/refresh`,
      { method: "POST" },
    );
  },

  dialogBounds(accountId: string, dialogId: string, signal?: AbortSignal) {
    return request<DialogBounds>(
      `/accounts/${encodeURIComponent(accountId)}/dialogs/${encodeURIComponent(dialogId)}/bounds`,
      { signal },
    );
  },

  createOutputRoot(path: string) {
    return request<OutputRoot>("/output-roots", {
      method: "POST",
      ...jsonBody({ path }),
    });
  },

  createExportJob(input: CreateExportJobInput, idempotencyKey = crypto.randomUUID()) {
    return request<ExportJob>("/export-jobs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      ...jsonBody(input),
    });
  },

  exportJob(jobId: string, signal?: AbortSignal) {
    return request<ExportJob>(`/export-jobs/${encodeURIComponent(jobId)}`, { signal });
  },

  exportJobs(signal?: AbortSignal) {
    return request<{ items: ExportJob[] }>("/export-jobs", { signal });
  },

  jobEventsUrl(jobId: string, after = 0) {
    const query = new URLSearchParams({ after: String(Math.max(0, after)) });
    return `${API_BASE}/export-jobs/${encodeURIComponent(jobId)}/events?${query.toString()}`;
  },

  exportJobAction(jobId: string, action: "pause" | "resume" | "cancel" | "confirm" | "recheck") {
    return request<ExportJob>(
      `/export-jobs/${encodeURIComponent(jobId)}/actions/${action}`,
      { method: "POST" },
    );
  },

  manifestUrl(jobId: string) {
    return `${API_BASE}/export-jobs/${encodeURIComponent(jobId)}/manifest`;
  },

  resultUrl(jobId: string) {
    return `${API_BASE}/export-jobs/${encodeURIComponent(jobId)}/result.json`;
  },

  openExportFolder(jobId: string) {
    return request<void>(`/export-jobs/${encodeURIComponent(jobId)}/open-folder`, { method: "POST" });
  },

  deleteExportJob(jobId: string, deleteFiles: boolean) {
    const query = new URLSearchParams({ delete_files: String(deleteFiles) });
    return request<void>(`/export-jobs/${encodeURIComponent(jobId)}?${query.toString()}`, {
      method: "DELETE",
    });
  },
};

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.name === "AbortError") return "请求已取消";
  if (error instanceof TypeError) return "无法连接本地服务，请确认后端已经启动。";
  return "发生未知错误，请稍后重试。";
}
