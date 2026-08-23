import type { LucideIcon } from "lucide-react";
import {
  Command as CommandIcon,
  Moon,
  PanelLeft,
  RefreshCw,
  Sun,
} from "lucide-react";
import { useId, type ReactNode } from "react";

import type { Snapshot } from "../../types";
import type { TabKey } from "../../lib/uiTypes";
import type { ThemeMode } from "../../lib/useTheme";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
} from "../ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";

export type ShellNavItem = {
  key: TabKey;
  label: string;
  icon: LucideIcon;
};

export function AppShell({
  baseUrl,
  children,
  error,
  loading,
  navItems,
  onCommand,
  onNavigate,
  onNavigateIntent,
  onRefresh,
  onToggleTheme,
  snapshot,
  tab,
  theme,
  unseenCount,
}: {
  baseUrl: string;
  children: ReactNode;
  error: string | null;
  loading: boolean;
  navItems: ShellNavItem[];
  onCommand: () => void;
  onNavigate: (key: TabKey) => void;
  onNavigateIntent?: (key: TabKey) => void;
  onRefresh: () => void;
  onToggleTheme: () => void;
  snapshot: Snapshot | null;
  tab: TabKey;
  theme: ThemeMode;
  unseenCount: number;
}) {
  return (
    <TooltipProvider delayDuration={150}>
      {/* The painted depth atmosphere: a fixed layer behind the content plane
          that the glass sidebar/panels bleed through their blur. See
          .alfred-app-atmosphere in index.css. */}
      <div className="alfred-app-atmosphere" aria-hidden="true" />
      <SidebarProvider className="relative z-[1]">
        <Sidebar
          collapsible="icon"
          variant="sidebar"
          className="alfred-glass-shell border-sidebar-border/70"
        >
          <div
            className="hidden h-3 shrink-0 md:block"
            data-tauri-drag-region
          />
          <SidebarHeader className="gap-3 px-3 py-4">
            <SidebarBrandButton onNavigate={onNavigate} />
          </SidebarHeader>

          <SidebarContent>
            <SidebarGroup>
              <SidebarMenu>
                {navItems.map((item) => {
                  const active =
                    item.key === "fleet" ? tab === "fleet" : tab === item.key;
                  const badge =
                    item.key === "fleet" && unseenCount > 0
                      ? unseenCount > 9
                        ? "9+"
                        : String(unseenCount)
                      : null;
                  return (
                    <ShellNavMenuItem
                      key={item.key}
                      active={active}
                      badge={badge}
                      item={item}
                      onNavigate={onNavigate}
                      onNavigateIntent={onNavigateIntent}
                      unseenCount={unseenCount}
                    />
                  );
                })}
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>

          <SidebarFooter className="gap-3 border-t border-sidebar-border/50 p-3">
            <div
              className="alfred-fleet-status group-data-[collapsible=icon]:hidden"
              title={baseUrl}
            >
              <span className="alfred-fleet-status__label">Local fleet</span>
              <FleetStatus snapshot={snapshot} error={error} />
            </div>
            <SidebarSeparator />
            <div className="grid grid-cols-3 gap-1 group-data-[collapsible=icon]:grid-cols-1">
              <ShellIconButton
                label={error ? "Reconnect" : "Refresh"}
                onClick={onRefresh}
                disabled={loading}
              >
                <RefreshCw
                  aria-hidden="true"
                  className={loading ? "animate-spin" : undefined}
                />
              </ShellIconButton>
              <ShellIconButton label="Commands" onClick={onCommand}>
                <CommandIcon aria-hidden="true" />
              </ShellIconButton>
              <ShellIconButton
                label={theme === "dark" ? "Light theme" : "Dark theme"}
                onClick={onToggleTheme}
              >
                {theme === "dark" ? (
                  <Sun aria-hidden="true" />
                ) : (
                  <Moon aria-hidden="true" />
                )}
              </ShellIconButton>
            </div>
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>

        <SidebarInset className="h-svh overflow-hidden bg-transparent">
          <div className="flex h-full min-w-0 flex-col">
            <header className="alfred-glass flex h-14 shrink-0 items-center gap-2.5 rounded-none border-x-0 border-t-0 px-4 md:hidden">
              <SidebarTrigger>
                <PanelLeft aria-hidden="true" />
              </SidebarTrigger>
              <AlfredBrandMark className="size-7 shrink-0" />
              <span className="font-heading text-sm font-medium">Alfred</span>
              <div className="ml-auto">
                <FleetStatus snapshot={snapshot} error={error} compact />
              </div>
            </header>
            <div
              className="min-h-0 flex-1 overflow-auto px-4 py-5 sm:px-6 lg:px-6"
              data-alfred-scroll-region
            >
              {children}
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}

function SidebarBrandButton({
  onNavigate,
}: {
  onNavigate: (key: TabKey) => void;
}) {
  const { isMobile, setOpenMobile } = useSidebar();
  const navigateHome = () => {
    onNavigate("home");
    if (isMobile) setOpenMobile(false);
  };

  return (
    <button
      className="group-data-[collapsible=icon]:justify-center flex h-12 min-w-0 items-center gap-3 rounded-lg px-2 text-left transition hover:bg-sidebar-accent/45 hover:text-sidebar-accent-foreground"
      type="button"
      onClick={navigateHome}
      aria-label="Open Alfred inbox"
    >
      <AlfredBrandMark className="size-9 shrink-0" />
      <span className="min-w-0 group-data-[collapsible=icon]:hidden">
        <span className="block truncate font-heading text-base font-semibold">
          Alfred
        </span>
      </span>
    </button>
  );
}

function AlfredBrandMark({ className }: { className: string }) {
  const gradientId = useId().replace(/:/g, "");
  return (
    <span className={`alfred-brand-mark ${className}`} aria-hidden="true">
      <svg
        className="alfred-brand-mark__prism"
        viewBox="0 0 36 36"
        fill="none"
      >
        <defs>
          <linearGradient id={gradientId} x1="5" y1="29" x2="31" y2="7">
            <stop stopColor="var(--signal-mint)" />
            <stop offset="0.5" stopColor="var(--signal-rose)" />
            <stop offset="1" stopColor="var(--signal-violet)" />
          </linearGradient>
        </defs>
        <path
          d="M11.7 28.1h14.8a6.3 6.3 0 0 0 1.2-12.5A9.3 9.3 0 0 0 9.8 13a7.6 7.6 0 0 0 1.9 15.1Z"
          stroke={`url(#${gradientId})`}
          strokeWidth="1.35"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <svg
        className="alfred-brand-mark__ledger"
        viewBox="0 0 36 36"
        fill="none"
      >
        <circle cx="18" cy="18" r="7" />
        <path d="M18 4v8M18 24v8M4 18h8M24 18h8" />
      </svg>
    </span>
  );
}

function ShellNavMenuItem({
  active,
  badge,
  item,
  onNavigate,
  onNavigateIntent,
  unseenCount,
}: {
  active: boolean;
  badge: string | null;
  item: ShellNavItem;
  onNavigate: (key: TabKey) => void;
  onNavigateIntent?: (key: TabKey) => void;
  unseenCount: number;
}) {
  const { isMobile, setOpenMobile } = useSidebar();
  const Icon = item.icon;
  const navigate = () => {
    onNavigate(item.key);
    if (isMobile) setOpenMobile(false);
  };

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={active}
        tooltip={item.label}
        onClick={navigate}
        onMouseEnter={() => onNavigateIntent?.(item.key)}
        onFocus={() => onNavigateIntent?.(item.key)}
        className={`${active ? "nav-item-active" : ""} transition-transform duration-150 hover:translate-x-0.5 data-active:translate-x-0.5`}
      >
        <Icon aria-hidden="true" />
        <span>{item.label}</span>
      </SidebarMenuButton>
      {badge ? (
        <SidebarMenuBadge aria-label={`${unseenCount} unread`}>
          {badge}
        </SidebarMenuBadge>
      ) : null}
    </SidebarMenuItem>
  );
}

