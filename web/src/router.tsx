import { Text } from "@mantine/core";
import { Navigate, createBrowserRouter } from "react-router";
import { Layout } from "./Layout";
import { ItemDetailPage } from "./pages/ItemDetailPage";
import { LibrariesPage } from "./pages/LibrariesPage";
import { LibraryWallPage } from "./pages/LibraryWallPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TasksPage } from "./pages/TasksPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/libraries" replace /> },
      { path: "libraries", element: <LibrariesPage /> },
      { path: "libraries/:libraryId", element: <LibraryWallPage /> },
      { path: "items/:itemId", element: <ItemDetailPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <Text c="dimmed">没有这个页面。</Text> },
    ],
  },
]);
