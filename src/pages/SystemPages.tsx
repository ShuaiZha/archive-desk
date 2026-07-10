import { Button, MessageBar, MessageBarBody } from "@fluentui/react-components";
import { Archive24Regular, ArrowClockwise24Regular, Dismiss24Regular } from "@fluentui/react-icons";
import { useArchiveDesk } from "../app/model";

export function LoadingPage() {
  return (
    <div className="onboarding-shell">
      <div className="loading-card" aria-live="polite">
        <div className="brand-mark"><Archive24Regular /></div>
        <h1>正在连接本地服务</h1>
        <p>正在读取凭据、账号和导出目录状态。</p>
        <div className="loading-lines" aria-hidden="true"><span /><span /><span /></div>
      </div>
    </div>
  );
}

export function BackendErrorPage() {
  const { bootError, loadBootstrap } = useArchiveDesk();
  return (
    <div className="onboarding-shell">
      <section className="onboarding-card" aria-labelledby="backend-error-heading">
        <div className="onboarding-icon error"><Dismiss24Regular /></div>
        <h1 id="backend-error-heading">本地服务未就绪</h1>
        <p>页面不会回退到演示数据。连接后端成功后才会显示账号、会话和任务。</p>
        <MessageBar intent="error"><MessageBarBody>{bootError}</MessageBarBody></MessageBar>
        <Button appearance="primary" icon={<ArrowClockwise24Regular />} onClick={() => void loadBootstrap()}>
          重新连接
        </Button>
        <code className="endpoint-hint">GET /api/v1/bootstrap</code>
      </section>
    </div>
  );
}
