import { defineWranglerConfig } from "wrangler/experimental-config";

// cf remains the project CLI; Wrangler is its Python-module build implementation.
export default defineWranglerConfig({
  assetsDirectory: "./.generated/assets",
  dev: { port: 8788 },
});
