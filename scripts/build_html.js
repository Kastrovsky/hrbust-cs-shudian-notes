/* ============================================================================
 * 把习题集 Markdown 编译成「自包含 HTML」——公式不再依赖页面自带的 MathJax
 * ----------------------------------------------------------------------------
 * 为什么需要它：md 里的 $...$ 要靠宿主（Gitee / 各类预览器）出 MathJax 才显示，
 * 那些渲染器时好时坏，一旦不加载，全篇公式集体变成 [Math Processing Error]。
 * 本脚本把公式在**构建时**就编译好，并把 KaTeX 的 20 个 woff2 字体 base64 内嵌，
 * 产出一个 1.7 MB 的单文件：双击即可离线阅读、可打印、答案仍可点击展开。
 *
 * 用法：
 *   在仓库根目录：npm i katex markdown-it      # 只需装一次
 *   node scripts/build_html.js "计算机专业真题/数字电路与逻辑_期末习题集_含答案与解析.md" \
 *                              "计算机专业真题/数字电路与逻辑_期末习题集_含答案与解析.html"
 *
 * 约定：
 *   · 数学模式里**不许出现汉字**（`$[X]_{原}$` 会报 unicodeTextInMathMode），
 *     下标写 `_{\text{原}}`，并列项拆成 `$A$、$B$、$C$`；
 *   · 构建日志里「公式编译失败」必须为 0，不为 0 就先修 md，不要放过去；
 *   · mermaid 状态图仍需联网渲染，离线时保留源码文本，不影响公式与答案。
 * ========================================================================== */
// 把习题集 md 转成单文件 HTML：
// 公式用 KaTeX 编译成 HTML（字体以 base64 内嵌），不依赖 MathJax 或任何网络资源
const fs = require('fs');
const path = require('path');
const katex = require('katex');
const MarkdownIt = require('markdown-it');

const src = process.argv[2];
const dst = process.argv[3];
if (!src || !dst) {
  console.error('用法：node scripts/build_html.js <输入.md> <输出.html>');
  process.exit(2);
}
const raw = fs.readFileSync(src, 'utf8').replace(/\r\n/g, '\n');


// ---------- KaTeX 样式 + 字体内嵌 ----------
// 用 require.resolve 定位 katex 包，避免脚本挪目录后找不到 dist
const DIST = path.join(path.dirname(require.resolve('katex/package.json')), 'dist');
let kcss = fs.readFileSync(path.join(DIST, 'katex.min.css'), 'utf8');
kcss = kcss.replace(/url\(fonts\/([A-Za-z0-9_\-]+\.woff2)\)/g, (m, f) => {
  const p = path.join(DIST, 'fonts', f);
  if (!fs.existsSync(p)) return m;
  return `url(data:font/woff2;base64,${fs.readFileSync(p).toString('base64')})`;
});

let codeStore = [], mathStore = [];

// ① 先摘出围栏代码块，避免里面的 $ 被当成公式
let text = raw.replace(/```([a-zA-Z]*)\n([\s\S]*?)```/g, (m, lang, body) => {
  codeStore.push({ lang, body });
  return `\n%%CODE${codeStore.length - 1}%%\n`;
});

// ② 块公式 $$...$$，③ 行内公式 $...$
text = text.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => {
  mathStore.push({ tex, display: true });
  return `%%MATH${mathStore.length - 1}%%`;
});
text = text.replace(/\$([^$\n]+?)\$/g, (m, tex) => {
  mathStore.push({ tex, display: false });
  return `%%MATH${mathStore.length - 1}%%`;
});

const md = new MarkdownIt({ html: true, linkify: false, typographer: false });
let html = md.render(text);

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// ④ 回填公式
let failed = [];
html = html.replace(/%%MATH(\d+)%%/g, (m, i) => {
  const { tex, display } = mathStore[+i];
  try {
    return katex.renderToString(tex.trim(), {
      output: 'html', displayMode: display, throwOnError: true, strict: 'ignore',
    });
  } catch (e) {
    failed.push(tex.trim().slice(0, 40) + ' :: ' + e.message.split('\n')[0]);
    return `<code class="mathfail">${esc(tex)}</code>`;
  }
});

