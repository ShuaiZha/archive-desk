import {
  FluentProvider,
  webDarkTheme,
  webLightTheme,
} from "@fluentui/react-components";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserRouter } from "react-router-dom";
import {
  api,
  getErrorMessage,
  type AuthFlow,
  type BootstrapResponse,
  type DialogBounds,
  type ExportJob,
  type OutputRoot,
  type TelegramAccount,
  type TelegramDialog,
} from "./api";
import { AppRouter } from "./app/AppRouter";
import { AppShell } from "./app/AppShell";
import {
  activeJobStatuses,
  formatDuration,
  terminalJobStatuses,
  type ArchiveDeskModel,
  type BootState,
  type ChatFilter,
  type MediaKey,
  type RangeMode,
} from "./app/model";
import { BackendErrorPage, LoadingPage } from "./pages/SystemPages";

function localDateValue(isoValue: string | null): string {
  if (!isoValue) return "";
  const date = new Date(isoValue);
  if (Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function ArchiveDeskApplication() {
  const [darkMode, setDarkMode] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)").matches : false,
  );
  const [bootState, setBootState] = useState<BootState>("loading");
  const [bootError, setBootError] = useState("");
  const [credentialsConfigured, setCredentialsConfigured] = useState(false);
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [outputRoots, setOutputRoots] = useState<OutputRoot[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState("");

  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [credentialsBusy, setCredentialsBusy] = useState(false);
  const [credentialsError, setCredentialsError] = useState("");

  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [authFlow, setAuthFlow] = useState<AuthFlow | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");

  const [dialogs, setDialogs] = useState<TelegramDialog[]>([]);
  const [dialogsLoading, setDialogsLoading] = useState(false);
  const [dialogsError, setDialogsError] = useState("");
  const [dialogSearch, setDialogSearch] = useState("");
  const [debouncedDialogSearch, setDebouncedDialogSearch] = useState("");
  const [dialogFilter, setDialogFilter] = useState<ChatFilter>("all");
  const [dialogReload, setDialogReload] = useState(0);
  const [selectedDialog, setSelectedDialog] = useState<TelegramDialog | null>(null);
  const [dialogBounds, setDialogBounds] = useState<DialogBounds | null>(null);
  const [dialogBoundsLoading, setDialogBoundsLoading] = useState(false);
  const [dialogBoundsError, setDialogBoundsError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [selectedMedia, setSelectedMedia] = useState<Set<MediaKey>>(new Set(["photo", "video", "file"]));
  const [rangeMode, setRangeMode] = useState<RangeMode>("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [maxFileSize, setMaxFileSize] = useState<number | null>(1024);
  const [outputPath, setOutputPath] = useState("");
  const [outputRootId, setOutputRootId] = useState("");
  const [outputBusy, setOutputBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");

  const [job, setJob] = useState<ExportJob | null>(null);
  const [jobBusy, setJobBusy] = useState(false);
  const [jobRequestError, setJobRequestError] = useState("");
  const [transferRate, setTransferRate] = useState(0);
  const lastTransferSample = useRef<{ bytes: number; time: number } | null>(null);
  const [notice, setNotice] = useState<{
    id: number;
    title: string;
    intent: "success" | "info" | "warning";
  } | null>(null);

  const notify = useCallback(
    (title: string, intent: "success" | "info" | "warning" = "success") => {
      setNotice({ id: Date.now(), title, intent });
    },
    [],
  );

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice((current) => current?.id === notice.id ? null : current), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const applyBootstrap = useCallback((data: BootstrapResponse) => {
    setCredentialsConfigured(data.credentials_configured);
    setAccounts(data.accounts);
    setOutputRoots(data.output_roots);
    setSelectedAccountId((current) =>
      data.accounts.some((account) => account.id === current) ? current : (data.accounts[0]?.id ?? ""),
    );
    setOutputPath((current) => current || data.output_roots[0]?.path || "");
    setOutputRootId((current) =>
      data.output_roots.some((root) => root.id === current) ? current : (data.output_roots[0]?.id ?? ""),
    );
  }, []);

  const loadBootstrap = useCallback(async () => {
    setBootState("loading");
    setBootError("");
    try {
      const data = await api.bootstrap();
      applyBootstrap(data);
      setBootState("ready");
    } catch (error) {
      setBootError(getErrorMessage(error));
      setBootState("error");
    }
  }, [applyBootstrap]);

  useEffect(() => {
    void loadBootstrap();
  }, [loadBootstrap]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedDialogSearch(dialogSearch), 300);
    return () => window.clearTimeout(timer);
  }, [dialogSearch]);

  useEffect(() => {
    if (bootState !== "ready" || !credentialsConfigured || !selectedAccountId) return;
    const controller = new AbortController();
    setDialogsLoading(true);
    setDialogsError("");
    void api
      .dialogs(selectedAccountId, debouncedDialogSearch, controller.signal)
      .then((response) => {
        setDialogs(response.items);
        setSelectedDialog((current) =>
          current ? response.items.find((dialog) => dialog.id === current.id) ?? null : null,
        );
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setDialogsError(getErrorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDialogsLoading(false);
      });
    return () => controller.abort();
  }, [bootState, credentialsConfigured, debouncedDialogSearch, dialogReload, selectedAccountId]);

  useEffect(() => {
    if (bootState !== "ready" || !selectedAccountId || !selectedDialog) {
      setDialogBounds(null);
      setDialogBoundsLoading(false);
      setDialogBoundsError("");
      return;
    }
    const controller = new AbortController();
    setDialogBounds(null);
    setDialogBoundsLoading(true);
    setDialogBoundsError("");
    void api
      .dialogBounds(selectedAccountId, selectedDialog.id, controller.signal)
      .then((bounds) => {
        setDialogBounds(bounds);
        setStartDate(localDateValue(bounds.earliest_message_at));
        setEndDate(localDateValue(bounds.latest_message_at));
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setDialogBoundsError(getErrorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDialogBoundsLoading(false);
      });
    return () => controller.abort();
  }, [bootState, selectedAccountId, selectedDialog?.id]);

  const acceptJob = useCallback((nextJob: ExportJob) => {
    const now = Date.now();
    const previous = lastTransferSample.current;
    if (previous && nextJob.progress.bytes_done >= previous.bytes && now > previous.time) {
      setTransferRate(((nextJob.progress.bytes_done - previous.bytes) * 1000) / (now - previous.time));
    } else {
      setTransferRate(0);
    }
    lastTransferSample.current = { bytes: nextJob.progress.bytes_done, time: now };
    setJob(nextJob);
  }, []);

  useEffect(() => {
    if (!job || terminalJobStatuses.has(job.status)) return;
    const controller = new AbortController();
    let requestRunning = false;
    let lastRevision = job.revision ?? 0;
    const poll = async () => {
      if (requestRunning) return;
      requestRunning = true;
      try {
        acceptJob(await api.exportJob(job.id, controller.signal));
        setJobRequestError("");
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setJobRequestError(getErrorMessage(error));
        }
      } finally {
        requestRunning = false;
      }
    };
    void poll();
    const eventSource = typeof EventSource === "undefined"
      ? null
      : new EventSource(api.jobEventsUrl(job.id, lastRevision));
    const onJobEvent = (event: Event) => {
      const message = event as MessageEvent<string>;
      try {
        const payload = JSON.parse(message.data) as { revision?: number };
        const revision = payload.revision ?? Number(message.lastEventId || 0);
        if (revision <= lastRevision) return;
        lastRevision = revision;
      } catch {
        return;
      }
      void poll();
    };
    eventSource?.addEventListener("job", onJobEvent);
    const timer = window.setInterval(() => void poll(), 15_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      eventSource?.removeEventListener("job", onJobEvent);
      eventSource?.close();
    };
  }, [acceptJob, job?.id, job?.status]);

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === selectedAccountId) ?? null,
    [accounts, selectedAccountId],
  );

  const visibleDialogs = useMemo(
    () => dialogs.filter((dialog) => dialogFilter === "all" || dialog.category === dialogFilter),
    [dialogFilter, dialogs],
  );

  const isJobActive = Boolean(job && activeJobStatuses.has(job.status));
  const jobFraction = useMemo(() => {
    if (!job) return undefined;
    if (job.status === "succeeded") return 1;
    if (job.progress.bytes_total > 0) return Math.min(1, job.progress.bytes_done / job.progress.bytes_total);
    if (job.progress.files_total > 0) return Math.min(1, job.progress.files_done / job.progress.files_total);
    return undefined;
  }, [job]);

  const eta = useMemo(() => {
    if (!job || transferRate <= 0 || job.progress.bytes_total <= job.progress.bytes_done) return "";
    return formatDuration((job.progress.bytes_total - job.progress.bytes_done) / transferRate);
  }, [job, transferRate]);

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "本地时区";

  const saveCredentials = async () => {
    const numericApiId = Number(apiId.trim());
    if (!Number.isSafeInteger(numericApiId) || numericApiId <= 0) {
      setCredentialsError("API ID 必须是正整数。");
      return false;
    }
    if (!apiHash.trim()) {
      setCredentialsError("请输入 API Hash。");
      return false;
    }
    setCredentialsBusy(true);
    setCredentialsError("");
    try {
      await api.configureCredentials(numericApiId, apiHash.trim());
      setApiHash("");
      setCredentialsConfigured(true);
      notify("Telegram API 凭据已保存");
      await loadBootstrap();
      return true;
    } catch (error) {
      setCredentialsError(getErrorMessage(error));
      return false;
    } finally {
      setCredentialsBusy(false);
    }
  };

  const resetAuthFlow = useCallback(() => {
    setAuthFlow(null);
    setCode("");
    setPassword("");
    setAuthError("");
  }, []);

  const prepareCredentialEditor = useCallback(async () => {
    resetAuthFlow();
    setApiHash("");
    setCredentialsError("");
    try {
      const current = await api.credentials();
      setApiId(current.api_id == null ? "" : String(current.api_id));
    } catch (error) {
      setCredentialsError(getErrorMessage(error));
    }
  }, [resetAuthFlow]);

  const completeAuthorization = async (flow: AuthFlow) => {
    setAuthFlow(flow);
    if (flow.status !== "authorized") return false;
    setCode("");
    setPassword("");
    notify("Telegram 账号已连接");
    await loadBootstrap();
    return true;
  };

  const beginAuthorization = async () => {
    if (!phone.trim()) {
      setAuthError("请输入包含国家或地区代码的手机号。");
      return false;
    }
    setAuthBusy(true);
    setAuthError("");
    try {
      return await completeAuthorization(await api.createAuthFlow(phone.trim()));
    } catch (error) {
      setAuthError(getErrorMessage(error));
      return false;
    } finally {
      setAuthBusy(false);
    }
  };

  const verifyCode = async () => {
    if (!authFlow || !code.trim()) {
      setAuthError("请输入 Telegram 发来的验证码。");
      return false;
    }
    setAuthBusy(true);
    setAuthError("");
    try {
      return await completeAuthorization(await api.submitCode(authFlow.id, code.replace(/\s+/g, "")));
    } catch (error) {
      setAuthError(getErrorMessage(error));
      return false;
    } finally {
      setAuthBusy(false);
    }
  };

  const verifyPassword = async () => {
    if (!authFlow || !password) {
      setAuthError("请输入 Telegram 两步验证密码。");
      return false;
    }
    setAuthBusy(true);
    setAuthError("");
    try {
      return await completeAuthorization(await api.submitPassword(authFlow.id, password));
    } catch (error) {
      setAuthError(getErrorMessage(error));
      return false;
    } finally {
      setAuthBusy(false);
    }
  };

  const resendAuthCode = async () => {
    if (!authFlow) return;
    setAuthBusy(true);
    setAuthError("");
    try {
      setAuthFlow(await api.resendAuthCode(authFlow.id));
      setCode("");
      notify("验证码已重新发送", "info");
    } catch (error) {
      setAuthError(getErrorMessage(error));
    } finally {
      setAuthBusy(false);
    }
  };

  const cancelAuthorization = async () => {
    const flowId = authFlow?.id;
    setAuthBusy(true);
    try {
      if (flowId) await api.cancelAuthFlow(flowId);
    } catch (error) {
      setAuthError(getErrorMessage(error));
      return;
    } finally {
      setAuthBusy(false);
    }
    resetAuthFlow();
  };

  const refreshDialogs = () => setDialogReload((value) => value + 1);

  const selectDialog = (dialog: TelegramDialog) => {
    setSelectedDialog(dialog);
    setDialogBounds(null);
    setDialogBoundsError("");
    setStartDate("");
    setEndDate("");
    setSidebarOpen(false);
    setWorkspaceError("");
  };

  const toggleMedia = (key: MediaKey) => {
    if (isJobActive) return;
    setSelectedMedia((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const changeOutputPath = (path: string) => {
    setOutputPath(path);
    setOutputRootId(outputRoots.find((root) => root.path === path)?.id ?? "");
    setWorkspaceError("");
  };

  const registerOutputRoot = async (): Promise<OutputRoot | null> => {
    const trimmedPath = outputPath.trim();
    if (!trimmedPath) {
      setWorkspaceError("请输入本机导出目录。");
      return null;
    }
    const existing = outputRoots.find((root) => root.path === trimmedPath);
    if (existing) {
      setOutputRootId(existing.id);
      return existing;
    }
    setOutputBusy(true);
    setWorkspaceError("");
    try {
      const created = await api.createOutputRoot(trimmedPath);
      setOutputRoots((current) => [...current.filter((root) => root.id !== created.id), created]);
      setOutputRootId(created.id);
      setOutputPath(created.path);
      notify("导出目录验证通过");
      return created;
    } catch (error) {
      setWorkspaceError(getErrorMessage(error));
      return null;
    } finally {
      setOutputBusy(false);
    }
  };

  const startExport = async (): Promise<ExportJob | null> => {
    if (!selectedAccount) {
      setWorkspaceError("没有可用的 Telegram 账号，请先完成登录。");
      return null;
    }
    if (!selectedDialog) {
      setWorkspaceError("请在左侧选择一个会话。");
      return null;
    }
    if (rangeMode === "custom" && (!startDate || !endDate)) {
      setWorkspaceError("指定日期范围时必须填写开始和结束日期。");
      return null;
    }
    if (rangeMode === "custom" && startDate > endDate) {
      setWorkspaceError("开始日期不能晚于结束日期。");
      return null;
    }
    setJobBusy(true);
    setWorkspaceError("");
    setJobRequestError("");
    try {
      let root = outputRoots.find((item) => item.id === outputRootId) ?? null;
      if (!root || root.path !== outputPath.trim()) root = await registerOutputRoot();
      if (!root) return null;
      const created = await api.createExportJob({
        account_id: selectedAccount.id,
        dialog_id: selectedDialog.id,
        output_root_id: root.id,
        ...(rangeMode === "custom" ? { date_from: startDate, date_to: endDate } : {}),
        time_zone: timezone,
        max_file_size_mb: maxFileSize,
        media_types: Array.from(selectedMedia),
      });
      lastTransferSample.current = null;
      setTransferRate(0);
      acceptJob(created);
      notify("真实导出任务已创建");
      return created;
    } catch (error) {
      setWorkspaceError(getErrorMessage(error));
      return null;
    } finally {
      setJobBusy(false);
    }
  };

  const runJobAction = async (action: "pause" | "resume" | "cancel" | "confirm" | "recheck") => {
    if (!job) return;
    if (action === "cancel" && !window.confirm("确定取消当前导出任务吗？已保存的断点会由后端按任务策略处理。")) return;
    setJobBusy(true);
    setJobRequestError("");
    try {
      acceptJob(await api.exportJobAction(job.id, action));
      notify(
        action === "pause"
          ? "暂停请求已提交"
          : action === "resume"
            ? "任务继续执行"
            : action === "confirm"
              ? "下载已经开始"
              : action === "recheck"
                ? "正在重新检查磁盘空间"
              : "取消请求已提交",
        "info",
      );
    } catch (error) {
      setJobRequestError(getErrorMessage(error));
    } finally {
      setJobBusy(false);
    }
  };

  const copyOutputPath = async () => {
    const path = job?.output_path ?? outputPath;
    try {
      await navigator.clipboard.writeText(path);
      notify("输出路径已复制");
    } catch {
      setJobRequestError("浏览器无法复制路径，请手动选择路径文本。");
    }
  };

  const loadJob = useCallback(async (jobId: string): Promise<ExportJob | null> => {
    setJobRequestError("");
    try {
      const loaded = await api.exportJob(jobId);
      acceptJob(loaded);
      return loaded;
    } catch (error) {
      setJobRequestError(getErrorMessage(error));
      return null;
    }
  }, [acceptJob]);

  const clearJob = useCallback((jobId: string) => {
    setJob((current) => current?.id === jobId ? null : current);
    setJobRequestError("");
    lastTransferSample.current = null;
    setTransferRate(0);
  }, []);

  const model: ArchiveDeskModel = {
    darkMode,
    setDarkMode,
    bootState,
    bootError,
    loadBootstrap,
    credentialsConfigured,
    accounts,
    selectedAccount,
    selectedAccountId,
    setSelectedAccountId,
    apiId,
    setApiId,
    apiHash,
    setApiHash,
    credentialsBusy,
    credentialsError,
    saveCredentials,
    prepareCredentialEditor,
    phone,
    setPhone,
    code,
    setCode,
    password,
    setPassword,
    authFlow,
    authBusy,
    authError,
    beginAuthorization,
    verifyCode,
    verifyPassword,
    resendAuthCode,
    cancelAuthorization,
    resetAuthFlow,
    dialogs,
    visibleDialogs,
    dialogsLoading,
    dialogsError,
    dialogSearch,
    setDialogSearch,
    dialogFilter,
    setDialogFilter,
    refreshDialogs,
    selectedDialog,
    selectDialog,
    dialogBounds,
    dialogBoundsLoading,
    dialogBoundsError,
    sidebarOpen,
    setSidebarOpen,
    selectedMedia,
    toggleMedia,
    rangeMode,
    setRangeMode,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    maxFileSize,
    setMaxFileSize,
    outputRoots,
    outputPath,
    outputRootId,
    outputBusy,
    changeOutputPath,
    registerOutputRoot,
    workspaceError,
    job,
    jobBusy,
    jobRequestError,
    transferRate,
    isJobActive,
    jobFraction,
    eta,
    timezone,
    startExport,
    runJobAction,
    copyOutputPath,
    loadJob,
    clearJob,
  };

  return (
    <FluentProvider theme={darkMode ? webDarkTheme : webLightTheme} className={darkMode ? "theme-dark" : "theme-light"}>
      {notice && <div className={`app-notice notice-${notice.intent}`} role="status" aria-live="polite">{notice.title}</div>}
      <AppShell model={model}>
        {bootState === "loading" ? <LoadingPage /> : bootState === "error" ? <BackendErrorPage /> : <AppRouter />}
      </AppShell>
    </FluentProvider>
  );
}

function App() {
  return (
    <BrowserRouter>
      <ArchiveDeskApplication />
    </BrowserRouter>
  );
}

export default App;
