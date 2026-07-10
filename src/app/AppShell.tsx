import { Button, Tooltip } from "@fluentui/react-components";
import {
  Archive24Regular,
  Navigation24Regular,
  WeatherMoon24Regular,
  WeatherSunny24Regular,
} from "@fluentui/react-icons";
import type { PropsWithChildren } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArchiveDeskProvider, type ArchiveDeskModel } from "./model";

type AppShellProps = PropsWithChildren<{ model: ArchiveDeskModel }>;

export function AppShell({ model, children }: AppShellProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const showWorkspaceMenu = location.pathname === "/export" && model.accounts.length > 0;

  return (
    <ArchiveDeskProvider value={model}>
      <div className="app-shell">
        <header className="topbar">
          <div className="topbar-left">
            <div className="brand-group">
              {showWorkspaceMenu && (
                <Tooltip content="打开会话列表" relationship="label">
                  <Button
                    className="mobile-menu-button"
                    appearance="subtle"
                    icon={<Navigation24Regular />}
                    aria-label="打开会话列表"
                    onClick={() => model.setSidebarOpen(true)}
                  />
                </Tooltip>
              )}
              <button className="brand-home" type="button" onClick={() => navigate("/")} aria-label="返回首页">
                <span className="brand-mark" aria-hidden="true"><Archive24Regular /></span>
                <span>
                  <span className="brand-name">Archive Desk</span>
                  <span className="brand-subtitle">本地 Telegram 历史导出</span>
                </span>
              </button>
            </div>
            {model.accounts.length > 0 && (
              <nav className="topbar-nav" aria-label="主导航">
                <button className={location.pathname === "/export" ? "is-active" : ""} type="button" onClick={() => navigate("/export")}>新建任务</button>
                <button className={location.pathname.startsWith("/jobs") ? "is-active" : ""} type="button" onClick={() => navigate("/jobs")}>任务</button>
                <button className={location.pathname === "/settings" ? "is-active" : ""} type="button" onClick={() => navigate("/settings")}>设置</button>
              </nav>
            )}
          </div>

          <div className="topbar-actions">
            {model.selectedAccount && (
              <button className="account-state account-state-button" type="button" onClick={() => navigate("/settings")}>
                <span className="status-dot" />
                <span className="account-copy">
                  <strong>
                    {model.selectedAccount.username
                      ? `@${model.selectedAccount.username}`
                      : model.selectedAccount.display_name}
                  </strong>
                  <small>{model.selectedAccount.phone_masked ?? "Telegram 账号已连接"}</small>
                </span>
              </button>
            )}
            <Tooltip content={model.darkMode ? "切换到浅色模式" : "切换到深色模式"} relationship="label">
              <Button
                className="theme-toggle-button"
                appearance="subtle"
                icon={model.darkMode ? <WeatherSunny24Regular /> : <WeatherMoon24Regular />}
                aria-label={model.darkMode ? "切换到浅色模式" : "切换到深色模式"}
                onClick={() => model.setDarkMode((value) => !value)}
              />
            </Tooltip>
          </div>
        </header>
        {children}
      </div>
    </ArchiveDeskProvider>
  );
}