// ⑤ 回填代码块：mermaid 交给浏览器渲染，其余原样
html = html.replace(/<p>%%CODE(\d+)%%<\/p>|%%CODE(\d+)%%/g, (m, a, b) => {
  const { lang, body } = codeStore[+(a === undefined ? b : a)];
  if (lang === 'mermaid') return `<pre class="mermaid">${esc(body.trim())}</pre>`;
  return `<pre class="code"><code>${esc(body.replace(/\n$/, ''))}</code></pre>`;
});

const CSS = `
body{font-family:"Microsoft YaHei","PingFang SC","Source Han Sans SC",sans-serif;
  line-height:1.8;color:#1a1a1a;max-width:1000px;margin:0 auto;padding:34px 30px 90px;font-size:16px}
h1{font-size:27px;border-bottom:3px solid #1f4e79;padding-bottom:8px;margin:38px 0 18px;color:#1f4e79}
h2{font-size:22px;border-left:6px solid #1f4e79;padding-left:10px;margin:34px 0 14px;color:#1f4e79}
h3{font-size:19px;margin:28px 0 12px;color:#22577a}
h4{font-size:17px;margin:22px 0 10px;background:#eef4fb;padding:9px 13px;border-left:4px solid #2e75b6;border-radius:4px}
blockquote{margin:14px 0;padding:10px 16px;background:#f7f9fc;border-left:4px solid #9dc3e6;color:#333}
table{border-collapse:collapse;width:100%;margin:16px 0;font-size:15px}
th,td{border:1px solid #b9cde5;padding:7px 10px;text-align:left;vertical-align:top}
th{background:#e8f0f9;font-weight:700}
tr:nth-child(even) td{background:#fafcfe}
code{background:#f2f4f7;padding:1px 5px;border-radius:3px;font-family:Consolas,"Courier New",monospace;font-size:14.5px}
pre.code{background:#f6f8fa;border:1px solid #dbe3ec;border-radius:6px;padding:12px 14px;overflow-x:auto}
pre.code code{background:none;padding:0;font-size:14px;line-height:1.6}
pre.mermaid{background:#fbfdff;border:1px dashed #b9cde5;border-radius:6px;padding:12px;text-align:left;overflow-x:auto;font-size:12.5px;color:#666}
details{border:1px solid #cfe0f2;border-radius:8px;margin:10px 0 20px;background:#fcfeff}
details[open]{background:#fbfdff}
summary{cursor:pointer;padding:11px 14px;font-weight:700;color:#1f4e79;background:#eaf2fb;
  border-radius:8px;list-style:none;user-select:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▶ ";font-size:11px}
details[open]>summary::before{content:"▼ "}
details[open]>summary{border-bottom:1px solid #cfe0f2;border-radius:8px 8px 0 0}
details>*:not(summary){margin-left:16px;margin-right:16px}
img{max-width:100%;border:1px solid #dbe3ec;border-radius:6px;margin:10px 0;background:#fff}
.katex{font-size:1.05em}
.katex-display{margin:14px 0}
hr{border:none;border-top:1px solid #dbe3ec;margin:28px 0}
@media print{body{max-width:none;padding:0;font-size:12pt}details{border-color:#999}
  summary{background:#eee}details>*:not(summary){margin-left:0}}
`;

const OUT = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数字电路与逻辑 · 期末习题集（含答案与解析）</title>
<style>${kcss}</style>
<style>${CSS}</style>
<script type="module">
  // 状态图用 mermaid 渲染，需联网；离线时保留源码文本，不影响公式与答案
  try{
    const m = await import('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs');
    m.default.initialize({startOnLoad:true, theme:'neutral'});
  }catch(e){}
</script>
</head><body>
${html}
</body></html>`;

fs.writeFileSync(dst, OUT, 'utf8');
console.log('已生成', dst, (OUT.length / 1024 / 1024).toFixed(2) + 'MB');
console.log('公式', mathStore.length, '处；代码块', codeStore.length, '个；公式编译失败', failed.length, '处');
failed.slice(0, 8).forEach(f => console.log('  X', f));
