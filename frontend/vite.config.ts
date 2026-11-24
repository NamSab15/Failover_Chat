import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server and preview listen on :3000 to match the docker-compose contract
// (frontend on http://localhost:3000).
export default defineConfig({
  plugins: [react()],
  server: { port: 3000, host: true },
  preview: { port: 3000, host: true },
});
