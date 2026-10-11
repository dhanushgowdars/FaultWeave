import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.FAULTWEAVE_GATEWAY_URL ?? "http://localhost:18110",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.FAULTWEAVE_GATEWAY_URL ?? "http://localhost:18110",
        changeOrigin: true,
      },
    },
  },
});
