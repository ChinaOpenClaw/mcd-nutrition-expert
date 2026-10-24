// 前端五视图截图验证脚本
// 用法: NODE_PATH=<workspace>/node_modules node scripts/shoot_views.js [baseUrl] [outDir]
const path = require('path');
const fs = require('fs');
const { webkit } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8899';
const OUT = process.argv[3] || path.join(__dirname, '..', '..', '..', '.shots');

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await webkit.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push('[console] ' + m.text()); });
  page.on('pageerror', e => errors.push('[pageerror] ' + e.message));

  const shot = async (name) => {
    const p = path.join(OUT, name + '.png');
    await page.screenshot({ path: p, fullPage: true });
    console.log('SHOT ' + name + ' -> ' + p);
  };
  const nav = async (label) => {
    await page.locator('#nav button', { hasText: label }).first().click();
    await page.waitForTimeout(600);
  };

  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await shot('01-profile');

  // ① 建档：填档案并计算
  await page.selectOption('#f-goal', 'cut');
  await page.click('#btn-target');
  await page.waitForTimeout(3000);
  await shot('02-profile-result');

  // ② SP 矩阵
  await nav('SP矩阵');
  await page.waitForTimeout(3500);
  await shot('03-sp-matrix');

  // ③ 套餐推荐
  await nav('套餐推荐');
  await page.click('#btn-recommend');
  await page.waitForTimeout(9000);
  await shot('04-recommend');

  // ④ 门店点餐：查门店
  await nav('门店点餐');
  await page.fill('#f-city', '沈阳');
  await page.click('#btn-stores');
  await page.waitForTimeout(6000);
  await shot('05-stores');

  // 进入门店菜单：逐个尝试，直到某家店菜单加载成功
  // （麦当劳部分门店当前已打烊，MCP 会返回「门店可能已关闭或不在营业时间」，
  //   这是真实业务状态，不是 bug —— 前端会给出可读提示）
  const cards = page.locator('#stores-out .storecard');
  const n = await cards.count();
  let menuOk = false;
  for (let i = 0; i < n; i++) {
    await page.locator('#btn-back').click().catch(() => {});
    await page.waitForTimeout(300);
    const c = cards.nth(i);
    const label = (await c.innerText()).replace(/\s+/g, ' ').slice(0, 40);
    await c.click();
    // 菜单要逐个套餐拉 query-meal-detail 聚合 SP，首次可长达 30s+
    await page.waitForTimeout(1500);
    await page.waitForSelector('#menu-grid .mitem, #menu-grid .err', { timeout: 120000 })
      .catch(() => console.log('  (timeout waiting menu)'));
    await page.waitForTimeout(1500);
    const grid = await page.locator('#menu-grid .mitem').count();
    if (grid > 0) {
      console.log('MENU OK @' + i + ' ' + label);
      menuOk = true;
      break;
    }
    console.log('MENU FAIL @' + i + ' ' + label);
  }
  if (!menuOk) console.log('WARN 没有门店能加载出菜单');
  await shot('06-menu');

  // 优惠券视图
  const coupBtn = page.locator('#btn-coupons');
  if (await coupBtn.count()) {
    await coupBtn.click();
    await page.waitForTimeout(5000);
    await shot('07-coupons');
    await page.click('#btn-claim');
    await page.waitForTimeout(5000);
    await shot('08-coupons-claimed');
  }

  // ⑤ 健康可视化
  await nav('健康可视化');
  await page.waitForTimeout(3000);
  await shot('09-health');

  console.log('ERRORS ' + JSON.stringify(errors, null, 2));
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
