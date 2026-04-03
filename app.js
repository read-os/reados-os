/**
 * ReadOS — Main JavaScript Application
 * Handles all UI interactions, API communication, reader logic,
 * library management, archive search, and import flows.
 */

'use strict';

/* ═══════════════════════════════════════════════════════════════
   API CLIENT
   ═══════════════════════════════════════════════════════════════ */
const API = (() => {
  let _token = localStorage.getItem('reados_token') || '';

  const headers = (extra = {}) => ({
    'Content-Type': 'application/json',
    ...((_token) ? { 'Authorization': `Bearer ${_token}` } : {}),
    ...extra,
  });

  const request = async (method, path, body = null, extraHeaders = {}) => {
    const opts = {
      method,
      headers: headers(extraHeaders),
      credentials: 'include',
    };
    if (body && method !== 'GET') {
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(`/api${path}`, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw Object.assign(new Error(data.error || 'Request failed'), { status: resp.status, data });
    return data;
  };

  return {
    setToken(t) { _token = t; if (t) localStorage.setItem('reados_token', t); else localStorage.removeItem('reados_token'); },
    getToken() { return _token; },
    get:    (p)    => request('GET', p),
    post:   (p, b) => request('POST', p, b),
    put:    (p, b) => request('PUT', p, b),
    delete: (p)    => request('DELETE', p),
    upload: async (path, formData) => {
      const resp = await fetch(`/api${path}`, {
        method: 'POST',
        headers: _token ? { 'Authorization': `Bearer ${_token}` } : {},
        body: formData,
        credentials: 'include',
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw Object.assign(new Error(data.error || 'Upload failed'), { status: resp.status });
      return data;
    },
  };
})();


/* ═══════════════════════════════════════════════════════════════
   INTERNATIONALIZATION
   ═══════════════════════════════════════════════════════════════ */
const i18n = (() => {
  let _strings = {};
  let _lang = localStorage.getItem('reados_lang') || 'en';

  const load = async (lang) => {
    try {
      const data = await API.get(`/settings/i18n/${lang}`);
      _strings = data;
      _lang = lang;
      localStorage.setItem('reados_lang', lang);
      applyToDOM();
    } catch (e) {
      console.warn('i18n load failed:', e);
    }
  };

  const t = (key, vars = {}) => {
    let s = _strings[key] || key;
    for (const [k, v] of Object.entries(vars)) {
      s = s.replace(`{${k}}`, v);
    }
    return s;
  };

  const applyToDOM = () => {
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      el.textContent = t(key);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      el.placeholder = t(key);
    });
  };

  return { load, t, applyToDOM, getLang: () => _lang };
})();


/* ═══════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
   ═══════════════════════════════════════════════════════════════ */
const toast = {
  _container: null,
  init() { this._container = document.getElementById('toast-container'); },
  show(msg, type = '', duration = 3000) {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    this._container.appendChild(el);
    setTimeout(() => el.remove(), duration + 100);
  },
  success(msg) { this.show(msg, 'success'); },
  error(msg)   { this.show(msg, 'error'); },
};


/* ═══════════════════════════════════════════════════════════════
   APP CORE — Navigation & Init
   ═══════════════════════════════════════════════════════════════ */
const app = (() => {
  let _currentPage = 'library';

  const navigate = (page) => {
    // Hide all pages
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.classList.add('active');

    const navEl = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (navEl) navEl.classList.add('active');

    _currentPage = page;

    // Page-specific load
    if (page === 'library') library.load();
    if (page === 'settings') settingsUI.load();
    if (page === 'account') authUI.refresh();
    if (page === 'import') importUI.init();
  };

  const init = async () => {
    toast.init();

    // Load settings first (for theme/lang)
    try {
      const settings = await API.get('/settings/');
      settingsUI.applySettings(settings);
      await i18n.load(settings.language || 'en');
    } catch (_) {
      await i18n.load('en');
    }

    // Check auth
    const token = API.getToken();
    if (token) {
      try {
        const user = await API.get('/auth/me');
        authUI.setUser(user);
      } catch (_) {
        API.setToken('');
      }
    }

    // Load library
    await library.load();

    // Show app shell
    const splash = document.getElementById('splash');
    splash.classList.add('fade-out');
    setTimeout(() => {
      splash.remove();
      document.getElementById('shell').classList.remove('hidden');
    }, 400);

    // Load version and start update polling
    try {
      const v = await API.get('/settings/version');
      document.getElementById('app-version').textContent = v.version;
    } catch (_) {}

    // Start background update checker (polls GitHub every hour)
    updateChecker.startPolling();
  };

  return { navigate, init };
})();


/* ═══════════════════════════════════════════════════════════════
   LIBRARY
   ═══════════════════════════════════════════════════════════════ */
const library = (() => {
  let _books = [];
  let _filter = 'all';
  let _query = '';

  const load = async () => {
    try {
      const data = await API.get('/library/books');
      _books = data.books || [];
      render();
    } catch (e) {
      toast.error(i18n.t('error_network'));
    }
  };

  const scan = async () => {
    const btn = document.querySelector('[onclick="library.scan()"]');
    if (btn) btn.disabled = true;
    try {
      const data = await API.post('/library/scan');
      _books = data.books || [];
      render();
      toast.success(`${data.scanned} books found`);
    } catch (e) {
      toast.error(i18n.t('error_generic'));
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  const filter = (fmt) => {
    _filter = fmt;
    document.querySelectorAll('.filter-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.filter === fmt);
    });
    render();
  };

  const search = (q) => {
    _query = q.toLowerCase();
    render();
  };

  const render = () => {
    let books = [..._books];
    if (_filter !== 'all') books = books.filter(b => b.format === _filter);
    if (_query) books = books.filter(b =>
      b.title.toLowerCase().includes(_query) ||
      (b.author || '').toLowerCase().includes(_query)
    );

    const grid = document.getElementById('library-grid');
    const empty = document.getElementById('library-empty');

    if (books.length === 0) {
      grid.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }
    empty.classList.add('hidden');
    grid.innerHTML = books.map((b, i) => bookCard(b, i)).join('');
  };

  const bookCard = (book, index) => {
    const pct = book.percentage || 0;
    const delay = Math.min(index * 30, 600);
    const coverUrl = `/api/library/covers/${book.id}`;
    const fmtClass = `fmt-${book.format}`;

    return `
      <div class="book-card" onclick="library.openBook('${book.id}')" style="animation-delay:${delay}ms">
        <div class="book-cover-wrap">
          <img class="book-cover-img" src="${coverUrl}"
            onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
            alt="${escHtml(book.title)}" loading="lazy" />
          <div class="book-cover-placeholder" style="display:none">
            <span class="ph-title">${escHtml(book.title)}</span>
            <span class="ph-author">${escHtml(book.author || '')}</span>
            <span class="ph-fmt">${book.format.toUpperCase()}</span>
          </div>
          ${pct > 0 ? `
            <div class="book-progress-bar">
              <div class="book-progress-fill" style="width:${pct}%"></div>
            </div>` : ''}
        </div>
        <div class="book-meta">
          <div class="book-title-small">${escHtml(book.title)}</div>
          <div class="book-author-small">${escHtml(book.author || i18n.t('book_unknown_author'))}</div>
          <span class="book-format-badge ${fmtClass}">${book.format.toUpperCase()}</span>
        </div>
      </div>`;
  };

  const openBook = (bookId) => {
    reader.open(bookId);
  };

  return { load, scan, filter, search, openBook, render, getBooks: () => _books };
})();


/* ═══════════════════════════════════════════════════════════════
   READER
   ═══════════════════════════════════════════════════════════════ */
const reader = (() => {
  let _session = null;
  let _currentChapter = 0;
  let _currentPage = 0;
  let _fontSize = parseInt(localStorage.getItem('reader_font_size') || '18');
  let _theme = localStorage.getItem('reader_theme') || 'light';
  let _font = localStorage.getItem('reader_font') || 'Lora, Georgia, serif';
  let _saveTimer = null;

  const overlay    = () => document.getElementById('reader-overlay');
  const contentEl  = () => document.getElementById('reader-content');
  const progressFill = () => document.getElementById('reader-progress-fill');
  const posLabel   = () => document.getElementById('reader-position-label');
  const progressText = () => document.getElementById('reader-progress-text');

  const open = async (bookId) => {
    try {
      _session = await API.get(`/reader/open/${bookId}`);
      document.getElementById('reader-book-title').textContent = _session.book.title;
      overlay().classList.remove('hidden');
      document.body.style.overflow = 'hidden';

      applyDisplaySettings();
      buildTOC();

      // Restore progress
      const prog = _session.progress;
      if (prog) {
        _currentChapter = prog.chapter || 0;
        _currentPage = prog.chapter || 0;
      }

      await loadContent();
    } catch (e) {
      toast.error(i18n.t('error_generic'));
      console.error(e);
    }
  };

  const close = () => {
    _saveProgress();
    overlay().classList.add('hidden');
    document.body.style.overflow = '';
    // Close any open panels
    closeAllPanels();
    _session = null;
  };

  const loadContent = async () => {
    if (!_session) return;
    const fmt = _session.format;
    contentEl().innerHTML = `<div class="loading-state"><div class="spinner"></div></div>`;

    try {
      if (fmt === 'epub') {
        const ch = await API.get(`/library/books/${_session.book.id}/epub/chapter/${_currentChapter}`);
        contentEl().innerHTML = ch.content_html || '<p>Empty chapter</p>';
        // Remove any external CSS links that might break our styles
        contentEl().querySelectorAll('link[rel="stylesheet"]').forEach(l => l.remove());
        updateProgress(_currentChapter, _session.total_chapters);
      } else if (fmt === 'pdf') {
        const pg = await API.get(`/library/books/${_session.book.id}/pdf/page/${_currentPage}`);
        const total = pg.total_pages || _session.total_pages || 1;
        contentEl().innerHTML = `
          <div class="pdf-page-container">
            ${pg.image ? `<img class="pdf-page-img" src="${pg.image}" alt="Page ${_currentPage + 1}" />` : ''}
            ${!pg.image && pg.text ? `<div class="txt-content">${escHtml(pg.text)}</div>` : ''}
          </div>`;
        updateProgress(_currentPage, total);
      } else if (fmt === 'txt') {
        const chunk = await API.get(`/library/books/${_session.book.id}/txt/chunk/${_currentPage}`);
        contentEl().innerHTML = `<div class="txt-content">${escHtml(chunk.text)}</div>`;
        updateProgress(_currentPage, chunk.total);
      }

      // Scroll to top of content
      document.getElementById('reader-content-area').scrollTop = 0;
    } catch (e) {
      contentEl().innerHTML = `<p class="error-state">${i18n.t('error_generic')}</p>`;
      console.error(e);
    }
  };

  const updateProgress = (current, total) => {
    const pct = total > 1 ? Math.round((current / (total - 1)) * 100) : 100;
    progressFill().style.width = `${pct}%`;
    const fmt = _session?.format;
    const label = fmt === 'epub'
      ? i18n.t('reader_chapter', { n: current + 1, total })
      : i18n.t('reader_page', { n: current + 1, total });
    posLabel().textContent = label;
    progressText().textContent = `${pct}%`;

    // Update prev/next buttons
    document.getElementById('btn-prev').disabled = current <= 0;
    document.getElementById('btn-next').disabled = current >= total - 1;

    // Highlight TOC item
    document.querySelectorAll('.toc-item').forEach((el, i) => {
      el.classList.toggle('active', i === current);
    });
  };

  const prevPage = async () => {
    if (!_session) return;
    const pos = _session.format === 'epub' ? _currentChapter : _currentPage;
    if (pos <= 0) return;
    if (_session.format === 'epub') _currentChapter--;
    else _currentPage--;
    await loadContent();
    scheduleSave();
  };

  const nextPage = async () => {
    if (!_session) return;
    const total = _session.format === 'epub' ? _session.total_chapters : _session.total_pages;
    const pos = _session.format === 'epub' ? _currentChapter : _currentPage;
    if (pos >= total - 1) return;
    if (_session.format === 'epub') _currentChapter++;
    else _currentPage++;
    await loadContent();
    scheduleSave();
  };

  const jumpToChapter = async (index) => {
    if (_session.format === 'epub') _currentChapter = index;
    else _currentPage = index;
    await loadContent();
    scheduleSave();
    togglePanel('toc');
  };

  const buildTOC = () => {
    const list = document.getElementById('toc-list');
    if (!_session?.toc?.length) {
      list.innerHTML = '<p style="padding:12px;color:var(--text-muted);font-size:0.85rem">No table of contents</p>';
      return;
    }
    list.innerHTML = _session.toc.map((item, i) => `
      <div class="toc-item" onclick="reader.jumpToChapter(${i})">${escHtml(item.title || `Chapter ${i + 1}`)}</div>
    `).join('');
  };

  const handleContentClick = (e) => {
    // Left/right half click to navigate
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const w = rect.width;
    if (x < w * 0.2) prevPage();
    else if (x > w * 0.8) nextPage();
  };

  const togglePanel = (name) => {
    const panel = document.getElementById(`reader-panel-${name}`);
    const btn = document.querySelector(`[onclick="reader.togglePanel('${name}')"]`);
    const isHidden = panel.classList.contains('hidden');
    closeAllPanels();
    if (isHidden) {
      panel.classList.remove('hidden');
      if (btn) btn.classList.add('active');
    }
  };

  const closeAllPanels = () => {
    document.querySelectorAll('.reader-panel').forEach(p => p.classList.add('hidden'));
    document.querySelectorAll('.reader-topbar-actions .reader-btn').forEach(b => b.classList.remove('active'));
  };

  const toggleBookmark = async () => {
    if (!_session || !API.getToken()) {
      toast.show(i18n.t('auth_required'));
      return;
    }
    const pos = String(_session.format === 'epub' ? _currentChapter : _currentPage);
    try {
      await API.post('/reader/bookmarks', {
        book_id: _session.book.id,
        position: pos,
        chapter: _currentChapter,
      });
      const btn = document.getElementById('bookmark-btn');
      btn.style.color = 'var(--accent)';
      setTimeout(() => btn.style.color = '', 2000);
      toast.success('Bookmark added');
    } catch (e) {
      toast.error(i18n.t('error_generic'));
    }
  };

  // Display settings
  const applyDisplaySettings = () => {
    document.getElementById('reader-content').style.setProperty('--reader-font-size', `${_fontSize}px`);
    document.getElementById('reader-content').style.fontSize = `${_fontSize}px`;
    document.getElementById('reader-content').style.fontFamily = _font;
    document.getElementById('reader-font-size-val').textContent = _fontSize;
    document.getElementById('reader-overlay').setAttribute('data-theme', _theme);
  };

  const changeFontSize = (delta) => {
    _fontSize = Math.min(32, Math.max(12, _fontSize + delta));
    localStorage.setItem('reader_font_size', _fontSize);
    applyDisplaySettings();
  };

  const setTheme = (theme) => {
    _theme = theme;
    localStorage.setItem('reader_theme', theme);
    document.getElementById('reader-overlay').setAttribute('data-theme', theme);
  };

  const setFont = (font) => {
    _font = font;
    localStorage.setItem('reader_font', font);
    document.getElementById('reader-content').style.fontFamily = font;
  };

  // Progress saving
  const scheduleSave = () => {
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(_saveProgress, 2000);
  };

  const _saveProgress = async () => {
    if (!_session || !API.getToken()) return;
    const pos = String(_session.format === 'epub' ? _currentChapter : _currentPage);
    const total = _session.format === 'epub' ? _session.total_chapters : (_session.total_pages || 1);
    const current = _session.format === 'epub' ? _currentChapter : _currentPage;
    const pct = total > 1 ? (current / (total - 1)) * 100 : 100;
    try {
      await API.post('/reader/progress', {
        book_id: _session.book.id,
        position: pos,
        chapter: _currentChapter,
        percentage: Math.round(pct * 10) / 10,
      });
    } catch (_) {}
  };

  return {
    open, close, prevPage, nextPage, jumpToChapter,
    togglePanel, toggleBookmark, handleContentClick,
    changeFontSize, setTheme, setFont,
  };
})();


/* ═══════════════════════════════════════════════════════════════
   ANNA'S ARCHIVE
   ═══════════════════════════════════════════════════════════════ */
const archive = (() => {
  let _activeJobs = {};

  const search = async () => {
    const q = document.getElementById('archive-query').value.trim();
    if (!q) return;
    const ext = document.getElementById('archive-ext').value;
    const lang = document.getElementById('archive-lang').value;

    const resultsEl = document.getElementById('archive-results');
    const loadingEl = document.getElementById('archive-loading');
    const emptyEl   = document.getElementById('archive-empty');
    const errorEl   = document.getElementById('archive-error');

    resultsEl.classList.add('hidden');
    loadingEl.classList.remove('hidden');
    emptyEl.classList.add('hidden');
    errorEl.classList.add('hidden');

    try {
      const params = new URLSearchParams({ q, ext, lang, page: 1 });
      const data = await API.get(`/archive/search?${params}`);
      loadingEl.classList.add('hidden');

      if (!data.results?.length) {
        emptyEl.classList.remove('hidden');
        return;
      }

      resultsEl.innerHTML = data.results.map(renderArchiveCard).join('');
      resultsEl.classList.remove('hidden');
    } catch (e) {
      loadingEl.classList.add('hidden');
      errorEl.classList.remove('hidden');
      errorEl.textContent = i18n.t('archive_error');
    }
  };

  const renderArchiveCard = (book) => {
    const fmtClass = `fmt-${(book.format || '').toLowerCase()}`;
    const coverHtml = book.cover_url
      ? `<img class="archive-cover" src="${escHtml(book.cover_url)}" alt="" onerror="this.outerHTML='<div class=\\"archive-cover-ph\\">${escHtml(book.format || 'BK')}</div>'" />`
      : `<div class="archive-cover-ph">${escHtml(book.format || 'BK')}</div>`;

    return `
      <div class="archive-card">
        ${coverHtml}
        <div class="archive-info">
          <div class="archive-title">${escHtml(book.title || 'Unknown Title')}</div>
          <div class="archive-author">${escHtml(book.author || '')}</div>
          <div class="archive-badges">
            ${book.format ? `<span class="archive-badge ${fmtClass}">${escHtml(book.format)}</span>` : ''}
            ${book.size ? `<span class="archive-badge">${escHtml(book.size)}</span>` : ''}
          </div>
        </div>
        <div class="archive-actions">
          <button class="btn btn-primary" onclick="archive.download('${escHtml(book.md5)}','${escHtml(book.title || book.md5)}.${(book.format||'epub').toLowerCase()}',this)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            <span data-i18n="archive_download">${i18n.t('archive_download')}</span>
          </button>
        </div>
      </div>`;
  };

  const download = async (md5, filename, btn) => {
    if (!md5) return;
    btn.disabled = true;
    btn.innerHTML = `<div class="spinner" style="width:16px;height:16px;border-width:2px"></div> ${i18n.t('archive_downloading')}`;

    // Show downloads panel
    const panel = document.getElementById('archive-downloads');
    panel.classList.remove('hidden');

    const jobEl = document.createElement('div');
    jobEl.className = 'download-progress';
    jobEl.id = `dl-${md5}`;
    jobEl.innerHTML = `
      <div><strong>${escHtml(filename)}</strong></div>
      <div id="dl-status-${md5}" style="color:var(--text-muted);font-size:0.75rem">Queued...</div>
      <div class="dl-bar"><div class="dl-bar-fill" id="dl-bar-${md5}" style="width:0%"></div></div>`;
    document.getElementById('download-list').appendChild(jobEl);

    try {
      const res = await API.post('/archive/download', { md5, filename });
      const jobId = res.job_id;
      _pollDownload(jobId, md5, filename, btn);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = i18n.t('archive_download');
      toast.error(i18n.t('error_generic'));
    }
  };

  const _pollDownload = (jobId, md5, filename, btn) => {
    const interval = setInterval(async () => {
      try {
        const status = await API.get(`/archive/download/${jobId}`);
        const statusEl = document.getElementById(`dl-status-${md5}`);
        const barEl = document.getElementById(`dl-bar-${md5}`);

        if (statusEl) statusEl.textContent = `${status.status} — ${status.progress || 0}%`;
        if (barEl) barEl.style.width = `${status.progress || 0}%`;

        if (status.status === 'complete') {
          clearInterval(interval);
          toast.success(`"${filename}" downloaded!`);
          btn.disabled = false;
          btn.textContent = '✓ Downloaded';
          btn.style.background = 'var(--success)';
          // Reload library
          library.load();
          // Remove after a bit
          setTimeout(() => document.getElementById(`dl-${md5}`)?.remove(), 5000);
        } else if (status.status === 'failed') {
          clearInterval(interval);
          toast.error(`Download failed: ${status.error || 'Unknown error'}`);
          btn.disabled = false;
          btn.textContent = i18n.t('archive_download');
          document.getElementById(`dl-${md5}`)?.remove();
        }
      } catch (_) { clearInterval(interval); }
    }, 1200);
  };

  return { search, download };
})();


/* ═══════════════════════════════════════════════════════════════
   IMPORT UI
   ═══════════════════════════════════════════════════════════════ */
const importUI = (() => {
  let _gdriveToken = null;

  const init = () => {
    // Load email import address if logged in
    if (API.getToken()) {
      API.get('/import/email-address')
        .then(d => {
          document.getElementById('import-email-addr').textContent = d.email;
        })
        .catch(() => {});
    }
  };

  const showTab = (tab) => {
    document.querySelectorAll('.import-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
    document.querySelectorAll('.import-panel').forEach(p => {
      p.classList.toggle('active', p.id === `import-tab-${tab}`);
    });
  };

  const onDragOver = (e) => {
    e.preventDefault();
    document.getElementById('drop-zone').classList.add('drag-over');
  };
  const onDragLeave = () => {
    document.getElementById('drop-zone').classList.remove('drag-over');
  };
  const onDrop = (e) => {
    e.preventDefault();
    onDragLeave();
    const files = Array.from(e.dataTransfer?.files || []);
    uploadFiles(files);
  };
  const onFileSelect = (e) => {
    uploadFiles(Array.from(e.target.files || []));
    e.target.value = '';
  };

  const uploadFiles = async (files) => {
    if (!files.length) return;
    const list = document.getElementById('import-progress-list');
    for (const file of files) {
      const item = document.createElement('div');
      item.className = 'import-item';
      item.innerHTML = `<span class="import-item-name">${escHtml(file.name)}</span><span class="import-item-status">Uploading...</span>`;
      list.appendChild(item);

      const fd = new FormData();
      fd.append('file', file);
      try {
        await API.upload('/import/upload', fd);
        item.classList.add('success');
        item.querySelector('.import-item-status').textContent = '✓ Imported';
        toast.success(i18n.t('import_success'));
        library.load();
      } catch (e) {
        item.classList.add('error');
        item.querySelector('.import-item-status').textContent = `✗ ${e.message}`;
        toast.error(i18n.t('import_error', { error: e.message }));
      }
    }
  };

  const connectGDrive = async () => {
    try {
      const { url } = await API.get('/import/gdrive/auth-url');
      window.open(url, '_blank', 'width=600,height=600');
      // Listen for message from popup
      window.addEventListener('message', (evt) => {
        if (evt.data?.reados_gdrive_token) {
          _gdriveToken = evt.data.reados_gdrive_token;
          loadGDriveFiles();
        }
      }, { once: true });
    } catch (e) {
      toast.error('Google Drive not configured');
    }
  };

  const loadGDriveFiles = async () => {
    if (!_gdriveToken) return;
    try {
      const data = await fetch('/api/import/gdrive/files', {
        headers: { 'X-GDrive-Token': _gdriveToken }
      }).then(r => r.json());

      document.getElementById('gdrive-connect').classList.add('hidden');
      const filesEl = document.getElementById('gdrive-files');
      filesEl.classList.remove('hidden');
      filesEl.innerHTML = (data.files || []).map(f => `
        <div class="gdrive-file">
          <span class="gdrive-file-name">${escHtml(f.name)}</span>
          <span class="gdrive-file-size">${formatBytes(f.size)}</span>
          <span class="archive-badge">${escHtml(f.format || '')}</span>
          <button class="btn btn-secondary" onclick="importUI.importGDriveFile('${f.gdrive_id}','${escHtml(f.name)}',this)">Import</button>
        </div>`).join('');
    } catch (e) {
      toast.error(i18n.t('error_network'));
    }
  };

  const importGDriveFile = async (fileId, filename, btn) => {
    btn.disabled = true;
    btn.textContent = 'Importing...';
    try {
      await API.post('/import/gdrive/download', {
        file_id: fileId,
        filename,
        access_token: _gdriveToken,
      });
      btn.textContent = '✓ Imported';
      toast.success(i18n.t('import_success'));
      library.load();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = 'Import';
      toast.error(i18n.t('import_error', { error: e.message }));
    }
  };

  const copyEmail = () => {
    const addr = document.getElementById('import-email-addr').textContent;
    navigator.clipboard?.writeText(addr).then(() => toast.show('Copied!')).catch(() => {});
  };

  return { init, showTab, onDragOver, onDragLeave, onDrop, onFileSelect, connectGDrive, importGDriveFile, copyEmail };
})();


/* ═══════════════════════════════════════════════════════════════
   SETTINGS UI
   ═══════════════════════════════════════════════════════════════ */
const settingsUI = (() => {
  let _settings = {};

  const load = async () => {
    try {
      // Load languages
      const langs = await API.get('/settings/languages');
      const sel = document.getElementById('lang-select');
      sel.innerHTML = Object.entries(langs).map(([code, name]) =>
        `<option value="${code}">${name}</option>`
      ).join('');
      sel.value = i18n.getLang();

      // Load sources
      const { sources } = await API.get('/archive/sources');
      document.getElementById('archive-sources-input').value = sources.join('\n');

      // Load API key status
      try {
        const ks = await API.get('/archive/api-key-status');
        const statusEl = document.getElementById('api-key-status');
        if (ks.configured) {
          statusEl.textContent = '✓ API key configured — fast downloads enabled';
          statusEl.className = 'api-key-status ok';
        } else {
          statusEl.textContent = '✗ No API key — slow fallback downloads only';
          statusEl.className = 'api-key-status none';
        }
      } catch (_) {}

      // Load settings
      const s = await API.get('/settings/');
      applySettings(s);
    } catch (e) {
      console.warn('Settings load error:', e);
    }
  };

  const applySettings = (s) => {
    _settings = s;
    if (s.theme) {
      setTheme(s.theme, false);
    }
    if (s.font_size) {
      document.getElementById('font-size-display').textContent = s.font_size;
      document.documentElement.style.setProperty('--reader-font-size', `${s.font_size}px`);
    }
  };

  const setTheme = (theme, save = true) => {
    document.documentElement.setAttribute('data-theme', theme);
    document.querySelectorAll('.theme-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.theme === theme);
    });
    _settings.theme = theme;
    if (save) _save();
  };

  const setLanguage = async (lang) => {
    await i18n.load(lang);
    _settings.language = lang;
    _save();
  };

  const changeFontSize = (delta) => {
    const current = parseInt(_settings.font_size || 18);
    const next = Math.min(32, Math.max(12, current + delta));
    _settings.font_size = next;
    document.getElementById('font-size-display').textContent = next;
    _save();
  };

  const syncNow = async () => {
    const btn = document.getElementById('sync-now-btn');
    btn.disabled = true;
    try {
      const res = await API.post('/cloud/sync');
      toast.success(`Synced: ${res.synced} items`);
      document.getElementById('sync-last').textContent = `Last sync: just now`;
    } catch (_) {
      toast.error('Sync failed');
    } finally {
      btn.disabled = false;
    }
  };

  const saveSources = async () => {
    const raw = document.getElementById('archive-sources-input').value;
    const sources = raw.split('\n').map(s => s.trim()).filter(Boolean);
    try {
      await API.put('/archive/sources', { sources });
      toast.success('Sources updated');
    } catch (e) {
      toast.error(i18n.t('error_generic'));
    }
  };

  const saveApiKey = async () => {
    const key = document.getElementById('archive-api-key-input').value.trim();
    try {
      await API.put('/archive/api-key', { key });
      const statusEl = document.getElementById('api-key-status');
      if (key) {
        statusEl.textContent = '✓ API key saved — fast downloads enabled';
        statusEl.className = 'api-key-status ok';
        toast.success('API key saved');
      } else {
        statusEl.textContent = '✗ API key cleared';
        statusEl.className = 'api-key-status none';
        toast.show('API key cleared');
      }
      document.getElementById('archive-api-key-input').value = '';
    } catch (e) {
      toast.error(i18n.t('error_generic'));
    }
  };

  const _save = async () => {
    try {
      await API.put('/settings/', _settings);
    } catch (_) {}
  };

  return { load, applySettings, setTheme, setLanguage, changeFontSize, syncNow, saveSources, saveApiKey };
})();


/* ═══════════════════════════════════════════════════════════════
   AUTH UI
   ═══════════════════════════════════════════════════════════════ */
const authUI = (() => {
  let _user = null;

  const setUser = (user) => {
    _user = user;
    API.setToken(user?.token || API.getToken());
    _updateUserBadge();
  };

  const _updateUserBadge = () => {
    if (_user) {
      const initial = (_user.username || _user.email || '?')[0].toUpperCase();
      document.getElementById('user-avatar').textContent = initial;
      document.getElementById('user-name').textContent = _user.username || _user.email;
      document.getElementById('user-email').textContent = _user.email;
      document.getElementById('profile-avatar').textContent = initial;
      document.getElementById('profile-name').textContent = _user.username || '';
      document.getElementById('profile-email').textContent = _user.email || '';
    } else {
      document.getElementById('user-avatar').textContent = '?';
      document.getElementById('user-name').textContent = i18n.t('auth_login');
      document.getElementById('user-email').textContent = '';
    }
  };

  const refresh = () => {
    if (_user) {
      document.getElementById('auth-logged-out').classList.add('hidden');
      document.getElementById('auth-logged-in').classList.remove('hidden');
    } else {
      document.getElementById('auth-logged-out').classList.remove('hidden');
      document.getElementById('auth-logged-in').classList.add('hidden');
    }
    _updateUserBadge();
  };

  const showTab = (tab) => {
    document.querySelectorAll('.auth-tab').forEach(t => {
      t.classList.toggle('active', t.textContent.toLowerCase().includes(tab === 'login' ? 'login' : 'register'));
    });
    document.getElementById('auth-tab-login').classList.toggle('active', tab === 'login');
    document.getElementById('auth-tab-register').classList.toggle('active', tab === 'register');
  };

  const login = async () => {
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    errEl.classList.add('hidden');
    try {
      const user = await API.post('/auth/login', { email, password });
      setUser(user);
      refresh();
      toast.success(`Welcome, ${user.username}!`);
      app.navigate('library');
    } catch (e) {
      errEl.textContent = e.data?.error || i18n.t('auth_login_error');
      errEl.classList.remove('hidden');
    }
  };

  const register = async () => {
    const username = document.getElementById('reg-username').value.trim();
    const email = document.getElementById('reg-email').value.trim();
    const password = document.getElementById('reg-password').value;
    const errEl = document.getElementById('reg-error');
    errEl.classList.add('hidden');
    try {
      const user = await API.post('/auth/register', { username, email, password });
      setUser(user);
      refresh();
      toast.success(`Welcome to ReadOS, ${user.username}!`);
      app.navigate('library');
    } catch (e) {
      errEl.textContent = e.data?.error || i18n.t('auth_register_error', { error: '' });
      errEl.classList.remove('hidden');
    }
  };

  const logout = async () => {
    try { await API.post('/auth/logout'); } catch (_) {}
    API.setToken('');
    _user = null;
    refresh();
    toast.show('Logged out');
    app.navigate('library');
  };

  const changePassword = async () => {
    const oldPw = document.getElementById('old-password').value;
    const newPw = document.getElementById('new-password').value;
    const errEl = document.getElementById('pw-error');
    const succEl = document.getElementById('pw-success');
    errEl.classList.add('hidden');
    succEl.classList.add('hidden');
    try {
      await API.post('/auth/change-password', { current_password: oldPw, new_password: newPw });
      succEl.classList.remove('hidden');
      document.getElementById('old-password').value = '';
      document.getElementById('new-password').value = '';
    } catch (e) {
      errEl.textContent = e.data?.error || i18n.t('error_generic');
      errEl.classList.remove('hidden');
    }
  };

  return { setUser, refresh, showTab, login, register, logout, changePassword };
})();


/* ═══════════════════════════════════════════════════════════════
   UTILITIES
   ═══════════════════════════════════════════════════════════════ */
const escHtml = (str) => {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

const formatBytes = (bytes) => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};


/* ═══════════════════════════════════════════════════════════════
   UPDATE CHECKER
   Polls /api/settings/version-check (backed by GitHub API).
   Shows a dismissible popup when a newer version is published
   to github.com/read-os/reados-os.
   ═══════════════════════════════════════════════════════════════ */
const updateChecker = (() => {
  const DISMISSED_KEY = 'reados_dismissed_update';
  const CHECK_INTERVAL_MS = 60 * 60 * 1000; // re-check every hour

  // Inject popup markup once
  const _inject = () => {
    if (document.getElementById('update-popup')) return;
    const el = document.createElement('div');
    el.id = 'update-popup';
    el.innerHTML = `
      <div class="update-popup-inner">
        <div class="update-popup-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22">
            <polyline points="23 4 23 10 17 10"/>
            <polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
        </div>
        <div class="update-popup-body">
          <div class="update-popup-title">Update available</div>
          <div class="update-popup-sub" id="update-popup-sub">A new version of ReadOS is ready.</div>
          <div class="update-popup-meta" id="update-popup-meta"></div>
        </div>
        <div class="update-popup-actions">
          <a id="update-popup-link" href="https://github.com/read-os/reados-os/releases/latest" target="_blank" class="update-popup-btn update-popup-btn-primary">View release</a>
          <button class="update-popup-btn update-popup-btn-secondary" onclick="updateChecker.dismiss()">Later</button>
        </div>
        <button class="update-popup-close" onclick="updateChecker.dismiss()" title="Dismiss">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="16" height="16">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>`;
    document.body.appendChild(el);
  };

  const _show = (data) => {
    _inject();
    const popup = document.getElementById('update-popup');
    const sub   = document.getElementById('update-popup-sub');
    const meta  = document.getElementById('update-popup-meta');
    const link  = document.getElementById('update-popup-link');

    const current = data.current_version || '';
    const latest  = data.latest_version  || data.latest_sha || '';
    sub.textContent = latest
      ? `Version ${latest} is available (you have ${current}).`
      : `A new version is available on GitHub.`;

    if (data.latest_message) {
      meta.textContent = `"${data.latest_message}"`;
      meta.style.display = 'block';
    } else {
      meta.style.display = 'none';
    }

    if (data.release_url) link.href = data.release_url;

    popup.classList.add('visible');
    // Auto-dismiss after 20 seconds if user doesn't interact
    setTimeout(() => { if (popup.classList.contains('visible')) dismiss(); }, 20000);
  };

  const dismiss = () => {
    const popup = document.getElementById('update-popup');
    if (popup) popup.classList.remove('visible');
    // Remember dismissed version so we don't nag on every page load
    const meta = document.getElementById('update-popup-meta');
    const ver  = (document.getElementById('update-popup-sub')?.textContent || '').match(/Version ([\d.]+)/)?.[1];
    if (ver) localStorage.setItem(DISMISSED_KEY, ver);
  };

  const check = async (force = false) => {
    try {
      const data = await API.get('/settings/version-check');
      if (!data.update_available) return;

      // Don't re-show for a version the user already dismissed
      const dismissed = localStorage.getItem(DISMISSED_KEY);
      if (!force && dismissed && dismissed === data.latest_version) return;

      _show(data);
    } catch (_) {
      // Version check failure is silent — network might be offline
    }
  };

  const startPolling = () => {
    // First check after 8 seconds (let app finish loading)
    setTimeout(() => check(), 8000);
    // Then every hour
    setInterval(() => check(), CHECK_INTERVAL_MS);
  };

  return { check, dismiss, startPolling };
})();

/* ── Keyboard shortcuts ────────────────────────────────────────── */
document.addEventListener('keydown', (e) => {
  const readerOpen = !document.getElementById('reader-overlay').classList.contains('hidden');
  if (readerOpen) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); reader.nextPage(); }
    if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   { e.preventDefault(); reader.prevPage(); }
    if (e.key === 'Escape') reader.close();
  }
});

/* ── Init ──────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => app.init());
