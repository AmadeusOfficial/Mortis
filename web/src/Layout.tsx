import { AppShell, Burger, Group, Progress, SegmentedControl, Text, useMantineColorScheme } from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { NavLink as RouterNavLink, Outlet, useLocation } from "react-router";
import { fetchLibraries, fetchSystemInfo, fetchTasks, fetchUnmatched } from "./api";
import { DataTypeIcon, IconLibraries, IconSettings, IconTasks, IconUnmatched, Logo } from "./icons";

function NavItem({
  to,
  icon,
  label,
  end,
  extra,
}: {
  to: string;
  icon: ReactNode;
  label: string;
  end?: boolean;
  extra?: ReactNode;
}) {
  return (
    <RouterNavLink to={to} end={end} className="mo-nav">
      {icon}
      <span className="mo-nav-label">{label}</span>
      {extra}
    </RouterNavLink>
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

function SchemeSwitch() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  return (
    <SegmentedControl
      size="xs"
      value={colorScheme === "light" ? "light" : "dark"}
      onChange={(value) => setColorScheme(value as "light" | "dark")}
      data={[
        { value: "dark", label: "深色" },
        { value: "light", label: "浅色" },
      ]}
    />
  );
}

function Sidebar() {
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
    <div className="mo-side">
      <div className="mo-brand">
        <Logo size={30} />
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
      </div>

      <nav className="mo-nav-group">
        <div className="mo-nav-title">媒体库</div>
        <NavItem to="/libraries" end icon={<IconLibraries />} label="全部媒体库" />
        {(libraries.data ?? []).map((library) => (
          <NavItem
            key={library.id}
            to={`/libraries/${library.id}`}
            icon={<DataTypeIcon dataTypeId={library.data_type_id} />}
            label={library.name}
            extra={<span className="mo-nav-count mo-mono">{library.item_count}</span>}
          />
        ))}
      </nav>

      <nav className="mo-nav-group">
        <div className="mo-nav-title">整理</div>
        <NavItem
          to="/unmatched"
          icon={<IconUnmatched />}
          label="未识别"
          extra={unmatchedCount > 0 && <span className="mo-nav-badge mo-mono">{unmatchedCount}</span>}
        />
        <NavItem
          to="/tasks"
          icon={<IconTasks />}
          label="任务"
          extra={activeCount > 0 && <span className="mo-nav-live">{activeCount} 进行中</span>}
        />
        <NavItem to="/settings" icon={<IconSettings />} label="设置" />
      </nav>

      <div className="mo-side-foot">
        <RunningTask />
        <Group justify="space-between" mt="sm">
          <Text fz={11} className="mo-mono" c="var(--mo-muted)">
            {info.isSuccess && `v${info.data.version}`}
            {info.isError && "连不上后端"}
          </Text>
          <SchemeSwitch />
        </Group>
      </div>
    </div>
  );
}

export function Layout() {
  const mobile = useMediaQuery("(max-width: 62em)");
  const [opened, { toggle, close }] = useDisclosure(false);
  const { pathname } = useLocation();
  useEffect(() => close(), [pathname, close]);

  return (
    <AppShell
      header={{ height: 52, collapsed: !mobile }}
      navbar={{ width: 240, breakpoint: "62em", collapsed: { mobile: !opened } }}
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
        <Sidebar />
      </AppShell.Navbar>
      <AppShell.Main className="mo-main">
        <div className="mo-page">
          <Outlet />
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
