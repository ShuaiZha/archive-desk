import { createContext, useContext } from "react";
import type { Dispatch, SetStateAction } from "react";
import type {
  AuthFlow,
  DialogBounds,
  DialogCategory,
  ExportJob,
  OutputRoot,
  TelegramAccount,
  TelegramDialog,
} from "../api";

export type BootState = "loading" | "ready" | "error";
export type ChatFilter = "all" | DialogCategory;
export type RangeMode = "all" | "custom";
export type MediaKey = "photo" | "video" | "file";

export const categoryLabels: Record<DialogCategory, string> = {
  private: "私聊",
  group: "群组",
  channel: "频道",
};

export const filterLabels: Array<{ key: ChatFilter; label: string }> = [
  { key: "all", label: "全部" },
  { key: "private", label: "私聊" },
  { key: "group", label: "群组" },
  { key: "channel", label: "频道" },
];

export const activeJobStatuses = new Set([
  "created",
  "queued",
  "running",
  "waiting",
  "pausing",
  "paused",
  "awaiting_confirmation",
  "cancelling",
]);

export const terminalJobStatuses = new Set(["succeeded", "partial", "failed", "cancelled"]);

export const jobStatusLabels: Record<string, string> = {
  created: "任务已创建",
  queued: "等待执行",
  running: "正在导出",
  waiting: "等待 Telegram",
  pausing: "正在暂停",
  paused: "任务已暂停",
  awaiting_confirmation: "等待确认下载",
  cancelling: "正在取消",
  succeeded: "导出完成",
  partial: "部分完成",
  failed: "导出失败",
  cancelled: "任务已取消",
};

export const jobStageLabels: Record<string, string> = {
  queued: "准备开始",
  preflight: "检查配置",
  takeout: "建立导出会话",
  enumerating: "读取并盘点消息与媒体",
  messages: "读取并保存消息",
  downloading: "下载媒体文件",
  capacity_check: "检查磁盘容量",
  preflight_ready: "扫描预估完成",
  media: "下载媒体文件",
  flood_wait: "等待 Telegram 限流结束",
  retry_wait: "网络异常，准备重试",
  rendering: "生成 JSON",
  verifying: "校验结果",
  committing: "提交输出文件",
  finalizing: "结束导出会话",
  completed: "导出完成",
  interrupted: "进程中断，等待继续",
  failed: "导出失败",
  cancelled: "任务已取消",
};

export const formatNumber = (value: number | null | undefined) =>
  value == null ? "待扫描" : new Intl.NumberFormat("zh-CN").format(value);

export const formatBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toFixed(index === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${units[index]}`;
};

export const formatSizeLimit = (mb: number | null) =>
  mb == null ? "∞ 无限制" : mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;

export const formatDuration = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `${Math.ceil(seconds)} 秒`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return minutes > 0 ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
};

export const initials = (title: string) => {
  const normalized = title.trim();
  if (!normalized) return "?";
  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length >= 2 && words.every((word) => /^[A-Za-z]/.test(word))) {
    return words.slice(0, 2).map((word) => word[0].toUpperCase()).join("");
  }
  return Array.from(normalized).slice(0, 1).join("").toUpperCase();
};

export const avatarTone = (id: string) => {
  const tones = ["blue", "plum", "teal", "amber", "rose", "slate"];
  const hash = Array.from(id).reduce((value, character) => value + character.charCodeAt(0), 0);
  return tones[hash % tones.length];
};

export const jobErrorMessage = (job: ExportJob | null) => {
  if (!job?.error) return "";
  return typeof job.error === "string" ? job.error : job.error.message;
};

export interface ArchiveDeskModel {
  darkMode: boolean;
  setDarkMode: Dispatch<SetStateAction<boolean>>;
  bootState: BootState;
  bootError: string;
  loadBootstrap: () => Promise<void>;
  credentialsConfigured: boolean;
  accounts: TelegramAccount[];
  selectedAccount: TelegramAccount | null;
  selectedAccountId: string;
  setSelectedAccountId: Dispatch<SetStateAction<string>>;
  apiId: string;
  setApiId: Dispatch<SetStateAction<string>>;
  apiHash: string;
  setApiHash: Dispatch<SetStateAction<string>>;
  credentialsBusy: boolean;
  credentialsError: string;
  saveCredentials: () => Promise<boolean>;
  prepareCredentialEditor: () => Promise<void>;
  phone: string;
  setPhone: Dispatch<SetStateAction<string>>;
  code: string;
  setCode: Dispatch<SetStateAction<string>>;
  password: string;
  setPassword: Dispatch<SetStateAction<string>>;
  authFlow: AuthFlow | null;
  authBusy: boolean;
  authError: string;
  beginAuthorization: () => Promise<boolean>;
  verifyCode: () => Promise<boolean>;
  verifyPassword: () => Promise<boolean>;
  resendAuthCode: () => Promise<void>;
  cancelAuthorization: () => Promise<void>;
  resetAuthFlow: () => void;
  dialogs: TelegramDialog[];
  visibleDialogs: TelegramDialog[];
  dialogsLoading: boolean;
  dialogsError: string;
  dialogSearch: string;
  setDialogSearch: Dispatch<SetStateAction<string>>;
  dialogFilter: ChatFilter;
  setDialogFilter: Dispatch<SetStateAction<ChatFilter>>;
  refreshDialogs: () => void;
  selectedDialog: TelegramDialog | null;
  selectDialog: (dialog: TelegramDialog) => void;
  dialogBounds: DialogBounds | null;
  dialogBoundsLoading: boolean;
  dialogBoundsError: string;
  sidebarOpen: boolean;
  setSidebarOpen: Dispatch<SetStateAction<boolean>>;
  selectedMedia: Set<MediaKey>;
  toggleMedia: (key: MediaKey) => void;
  rangeMode: RangeMode;
  setRangeMode: Dispatch<SetStateAction<RangeMode>>;
  startDate: string;
  setStartDate: Dispatch<SetStateAction<string>>;
  endDate: string;
  setEndDate: Dispatch<SetStateAction<string>>;
  maxFileSize: number | null;
  setMaxFileSize: Dispatch<SetStateAction<number | null>>;
  outputRoots: OutputRoot[];
  outputPath: string;
  outputRootId: string;
  outputBusy: boolean;
  changeOutputPath: (path: string) => void;
  registerOutputRoot: () => Promise<OutputRoot | null>;
  workspaceError: string;
  job: ExportJob | null;
  jobBusy: boolean;
  jobRequestError: string;
  transferRate: number;
  isJobActive: boolean;
  jobFraction: number | undefined;
  eta: string;
  timezone: string;
  startExport: () => Promise<ExportJob | null>;
  runJobAction: (action: "pause" | "resume" | "cancel" | "confirm" | "recheck") => Promise<void>;
  copyOutputPath: () => Promise<void>;
  loadJob: (jobId: string) => Promise<ExportJob | null>;
  clearJob: (jobId: string) => void;
}

const ArchiveDeskContext = createContext<ArchiveDeskModel | null>(null);

export const ArchiveDeskProvider = ArchiveDeskContext.Provider;

export function useArchiveDesk(): ArchiveDeskModel {
  const value = useContext(ArchiveDeskContext);
  if (!value) throw new Error("ArchiveDeskProvider is missing");
  return value;
}
