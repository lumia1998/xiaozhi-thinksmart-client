/* Fixed choice widgets and sanitized Markdown. All dependencies are bundled offline. */
const RichContent = (() => {
  const markdown = window.markdownit({html: false, breaks: true, linkify: false});
  const escape = value => markdown.utils.escapeHtml(String(value));
  const safeURL = value => {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !value.includes('@') ? url.href : '';
    } catch { return ''; }
  };
  markdown.renderer.rules.image = (tokens, index) => {
    const token = tokens[index];
    const url = safeURL(token.attrGet('src'));
    if (!url) return escape(token.content);
    return `<img src="${escape(url)}" alt="${escape(token.content)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
  };
  markdown.renderer.rules.fence = (tokens, index) => {
    const token = tokens[index];
    const language = token.info.trim().split(/\s/)[0].slice(0, 40);
    const code = token.content;
    let highlighted = escape(code);
    if (code.length < 20000 && window.hljs && hljs.getLanguage(language)) {
      try { highlighted = hljs.highlight(code, {language, ignoreIllegals: true}).value; } catch {}
    }
    return `<section class="code-block"><header><span>${escape(language || '代码')}</span><button type="button" class="copy-code">复制</button></header><pre tabindex="0" aria-label="代码内容"><code>${highlighted}</code></pre></section>`;
  };

  function renderText(element, text) {
    if (element.dataset.source === text) return;
    element.dataset.source = text;
    const html = markdown.render(String(text || '').slice(0, 60000));
    element.innerHTML = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 's', 'del', 'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4',
        'h5', 'h6', 'hr', 'a', 'img', 'section', 'header', 'span', 'button', 'pre', 'code', 'table', 'thead', 'tbody', 'tr', 'th', 'td'],
      ALLOWED_ATTR: ['href', 'src', 'alt', 'class', 'type', 'loading', 'decoding', 'referrerpolicy', 'tabindex', 'aria-label'],
      ALLOW_DATA_ATTR: false,
    });
    for (const anchor of element.querySelectorAll('a')) {
      const href = safeURL(anchor.getAttribute('href'));
      if (!href) anchor.removeAttribute('href');
      else { anchor.href = href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; }
    }
    for (const button of element.querySelectorAll('.copy-code')) {
      button.addEventListener('click', async () => {
        const code = button.closest('.code-block').querySelector('code').textContent;
        try {
          if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(code);
          else {
            const input = document.createElement('textarea');
            input.value = code;
            input.style.position = 'fixed';
            input.style.top = '-10000px';
            document.body.appendChild(input);
            input.select();
            try { if (!document.execCommand('copy')) throw new Error(); }
            finally { input.remove(); button.focus(); }
          }
          button.textContent = '已复制';
        } catch { button.textContent = '复制失败'; }
      });
    }
  }

  function renderChoice(container, card, submit) {
    container.replaceChildren();
    if (!card || !Array.isArray(card.options)) return;
    const title = document.createElement('h3');
    title.textContent = card.question;
    const options = document.createElement('div');
    options.className = 'choice-options';
    const status = document.createElement('p');
    status.className = 'choice-status';
    status.setAttribute('role', 'status');
    const expired = card.expired || card.expires * 1000 < Date.now();
    status.textContent = card.selected ? '已选择' : expired ? '选项已过期，请重新提问' : '';
    for (const option of card.options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'choice-option';
      button.disabled = Boolean(card.selected || expired);
      button.setAttribute('aria-pressed', String(card.selected === option.id));
      const label = document.createElement('b');
      label.textContent = option.label;
      const description = document.createElement('span');
      description.textContent = option.description;
      button.append(label, description);
      button.addEventListener('click', async () => {
        options.querySelectorAll('button').forEach(item => { item.disabled = true; });
        status.textContent = '正在提交';
        try {
          const result = await submit(card.id, option.id);
          if (!result.ok) throw new Error(result.error || '选择未成功，请重试');
          card.selected = result.selected;
          renderChoice(container, card, submit);
        } catch (error) {
          status.textContent = error.message;
          options.querySelectorAll('button').forEach(item => { item.disabled = false; });
        }
      });
      options.appendChild(button);
    }
    container.append(title, options, status);
  }
  return {renderText, renderChoice};
})();
