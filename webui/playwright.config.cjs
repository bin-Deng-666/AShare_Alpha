const { defineConfig } = require('@playwright/test');
const os = require('node:os');
const path = require('node:path');
module.exports = defineConfig({
  testDir: './tests/browser',
  use: { baseURL: 'http://127.0.0.1:18787', trace: 'retain-on-failure' },
  webServer: {
    command: '../.venv/bin/python -m ashare_alpha web --port 18787',
    url: 'http://127.0.0.1:18787',
    env: { ASHARE_WORKSPACE: path.join(os.tmpdir(), 'ashare-browser-' + process.pid) },
    reuseExistingServer: false,
  },
});
