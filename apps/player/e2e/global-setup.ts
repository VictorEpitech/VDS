import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

/** Fabrique un court clip WebM (VP8) en enregistrant une page : aucun ffmpeg système requis. */
export default async function globalSetup(): Promise<void> {
  const dir = path.resolve(import.meta.dirname, ".fixtures");
  const clip = path.join(dir, "clip.webm");
  if (fs.existsSync(clip)) return;
  fs.mkdirSync(dir, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ recordVideo: { dir, size: { width: 160, height: 90 } }, viewport: { width: 160, height: 90 } });
  const page = await ctx.newPage();
  await page.setContent('<body style="margin:0;background:#0a0"><script>setInterval(()=>{document.body.style.background=document.body.style.background==="rgb(0, 170, 0)"?"#00a":"#0a0"},200)</script></body>');
  await page.waitForTimeout(1500);
  const video = page.video();
  await ctx.close();
  if (video) fs.renameSync(await video.path(), clip);
  await browser.close();
}
