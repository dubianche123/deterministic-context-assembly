import { bindings, defineConfig, exports as workerExports } from "cf/config";

export default defineConfig({
  worker: {
    name: "norn-machine",
    entrypoint: "./.generated/worker/entry.py",
    compatibilityDate: "2026-10-20",
    compatibilityFlags: ["python_workers", "python_workers_314", "disable_python_external_sdk"],
    exports: {
      NornSession: workerExports.durableObject({ storage: "sqlite" }),
      NornBudget: workerExports.durableObject({ storage: "sqlite" }),
      NornArchive: workerExports.durableObject({ storage: "sqlite" }),
    },
    env: {
      ASSETS: bindings.assets(),
      NORN_SESSION: bindings.durableObject({ worker: "norn-machine", exportName: "NornSession" }),
      NORN_BUDGET: bindings.durableObject({ worker: "norn-machine", exportName: "NornBudget" }),
      NORN_ARCHIVE: bindings.durableObject({ worker: "norn-machine", exportName: "NornArchive" }),
      NORN_PROVIDER: bindings.text("openai-compatible"),
      NORN_DECK: bindings.text("pamela-v1"),
      NORN_API_KEY: bindings.secret(),
      NORN_JEV_API_KEY: bindings.secret(),
      // Operator-only bearer token for GET /api/archive/export.
      NORN_ARCHIVE_TOKEN: bindings.secret(),
    },
    assets: { htmlHandling: "auto-trailing-slash", notFoundHandling: "none", runWorkerFirst: ["/api/*"] },
    observability: {
      enabled: true,
      logs: { enabled: true, headSamplingRate: 1 },
      traces: { enabled: true, headSamplingRate: 0.01 },
    },
  },
});
