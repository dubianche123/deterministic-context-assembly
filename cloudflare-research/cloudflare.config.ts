import { bindings, defineConfig, exports as workerExports } from "cf/config";

// The research deployment: Norn's Worker code serving the PAM∃LA research deck
// (engine research_deck), with its own sessions, budget and record archive.
export default defineConfig({
  worker: {
    name: "norn-research",
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
      NORN_SESSION: bindings.durableObject({ worker: "norn-research", exportName: "NornSession" }),
      NORN_BUDGET: bindings.durableObject({ worker: "norn-research", exportName: "NornBudget" }),
      NORN_ARCHIVE: bindings.durableObject({ worker: "norn-research", exportName: "NornArchive" }),
      NORN_PROVIDER: bindings.text("openai-compatible"),
      NORN_DECK: bindings.text("pamela-v1"),
      NORN_API_KEY: bindings.secret(),
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
