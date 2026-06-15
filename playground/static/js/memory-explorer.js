/* ============================================================================
   MemWal Memory Explorer Panel
   ============================================================================ */

var MemoryExplorer = (function () {
  'use strict';

  function init() {
    var searchBtn = document.getElementById('memory-search-btn');
    var searchInput = document.getElementById('memory-search-input');

    if (searchBtn) {
      searchBtn.addEventListener('click', function () {
        doLookup();
      });
    }

    if (searchInput) {
      searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          doLookup();
        }
      });
    }
  }

  function doLookup() {
    var input = document.getElementById('memory-search-input');
    var resultsContainer = document.getElementById('memory-results');
    if (!input || !resultsContainer) return;

    var threadId = input.value.trim();
    if (!threadId) {
      MemwalAPI.log('warn', 'Please enter a thread_id');
      return;
    }

    resultsContainer.innerHTML = '<div class="empty-state"><div class="spinner spinner--lg"></div><p class="text-sm text-muted" style="margin-top: var(--sp-4);">Looking up thread on Sui...</p></div>';

    // First lookup the blob_id
    MemwalAPI.lookupThread(threadId).then(function (lookupResult) {
      if (!lookupResult.ok) {
        renderError(resultsContainer, 'Lookup failed', lookupResult.data);
        return;
      }

      var blobId = lookupResult.data.blob_id;
      if (!blobId) {
        renderNotFound(resultsContainer, threadId);
        return;
      }

      // Now fetch the blob data
      MemwalAPI.fetchBlob(blobId).then(function (fetchResult) {
        renderTimeline(resultsContainer, threadId, blobId, lookupResult, fetchResult);
      });
    });
  }

  function renderNotFound(container, threadId) {
    container.innerHTML =
      '<div class="empty-state">' +
      '  <div class="empty-state__icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg></div>' +
      '  <h3 class="empty-state__title">No checkpoint found</h3>' +
      '  <p class="empty-state__desc">Thread <code>' + escapeHtml(threadId) + '</code> has no checkpoint registered on-chain.</p>' +
      '</div>';
  }

  function renderError(container, title, data) {
    container.innerHTML =
      '<div class="empty-state">' +
      '  <div class="empty-state__icon" style="color: var(--status-error);"><svg viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></div>' +
      '  <h3 class="empty-state__title">' + escapeHtml(title) + '</h3>' +
      '  <p class="empty-state__desc" style="font-family: var(--font-mono); font-size: var(--text-xs);">' + escapeHtml(JSON.stringify(data)) + '</p>' +
      '</div>';
  }

  function renderTimeline(container, threadId, blobId, lookupResult, fetchResult) {
    var html = '<div class="memory-explorer__timeline">';

    // Registry entry
    html += '<div class="memory-explorer__entry">';
    html += '  <div class="memory-explorer__entry-header">';
    html += '    <span class="memory-explorer__entry-thread">' + escapeHtml(threadId) + '</span>';
    html += '    <span class="badge badge--success">on-chain</span>';
    html += '  </div>';
    html += '  <div class="memory-explorer__entry-meta">';
    html += '    <span class="memory-explorer__entry-key">blob_id</span>';
    html += '    <span class="memory-explorer__entry-val">' + escapeHtml(blobId) + '</span>';
    html += '    <span class="memory-explorer__entry-key">source</span>';
    html += '    <span class="memory-explorer__entry-val">sui registry</span>';
    html += '    <span class="memory-explorer__entry-key">lookup_time</span>';
    html += '    <span class="memory-explorer__entry-val">' + (lookupResult.elapsed || '—') + 'ms</span>';
    html += '  </div>';
    html += '</div>';

    // Blob data entry
    html += '<div class="memory-explorer__entry">';
    html += '  <div class="memory-explorer__entry-header">';
    html += '    <span class="memory-explorer__entry-thread">Blob Data</span>';
    if (fetchResult.ok) {
      html += '    <span class="badge badge--info">walrus</span>';
    } else {
      html += '    <span class="badge badge--error">fetch failed</span>';
    }
    html += '  </div>';

    if (fetchResult.ok) {
      html += '  <div class="memory-explorer__entry-meta">';
      html += '    <span class="memory-explorer__entry-key">fetch_time</span>';
      html += '    <span class="memory-explorer__entry-val">' + (fetchResult.elapsed || '—') + 'ms</span>';
      html += '  </div>';
      html += '  <pre class="api-tester__response-body" style="margin-top: var(--sp-3); max-height: 300px;">' + MemwalAPI.highlightJSON(fetchResult.data) + '</pre>';
    } else {
      html += '  <p class="text-sm text-tertiary" style="margin-top: var(--sp-2);">Could not fetch blob data from Walrus.</p>';
    }

    html += '</div>';

    // Explorer links
    html += '<div style="margin-top: var(--sp-4); display: flex; gap: var(--sp-3);">';
    html += '  <a href="https://suiscan.xyz/testnet/object/' + escapeHtml(blobId) + '" target="_blank" class="btn btn--ghost btn--sm">View on SuiScan ↗</a>';
    html += '  <a href="https://aggregator.walrus-testnet.walrus.space/v1/blobs/' + encodeURIComponent(blobId) + '" target="_blank" class="btn btn--ghost btn--sm">View on Walrus ↗</a>';
    html += '</div>';

    html += '</div>';
    container.innerHTML = html;
  }

  function escapeHtml(str) {
    if (typeof str !== 'string') str = String(str);
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  return {
    init: init,
    doLookup: doLookup
  };
})();
