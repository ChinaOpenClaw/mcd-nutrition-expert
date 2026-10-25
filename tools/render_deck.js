// 麦当劳营养膳食专家 · 讲解视频幻灯片渲染
// 用 Playwright + webkit 把 deck/index.html 的每一页截成 1920x1080@2x PNG
//
// 注意（沿用前几项目经验）：
//   1. 每页渲染前必须清空 .slide.on 再激活目标页，否则第 2~N 页会与第 1 页同显
//   2. 打印 on= 激活数与 imgLoaded= 图片解码状态，出错一眼可见
//   3. 本机无 Chrome channel，Chromium 走 msedge 会不稳，统一用 webkit
const { webkit } = require('playwright');
const path = require('path');
const fs = require('fs');

(async () => {
  const TOTAL = 14;
  const outDir = path.resolve(__dirname, '..', 'docs', 'video', 'slides');
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await webkit.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error') console.log('  [console.error]', m.text());
  });
  page.on('requestfailed', (r) => {
    console.log('  [requestfailed]', r.url(), r.failure() && r.failure().errorText);
  });

  const file = 'file:///' + path.resolve(__dirname, '..', 'deck', 'index.html').replace(/\\/g, '/');

  for (let i = 1; i <= TOTAL; i++) {
    await page.goto(`${file}?s=${i}&t=${Date.now()}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1100);
    const onCount = await page.evaluate(() => document.querySelectorAll('.slide.on').length);
    const imgOk = await page.evaluate(() => {
      const imgs = Array.from(document.querySelectorAll('.slide.on img'));
      return imgs.map((im) => (im.naturalWidth > 0 ? 'ok' : 'FAIL')).join(',');
    });
    const out = path.join(outDir, `slide-${String(i).padStart(2, '0')}.png`);
    await page.screenshot({ path: out });
    const kb = (fs.statSync(out).size / 1024).toFixed(0);
    console.log(`rendered slide-${String(i).padStart(2, '0')}.png  on=${onCount}  imgLoaded=[${imgOk}]  ${kb}KB`);
  }
  await browser.close();
  console.log('\n完成：' + TOTAL + ' 页');
})();
