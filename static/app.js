/* 网页正文提取器 · 前端逻辑 */
(function () {
  'use strict';

  var state = { mode: 'url', results: [], sel: {} };

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function toast(msg) {
    var t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._tid);
    t._tid = setTimeout(function () { t.classList.remove('show'); }, 1800);
  }
  function setStatus(s) { $('#status').textContent = s || ''; }

  var TAG = {
    heading: '标题', paragraph: '段落', list: '列表', code: '代码',
    table: '表格', image: '图片', quote: '引用', caption: '图注', divider: '分隔'
  };

  /* ---------- 事件 ---------- */
  $$('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      $$('.tab').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      state.mode = t.dataset.mode;
      $('#pane-url').classList.toggle('hidden', state.mode !== 'url');
      $('#pane-html').classList.toggle('hidden', state.mode !== 'html');
    });
  });

  $('#btn-sample').addEventListener('click', function () {
    $('#urls').value = 'https://example.com/\nhttps://www.python.org/';
  });

  $('#btn-run').addEventListener('click', run);

  $('#btn-theme').addEventListener('click', function () {
    document.body.classList.toggle('dark');
    try { localStorage.setItem('wx-theme', document.body.className); } catch (e) {}
  });
  try {
    if (localStorage.getItem('wx-theme') === 'dark') document.body.classList.add('dark');
  } catch (e) {}

  $('#btn-copy').addEventListener('click', copySelected);
  $('#btn-download').addEventListener('click', download);
  $('#btn-clear').addEventListener('click', function () {
    state.results = []; state.sel = {}; render(); toast('已清空');
  });

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run(); }
  });

  /* ---------- 提取 ---------- */
  function run() {
    var btn = $('#btn-run');
    var payload;
    if (state.mode === 'url') {
      var urls = $('#urls').value.trim();
      if (!urls) { toast('请先填写网址'); return; }
      payload = {
        urls: urls,
        timeout: parseInt($('#opt-timeout').value, 10) || 20,
        min_len: parseInt($('#opt-minlen').value, 10) || 0,
        keep_images: $('#opt-images').checked,
        keep_tables: $('#opt-tables').checked
      };
    } else {
      var html = $('#html-src').value;
      if (html.trim().length < 20) { toast('源码内容太少'); return; }
      payload = { html: html, url: $('#html-url').value.trim() };
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spin">◌</span> 提取中…';
    setStatus('正在抓取并解析…');

    var ep = state.mode === 'url' ? '/api/extract' : '/api/parse_html';
    fetch(ep, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) throw new Error(data.error || '失败');
      state.results = data.results || [];
      state.sel = {};
      render();
      var bad = state.results.filter(function (r) { return !r.ok; }).length;
      setStatus(bad ? ('成功 ' + (state.results.length - bad) + ' / ' + state.results.length) : '完成');
      if (bad) toast('部分页面抓取失败');
    }).catch(function (e) {
      setStatus('');
      toast('提取失败：' + (e.message || e));
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = '⚡ 自动提取正文';
    });
  }

  /* ---------- 渲染 ---------- */
  function render() {
    var wrap = $('#results');
    if (!state.results.length) {
      wrap.innerHTML = '';
      $('#bar').classList.add('hidden');
      return;
    }
    var html = state.results.map(function (r, gi) { return renderPage(r, gi); }).join('');
    wrap.innerHTML = html;
    updateBar();

    wrap.querySelectorAll('.block').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        if (ev.target.tagName === 'INPUT') return;
        var cb = el.querySelector('input');
        cb.checked = !cb.checked;
        toggle(el, cb.checked);
      });
    });
    wrap.querySelectorAll('.block input').forEach(function (cb) {
      cb.addEventListener('change', function () { toggle(cb.closest('.block'), cb.checked); });
    });
    wrap.querySelectorAll('[data-pg-all]').forEach(function (b) {
      b.addEventListener('click', function () { setPage(b.dataset.pgAll, true); });
    });
    wrap.querySelectorAll('[data-pg-none]').forEach(function (b) {
      b.addEventListener('click', function () { setPage(b.dataset.pgNone, false); });
    });
  }

  function toggle(el, on) {
    el.classList.toggle('sel', on);
    var key = el.dataset.key;
    if (on) state.sel[key] = true; else delete state.sel[key];
    updateBar();
  }

  function setPage(gi, on) {
    var page = state.results[gi];
    if (!page) return;
    page.blocks.forEach(function (b, bi) {
      var key = gi + ':' + bi;
      if (on) state.sel[key] = true; else delete state.sel[key];
    });
    $$('.block').forEach(function (el) {
      var k = el.dataset.key;
      var p = parseInt(k.split(':')[0], 10);
      if (p !== gi) return;
      el.classList.toggle('sel', on);
      el.querySelector('input').checked = on;
    });
    updateBar();
  }

  function renderPage(r, gi) {
    if (!r.ok) {
      return '<div class="page"><div class="page-head"><h2>' + esc(r.url || '未知页面') + '</h2>' +
        '<span class="meta">抓取失败</span></div>' +
        '<div class="err-box">无法访问：' + esc(r.error || '未知错误') +
        '<br/>该页面可能需要登录、禁止非浏览器访问，或对本地网络不可达。可尝试用「粘贴源码」模式：浏览器里按 Ctrl+U 全选复制后粘到这里。</div></div>';
    }
    var m = r.meta || {};
    var meta = [m.author, m.date, m.site].filter(Boolean).join(' · ');
    var blocks = (r.blocks || []).map(function (b, bi) {
      var key = gi + ':' + bi;
      var text = b.type === 'image'
        ? '[图片] ' + (b.text || '') + ' ' + (b.src || '')
        : (b.text || '');
      var cls = 'block ' + (b.type === 'heading' ? 'h' + (b.level || 2) : b.type);
      var tag = b.type === 'heading' ? ('H' + (b.level || 2)) : (TAG[b.type] || b.type);
      var on = !!state.sel[key];
      return '<label class="' + cls + (on ? ' sel' : '') + '" data-key="' + key + '">' +
        '<input type="checkbox" ' + (on ? 'checked' : '') + ' />' +
        '<span class="btag">' + esc(tag) + '</span>' +
        '<span class="btext">' + esc(text) + '</span></label>';
    }).join('');

    return '<div class="page"><div class="page-head">' +
      '<h2>' + esc(m.title || r.url) + '</h2>' +
      '<span class="meta">' + esc(meta) + ' · ' + (r.word_count || 0) + ' 字 · ' + (r.blocks || []).length + ' 段</span>' +
      '<div class="page-tools">' +
      '<button class="tiny" data-pg-all="' + gi + '">全选本页</button>' +
      '<button class="tiny" data-pg-none="' + gi + '">清空本页</button></div>' +
      '</div><div class="blocks">' + (blocks || '<div class="block"><span class="btext">没有解析到正文内容</span></div>') + '</div></div>';
  }

  function updateBar() {
    var n = Object.keys(state.sel).length;
    var chars = 0;
    state.results.forEach(function (r, gi) {
      (r.blocks || []).forEach(function (b, bi) {
        if (state.sel[gi + ':' + bi]) chars += (b.text || '').length;
      });
    });
    $('#bar-count').textContent = '已选 ' + n + ' 段';
    $('#bar-chars').textContent = '约 ' + chars + ' 字';
    $('#bar').classList.toggle('hidden', n === 0);
  }

  /* ---------- 导出 ---------- */
  function collect() {
    var groups = [];
    state.results.forEach(function (r, gi) {
      if (!r.ok) return;
      var blocks = (r.blocks || []).filter(function (b, bi) { return state.sel[gi + ':' + bi]; });
      if (!blocks.length) return;
      groups.push({
        title: (r.meta && r.meta.title) || r.url,
        url: r.final_url || r.url,
        blocks: blocks
      });
    });
    return groups;
  }

  function renderText() {
    var groups = collect();
    if (!groups.length) { toast('还没有选中任何段落'); return null; }
    var fmt = $('#fmt').value;
    var numbered = $('#opt-number').checked;
    var withHead = $('#opt-head').checked;
    var lines = [], idx = 0;
    groups.forEach(function (g) {
      var body = [];
      g.blocks.forEach(function (b) {
        var piece;
        if (fmt === 'md') {
          if (b.type === 'heading') piece = new Array(Math.min(b.level || 2, 6) + 1).join('#') + ' ' + b.text;
          else if (b.type === 'list') {
            piece = (b.items || [b.text]).map(function (t, i) {
              return b.ordered ? (i + 1) + '. ' + t : '- ' + t;
            }).join('\n');
          } else if (b.type === 'code') piece = '```\n' + b.text + '\n```';
          else if (b.type === 'quote') piece = b.text.split('\n').map(function (l) { return '> ' + l; }).join('\n');
          else if (b.type === 'image') piece = '![' + (b.text || '') + '](' + (b.src || '') + ')';
          else piece = b.text || '';
        } else {
          if (b.type === 'list') {
            piece = (b.items || [b.text]).map(function (t, i) {
              return b.ordered ? (i + 1) + '. ' + t : '- ' + t;
            }).join('\n');
          } else if (b.type === 'image') piece = '[图片] ' + (b.text || '') + ' ' + (b.src || '');
          else piece = b.text || '';
        }
        if (!piece || !piece.trim()) return;
        if (numbered) { idx++; piece = '[' + idx + '] ' + piece; }
        body.push(piece);
      });
      if (!body.length) return;
      if (withHead) {
        lines.push(fmt === 'md' ? '# ' + g.title : g.title);
        if (g.url) lines.push(fmt === 'md' ? '<' + g.url + '>' : g.url);
        lines.push('');
      }
      lines = lines.concat(body);
      lines.push('');
    });
    return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
  }

  function copySelected() {
    var text = renderText();
    if (text == null) return;
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); toast('已复制 ' + text.length + ' 字'); }
      catch (e) { toast('复制失败，请手动选择'); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { toast('已复制 ' + text.length + ' 字'); }, fallback);
    } else fallback();
  }

  function download() {
    var text = renderText();
    if (text == null) return;
    var blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'extracted-' + new Date().toISOString().slice(0, 10) + '.md';
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    toast('已下载');
  }
})();
