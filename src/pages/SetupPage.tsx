import { Badge, Button, Field, Input, MessageBar, MessageBarBody } from "@fluentui/react-components";
import { Key24Regular, ShieldLock24Regular } from "@fluentui/react-icons";
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useArchiveDesk } from "../app/model";

export function SetupPage() {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const prepared = useRef(false);

  useEffect(() => {
    if (!model.credentialsConfigured || prepared.current) return;
    prepared.current = true;
    void model.prepareCredentialEditor();
  }, [model.credentialsConfigured, model.prepareCredentialEditor]);

  const save = async () => {
    if (!await model.saveCredentials()) return;
    navigate(model.accounts.length > 0 ? "/settings" : "/login", { replace: true });
  };

  return (
    <div className="onboarding-shell">
      <section className="onboarding-card" aria-labelledby="credentials-heading">
        <div className="onboarding-icon"><Key24Regular /></div>
        <Badge appearance="tint" color="brand">本机配置</Badge>
        <h1 id="credentials-heading">连接 Telegram API</h1>
        <p>
          填写你在 Telegram 开发者页面申请的 API ID 和 API Hash。API ID 会回填，API Hash
          为了安全必须重新输入。
        </p>
        {model.credentialsError && (
          <MessageBar intent="error"><MessageBarBody>{model.credentialsError}</MessageBarBody></MessageBar>
        )}
        <div className="onboarding-form">
          <Field label="API ID" hint="仅接受正整数。">
            <Input
              inputMode="numeric"
              value={model.apiId}
              disabled={model.credentialsBusy}
              onChange={(_, data) => model.setApiId(data.value.replace(/[^0-9]/g, ""))}
            />
          </Field>
          <Field label="API Hash" hint="保存后不会在页面中显示完整值。">
            <Input
              type="password"
              autoComplete="off"
              value={model.apiHash}
              disabled={model.credentialsBusy}
              onChange={(_, data) => model.setApiHash(data.value)}
              onKeyDown={(event) => event.key === "Enter" && void save()}
            />
          </Field>
          <a className="external-help-link" href="https://my.telegram.org/apps" target="_blank" rel="noreferrer">
            打开 Telegram API 申请页面
          </a>
          <div className="onboarding-actions">
            <Button appearance="primary" disabled={model.credentialsBusy} onClick={() => void save()}>
              {model.credentialsBusy ? "正在保存" : "保存并继续"}
            </Button>
            {model.accounts.length > 0 && (
              <Button appearance="secondary" disabled={model.credentialsBusy} onClick={() => navigate("/settings")}>
                返回设置
              </Button>
            )}
          </div>
        </div>
        <div className="security-callout">
          <ShieldLock24Regular />
          <span>不要把 API Hash、Session 文件或两步验证密码放入导出目录。</span>
        </div>
      </section>
    </div>
  );
}
