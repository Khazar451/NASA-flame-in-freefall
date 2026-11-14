import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base './' keeps asset and data paths relative, so the built site works from
// any sub-path (GitHub Pages serves it under /NASA-flame-in-freefall/).
export default defineConfig({
  base: './',
  plugins: [react()],
});
