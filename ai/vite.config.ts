import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Library/IIFE build rather than an app build: the bundle is loaded INTO a Frappe
// desk page via frappe.require, so it must expose a single global mount function
// and must not assume it owns the document.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  build: {
    outDir: '../erpnext/public/ai',
    emptyOutDir: true,
    target: 'es2020',
    lib: {
      entry: path.resolve(__dirname, 'src/main.tsx'),
      name: 'ERPNextAI',
      formats: ['iife'],
      fileName: () => 'ai.bundle.js',
    },
    rollupOptions: {
      output: { assetFileNames: 'ai.bundle.[ext]' },
    },
  },
});
