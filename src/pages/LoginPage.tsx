import { Badge, Button, Field, Input, MessageBar, MessageBarBody } from "@fluentui/react-components";
import { Key24Regular, Phone24Regular, Settings24Regular, ShieldLock24Regular } from "@fluentui/react-icons";
import { useNavigate } from "react-router-dom";
import { useArchiveDesk } from "../app/model";

export function LoginPage() {
  const model = useArchiveDesk();
  const navigate = useNavigate();
  const status = model.authFlow?.status;
  const isPhoneStep = !model.authFlow;
  const isCodeStep = status === "code_required";
  const isPasswordStep = status === "password_required";

  const submit = async () => {
    const authorized = isPhoneStep
      ? await model.beginAuthorization()
      : isCodeStep
        ? await model.verifyCode()
        : await model.verifyPassword();
    if (authorized) navigate("/export", { replace: true });
  };

  const editCredentials = async () => {
    await model.prepareCredentialEditor();
    navigate("/setup");
  };

  return (
    <div className="onboarding-shell">
      <section className="onboarding-card" aria-labelledby="login-heading">
        <div className="onboarding-icon"><Phone24Regular /></div>
        <Badge appearance="tint" color="brand">账号认证</Badge>
        <h1 id="login-heading">
          {isPhoneStep ? "登录 Telegram" : isCodeStep ? "输入验证码" : "输入两步验证密码"}
        </h1>
        <p>
          {isPhoneStep
            ? "使用包含国家或地区代码的手机号，例如 +86 13800000000。"
            : isCodeStep
              ? `验证码已发送到 ${model.authFlow?.phone_masked ?? "你的 Telegram"}。`
              : "这个账号启用了两步验证，请输入云密码继续。"}
        </p>
        {model.authError && (
          <MessageBar intent="error"><MessageBarBody>{model.authError}</MessageBarBody></MessageBar>
        )}
        <div className="onboarding-form">
          {isPhoneStep && (
            <Field label="手机号">
              <Input
                type="tel"
                autoComplete="tel"
                contentBefore={<Phone24Regular />}
                value={model.phone}
                disabled={model.authBusy}
                onChange={(_, data) => model.setPhone(data.value)}
                onKeyDown={(event) => event.key === "Enter" && void submit()}
              />
            </Field>
          )}
          {isCodeStep && (
            <Field label="验证码" hint="验证码不会保存到浏览器或后端数据库。">
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={model.code}
                disabled={model.authBusy}
                onChange={(_, data) => model.setCode(data.value)}
                onKeyDown={(event) => event.key === "Enter" && void submit()}
              />
            </Field>
          )}
          {isPasswordStep && (
            <Field label="两步验证密码">
              <Input
                type="password"
                autoComplete="current-password"
                contentBefore={<Key24Regular />}
                value={model.password}
                disabled={model.authBusy}
                onChange={(_, data) => model.setPassword(data.value)}
                onKeyDown={(event) => event.key === "Enter" && void submit()}
              />
            </Field>
          )}
          <div className="onboarding-actions">
            <Button appearance="primary" disabled={model.authBusy} onClick={() => void submit()}>
              {model.authBusy ? "正在验证" : isPhoneStep ? "发送验证码" : "验证并登录"}
            </Button>
            {!isPhoneStep && (
              <Button appearance="secondary" disabled={model.authBusy} onClick={() => void model.cancelAuthorization()}>
                更换手机号
              </Button>
            )}
            {isCodeStep && (
              <Button appearance="subtle" disabled={model.authBusy} onClick={() => void model.resendAuthCode()}>
                重新发送验证码
              </Button>
            )}
            <Button
              appearance="secondary"
              icon={<Settings24Regular />}
              disabled={model.authBusy}
              onClick={() => void editCredentials()}
            >
              修改 API ID / API Hash
            </Button>
            {model.accounts.length > 0 && (
              <Button appearance="subtle" disabled={model.authBusy} onClick={() => navigate("/export")}>
                返回导出
              </Button>
            )}
          </div>
        </div>
        <div className="security-callout">
          <ShieldLock24Regular />
          <span>授权 Session 由本机后端保存，浏览器不会接收 Session 内容。</span>
        </div>
      </section>
    </div>
  );
}
