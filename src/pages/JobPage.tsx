import { Button, MessageBar, MessageBarBody, ProgressBar } from "@fluentui/react-components";
import {
  ArrowClockwise24Regular,
  ArrowDownload24Regular,
  Calendar24Regular,
  CheckmarkCircle24Regular,
  Copy24Regular,
  Delete24Regular,
  Document24Regular,
  FolderOpen24Regular,
  Image24Regular,
  Pause24Regular,
  Play24Regular,
  ShieldCheckmark24Regular,
  Video24Regular,
  Warning24Regular,
} from "@fluentui/react-icons";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, getErrorMessage, type ExportJob } from "../api";
import {
  activeJobStatuses,
  formatBytes,
  formatNumber,
  formatSizeLimit,
  jobErrorMessage,
  jobStageLabels,
  useArchiveDesk,
} from "../app/model";
import { ExportStepper } from "../app/ExportStepper";

function formatJobTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

function rangeLabel(job: ExportJob): string {
  const start = job.config?.date_from;
  const end = job.config?.date_to;
  if (!start && !end) return "全部历史记录";
  return `${start || "最早"} 至 ${end || "最新"}`;
}

function mediaTypesLabel(job: ExportJob): string {
  return job.config?.media_types
    ?.map((type) => type === "photo" ? "图片" : type === "video" ? "视频" : "普通文件")
    .join("、") || "无";
}

function jobCurrentStep(job: ExportJob): 2 | 3 | 4 {
  const capacityBlocked = job.progress.capacity_checked === true
    && (job.progress.capacity_sufficient === false
      || ((job.progress.disk_free_bytes ?? 0) < (job.progress.disk_required_bytes ?? 0)));
  return job.status === "succeeded" || job.status === "partial"
    ? 4
    : job.status === "awaiting_confirmation" || capacityBlocked || ["enumerating", "capacity_check", "preflight_ready"].includes(job.stage)
      ? 2
      : 3;
}

function JobContext({ job, scanLabel }: { job: ExportJob; scanLabel: string }) {
  return (
    <section className="preflight-context" aria-label="导出范围">
      <div className="preflight-dialog">
        <span className="preflight-avatar">{job.dialog_title?.slice(0, 1) || "?"}</span>
        <div><strong>{job.dialog_title || "Telegram 会话"}</strong><small>{mediaTypesLabel(job)}</small></div>
      </div>
      <div><Calendar24Regular /><span><small>时间范围</small><strong>{rangeLabel(job)}</strong></span></div>
      <div><CheckmarkCircle24Regular /><span><small>扫描状态</small><strong>{scanLabel}</strong><em>已读取 {formatNumber(job.progress.messages_saved)} 条消息</em></span></div>
    </section>
  );
}

