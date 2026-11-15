import { AppShell, Group, NavLink, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Outlet, NavLink as RouterNavLink, useLocation } from "react-router";
import { fetchSystemInfo } from "./api";

export const NAV_ITEMS: { to: string; label: string }[] = [
  { to: "/libraries", label: "媒体库" },
  { to: "/settings", label: "设置" },
];

export function Layout() {
  const info = useQuery({ queryKey: ["system-info"], queryFn: fetchSystemInfo });
  const { pathname } = useLocation();

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 200, breakpoint: "sm" }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={3}>莫提斯</Title>
          <Text size="sm" c="dimmed">
            {info.isSuccess && `版本 ${info.data.version}`}
            {info.isError && "连不上后端"}
          </Text>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            component={RouterNavLink}
            to={item.to}
            label={item.label}
            active={pathname.startsWith(item.to)}
          />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
