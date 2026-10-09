import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => {
  // Relative base: assets and latest.csv load relative to the page, so the
  // same build works at riscv.github.io/adm-spec-dashboard/ and behind a
  // proxy at another path (tech.riscv.org/development/). Set BASE_URL to
  // force an absolute base.
  const base = process.env.BASE_URL || "./";
  const repoRoot = path.resolve(__dirname, "..");
  const localCsvPath = "/Users/rpsene/Downloads/RISC-V_Downloads/specs_20260127_135041.csv";
  const localCsvUrl = command === "serve" ? `/@fs/${localCsvPath}` : "";

  return {
    plugins: [react()],
    base,
    server: {
      fs: {
        allow: [repoRoot, "/Users/rpsene/Downloads"],
      },
    },
    define: {
      __LOCAL_CSV_URL__: JSON.stringify(localCsvUrl),
    },
  };
});
