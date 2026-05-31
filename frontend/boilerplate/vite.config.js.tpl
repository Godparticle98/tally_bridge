import vue from '@vitejs/plugin-vue'
import frappeui from 'frappe-ui/vite'
import path from 'path'
import fs from 'fs'
import { execFileSync } from "child_process";
import { defineConfig } from 'vite'

function getBenchPath(params) {
    let currentDir = process.cwd()
  while (currentDir !== '/') {
    if (
      fs.existsSync(path.join(currentDir, 'Procfile')) 
    ) {
      return path.join(currentDir)
    }
    currentDir = path.resolve(currentDir, '..')
}
}

function getLatestDirectory(dirPath) {
  
  const IGNORE = [
    "common_site_config.json",
    "assets",
    "apps.txt",
    "apps.json",
    ".hypothesis"
  ];
  const dirs = fs
  .readdirSync(dirPath, { withFileTypes: true })
  .filter(d => d.isDirectory() && !IGNORE.includes(d.name))
  .map(d => {
    const fullPath = path.join(dirPath, d.name);
    const stats = fs.statSync(fullPath);
    return {
      name: d.name,
      createdAt: stats.birthtimeMs || stats.mtimeMs
    };
  })
  .sort((a, b) => b.createdAt - a.createdAt)
  .map(d => d.name); // 👈 convert to list
  return dirs
}

function getAppsSiteMap() {
  const output = execFileSync(
    "bench",
    ["--site", "all", "list-apps", "--format", "json"],
    {
      cwd: getBenchPath(),
      stdio: ["ignore", "pipe", fs.openSync("/dev/null", "w")], // suppress stderr
      encoding: "utf-8"
    }
  );

  return JSON.parse(output);
}

function getSitesInfo() {
  const benchPath = getBenchPath();
  const sitesDir = path.join(benchPath, "sites");

  const siteMap = getAppsSiteMap(); // { site: [apps] }

  return Object.entries(siteMap)
    .map(([site, apps]) => {
      const sitePath = path.join(sitesDir, site);
      let time = 0;

      try {
        const configPath = path.join(sitePath, "site_config.json");
        const stats = fs.existsSync(configPath)
          ? fs.statSync(configPath)
          : fs.statSync(sitePath);

        time = stats.birthtimeMs || stats.mtimeMs;
      } catch {
        // ignore missing sites
      }

      return { site, apps, time };
    })
    .sort((a, b) => b.time - a.time)
    .map(({ site, apps }) => ({ site, apps }));
}

function getCurrentSite(sites, app_name){
    let currentSite = null;
    for(const [index, info] of Object.entries(sites)){
      if(info.apps.includes(app_name)){
        currentSite = info.site
      }
    }
    return currentSite
}

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
        indexHtmlPath: "../$app_name/www$route.html",
      },
    }),
    {
      name: 'custom-start-message',
      configureServer(server) {
        server.httpServer?.once('listening', () => {
          let sites = getSitesInfo()
          let app_name = path.basename(path.dirname(path.resolve(process.cwd())))
          let currentSite = getCurrentSite(sites, app_name)
          if(currentSite){
            const info = server.config.server;
            const url = new URL("http://localhost");
            url.hostname = currentSite;
            url.port = info.port;
            url.pathname = "$route";
            console.log("Open in Browser: " + url.href);
          }
        })
      }
    }
  ],
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
      'interactjs'
    ],
  },
})