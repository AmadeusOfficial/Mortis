import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { router } from "./router";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("页面缺少 #root 节点");
}

// 本机后端要么在、要么没起，多次重试只会让「连不上」的提示来得更晚。
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });

createRoot(container).render(
  <StrictMode>
    <MantineProvider defaultColorScheme="auto">
      <Notifications />
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
