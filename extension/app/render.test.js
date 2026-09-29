import { describe, expect, it } from 'vitest';
import { decodeEntities, highlightPython, renderMarkdown } from './render.js';

const NOTE = `---
title: x
---

> [!abstract] In one breath
> Averages **matter**.

## Weights [01:10](https://u.com/l#t=70)

$$\\begin{bmatrix} a \\\\ b \\end{bmatrix}$$ and $\\mu_p$.

![[assets/28-S001.jpg|720]]

See [[27 - Previous lecture|last time]].

\`\`\`mermaid
flowchart LR
  A["Returns"] --> B["Weighted avg"]
\`\`\`

> [!question]- Why weights?
> Because money is split.
`;

describe('renderMarkdown', () => {
  const html = renderMarkdown(NOTE, { folder: 'Quant Finance' });

  it('drops frontmatter', () => {
    expect(html).not.toContain('title: x');
  });

  it('turns callouts into styled blocks and collapsed Q&A into details', () => {
    expect(html).toContain('<div class="callout callout-abstract"><div class="callout-title">In one breath</div>');
    expect(html).toContain('<strong>matter</strong>');
    expect(html).toMatch(/<details class="callout callout-question"><summary>Why weights\?<\/summary>/);
    expect(html).toContain('Because money is split.');
  });

  it('keeps LaTeX row separators intact for KaTeX', () => {
    expect(html).toContain('$$\\begin{bmatrix} a \\\\ b \\end{bmatrix}$$');
    expect(html).toContain('$\\mu_p$');
  });

  it('resolves embeds against the note folder', () => {
    expect(html).toContain('<figure class="embed"><img data-vault-path="Quant Finance/assets/28-S001.jpg" alt="" style="max-width:720px"></figure>');
  });

  it('renders wikilinks as text with the target', () => {
    expect(html).toContain('<span class="wikilink" title="27 - Previous lecture">last time</span>');
  });

  it('hands mermaid source to a block the panel draws', () => {
    const m = html.match(/<div class="mermaid-block" data-src="([^"]*)"/);
    expect(m).not.toBeNull();
    expect(decodeEntities(m[1])).toContain('A["Returns"] --> B["Weighted avg"]');
  });

  it('makes timestamp links seek in place', () => {
    expect(html).toContain('<a class="ts" href="https://u.com/l#t=70" data-seek="70">01:10</a>');
  });

  it('cannot be made to emit markup or scripts', () => {
    const evil = renderMarkdown('<img src=x onerror=alert(1)> [x](javascript:alert(1)) ![[a"onload="x.jpg]]');
    expect(evil).not.toContain('<img src=x');
    expect(evil).not.toContain('javascript:');
    expect(evil).not.toContain('"onload="');
  });
});

describe('highlightPython', () => {
  it('marks keywords, strings, comments and numbers, and escapes the rest', () => {
    const out = highlightPython('import numpy as np  # why\nx = "a<b" + 2');
    expect(out).toContain('<span class="k">import</span>');
    expect(out).toContain('<span class="c"># why</span>');
    expect(out).toContain('<span class="s">"a&lt;b"</span>');
    expect(out).toContain('<span class="n">2</span>');
  });
});
