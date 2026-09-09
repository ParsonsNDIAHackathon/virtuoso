import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Dev proxy target for /api. Override with VITE_API_TARGET (e.g. in frontend/.env.local) when the
// engine runs on another port; the containers use nginx.conf instead of this proxy.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    define: { __BUILD__: JSON.stringify(process.env.VITE_BUILD || env.VITE_BUILD || "dev") },
    plugins: [react()],
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: {
      proxy: { "/api": env.VITE_API_TARGET || "http://localhost:8000" },
    },
  };
});
