import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Library/IIFE build rather than an app build: the bundle is loaded INTO a Frappe
// desk page via frappe.require, so it must expose a single global mount function
// and must not assume it owns the document.
export default defineConfig({
  plugins: [react()],
  // Vite's library mode leaves `process.env.NODE_ENV` for the consumer to define,
  // unlike an app build. React reads it at module scope, so an undefined `process`
  // in the browser throws a ReferenceError before the IIFE ever assigns
  // window.mountAI -- the page then reports the bundle as "not loaded" while a
  // perfectly good file sits in the network tab. It only reproduces in a browser:
  // node has `process`, so a node smoke test passes happily.
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  build: {
    outDir: '../erpnext/public/ai',
    emptyOutDir: true,
    target: 'es2020',
    lib: {
      entry: path.resolve(__dirname, 'src/main.tsx'),
      name: 'ERPNextAI',
      formats: ['iife'],
      // NOT '*.bundle.js': frappe.assets.execute() refuses to cache-bust any path
      // containing '.bundle.' (it assumes its own esbuild content-hashed those), and
      // frappe.assets.extn() reads the extension from AFTER a '?', so busting it by
      // hand in the path makes it resolve the handler for "0" -- "s is not a function".
      // With a plain name frappe appends ?v=<build> itself and both problems vanish.
      fileName: () => 'ai.js',
    },
    rollupOptions: {
      output: { assetFileNames: 'ai.[ext]' },
    },
  },
});
