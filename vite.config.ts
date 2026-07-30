import path from 'path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import compression from 'vite-plugin-compression';
import { viteSingleFile } from 'vite-plugin-singlefile';
import { exec } from 'child_process';
import fs from 'fs';

// Two build modes:
//
//   vite build                 → dist/, ordinary multi-file site (what is deployed)
//   vite build --mode single   → dist-single/index.html, everything inlined
//
// The difference matters because of the PDF reader: pdf.js is a megabyte and a
// half, and it is loaded through a dynamic import so the deployed site only
// fetches it once someone actually opens a PDF. Inlining collapses every dynamic
// import into the page, which is what the offline copy needs and what everyone
// else should not have to download.
const SINGLE_FILE_OUT_DIR = 'dist-single';

export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, '.', '');
    const singleFile = mode === 'single';
    return {
      server: {
        port: 3000,
        host: '0.0.0.0',
      },
      plugins: [
        react(),
        tailwindcss(),
        ...(singleFile ? [viteSingleFile()] : []),
        {
          name: 'download-endpoint',
          configureServer(server) {
            server.middlewares.use('/api/download-html', (req, res) => {
              exec('npm run build:single', (error) => {
                if (error) {
                  console.error(`Build error: ${error}`);
                  res.statusCode = 500;
                  res.end('Error building file');
                  return;
                }
                const distPath = path.resolve(__dirname, SINGLE_FILE_OUT_DIR, 'index.html');
                if (fs.existsSync(distPath)) {
                  const html = fs.readFileSync(distPath);
                  res.setHeader('Content-Disposition', 'attachment; filename="LegalLens-Redline-Offline.html"');
                  res.setHeader('Content-Type', 'text/html');
                  res.end(html);
                } else {
                  res.statusCode = 500;
                  res.end('Index.html not found in dist.');
                }
              });
            });
          }
        },
        // Pre-compressed twins for the static host; pointless for a file meant to
        // be downloaded and opened from disk.
        ...(singleFile
          ? []
          : [
              compression({ algorithm: 'gzip', ext: '.gz' }),
              compression({ algorithm: 'brotliCompress', ext: '.br' }),
            ])
      ],
      define: {
        'process.env.API_KEY': JSON.stringify(env.GEMINI_API_KEY),
        'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY)
      },
      resolve: {
        alias: {
          '@': path.resolve(__dirname, '.'),
        }
      },
      build: {
        outDir: singleFile ? SINGLE_FILE_OUT_DIR : 'dist',
        target: 'esnext',
        minify: 'esbuild',
        cssMinify: true,
        reportCompressedSize: false,
        chunkSizeWarningLimit: 100000000 // the inlined single file gets large
      }
    };
});