function MediaBreakdown({ job, scanning = false }: { job: ExportJob; scanning?: boolean }) {
  const selected = new Set(job.config?.media_types ?? ["photo", "video", "file"]);
  const rows = [
    {
      kind: "photo",
      label: "图片",
      icon: <Image24Regular />,
      count: job.progress.photos_total ?? 0,
      bytes: job.progress.photos_bytes_total,
      unknown: job.progress.photos_unknown_size,
    },
    {
      kind: "video",
      label: "视频",
      icon: <Video24Regular />,
      count: job.progress.videos_total ?? 0,
      bytes: job.progress.videos_bytes_total,
      unknown: job.progress.videos_unknown_size,
    },
    {
      kind: "file",
      label: "普通文件",
      icon: <Document24Regular />,
      count: job.progress.regular_files_total ?? 0,
      bytes: job.progress.regular_files_bytes_total,
      unknown: job.progress.regular_files_unknown_size,
    },
  ].filter((row) => selected.has(row.kind));

  return (
    <section className="content-breakdown">
      <div className="result-section-heading">
        <h2>内容明细</h2>
        <p>{scanning ? "数字会随扫描继续增长。" : "已知大小不包含被跳过的文件。"}</p>
      </div>
      <div className="content-breakdown-table-wrap">
        <table className="content-breakdown-table" aria-label="导出内容明细">
          <thead><tr><th>类型</th><th>{scanning ? "已发现" : "文件数"}</th><th>已知大小</th><th>大小未知</th></tr></thead>
          <tbody>
            {rows.map((row) => {
              const bytes = row.bytes ?? (rows.length === 1 ? job.progress.bytes_total : undefined);
              const unknown = row.unknown ?? (rows.length === 1 ? job.progress.unknown_size_files : undefined);
              return (
                <tr key={row.kind}>
                  <th scope="row"><span className={`breakdown-icon ${row.kind}`}>{row.icon}</span><strong>{row.label}</strong></th>
                  <td>{formatNumber(row.count)}</td>
                  <td>{bytes == null ? scanning ? "计算中" : "待刷新" : formatBytes(bytes)}</td>
                  <td className={(unknown ?? 0) > 0 ? "has-warning" : ""}>{unknown == null ? scanning ? "计算中" : "待刷新" : formatNumber(unknown)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ScanView({ job, errorMessage }: { job: ExportJob; errorMessage: string }) {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const enumerationComplete = job.progress.enumeration_completed === true;
  const paused = job.status === "paused";
  const failed = job.status === "failed";
  const cancelled = job.status === "cancelled" || job.status === "cancelling";
  const working = !paused && !failed && !cancelled;
  const skipped = job.progress.files_skipped ?? 0;
  const unknown = job.progress.unknown_size_files ?? 0;

  const title = failed
    ? "扫描未完成"
    : cancelled
      ? "扫描已取消"
      : paused
        ? "扫描已暂停"
        : enumerationComplete
          ? "扫描完成，正在检查磁盘空间"
          : "正在读取历史消息";
  const description = enumerationComplete
    ? "内容清单已经生成，正在计算所需空间。"
    : "当前数字是已扫描部分的累计结果，最终大小可能继续增加。";

  return (
    <main className="job-flow-page">
      <ExportStepper currentStep={jobCurrentStep(job)} />
      <JobContext job={job} scanLabel={enumerationComplete ? "消息扫描完成" : "正在扫描"} />
      <section className={`scan-live-panel ${failed ? "is-failed" : ""}`}>
        <div className="scan-live-heading">
          <span className="scan-live-icon"><ArrowClockwise24Regular /></span>
          <div><span>扫描预估</span><h1>{title}</h1><p>{description}</p></div>
        </div>
        {working && <ProgressBar thickness="large" />}
        <div className="scan-live-total">
          <span>{enumerationComplete ? "预计下载" : "当前已发现大小"}</span>
          <strong>{formatBytes(job.progress.bytes_total ?? 0)}</strong>
          <small>{formatNumber(job.progress.files_total)} 个文件，已读取 {formatNumber(job.progress.messages_saved)} 条消息</small>
        </div>
        <dl className="scan-live-facts">
          <div><dt>将跳过</dt><dd>{formatNumber(skipped)}</dd></div>
          <div><dt>大小未知</dt><dd className={unknown > 0 ? "has-warning" : ""}>{formatNumber(unknown)}</dd></div>
          <div><dt>单文件限制</dt><dd>{formatSizeLimit(job.config?.max_file_size_mb ?? null)}</dd></div>
        </dl>
      </section>

      <MediaBreakdown job={job} scanning={!enumerationComplete} />

      {!enumerationComplete && !failed && !cancelled && (
        <MessageBar intent="info"><MessageBarBody>扫描期间显示的是当前累计值。全部历史消息读取完成后，系统才会生成最终预估并检查磁盘空间。</MessageBarBody></MessageBar>
      )}
      {errorMessage && <MessageBar intent="error"><MessageBarBody>{errorMessage}</MessageBarBody></MessageBar>}

      <div className="scan-actions">
        <div>
          {(job.status === "running" || job.status === "waiting" || job.status === "queued") && <Button appearance="secondary" icon={<Pause24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("pause")}>暂停扫描</Button>}
          {job.status === "paused" && <Button appearance="primary" icon={<Play24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("resume")}>继续扫描</Button>}
          {job.status === "failed" && <Button appearance="primary" icon={<ArrowClockwise24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("resume")}>继续扫描</Button>}
          {activeJobStatuses.has(job.status) && job.status !== "cancelling" && <Button appearance="subtle" icon={<Delete24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("cancel")}>取消任务</Button>}
        </div>
        {(failed || cancelled) && <Button appearance="secondary" onClick={() => navigate("/export")}>返回导出配置</Button>}
      </div>
    </main>
  );
}

function PreflightView({ job }: { job: ExportJob }) {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const free = job.progress.disk_free_bytes ?? 0;
  const reserve = job.progress.disk_reserve_bytes ?? 0;
  const download = job.progress.bytes_total ?? 0;
  const required = job.progress.disk_required_bytes ?? download + reserve;
  const shortfall = job.progress.disk_shortfall_bytes ?? Math.max(0, required - free);
  const capacitySufficient = job.progress.capacity_sufficient ?? free >= required;
  const remainingAfterDownload = Math.max(0, free - required);
  const requiredPercent = free > 0 ? Math.min(100, (required / free) * 100) : 100;
  const unknown = job.progress.unknown_size_files ?? 0;
  const skipped = job.progress.files_skipped ?? 0;
  const willDownload = Math.max(0, job.progress.files_total - skipped);
  const decisionTitle = capacitySufficient
    ? unknown > 0 ? "空间检查通过，但预估存在未知量" : "可以开始下载"
    : "磁盘空间不足";

  const adjust = async () => {
    await model.runJobAction("cancel");
    navigate("/export");
  };

  const primaryAction = async () => {
    await model.runJobAction(capacitySufficient ? "confirm" : "recheck");
  };

  return (
    <main className="job-flow-page">
      <ExportStepper currentStep={jobCurrentStep(job)} />
      <JobContext job={job} scanLabel="扫描完成" />

      <section className={`preflight-decision ${capacitySufficient ? unknown > 0 ? "has-warning" : "is-ready" : "is-insufficient"}`}>
        <div className="decision-status">
          {capacitySufficient && unknown === 0 ? <CheckmarkCircle24Regular /> : <Warning24Regular />}
          <span><small>扫描预估结果</small><h1>{decisionTitle}</h1></span>
        </div>
        <div className="decision-total">
          <span>{unknown > 0 ? "已知内容至少" : "预计下载"}</span>
          <strong>{formatBytes(download)}</strong>
          <small>将下载 {formatNumber(willDownload)} 个文件{unknown > 0 ? `，其中 ${formatNumber(unknown)} 个大小未知` : ""}</small>
        </div>
        {!capacitySufficient && <div className="decision-shortfall"><span>当前空间缺口</span><strong>{formatBytes(shortfall)}</strong><small>释放空间或缩小导出范围后重新检查</small></div>}
      </section>

      <MediaBreakdown job={job} />

      <section className={`capacity-panel ${capacitySufficient ? "" : "is-insufficient"}`}>
        <div className="result-section-heading">
          <h2>磁盘空间</h2>
          <p>{unknown > 0 ? "空间检查基于已知文件大小。" : "包含下载内容和安全预留。"}</p>
        </div>
        <dl className="capacity-facts">
          <div><dt>预计下载</dt><dd>{formatBytes(download)}</dd></div>
          <div><dt>安全预留</dt><dd>{formatBytes(reserve)}</dd></div>
          <div><dt>总共需要</dt><dd>{formatBytes(required)}</dd></div>
          <div><dt>当前可用</dt><dd>{formatBytes(free)}</dd></div>
        </dl>
        <div className="capacity-track" aria-label={`所需空间约占当前可用空间的 ${Math.round(requiredPercent)}%`}><span style={{ width: `${Math.max(2, requiredPercent)}%` }} /></div>
        <div className="capacity-result">
          {capacitySufficient ? <CheckmarkCircle24Regular /> : <Warning24Regular />}
          <span><strong>{capacitySufficient ? `下载后预计剩余 ${formatBytes(remainingAfterDownload)}` : `至少还差 ${formatBytes(shortfall)}`}</strong><small>{capacitySufficient ? "磁盘空间检查通过" : "当前不能开始下载"}</small></span>
        </div>
      </section>

      <div className="preflight-notices">
        {skipped > 0 && <MessageBar intent="warning"><MessageBarBody>有 {formatNumber(skipped)} 个文件超过单文件大小限制，将只记录元数据。当前限制为 {formatSizeLimit(job.config?.max_file_size_mb ?? null)}。</MessageBarBody></MessageBar>}
        {unknown > 0 && <MessageBar intent="warning"><MessageBarBody>有 {formatNumber(unknown)} 个文件大小未知，实际占用可能高于当前预估。下载仍支持暂停和断点续传。</MessageBarBody></MessageBar>}
        {!capacitySufficient && <MessageBar intent="error"><MessageBarBody>总共需要 {formatBytes(required)}，当前可用 {formatBytes(free)}，至少还差 {formatBytes(shortfall)}。请释放空间或返回调整导出范围。</MessageBarBody></MessageBar>}
        {model.jobRequestError && <MessageBar intent="error"><MessageBarBody>{model.jobRequestError}</MessageBarBody></MessageBar>}
      </div>

      <details className="preflight-settings">
        <summary><ShieldCheckmark24Regular aria-hidden="true" /><span><strong>导出设置</strong><small>历史范围：{rangeLabel(job)}；内容：{mediaTypesLabel(job)}；单文件限制：{formatSizeLimit(job.config?.max_file_size_mb ?? null)}；输出目录：{job.output_path}</small></span></summary>
      </details>

      <div className="preflight-actions">
        <Button appearance="secondary" size="large" disabled={model.jobBusy} onClick={() => void adjust()}>返回调整</Button>
        <Button appearance="primary" size="large" icon={capacitySufficient ? <ArrowDownload24Regular /> : <ArrowClockwise24Regular />} disabled={model.jobBusy} onClick={() => void primaryAction()}>{model.jobBusy ? "正在检查" : capacitySufficient ? unknown > 0 ? "了解风险并开始下载" : "开始下载" : "重新检查空间"}</Button>
      </div>
    </main>
  );
}

export function JobPage() {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const { jobId = "" } = useParams();
  const job = model.job?.id === jobId ? model.job : null;
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    if (!jobId || job) return;
    void model.loadJob(jobId);
  }, [job, jobId, model.loadJob]);

  const completed = job?.status === "succeeded" || job?.status === "partial";
  const fraction = useMemo(() => {
    if (!job) return undefined;
    if (completed) return 1;
    if (job.progress.bytes_total > 0) return Math.min(1, job.progress.bytes_done / job.progress.bytes_total);
    return undefined;
  }, [completed, job]);

  if (!job) {
    return <main className="job-page-shell"><section className="job-page-card"><div className="job-page-loading"><ArrowDownload24Regular /><h1>正在读取任务</h1><p>{model.jobRequestError || "正在从本地后端恢复任务状态。"}</p></div></section></main>;
  }

  const errorMessage = localError || jobErrorMessage(job) || model.jobRequestError;
  const isCapacityFailure = job.progress.capacity_checked === true
    && (job.progress.capacity_sufficient === false
      || ((job.progress.disk_free_bytes ?? 0) < (job.progress.disk_required_bytes ?? 0)));
  if (job.status === "awaiting_confirmation" || isCapacityFailure) return <PreflightView job={job} />;

  const showScanView = !completed && (
    job.stage === "enumerating"
    || job.stage === "capacity_check"
    || job.progress.enumeration_completed !== true
  );
  if (showScanView) return <ScanView job={job} errorMessage={errorMessage} />;

  const openFolder = async () => {
    setLocalError("");
    try {
      await api.openExportFolder(job.id);
    } catch (error) {
      setLocalError(getErrorMessage(error));
    }
  };

  return (
    <main className="job-flow-page">
      <ExportStepper currentStep={jobCurrentStep(job)} />
      <section className={`job-runtime ${completed ? "is-complete" : job.status === "failed" ? "is-failed" : ""}`}>
        <div className="runtime-heading">
          <span className="runtime-icon">{completed ? <CheckmarkCircle24Regular /> : <ArrowDownload24Regular />}</span>
          <div><span className="page-kicker">{completed ? "导出完成" : job.status === "failed" ? "需要处理" : "任务进行中"}</span><h1>{completed ? job.dialog_title || "导出完成" : jobStageLabels[job.stage] ?? job.stage}</h1><p>任务 {job.id.slice(0, 8)}，{rangeLabel(job)}</p></div>
          {!completed && fraction != null && <strong className="runtime-percent">{Math.round(fraction * 100)}%</strong>}
        </div>

        {!completed && <ProgressBar value={fraction} thickness="large" />}

        <dl className="runtime-metrics">
          <div><dt>已保存消息</dt><dd>{formatNumber(job.progress.messages_saved)}</dd></div>
          <div><dt>图片</dt><dd>{formatNumber(job.progress.photos_total ?? 0)}</dd></div>
          <div><dt>视频</dt><dd>{formatNumber(job.progress.videos_total ?? 0)}</dd></div>
          <div><dt>普通文件</dt><dd>{formatNumber(job.progress.regular_files_total ?? 0)}</dd></div>
          <div><dt>{completed ? "下载总量" : "已下载"}</dt><dd>{formatBytes(job.progress.bytes_done)}</dd></div>
          <div><dt>{completed ? "完成时间" : "预计剩余"}</dt><dd>{completed ? formatJobTime(job.updated_at) : model.eta || "计算中"}</dd></div>
        </dl>

        {errorMessage && <MessageBar intent="error"><MessageBarBody>{errorMessage}</MessageBarBody></MessageBar>}

        {completed ? (
          <div className="completion-result">
            <div><strong>文件已保存到</strong><code>{job.output_path}</code></div>
            <div className="completion-actions">
              {model.canOpenLocalFolder && <Button appearance="primary" size="large" icon={<FolderOpen24Regular />} onClick={() => void openFolder()}>打开导出文件夹</Button>}
              <Button appearance={model.canOpenLocalFolder ? "secondary" : "primary"} size={model.canOpenLocalFolder ? "medium" : "large"} icon={<Copy24Regular />} onClick={() => void model.copyOutputPath()}>{model.containerMode ? "复制容器路径" : "复制路径"}</Button>
              <a className="artifact-link" href={api.manifestUrl(job.id)} target="_blank" rel="noreferrer">查看完整性报告</a>
            </div>
          </div>
        ) : (
          <div className="runtime-actions">
            {(job.status === "running" || job.status === "waiting" || job.status === "queued") && <Button appearance="secondary" icon={<Pause24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("pause")}>暂停</Button>}
            {job.status === "paused" && <Button appearance="primary" icon={<Play24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("resume")}>继续</Button>}
            {job.status === "failed" && <Button appearance="primary" icon={<ArrowClockwise24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("resume")}>重试任务</Button>}
            {activeJobStatuses.has(job.status) && job.status !== "cancelling" && <Button appearance="subtle" icon={<Delete24Regular />} disabled={model.jobBusy} onClick={() => void model.runJobAction("cancel")}>取消</Button>}
          </div>
        )}

        <div className="runtime-footer-actions"><Button appearance="secondary" onClick={() => navigate("/jobs")}>任务历史</Button><Button appearance="subtle" onClick={() => navigate("/export")}>新建任务</Button></div>
      </section>
    </main>
  );
}
