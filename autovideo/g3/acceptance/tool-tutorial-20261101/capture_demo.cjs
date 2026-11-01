// Render the actual local input and command output with the existing browser dependency.
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const {openBrowser} = require(path.join(root, 'g0/vendor/remotion-audiogram/node_modules/@remotion/renderer'));
const out = path.resolve(process.argv[2]);
const inputFile = process.argv[3] || 'weekly.html';
const outputFile = process.argv[4] || 'output-view.html';
(async () => {
  const browser = await openBrowser('chrome', {
    browserExecutable: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', logLevel: 'error'});
  try {
    const page = await browser.newPage({context: () => null, logLevel: 'error', indent: false,
      pageIndex: 0, onBrowserLog: null, onLog: null});
    await page.setViewport({width: 1200, height: 900, deviceScaleFactor: 1});
    const records = [];
    for (const [file, name] of [[inputFile, 'input'], [outputFile, 'output']]) {
      await page.goto({url: pathToFileURL(path.join(out, file)).href, timeout: 30000, options: {}});
      await page.evaluate(async () => {await document.fonts.ready; return true;});
      const regions = await page.evaluate(() => ['summary','tasks'].map(id => {
        const r = document.getElementById(id).getBoundingClientRect();
        return {id, x: r.x, y: r.y, width: r.width, height: r.height};
      }));
      const image = await page._client().send('Page.captureScreenshot', {format: 'png', fromSurface: true});
      fs.writeFileSync(path.join(out, name + '.png'), Buffer.from(image.value.data, 'base64'));
      records.push({name, width: 1200, height: 900, regions});
    }
    fs.writeFileSync(path.join(out, 'screenshots.json'), JSON.stringify(records, null, 2) + '\n');
  } finally {await browser.close({silent: true});}
})().catch(error => {process.stderr.write(error.stack || String(error)); process.exitCode = 1;});
