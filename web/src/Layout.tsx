import { ActionIcon, AppShell, Burger, Group, Progress, Text, Tooltip } from "@mantine/core";
import { useDisclosure, useLocalStorage, useMediaQuery } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { NavLink as RouterNavLink, Outlet, useLocation } from "react-router";
import { fetchLibraries, fetchSystemInfo, fetchTasks, fetchUnmatched } from "./api";
import { DataTypeIcon, IconLibraries, IconSettings, IconSidebar, IconTasks, IconUnmatched, Logo } from "./icons";

function NavItem({
  to,
  icon,
  label,
  end,
  extra,
  tip,
  dot,
  collapsed,
}: {
  to: string;
  icon: ReactNode;
  label: string;
  end?: boolean;
  extra?: ReactNode;
  tip?: string;
  dot?: boolean;
  collapsed: boolean;
}) {
  return (
    <Tooltip label={tip ?? label} position="right" withArrow disabled={!collapsed}>
      <RouterNavLink to={to} end={end} className="mo-nav" aria-label={collapsed ? (tip ?? label) : undefined}>
        <span className="mo-nav-icon">
          {icon}
          {collapsed && dot && <span className="mo-nav-dot" />}
        </span>
        {!collapsed && (
          <>
            <span className="mo-nav-label">{label}</span>
            {extra}
          </>
        )}
      </RouterNavLink>
    </Tooltip>
  );
}

function RunningTask() {
  const active = useQuery({
    queryKey: ["tasks", "active", 0],
    queryFn: () => fetchTasks("active"),
    refetchInterval: 3000,
  });
  const task = active.data?.items.find((row) => row.status === "running") ?? active.data?.items[0];
  if (task === undefined) return null;
  const done = (task.tree_status.succeeded ?? 0) + (task.tree_status.failed ?? 0);
  const total =
    task.progress?.total ??
    Object.values(task.tree_status).reduce((sum, count) => sum + (count ?? 0), 0);
  const current = task.progress?.current ?? done;
  return (
    <RouterNavLink to="/tasks" className="mo-running">
      <Group gap={8} wrap="nowrap" justify="space-between">
        <Group gap={8} wrap="nowrap" miw={0}>
          <span className="mo-running-dot" />
          <Text size="xs" fw={600} truncate>
            {task.type_label}「{task.title}」
          </Text>
        </Group>
        {total > 0 && (
          <Text size="xs" className="mo-mono" c="var(--mo-dim)">
            {current} / {total}
          </Text>
        )}
      </Group>
      <Progress
        value={total > 0 ? (current / total) * 100 : 100}
        animated={total === 0}
        size={4}
        mt={8}
      />
    </RouterNavLink>
  );
}

function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle?: () => void }) {
  const info = useQuery({ queryKey: ["system-info"], queryFn: fetchSystemInfo });
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const unmatched = useQuery({ queryKey: ["unmatched", null], queryFn: () => fetchUnmatched(null) });
  const active = useQuery({
    queryKey: ["tasks", "active", 0],
    queryFn: () => fetchTasks("active"),
    refetchInterval: 3000,
  });
  const unmatchedCount = unmatched.data?.length ?? 0;
  const activeCount = active.data?.total ?? 0;

  return (
    <div className="mo-side" data-collapsed={collapsed}>
      <div className="mo-brand">
        <Logo size={30} />
        {!collapsed && (
          <div>
            <Group gap={6}>
              <Text fw={700} fz={16}>
                莫提斯
              </Text>
              <span className="mo-brand-en">MORTIS</span>
            </Group>
            <Text fz={11} c="var(--mo-muted)">
              单人本地片库
            </Text>
          </div>
        )}
      </div>

      <nav className="mo-nav-group">
        <div className="mo-nav-title">媒体库</div>
        <NavItem to="/libraries" end icon={<IconLibraries />} label="全部媒体库" collapsed={collapsed} />
        {(libraries.data ?? []).map((library) => (
          <NavItem
            key={library.id}
            to={`/libraries/${library.id}`}
            icon={<DataTypeIcon dataTypeId={library.data_type_id} />}
            label={library.name}
            extra={<span className="mo-nav-count mo-mono">{library.item_count}</span>}
            collapsed={collapsed}
          />
        ))}
      </nav>

      <nav className="mo-nav-group">
        <div className="mo-nav-title">整理</div>
        <NavItem
          to="/unmatched"
          icon={<IconUnmatched />}
          label="未识别"
          tip={unmatchedCount > 0 ? `未识别（${unmatchedCount}）` : undefined}
          extra={unmatchedCount > 0 && <span className="mo-nav-badge mo-mono">{unmatchedCount}</span>}
          dot={unmatchedCount > 0}
          collapsed={collapsed}
        />
        <NavItem
          to="/tasks"
          icon={<IconTasks />}
          label="任务"
          tip={activeCount > 0 ? `任务（${activeCount} 进行中）` : undefined}
          extra={activeCount > 0 && <span className="mo-nav-live">{activeCount} 进行中</span>}
          dot={activeCount > 0}
          collapsed={collapsed}
        />
        <NavItem to="/settings" icon={<IconSettings />} label="设置" collapsed={collapsed} />
      </nav>

      <div className="mo-side-foot">
        {!collapsed && <RunningTask />}
        <Group justify={collapsed ? "center" : "space-between"} mt={collapsed ? 0 : "sm"}>
          {!collapsed && (
            <Text fz={11} className="mo-mono" c="var(--mo-muted)">
              {info.isSuccess && `v${info.data.version}`}
              {info.isError && "连不上后端"}
            </Text>
          )}
          {onToggle !== undefined && (
            <Tooltip label={collapsed ? "展开侧栏" : "收起侧栏"} position="right" withArrow>
              <ActionIcon
                variant="subtle"
                color="gray"
                onClick={onToggle}
                aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              >
                <IconSidebar size={16} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </div>
    </div>
  );
}

export function Layout() {
  const mobile = useMediaQuery("(max-width: 62em)");
  const [opened, { toggle, close }] = useDisclosure(false);
  const [sideCollapsed, setSideCollapsed] = useLocalStorage({
    key: "mortis-sidebar-collapsed",
    defaultValue: false,
  });
  const collapsed = !mobile && sideCollapsed;
  const { pathname } = useLocation();
  useEffect(() => close(), [pathname, close]);

  return (
    <AppShell
      header={{ height: 52, collapsed: !mobile }}
      navbar={{ width: collapsed ? 68 : 240, breakpoint: "62em", collapsed: { mobile: !opened } }}
      padding={0}
    >
      <AppShell.Header className="mo-topbar">
        <Group h="100%" px="md" gap="sm">
          <Burger opened={opened} onClick={toggle} size="sm" aria-label="打开导航" />
          <Logo size={24} />
          <Text fw={700}>莫提斯</Text>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar className="mo-navbar">
        <Sidebar
          collapsed={collapsed}
          onToggle={mobile ? undefined : () => setSideCollapsed((value) => !value)}
        />
      </AppShell.Navbar>
      <AppShell.Main className="mo-main">
        <div className="mo-page">
          <Outlet />
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
