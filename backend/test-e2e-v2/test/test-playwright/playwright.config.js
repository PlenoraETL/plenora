const { defineConfig, devices } = require('../../../../frontend/node_modules/@playwright/test');

const chromiumUse = { ...devices['Desktop Chrome'] };
if (process.env.PLAYWRIGHT_CHROMIUM_CHANNEL) {
  chromiumUse.channel = process.env.PLAYWRIGHT_CHROMIUM_CHANNEL;
}
if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
  chromiumUse.launchOptions = {
    ...(chromiumUse.launchOptions || {}),
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  };
}

module.exports = defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.js/,
  timeout: 45_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  outputDir: 'test-results',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: process.env.PLAYWRIGHT_VIDEO || 'retain-on-failure',
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: 'chromium',
      use: chromiumUse,
    },
  ],
});
