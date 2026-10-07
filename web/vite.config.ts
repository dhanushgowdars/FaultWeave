import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("recharts") || id.includes("d3-")) return "charts";
          if (id.includes("@tanstack")) return "query";
          if (
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-dom/") ||
            id.includes("node_modules/react-router")
          ) {
            return "react";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:18110",
        changeOrigin: true,
      },
      "/health": {
        target: "http://localhost:18110",
        changeOrigin: true,
      },
      "/demo-control": {
        target: "http://127.0.0.1:18116",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/demo-control/, ""),
      },
    },
  },
});
