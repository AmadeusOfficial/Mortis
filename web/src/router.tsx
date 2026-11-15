import { Text } from "@mantine/core";
import { Navigate, createBrowserRouter } from "react-router";
import { Layout } from "./Layout";
import { LibrariesPage } from "./pages/LibrariesPage";
import { SettingsPage } from "./pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/libraries" replace /> },
      { path: "libraries", element: <LibrariesPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <Text c="dimmed">没有这个页面。</Text> },
    ],
  },
]);
