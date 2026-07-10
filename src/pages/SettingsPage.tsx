import { Badge, Button } from "@fluentui/react-components";
import { Folder24Regular, Key24Regular, PersonAdd24Regular, Person24Regular, ShieldLock24Regular } from "@fluentui/react-icons";
import { useNavigate } from "react-router-dom";
import { useArchiveDesk } from "../app/model";

export function SettingsPage() {
  const model = useArchiveDesk();
  const navigate = useNavigate();

  const editCredentials = async () => {
    await model.prepareCredentialEditor();
    navigate("/setup");
  };

  return (
    <main className="settings-page">
      <div className="settings-heading">
        <div>
          <Badge appearance="tint" color="brand">{model.containerMode ? "容器设置" : "本机设置"}</Badge>
          <h1>账号与应用设置</h1>
          <p>管理 Telegram API 凭据、当前账号和已经验证的{model.containerMode ? "容器挂载" : "本地"}目录。</p>
        </div>
        <Button appearance="primary" onClick={() => navigate("/export")}>返回导出</Button>
      </div>

      <section className="settings-card">
        <div className="settings-card-icon"><Key24Regular /></div>
        <div className="settings-card-copy">
            <h2>Telegram API 凭据</h2>
          <p>API Hash 只保存在{model.containerMode ? "Docker 数据卷" : "本机后端"}，页面不会读取或显示现有值。</p>
          <Badge appearance="outline" color={model.credentialsConfigured ? "success" : "danger"}>
            {model.credentialsConfigured ? "已配置" : "未配置"}
          </Badge>
        </div>
        <Button appearance="secondary" onClick={() => void editCredentials()}>修改凭据</Button>
      </section>

      <section className="settings-card settings-card-stack">
        <div className="settings-card-header">
          <div className="settings-card-icon"><Person24Regular /></div>
          <div className="settings-card-copy">
            <h2>已授权账号</h2>
            <p>选择导出时使用的 Telegram 账号，或继续添加另一个账号。</p>
          </div>
          <Button appearance="secondary" icon={<PersonAdd24Regular />} onClick={() => navigate("/login")}>添加账号</Button>
        </div>
        <div className="account-settings-list">
          {model.accounts.map((account) => {
            const selected = account.id === model.selectedAccountId;
            return (
              <button
                type="button"
                key={account.id}
                className={`account-settings-row ${selected ? "is-selected" : ""}`}
                aria-pressed={selected}
                onClick={() => model.setSelectedAccountId(account.id)}
              >
                <span className="account-settings-avatar">{account.display_name.slice(0, 1).toUpperCase()}</span>
                <span>
                  <strong>{account.display_name}</strong>
                  <small>{account.username ? `@${account.username}` : account.phone_masked ?? "Telegram 账号"}</small>
                </span>
                <Badge appearance={selected ? "filled" : "outline"} color={selected ? "brand" : "informative"}>
                  {selected ? "当前账号" : "切换"}
                </Badge>
              </button>
            );
          })}
        </div>
      </section>

      <section className="settings-card settings-card-stack">
        <div className="settings-card-header">
          <div className="settings-card-icon"><Folder24Regular /></div>
          <div className="settings-card-copy">
            <h2>已验证的输出目录</h2>
            <p>这些目录曾由{model.containerMode ? "容器后端" : "本地后端"}验证为可写。</p>
          </div>
        </div>
        <div className="output-root-list">
          {model.outputRoots.length > 0
            ? model.outputRoots.map((root) => <code key={root.id}>{root.path}</code>)
            : <span>还没有验证过输出目录。</span>}
        </div>
      </section>

      <div className="settings-security-note"><ShieldLock24Regular /><span>账号 Session 和 API Hash 不会进入浏览器存储{model.containerMode ? "，请保护 Docker 数据卷" : ""}。</span></div>
    </main>
  );
}
