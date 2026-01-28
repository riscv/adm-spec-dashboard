import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => {
  const repoName = process.env.GITHUB_REPOSITORY
    ? process.env.GITHUB_REPOSITORY.split("/")[1]
    : "";
  const defaultBase = repoName ? `/${repoName}/` : "/";
  const base = process.env.BASE_URL || defaultBase;
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
