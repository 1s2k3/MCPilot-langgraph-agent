import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // 开发模式下后端 API（SSE 由 vite 代理转发，注意关闭缓冲即可）
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
