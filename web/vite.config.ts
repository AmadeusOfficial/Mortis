import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiPort = process.env.MORTIS_PORT ?? "8100";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
    },
  },
});
