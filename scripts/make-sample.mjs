// Engrave our own short melody. This is an input to real OMR, not a stored result.
import { chromium } from "@playwright/test";
import { fileURLToPath } from "node:url";

const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 600 }, deviceScaleFactor: 2 });
  await page.setContent(`<!doctype html><html><head><style>
    body { margin: 0; background: white; }
    #sheet { width: 1100px; padding: 45px 40px 65px; box-sizing: border-box; }
  </style></head><body><div id="sheet"><div id="notation"></div></div></body></html>`);
  await page.addScriptTag({ path: fileURLToPath(new URL("../node_modules/abcjs/dist/abcjs-basic-min.js", import.meta.url)) });
  await page.evaluate(() => {
    window.ABCJS.renderAbc("notation", `X:1
T:A Little Melody
C:Note Lens
M:4/4
L:1/4
Q:1/4=120
K:G
G A B c | d2 B2 | =F ^F G z | C4 |]`, {
      staffwidth: 990, paddingtop: 20, paddingbottom: 20,
      paddingleft: 15, paddingright: 15, scale: 1,
      add_classes: true,
    });
  });
  await page.locator("#sheet").screenshot({
    path: fileURLToPath(new URL("../web/sample-score.png", import.meta.url)),
  });
} finally {
  await browser.close();
}
