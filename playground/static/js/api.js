/* ============================================================================
   MemWal API Client
   Communicates with the bridge server at /api/*
   ============================================================================ */

var MemwalAPI = (function () {
  'use strict';

  var BASE_URL = '';  // Same origin

  // ── State ──────────────────────────────────────────────────────────────
  var state = {
    connected: false,
    requestCount: 0,
    blobsStored: 0,
    txCount: 0,
    lastThreadId: null,
    lastBlobId: null,
    lastCheckpointId: null,
    lastResponse: null,
    timingHistory: [],
    config: null
  };

  // ── Logger ─────────────────────────────────────────────────────────────
  var logListeners = [];

  function onLog(fn) {
    logListeners.push(fn);
  }

  function log(level, msg) {
    var now = new Date();
    var ts = now.toLocaleTimeString('en-GB', { hour12: false });
    var entry = { time: ts, level: level, msg: msg };
    logListeners.forEach(function (fn) { fn(entry); });
  }

  // ── State change listeners ─────────────────────────────────────────────
  var stateListeners = [];

  function onStateChange(fn) {
    stateListeners.push(fn);
  }

  function notifyState() {
    stateListeners.forEach(function (fn) { fn(state); });
  }

  // ── HTTP helpers ───────────────────────────────────────────────────────

  function request(method, path, body) {
    var url = BASE_URL + path;
    var opts = {
      method: method,
      headers: { 'Content-Type': 'application/json' }
    };
    if (body !== undefined && body !== null) {
      opts.body = JSON.stringify(body);
    }

    state.requestCount++;
    var startTime = performance.now();
    log('info', method + ' ' + path);

    return fetch(url, opts)
      .then(function (resp) {
        var elapsed = Math.round(performance.now() - startTime);
        var statusOk = resp.ok;
        return resp.json().then(function (data) {
          var result = {
            ok: statusOk,
            status: resp.status,
            data: data,
            elapsed: elapsed
          };

          state.lastResponse = result;
          state.timingHistory.unshift({
            endpoint: path,
            method: method,
            elapsed: elapsed,
            status: resp.status,
            time: new Date().toLocaleTimeString('en-GB', { hour12: false })
          });
          if (state.timingHistory.length > 50) {
            state.timingHistory = state.timingHistory.slice(0, 50);
          }

          if (statusOk) {
            log('success', 'HTTP ' + resp.status + ' — ' + elapsed + 'ms');
          } else {
            log('error', 'HTTP ' + resp.status + ' — ' + (data.detail || JSON.stringify(data)));
          }

          notifyState();
          return result;
        });
      })
      .catch(function (err) {
        var elapsed = Math.round(performance.now() - startTime);
        log('error', 'Request failed: ' + err.message);
        state.lastResponse = { ok: false, status: 0, data: { error: err.message }, elapsed: elapsed };
        notifyState();
        return state.lastResponse;
      });
  }

  // ── Public API methods ─────────────────────────────────────────────────

  function healthCheck() {
    return request('GET', '/api/health')
      .then(function (result) {
        state.connected = result.ok;
        notifyState();
        return result;
      })
      .catch(function () {
        state.connected = false;
        notifyState();
        return { ok: false };
      });
  }

  function getConfig() {
    return request('GET', '/api/config')
      .then(function (result) {
        if (result.ok) {
          state.config = result.data;
        }
        return result;
      });
  }

  function storeBlob(data, epochs) {
    return request('POST', '/api/blob/store', { data: data, epochs: epochs || 5 })
      .then(function (result) {
        if (result.ok && result.data.blob_id) {
          state.lastBlobId = result.data.blob_id;
          state.blobsStored++;
          notifyState();
        }
        return result;
      });
  }

  function fetchBlob(blobId) {
    return request('GET', '/api/blob/' + encodeURIComponent(blobId));
  }

  function registerThread(threadId, blobId) {
    return request('POST', '/api/registry/register', { thread_id: threadId, blob_id: blobId })
      .then(function (result) {
        if (result.ok && result.data.digest) {
          state.lastThreadId = threadId;
          state.txCount++;
          notifyState();
        }
        return result;
      });
  }

  function lookupThread(threadId) {
    return request('GET', '/api/registry/lookup/' + encodeURIComponent(threadId))
      .then(function (result) {
        if (result.ok && result.data.blob_id) {
          state.lastThreadId = threadId;
          state.lastBlobId = result.data.blob_id;
          notifyState();
        }
        return result;
      });
  }

  function putCheckpoint(threadId, data) {
    return request('POST', '/api/checkpoint/put', { thread_id: threadId, data: data })
      .then(function (result) {
        if (result.ok) {
          state.lastThreadId = threadId;
          if (result.data.checkpoint_id) state.lastCheckpointId = result.data.checkpoint_id;
          if (result.data.blob_id) { state.lastBlobId = result.data.blob_id; state.blobsStored++; }
          if (result.data.digest) state.txCount++;
          notifyState();
        }
        return result;
      });
  }

  function getCheckpoint(threadId) {
    return request('GET', '/api/checkpoint/get/' + encodeURIComponent(threadId))
      .then(function (result) {
        if (result.ok) {
          state.lastThreadId = threadId;
          if (result.data.checkpoint_id) state.lastCheckpointId = result.data.checkpoint_id;
          if (result.data.blob_id) state.lastBlobId = result.data.blob_id;
          notifyState();
        }
        return result;
      });
  }

  function uploadFile(file) {
    var formData = new FormData();
    formData.append('file', file);

    state.requestCount++;
    var startTime = performance.now();
    log('info', 'POST /api/file/upload — ' + file.name + ' (' + file.size + ' bytes)');

    return fetch(BASE_URL + '/api/file/upload', {
      method: 'POST',
      body: formData
    })
      .then(function (resp) {
        var elapsed = Math.round(performance.now() - startTime);
        return resp.json().then(function (data) {
          if (resp.ok) {
            log('success', 'File uploaded — ' + elapsed + 'ms');
            if (data.blob_id) { state.lastBlobId = data.blob_id; state.blobsStored++; }
          } else {
            log('error', 'Upload failed: ' + (data.detail || 'unknown'));
          }
          state.lastResponse = { ok: resp.ok, status: resp.status, data: data, elapsed: elapsed };
          notifyState();
          return state.lastResponse;
        });
      })
      .catch(function (err) {
        log('error', 'Upload failed: ' + err.message);
        return { ok: false, data: { error: err.message } };
      });
  }

  // ── JSON syntax highlight ──────────────────────────────────────────────

  function highlightJSON(obj) {
    var json = JSON.stringify(obj, null, 2);
    if (!json) return '';
    return json
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"([^"]+)"(?=\s*:)/g, '<span class="json-key">"$1"</span>')
      .replace(/: "([^"]*?)"/g, ': <span class="json-string">"$1"</span>')
      .replace(/: (-?\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
      .replace(/: (true|false)/g, ': <span class="json-boolean">$1</span>')
      .replace(/: (null)/g, ': <span class="json-null">$1</span>');
  }

  // ── Expose ─────────────────────────────────────────────────────────────

  return {
    state: state,
    healthCheck: healthCheck,
    getConfig: getConfig,
    storeBlob: storeBlob,
    fetchBlob: fetchBlob,
    registerThread: registerThread,
    lookupThread: lookupThread,
    putCheckpoint: putCheckpoint,
    getCheckpoint: getCheckpoint,
    uploadFile: uploadFile,
    highlightJSON: highlightJSON,
    onLog: onLog,
    onStateChange: onStateChange,
    log: log
  };
})();
