/* ============================================================================
   MemWal API Tester Panel
   ============================================================================ */

var ApiTester = (function () {
  'use strict';

  // ── Endpoint definitions ───────────────────────────────────────────────

  var ENDPOINTS = {
    'store-blob': {
      method: 'POST',
      url: '/api/blob/store',
      description: 'Store data as a Walrus blob',
      params: [
        { key: 'data', label: 'Data (text)', type: 'textarea', placeholder: 'Enter text data to store...', defaultValue: 'Hello from MemWal playground!' },
        { key: 'epochs', label: 'Storage Epochs', type: 'number', placeholder: '5', defaultValue: '5' }
      ],
      execute: function (params) {
        return MemwalAPI.storeBlob(params.data, parseInt(params.epochs) || 5);
      }
    },
    'fetch-blob': {
      method: 'GET',
      url: '/api/blob/{blob_id}',
      description: 'Fetch a stored blob by ID',
      params: [
        { key: 'blob_id', label: 'Blob ID', type: 'text', placeholder: 'Enter blob_id...', defaultValue: '' }
      ],
      execute: function (params) {
        return MemwalAPI.fetchBlob(params.blob_id);
      }
    },
    'register': {
      method: 'POST',
      url: '/api/registry/register',
      description: 'Register thread→blob mapping on-chain',
      params: [
        { key: 'thread_id', label: 'Thread ID', type: 'text', placeholder: 'e.g. my-agent-thread-001', defaultValue: 'playground-test-thread' },
        { key: 'blob_id', label: 'Blob ID', type: 'text', placeholder: 'Walrus blob_id to register', defaultValue: '' }
      ],
      execute: function (params) {
        return MemwalAPI.registerThread(params.thread_id, params.blob_id);
      }
    },
    'lookup': {
      method: 'GET',
      url: '/api/registry/lookup/{thread_id}',
      description: 'Look up blob_id for a thread',
      params: [
        { key: 'thread_id', label: 'Thread ID', type: 'text', placeholder: 'e.g. my-agent-thread-001', defaultValue: 'memwal-demo-thread-001' }
      ],
      execute: function (params) {
        return MemwalAPI.lookupThread(params.thread_id);
      }
    },
    'checkpoint-put': {
      method: 'POST',
      url: '/api/checkpoint/put',
      description: 'Create a full checkpoint (store + register)',
      params: [
        { key: 'thread_id', label: 'Thread ID', type: 'text', placeholder: 'e.g. my-agent-thread-001', defaultValue: 'playground-checkpoint-test' },
        { key: 'data', label: 'Checkpoint Data (JSON)', type: 'textarea', placeholder: '{"messages": [...], "context": {...}}', defaultValue: '{\n  "messages": [\n    {"role": "user", "content": "Hello, remember me!"},\n    {"role": "assistant", "content": "Of course! Stored on Walrus."}\n  ],\n  "metadata": {"source": "playground"}\n}' }
      ],
      execute: function (params) {
        var data;
        try {
          data = JSON.parse(params.data);
        } catch (e) {
          data = { raw: params.data };
        }
        return MemwalAPI.putCheckpoint(params.thread_id, data);
      }
    },
    'checkpoint-get': {
      method: 'GET',
      url: '/api/checkpoint/get/{thread_id}',
      description: 'Retrieve the latest checkpoint',
      params: [
        { key: 'thread_id', label: 'Thread ID', type: 'text', placeholder: 'e.g. my-agent-thread-001', defaultValue: 'memwal-demo-thread-001' }
      ],
      execute: function (params) {
        return MemwalAPI.getCheckpoint(params.thread_id);
      }
    }
  };

  var currentEndpoint = 'store-blob';

  // ── Render params form ─────────────────────────────────────────────────

  function renderParams(endpointKey) {
    var endpoint = ENDPOINTS[endpointKey];
    if (!endpoint) return;

    currentEndpoint = endpointKey;

    // Update method and URL display
    var methodEl = document.getElementById('endpoint-method');
    var urlEl = document.getElementById('endpoint-url');

    if (methodEl) {
      methodEl.textContent = endpoint.method;
      methodEl.className = 'api-tester__method api-tester__method--' + endpoint.method.toLowerCase();
    }
    if (urlEl) {
      urlEl.textContent = endpoint.url;
    }

    // Update selector active state
    var selectorBtns = document.querySelectorAll('.api-tester__selector-btn');
    selectorBtns.forEach(function (btn) {
      btn.classList.toggle('is-active', btn.dataset.endpoint === endpointKey);
    });

    // Render param fields
    var formEl = document.getElementById('params-form');
    if (!formEl) return;

    var html = '';
    endpoint.params.forEach(function (param) {
      html += '<div class="api-tester__param-group">';
      html += '<label class="api-tester__param-label" for="param-' + param.key + '">' + param.label + '</label>';

      if (param.type === 'textarea') {
        html += '<textarea class="api-tester__param-textarea" id="param-' + param.key + '" placeholder="' + param.placeholder + '">' + (param.defaultValue || '') + '</textarea>';
      } else {
        html += '<input class="api-tester__param-input" id="param-' + param.key + '" type="' + param.type + '" placeholder="' + param.placeholder + '" value="' + (param.defaultValue || '') + '" />';
      }

      html += '</div>';
    });

    formEl.innerHTML = html;

    // Auto-fill blob_id from last response if applicable
    if (endpointKey === 'fetch-blob' || endpointKey === 'register') {
      var blobInput = document.getElementById('param-blob_id');
      if (blobInput && MemwalAPI.state.lastBlobId) {
        blobInput.value = MemwalAPI.state.lastBlobId;
      }
    }
  }

  // ── Send request ───────────────────────────────────────────────────────

  function sendRequest() {
    var endpoint = ENDPOINTS[currentEndpoint];
    if (!endpoint) return;

    // Collect params
    var params = {};
    endpoint.params.forEach(function (param) {
      var el = document.getElementById('param-' + param.key);
      params[param.key] = el ? el.value : '';
    });

    // UI: loading state
    var sendBtn = document.getElementById('send-btn');
    var sendText = document.getElementById('send-btn-text');
    var sendSpinner = document.getElementById('send-btn-spinner');
    var responseBody = document.getElementById('response-body');
    var responseStatus = document.getElementById('response-status');
    var responseTime = document.getElementById('response-time');

    if (sendBtn) sendBtn.disabled = true;
    if (sendText) sendText.textContent = 'Sending...';
    if (sendSpinner) sendSpinner.style.display = 'inline-block';
    if (responseBody) responseBody.innerHTML = '<span class="text-muted animate-pulse">// Loading...</span>';
    if (responseStatus) responseStatus.textContent = '';
    if (responseTime) responseTime.textContent = '';

    endpoint.execute(params).then(function (result) {
      // UI: restore
      if (sendBtn) sendBtn.disabled = false;
      if (sendText) sendText.textContent = 'Send';
      if (sendSpinner) sendSpinner.style.display = 'none';

      // Display response
      if (responseStatus) {
        responseStatus.textContent = 'HTTP ' + result.status;
        responseStatus.className = 'api-tester__response-status ' +
          (result.ok ? 'api-tester__response-status--ok' : 'api-tester__response-status--err');
      }
      if (responseTime) {
        responseTime.textContent = result.elapsed + 'ms';
      }
      if (responseBody) {
        responseBody.innerHTML = MemwalAPI.highlightJSON(result.data);
      }
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────

  function init() {
    // Endpoint selector buttons
    var selectorBtns = document.querySelectorAll('.api-tester__selector-btn');
    selectorBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        renderParams(this.dataset.endpoint);
      });
    });

    // Send button
    var sendBtn = document.getElementById('send-btn');
    if (sendBtn) {
      sendBtn.addEventListener('click', sendRequest);
    }

    // Keyboard: Enter to send
    var paramsForm = document.getElementById('params-form');
    if (paramsForm) {
      paramsForm.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey && e.target.tagName !== 'TEXTAREA') {
          e.preventDefault();
          sendRequest();
        }
      });
    }
  }

  return {
    init: init,
    renderParams: renderParams,
    sendRequest: sendRequest,
    loadTemplate: function (endpointKey, params) {
      renderParams(endpointKey);
      // Fill in template values
      if (params) {
        Object.keys(params).forEach(function (key) {
          var el = document.getElementById('param-' + key);
          if (el) el.value = params[key];
        });
      }
    }
  };
})();
