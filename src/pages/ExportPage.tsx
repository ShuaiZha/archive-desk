import {
  Button,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Radio,
  RadioGroup,
  SearchBox,
  Slider,
} from "@fluentui/react-components";
import {
  ArrowRight24Regular,
  Chat24Regular,
  Checkmark24Regular,
  Dismiss24Regular,
  Document24Regular,
  Folder24Regular,
  History24Regular,
  Image24Regular,
  ShieldLock24Regular,
  Video24Regular,
} from "@fluentui/react-icons";
import { useNavigate } from "react-router-dom";
import {
  activeJobStatuses,
  avatarTone,
  categoryLabels,
  filterLabels,
  formatSizeLimit,
  initials,
  useArchiveDesk,
  type RangeMode,
} from "../app/model";
import { ExportStepper } from "../app/ExportStepper";

function formatMessageTime(value: string | null | undefined, timezone: string): string {
  if (!value) return "没有消息";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function ExportPage() {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const hasActiveJob = Boolean(model.job && activeJobStatuses.has(model.job.status));

  const primaryAction = async () => {
    if (model.job && activeJobStatuses.has(model.job.status)) {
      navigate(`/jobs/${model.job.id}`);
      return;
    }
    const created = await model.startExport();
    if (created) navigate(`/jobs/${created.id}`);
  };

  return (
    <div className="export-workspace">
      {model.sidebarOpen && (
        <button className="sidebar-scrim" aria-label="关闭会话列表" onClick={() => model.setSidebarOpen(false)} />
      )}

      <aside className={`chat-sidebar export-chat-sidebar ${model.sidebarOpen ? "is-open" : ""}`}>
        <div className="sidebar-header">
          <div><h2>选择会话</h2><p>{model.selectedDialog ? "已选择 1 个会话" : "从一个会话开始"}</p></div>
          <Button className="sidebar-close" appearance="subtle" icon={<Dismiss24Regular />} aria-label="关闭会话列表" onClick={() => model.setSidebarOpen(false)} />
        </div>

        <SearchBox
          className="search-control"
          placeholder="搜索会话"
          value={model.dialogSearch}
          disabled={model.isJobActive}
          onChange={(_, data) => model.setDialogSearch(data.value)}
        />

        <div className="filter-tabs" aria-label="会话类型筛选">
          {filterLabels.map((item) => (
            <button key={item.key} type="button" className={model.dialogFilter === item.key ? "is-active" : ""} aria-pressed={model.dialogFilter === item.key} onClick={() => model.setDialogFilter(item.key)}>
              {item.label}
            </button>
          ))}
        </div>

        <div className="select-visible-row">
          <span>{model.dialogsLoading ? "正在读取" : `${model.visibleDialogs.length} 个会话`}</span>
          <button type="button" disabled={model.dialogsLoading || model.isJobActive} onClick={model.refreshDialogs}>刷新</button>
        </div>

        <div className="chat-list">
          {model.dialogsLoading ? (
            <div className="dialog-skeleton" aria-label="正在加载会话"><span /><span /><span /><span /></div>
          ) : model.dialogsError ? (
            <div className="empty-state"><Dismiss24Regular /><strong>无法加载会话</strong><span>{model.dialogsError}</span><Button size="small" appearance="secondary" onClick={model.refreshDialogs}>重试</Button></div>
          ) : model.visibleDialogs.length === 0 ? (
            <div className="empty-state"><Chat24Regular /><strong>没有匹配的会话</strong><span>清除搜索词或切换类型后重试。</span></div>
          ) : model.visibleDialogs.map((dialog) => {
            const selected = model.selectedDialog?.id === dialog.id;
            const subtitle = dialog.subtitle || (dialog.username ? `@${dialog.username}` : categoryLabels[dialog.category]);
            return (
              <button
                type="button"
                key={dialog.id}
                className={`chat-row ${selected ? "is-selected" : ""}`}
                disabled={model.isJobActive}
                aria-pressed={selected}
                onClick={() => model.selectDialog(dialog)}
              >
                <span className={`chat-avatar tone-${avatarTone(dialog.id)}`}>{initials(dialog.title)}</span>
                <span className="chat-copy"><strong>{dialog.title}</strong><small>{subtitle}</small></span>
                {selected && <Checkmark24Regular className="selected-check" aria-hidden="true" />}
              </button>
            );
          })}
        </div>

        <div className="sidebar-security"><ShieldLock24Regular /><span>来自当前已授权账号</span></div>
      </aside>

      <main className="export-main">
        <div className="export-main-scroll">
          <div className="export-form">
            <ExportStepper currentStep={1} className="export-config-stepper" />
            <div className="page-heading export-page-heading">
              <div><span className="page-kicker">新建任务</span><h1>任务配置</h1><p>设置导出范围和内容，下一步会先扫描并预估容量。</p></div>
            </div>

            {model.workspaceError && <MessageBar intent="error" className="error-message"><MessageBarBody>{model.workspaceError}</MessageBarBody></MessageBar>}

            <section className={`selected-dialog-header ${model.selectedDialog ? "has-selection" : ""}`}>
              <span className={`selected-dialog-avatar ${model.selectedDialog ? `tone-${avatarTone(model.selectedDialog.id)}` : ""}`}>
                {model.selectedDialog ? initials(model.selectedDialog.title) : <Chat24Regular />}
              </span>
              <div>
                <small>导出来源</small>
                <strong>{model.selectedDialog?.title ?? "请在左侧选择一个会话"}</strong>
                <span>{model.selectedDialog ? (model.selectedDialog.username ? `@${model.selectedDialog.username} · ${categoryLabels[model.selectedDialog.category]}` : categoryLabels[model.selectedDialog.category]) : "每个任务导出一个会话"}</span>
              </div>
            </section>

            <section className="config-section" aria-labelledby="range-heading">
              <div className="section-heading">
                <div className="section-icon"><History24Regular /></div>
                <div><h2 id="range-heading">1. 历史范围</h2><p>日期按当前设备时区 {model.timezone} 解释。</p></div>
              </div>
              <RadioGroup layout="horizontal" value={model.rangeMode} onChange={(_, data) => model.setRangeMode(data.value as RangeMode)}>
                <Radio value="all" label="全部历史记录" disabled={model.isJobActive} />
                <Radio value="custom" label="指定日期范围" disabled={model.isJobActive} />
              </RadioGroup>
              {model.rangeMode === "all" ? (
                <div className="date-grid history-bounds-grid">
                  <Field label="最早消息"><Input value={!model.selectedDialog ? "选择会话后读取" : model.dialogBoundsLoading ? "正在查询..." : formatMessageTime(model.dialogBounds?.earliest_message_at, model.timezone)} disabled /></Field>
                  <Field label="最晚消息"><Input value={!model.selectedDialog ? "选择会话后读取" : model.dialogBoundsLoading ? "正在查询..." : formatMessageTime(model.dialogBounds?.latest_message_at, model.timezone)} disabled /></Field>
                </div>
              ) : (
                <div className="date-grid">
                  <Field label="开始日期"><Input type="date" value={model.startDate} disabled={model.isJobActive} onChange={(_, data) => model.setStartDate(data.value)} /></Field>
                  <Field label="结束日期"><Input type="date" value={model.endDate} disabled={model.isJobActive} onChange={(_, data) => model.setEndDate(data.value)} /></Field>
                </div>
              )}
              {model.dialogBoundsError && <MessageBar intent="warning" className="range-message"><MessageBarBody>无法读取会话时间范围：{model.dialogBoundsError}</MessageBarBody></MessageBar>}
            </section>

            <section className="config-section" aria-labelledby="media-heading">
              <div className="section-heading">
                <div className="section-icon"><Image24Regular /></div>
                <div><h2 id="media-heading">2. 下载内容</h2><p>消息文本与元数据始终保存，下面只控制媒体下载。</p></div>
              </div>
              <div className="media-grid">
                {[
                  { key: "photo" as const, label: "图片", help: "Telegram 照片与原图", icon: <Image24Regular /> },
                  { key: "video" as const, label: "视频", help: "普通视频附件", icon: <Video24Regular /> },
                  { key: "file" as const, label: "普通文件", help: "文档和其他附件", icon: <Document24Regular /> },
                ].map((item) => {
                  const selected = model.selectedMedia.has(item.key);
                  return (
                    <button key={item.key} type="button" className={`media-option ${selected ? "is-selected" : ""}`} disabled={model.isJobActive} aria-pressed={selected} onClick={() => model.toggleMedia(item.key)}>
                      <span className="media-icon">{item.icon}</span><span className="media-copy"><strong>{item.label}</strong><small>{item.help}</small></span><span className={`option-check ${selected ? "is-checked" : ""}`}>{selected && <Checkmark24Regular />}</span>
                    </button>
                  );
                })}
              </div>
              <div className="size-limit-block size-limit-inline">
                <div className="size-limit-heading">
                  <div><strong>单个文件大小</strong><small>{model.maxFileSize == null ? "不限制单个文件大小；扫描后仍会检查总磁盘空间。" : "超过限制的媒体只记录元数据和跳过原因。"}</small></div>
                  <span>{formatSizeLimit(model.maxFileSize)}</span>
                </div>
                <Slider className="size-limit-slider" aria-label="单个文件大小限制" min={16} max={4096} step={16} value={model.maxFileSize ?? 4096} disabled={model.isJobActive} onChange={(_, data) => model.setMaxFileSize(data.value)} />
                <div className="size-presets" aria-label="常用大小限制">
                  {([100, 512, 1024, 4096, null] as const).map((value) => <button type="button" key={value ?? "unlimited"} disabled={model.isJobActive} className={model.maxFileSize === value ? "is-active" : ""} onClick={() => model.setMaxFileSize(value)}>{formatSizeLimit(value)}</button>)}
                </div>
              </div>
            </section>

            <section className="config-section" aria-labelledby="output-heading">
              <div className="section-heading">
                <div className="section-icon"><Folder24Regular /></div>
                <div><h2 id="output-heading">3. 保存位置</h2><p>扫描前会验证目录，完成后媒体按类型和年月整理。</p></div>
              </div>
              <Field label="本机导出目录" hint={model.outputRootId ? "目录已经通过写入验证。" : "输入绝对路径后验证目录。"}>
                <div className="path-input-row">
                  <Input contentBefore={<Folder24Regular />} value={model.outputPath} disabled={model.isJobActive} onChange={(_, data) => model.changeOutputPath(data.value)} />
                  <Button appearance="secondary" disabled={model.isJobActive || model.outputBusy} onClick={() => void model.registerOutputRoot()}>{model.outputBusy ? "正在验证" : "验证目录"}</Button>
                </div>
              </Field>
            </section>

          </div>
        </div>

        <footer className="export-actionbar">
          <div><ShieldLock24Regular /><span>所有数据只在本机处理。扫描不会下载媒体。</span></div>
          <Button
            appearance="primary"
            size="large"
            icon={<ArrowRight24Regular />}
            iconPosition="after"
            disabled={model.jobBusy || (!hasActiveJob && (!model.selectedDialog || model.selectedMedia.size === 0))}
            onClick={() => void primaryAction()}
          >
            {model.jobBusy ? "正在创建任务" : hasActiveJob ? "查看当前任务" : "扫描并预估"}
          </Button>
        </footer>
      </main>
    </div>
  );
}
