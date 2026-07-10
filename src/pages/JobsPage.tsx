import { Button, MessageBar, MessageBarBody, ProgressBar } from "@fluentui/react-components";
import {
  ArrowClockwise24Regular,
  Delete24Regular,
  FolderOpen24Regular,
  History24Regular,
  MoreHorizontal24Regular,
  Play24Regular,
} from "@fluentui/react-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, getErrorMessage, type ExportJob } from "../api";
import { activeJobStatuses, formatBytes, formatNumber, jobStatusLabels, useArchiveDesk } from "../app/model";

type JobFilter = "all" | "active" | "succeeded" | "failed";
const filters: Array<{ key: JobFilter; label: string }> = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "succeeded", label: "已完成" },
  { key: "failed", label: "失败" },
];

function formatJobTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

function rangeLabel(job: ExportJob): string {
  if (!job.config?.date_from && !job.config?.date_to) return "全部历史";
  return `${job.config.date_from || "最早"} — ${job.config.date_to || "最新"}`;
}

function mediaLabel(job: ExportJob): string {
  return job.config?.media_types?.map((type) => type === "photo" ? "图片" : type === "video" ? "视频" : "文件").join(" · ") || "仅消息";
}

function jobProgress(job: ExportJob): number | undefined {
  if (job.progress.bytes_total > 0) return Math.min(1, job.progress.bytes_done / job.progress.bytes_total);
  if (job.progress.files_total > 0) return Math.min(1, job.progress.files_done / job.progress.files_total);
  return undefined;
}

export function JobsPage() {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [filter, setFilter] = useState<JobFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyJobId, setBusyJobId] = useState("");

  const loadJobs = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    try { setJobs((await api.exportJobs()).items); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { if (!silent) setLoading(false); }
  }, []);

  useEffect(() => { void loadJobs(); }, [loadJobs]);
  const hasActiveJobs = jobs.some((job) => activeJobStatuses.has(job.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => void loadJobs(true), 1800);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, loadJobs]);

  const visibleJobs = useMemo(() => jobs.filter((job) => {
    if (filter === "active") return activeJobStatuses.has(job.status);
    if (filter === "succeeded") return job.status === "succeeded" || job.status === "partial";
    if (filter === "failed") return job.status === "failed" || job.status === "cancelled";
    return true;
  }), [filter, jobs]);

  const deleteJob = async (job: ExportJob, deleteFiles: boolean) => {
    const message = deleteFiles ? "将永久删除任务记录、导出目录和断点文件。确定继续吗？" : "只删除任务记录，已经导出的文件会保留。确定继续吗？";
    if (!window.confirm(message)) return;
    setBusyJobId(job.id);
    try {
      await api.deleteExportJob(job.id, deleteFiles);
      model.clearJob(job.id);
      setJobs((current) => current.filter((item) => item.id !== job.id));
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusyJobId(""); }
  };

  const retryJob = async (job: ExportJob) => {
    setBusyJobId(job.id);
    try {
      await api.exportJobAction(job.id, "resume");
      await model.loadJob(job.id);
      navigate(`/jobs/${job.id}`);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusyJobId(""); }
  };

  const openFolder = async (job: ExportJob) => {
    setBusyJobId(job.id);
    try { await api.openExportFolder(job.id); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusyJobId(""); }
  };

  return (
    <main className="jobs-page jobs-page-compact">
      <div className="jobs-heading">
        <div><span className="page-kicker">本机任务</span><h1>任务</h1><p>查看下载进度、预估结果和已完成导出。</p></div>
        <div className="jobs-heading-actions"><Button appearance="secondary" icon={<ArrowClockwise24Regular />} disabled={loading} onClick={() => void loadJobs()}>刷新</Button><Button appearance="primary" onClick={() => navigate("/export")}>新建任务</Button></div>
      </div>

      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}

      <div className="jobs-toolbar" aria-label="任务状态筛选">
        <div className="filter-tabs jobs-filter-tabs">{filters.map((item) => <button key={item.key} type="button" className={filter === item.key ? "is-active" : ""} aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>{item.label}</button>)}</div>
        <span>{visibleJobs.length} 个任务</span>
      </div>

      {loading ? (
        <div className="jobs-loading" aria-label="正在加载任务"><span /><span /><span /></div>
      ) : visibleJobs.length === 0 ? (
        <section className="jobs-empty"><History24Regular /><h2>没有匹配的任务</h2><p>新建任务后，任务会显示在这里。</p><Button appearance="primary" onClick={() => navigate("/export")}>新建任务</Button></section>
      ) : (
        <section className="job-table" aria-label="导出任务列表">
          <div className="job-table-head"><span>会话与状态</span><span>范围</span><span>内容</span><span>结果</span><span>时间</span><span aria-hidden="true" /></div>
          {visibleJobs.map((job) => {
            const active = activeJobStatuses.has(job.status);
            const completed = job.status === "succeeded" || job.status === "partial";
            const failed = job.status === "failed";
            return (
              <article className="job-table-row" key={job.id}>
                <div className="job-table-title"><span className={`job-status-dot status-${completed ? "success" : failed ? "failed" : active ? "active" : "neutral"}`} /><span><strong>{job.dialog_title || `会话 ${job.dialog_id ?? ""}`}</strong><small>{jobStatusLabels[job.status] ?? job.status} · {job.id.slice(0, 8)}</small></span>{active && <ProgressBar value={jobProgress(job)} />}</div>
                <div><strong>{rangeLabel(job)}</strong><small>{formatNumber(job.progress.messages_saved)} 条消息</small></div>
                <div><strong>{mediaLabel(job)}</strong><small>{formatNumber(job.progress.files_done)} / {formatNumber(job.progress.files_total)} 个文件</small></div>
                <div><strong>{formatBytes(job.progress.bytes_done)}</strong><small>{(job.progress.files_skipped ?? 0) > 0 ? `跳过 ${formatNumber(job.progress.files_skipped)}` : "无跳过"}</small></div>
                <div><strong>{formatJobTime(job.updated_at)}</strong><small>{completed ? "已完成" : jobStatusLabels[job.status] ?? job.status}</small></div>
                <div className="job-table-actions">
                  {completed && <Button appearance="subtle" icon={<FolderOpen24Regular />} aria-label={`打开 ${job.dialog_title || "导出"} 文件夹`} disabled={busyJobId === job.id} onClick={() => void openFolder(job)} />}
                  {failed && <Button appearance="subtle" icon={<Play24Regular />} aria-label="重试任务" disabled={busyJobId === job.id} onClick={() => void retryJob(job)} />}
                  <Button appearance={job.status === "awaiting_confirmation" ? "primary" : "secondary"} onClick={() => navigate(`/jobs/${job.id}`)}>{job.status === "awaiting_confirmation" ? "查看预估" : "查看"}</Button>
                  {!active && (
                    <details className="row-menu">
                      <summary aria-label="更多操作"><MoreHorizontal24Regular /></summary>
                      <div><button type="button" disabled={busyJobId === job.id} onClick={() => void deleteJob(job, false)}>删除记录</button><button type="button" className="danger-action" disabled={busyJobId === job.id} onClick={() => void deleteJob(job, true)}><Delete24Regular />删除记录及文件</button></div>
                    </details>
                  )}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
