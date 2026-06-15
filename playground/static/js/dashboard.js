/* ============================================================================
   MemWal Dashboard — Main orchestrator
   Wires up sidebar navigation, inspector tabs, logging, and state updates.
   ============================================================================ */

var Dashboard = (function () {
  'use strict';

  // ── Panel switching ────────────────────────────────────────────────────

  function switchPanel(panelId) {
    // Update sidebar
    document.querySelectorAll('.sidebar__item').forEach(function (item) {
      item.classList.toggle('is-active', item.dataset.panel === panelId);
    });

    // Update panels
    document.querySelectorAll('.panel').forEach(function (panel) {
      panel.classList.toggle('is-active', panel.id === 'panel-' + panelId);
    });

    // Update header page title
    var headerTitle = document.getElementById('header-page-title');
    if (headerTitle) {
      var labels = {
        playground: 'playground',
        memory: 'memory explorer',
        files: 'file explorer',
        api: 'API reference',
        examples: 'examples',
        logs: 'logs',
        settings: 'settings'
      };
      headerTitle.textContent = labels[panelId] || panelId;
    }
  }

  // ── Inspector tabs ─────────────────────────────────────────────────────

  function switchInspector(tabId) {
    document.querySelectorAll('.inspector__tab').forEach(function (tab) {
      tab.classList.toggle('is-active', tab.dataset.inspector === tabId);
    });
    document.querySelectorAll('.inspector__panel').forEach(function (panel) {
      panel.classList.toggle('is-active', panel.id === 'inspector-' + tabId);
    });
  }

  // ── Logging ────────────────────────────────────────────────────────────

  function addLogEntry(entry) {
    var logsList = document.getElementById('logs-list');
    if (!logsList) return;

    var levelClass = {
      info: 'log-entry__level--info',
      success: 'log-entry__level--success',
      warn: 'log-entry__level--warn',
      error: 'log-entry__level--error'
    };

    var div = document.createElement('div');
    div.className = 'log-entry';
    div.innerHTML =
      '<span class="log-entry__time">' + entry.time + '</span>' +
      '<span class="log-entry__level ' + (levelClass[entry.level] || '') + '">' + entry.level.toUpperCase() + '</span>' +
      '<span class="log-entry__msg">' + escapeHtml(entry.msg) + '</span>';

    logsList.appendChild(div);
    logsList.scrollTop = logsList.scrollHeight;
  }

  // ── State updates (inspector panel) ────────────────────────────────────

  function updateInspector(state) {
    // Connection status
    var connected = state.connected;
    setConnectionUI(connected);

    // Session panel
    setText('insp-conn-status', connected ? 'connected' : 'disconnected');
    setText('insp-thread-id', state.lastThreadId || '—');
    setText('insp-blob-id', state.lastBlobId ? truncate(state.lastBlobId, 24) : '—');
    setText('insp-checkpoint-id', state.lastCheckpointId ? truncate(state.lastCheckpointId, 24) : '—');
    setText('insp-req-count', String(state.requestCount));
    setText('insp-blobs-stored', String(state.blobsStored));
    setText('insp-tx-count', String(state.txCount));

    // Metadata panel — last response
    var metaEl = document.getElementById('insp-last-response');
    if (metaEl && state.lastResponse) {
      metaEl.innerHTML = MemwalAPI.highlightJSON(state.lastResponse.data);
    }

    // Timing panel
    if (state.lastResponse) {
      setText('insp-timing-total', state.lastResponse.elapsed + 'ms');
      setText('insp-timing-ts', new Date().toLocaleTimeString('en-GB', { hour12: false }));
    }

    if (state.timingHistory.length > 0) {
      setText('insp-timing-endpoint', state.timingHistory[0].method + ' ' + state.timingHistory[0].endpoint);
    }

    // Timing history
    var histEl = document.getElementById('insp-timing-history');
    if (histEl && state.timingHistory.length > 0) {
      var html = '';
      state.timingHistory.slice(0, 10).forEach(function (entry) {
        var color = entry.status >= 200 && entry.status < 300 ? 'var(--status-success)' : 'var(--status-error)';
        html += '<div style="display: flex; gap: var(--sp-2); padding: 2px 0; border-bottom: 1px solid var(--border-subtle);">';
        html += '  <span style="color: var(--text-muted); width: 55px;">' + entry.time + '</span>';
        html += '  <span style="color: ' + color + '; width: 32px;">' + entry.status + '</span>';
        html += '  <span style="color: var(--text-secondary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">' + entry.method + ' ' + entry.endpoint + '</span>';
        html += '  <span style="color: var(--text-muted); width: 50px; text-align: right;">' + entry.elapsed + 'ms</span>';
        html += '</div>';
      });
      histEl.innerHTML = html;
    }
  }

  function setConnectionUI(connected) {
    // Header dot
    var statusDot = document.getElementById('status-dot');
    var statusText = document.getElementById('status-text');
    if (statusDot) {
      statusDot.className = 'dot ' + (connected ? 'dot--success' : 'dot--error');
    }
    if (statusText) {
      statusText.textContent = connected ? 'connected' : 'disconnected';
    }

    // Sidebar dot
    var sidebarDot = document.getElementById('sidebar-status-dot');
    var sidebarText = document.getElementById('sidebar-status-text');
    if (sidebarDot) {
      sidebarDot.style.background = connected ? 'var(--status-success)' : 'var(--status-error)';
      sidebarDot.style.boxShadow = connected
        ? '0 0 6px rgba(52,211,153,0.4)'
        : '0 0 6px rgba(248,113,113,0.4)';
    }
    if (sidebarText) {
      sidebarText.textContent = connected ? 'online' : 'offline';
    }
  }

  // ── Config population ──────────────────────────────────────────────────

  function populateConfig(config) {
    if (!config) return;
    setInputVal('settings-publisher', config.WALRUS_PUBLISHER || '');
    setInputVal('settings-aggregator', config.WALRUS_AGGREGATOR || '');
    setInputVal('settings-rpc', config.SUI_RPC_URL || '');
    setInputVal('settings-package', config.REGISTRY_PACKAGE_ID || '');
    setInputVal('settings-registry', config.REGISTRY_OBJECT_ID || '');
  }

  // ── Helpers ────────────────────────────────────────────────────────────

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function setInputVal(id, val) {
    var el = document.getElementById(id);
    if (el) el.value = val;
  }

  function truncate(str, len) {
    if (str.length <= len) return str;
    return str.substring(0, len) + '...';
  }

  function escapeHtml(str) {
    if (typeof str !== 'string') str = String(str);
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Init ───────────────────────────────────────────────────────────────

  function init() {
    // Sidebar navigation
    document.querySelectorAll('.sidebar__item').forEach(function (item) {
      item.addEventListener('click', function () {
        switchPanel(this.dataset.panel);
      });
    });

    // Inspector tabs
    document.querySelectorAll('.inspector__tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        switchInspector(this.dataset.inspector);
      });
    });

    // Clear logs button
    var clearLogsBtn = document.getElementById('clear-logs-btn');
    if (clearLogsBtn) {
      clearLogsBtn.addEventListener('click', function () {
        var logsList = document.getElementById('logs-list');
        if (logsList) logsList.innerHTML = '';
        MemwalAPI.log('info', 'Logs cleared');
      });
    }

    // Wire up API logging
    MemwalAPI.onLog(addLogEntry);

    // Wire up state changes
    MemwalAPI.onStateChange(updateInspector);

    // Init sub-modules
    ApiTester.init();
    MemoryExplorer.init();
    FileExplorer.init();
    Templates.init();

    // Health check + config
    MemwalAPI.log('info', 'Initialising playground...');
    MemwalAPI.healthCheck().then(function (result) {
      if (result.ok) {
        MemwalAPI.log('success', 'Connected to bridge server');
        MemwalAPI.getConfig().then(function (cfgResult) {
          if (cfgResult.ok) {
            populateConfig(cfgResult.data);
            MemwalAPI.log('info', 'Config loaded: ' +
              (cfgResult.data.SUI_RPC_URL || 'unknown') + ' | ' +
              (cfgResult.data.REGISTRY_PACKAGE_ID ? 'Registry: ' + cfgResult.data.REGISTRY_PACKAGE_ID.substring(0, 12) + '...' : 'No registry'));
          }
        });
      } else {
        MemwalAPI.log('warn', 'Bridge server not available. Start with: python playground/server.py');
      }
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', function (e) {
      if (e.ctrlKey || e.metaKey) {
        switch (e.key) {
          case '1': e.preventDefault(); switchPanel('playground'); break;
          case '2': e.preventDefault(); switchPanel('memory'); break;
          case '3': e.preventDefault(); switchPanel('files'); break;
          case '4': e.preventDefault(); switchPanel('api'); break;
          case '5': e.preventDefault(); switchPanel('examples'); break;
        }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    switchPanel: switchPanel
  };
})();
