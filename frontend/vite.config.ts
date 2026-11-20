import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "");
  return {
    plugins: [vue()],
    server: {
      proxy: {
        "/api": {
          target: environment.GITFERRY_API_TARGET ?? "http://127.0.0.1:8080",
          changeOrigin: false,
        },
      },
    },
  };
});
