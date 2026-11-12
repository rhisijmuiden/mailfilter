import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Libraries change less often than our own code: separate files stay
        // cached in the browser across Mailfilter updates.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom|@remix-run)[\\/]/.test(id)) return "react";
          if (/[\\/]node_modules[\\/]lucide-react[\\/]/.test(id)) return "icons";
          if (/[\\/]node_modules[\\/](@tanstack|i18next|react-i18next)[\\/]/.test(id)) return "libs";
          return undefined;
        },
      },
    },
  },
});