function ShellIconButton({
  children,
  disabled,
  label,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          disabled={disabled}
          onClick={onClick}
          aria-label={label}
          className="size-8"
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="top">{label}</TooltipContent>
    </Tooltip>
  );
}

function FleetStatus({
  compact,
  error,
  snapshot,
}: {
  compact?: boolean;
  error: string | null;
  snapshot: Snapshot | null;
}) {
  const offline = Boolean(error);
  const health = snapshot?.status.reliability.status || "checking";
  const text = offline
    ? "Offline"
    : health === "ok"
      ? "Live"
      : health === "checking"
        ? "Checking"
        : "Needs attention";
  const title = offline
    ? "Alfred serve offline"
    : health === "ok"
      ? "Agents live"
      : health === "checking"
        ? "Checking agent status"
        : "Agents need attention";
  const variant = offline
    ? "destructive"
    : health === "ok"
      ? "secondary"
      : "outline";
  const dot = offline
    ? "bg-destructive"
    : health === "ok"
      ? "bg-primary"
      : health === "checking"
        ? "bg-muted-foreground"
        : "bg-[var(--warn)]";
  return (
    <Badge
      variant={variant}
      className={compact ? "h-6 gap-1.5 px-2" : "h-7 gap-1.5 px-2"}
      title={title}
    >
      <span className={`size-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {text}
    </Badge>
  );
}
