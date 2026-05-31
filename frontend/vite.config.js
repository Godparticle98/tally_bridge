import vue from '@vitejs/plugin-vue'
import frappeui from 'frappe-ui/vite'
import path from 'path'
import { defineConfig } from 'vite'

export default defineConfig({
  define: {
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false',
  },
  plugins: [
    vue(),
    frappeui({
      frappeProxy: true,
      lucideIcons: true,
      jinjaBootData: true,
      buildConfig: {
        outDir: path.resolve(__dirname, `../${getAppName()}/public/frontend`),
        indexHtmlPath: path.resolve(__dirname, `../${getAppName()}/www/${getAppName()}.html`),
      },
    }),
  ],
  build: {
    outDir: path.resolve(__dirname, `../${getAppName()}/public/frontend`),
    emptyOutDir: true,
  },
  server: {
    allowedHosts: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      'tailwind.config.js': path.resolve(__dirname, 'tailwind.config.js'),
    },
  },
  optimizeDeps: {
    include: [
      'frappe-ui > feather-icons',
      'showdown',
      'tailwind.config.js',
      'engine.io-client',
      'highlight.js/lib/core',
    ],
  },
})


function getAppName() {
  // frappe-ui projects are structured as follows:
  // - apps
  //   - <app_name>
  //     - frontend
  //       - vite.config.js
  return path.basename(path.resolve(__dirname, '..'))
}
