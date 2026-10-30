import { defineConfig, mergeConfig } from "vite";
import base from "../vite.config.ts";

export default mergeConfig(
  base,
  defineConfig({
    server: {
      port: 15173,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:18765",
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }),
);
