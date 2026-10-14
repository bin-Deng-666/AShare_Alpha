const { test, expect } = require('@playwright/test');

test('empty workspace shows safe controls at desktop and mobile widths', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#strategy-name')).toContainText('Buy & Hold');
  await expect(page.locator('#strategy-select option')).toHaveCount(1);
  await expect(page.locator('#generate-button')).toBeDisabled();
  await expect(page.locator('#prepare-button')).toBeEnabled();
  await expect(page.locator('input[type=date]')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('one click, strategy switching and history retain matching orders and positions', async ({ page, request }) => {
  const cfg = await (await request.get('/api/strategies/demo/config')).json();
  const configs = { demo: cfg, example_rule: { ...cfg, strategy_id: 'example_rule', display_name: 'Example rule' } };
  let ready = false, running = false, polls = 0, selected = 'demo';
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  function plan(id) {
    return { strategy: id, asof: '2026-10-12', exec_date: '2026-10-13', meta: {}, warnings: [],
      buy: id === 'demo' ? [{ code: '000001', budget: 12000, reason: 'buy_and_hold' }] : [],
      reduce: id === 'example_rule' ? [{ code: '600000', quantity: 200, reason: 'stop_loss' }] : [],
      positions: [{ code: id === 'demo' ? '000001' : '600000', shares: 100 }] };
  }
  await page.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname; let body;
    if (pathname === '/api/strategies') body = { strategies: Object.keys(configs) };
    else if (pathname === '/api/job/current') {
      if (running && ++polls > 1) { running = false; ready = true; }
      body = polls ? { status: running ? 'running' : 'done', job_id: 'browser', strategy_id: selected,
        job_type: 'prepare', stage: '更新', total_steps: 1, completed_steps: running ? 0 : 1, log_tail: [] } : { status: 'idle' };
    } else {
      const parts = pathname.split('/'), id = parts[3], action = parts.slice(4).join('/');
      if (action === 'config') body = configs[id];
      else if (action === 'status') body = { display_name: configs[id].display_name, t_full: '2026-10-12',
        latest_trade_date: '2026-10-12', data_current: true, can_generate: true, quality_status: 'pass', messages: [], per_source_dates: {} };
      else if (action === 'daily/latest') body = { current: true, result: ready ? plan(id) : null };
      else if (action === 'history') body = { reports: [{ date: '20261013', asof: '2026-10-12', exec_date: '2026-10-13', n_buy: 1, n_reduce: 0 }] };
      else if (action === 'daily/20261013') body = { result: plan(id) };
      else if (action === 'prepare') { selected = id; running = true; polls = 0; body = { job_id: 'browser' }; }
      else body = {};
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/');
  await page.locator('#prepare-button').click();
  await expect(page.locator('#plan-badge')).toHaveText('最新收盘计划');
  await expect(page.locator('#buy-table')).toContainText('12,000');
  await expect(page.locator('#positions-table')).toContainText('000001');
  await page.locator('#strategy-select').selectOption('example_rule');
  await expect(page.locator('#sell-table')).toContainText('600000');
  await expect(page.locator('#buy-table')).not.toContainText('000001');
  await expect(page.locator('#positions-table')).toContainText('600000');
  await page.locator('#history-panel summary').click();
  await page.locator('.history-item').first().click();
  await expect(page.locator('#plan-badge')).toHaveText('历史计划');
  await page.locator('#latest-button').click();
  await expect(page.locator('#plan-badge')).toHaveText('最新收盘计划');
  expect(errors).toEqual([]);
});
