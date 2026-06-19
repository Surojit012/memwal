var MemwalAPI = (function () {
  'use strict';

  var DEFAULT_REMOTE_BASE_URL = 'https://memwal.onrender.com';

  function normalizeBaseUrl(value) {
    if (!value) return '';
    return String(value).replace(/\/+$/, '');
  }

  function queryBaseUrl() {
    try {
      var params = new URLSearchParams(window.location.search);
      return params.get('api_base') || params.get('apiBase');
    } catch (err) {
      return '';
    }
  }

  function storedBaseUrl() {
    try {
      return window.localStorage.getItem('memwalApiBaseUrl');
    } catch (err) {
      return '';
    }
  }

  function inferBaseUrl() {
    var explicit = window.MEMWAL_API_BASE_URL || queryBaseUrl() || storedBaseUrl();
    if (explicit) return normalizeBaseUrl(explicit);

    var hostname = window.location.hostname;
    var isLocal =
      hostname === 'localhost' ||
      hostname === '127.0.0.1' ||
      hostname === '0.0.0.0';
    var isRender = hostname === 'memwal.onrender.com';

    if (isLocal || isRender) return '';
    return DEFAULT_REMOTE_BASE_URL;
  }

  var BASE_URL = inferBaseUrl();
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
    config: null,
    apiBaseUrl: BASE_URL || window.location.origin
  };

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

  var stateListeners = [];

  function onStateChange(fn) {
    stateListeners.push(fn);
  }

  function notifyState() {
    stateListeners.forEach(function (fn) { fn(state); });
  }


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

  function putCheckpoint(threadId, data, strategy, forceSnapshot) {
    return request('POST', '/api/checkpoint/put', {
      thread_id: threadId,
      data: data,
      strategy: strategy || 'snapshot',
      force_snapshot: forceSnapshot !== false
    })
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

  function getCheckpoint(threadId, strategy) {
    var suffix = strategy ? '?strategy=' + encodeURIComponent(strategy) : '';
    return request('GET', '/api/checkpoint/get/' + encodeURIComponent(threadId) + suffix)
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

  function runBasicProof() {
    return request('POST', '/api/proof/basic')
      .then(function (result) {
        if (result.ok) {
          if (result.data.thread_id) state.lastThreadId = result.data.thread_id;
          if (result.data.blob_id) state.lastBlobId = result.data.blob_id;
          if (result.data.tx_digest) state.txCount++;
          notifyState();
        }
        return result;
      });
  }

  function runBenchmarkProof(steps, strategy) {
    return request('POST', '/api/proof/benchmark', {
      steps: parseInt(steps, 10) || 5,
      strategy: strategy || 'both'
    }).then(function (result) {
      if (result.ok && result.data.results && result.data.results.length) {
        var last = result.data.results[result.data.results.length - 1];
        if (last.thread_id) state.lastThreadId = last.thread_id;
        if (last.blob_ids && last.blob_ids.length) state.lastBlobId = last.blob_ids[last.blob_ids.length - 1];
        if (last.tx_digests) state.txCount += last.tx_digests.length;
        notifyState();
      }
      return result;
    });
  }

  function runCrossMachineProof() {
    return request('POST', '/api/proof/cross-machine')
      .then(function (result) {
        if (result.ok) {
          if (result.data.thread_id) state.lastThreadId = result.data.thread_id;
          if (result.data.blob_id) state.lastBlobId = result.data.blob_id;
          if (result.data.tx_digest) state.txCount++;
          notifyState();
        }
        return result;
      });
  }

  function runIsolationProof() {
    return request('POST', '/api/proof/isolation')
      .then(function (result) {
        if (result.ok) {
          if (result.data.threads && result.data.threads.C) state.lastThreadId = result.data.threads.C.thread_id;
          if (result.data.blob_ids && result.data.blob_ids.length) state.lastBlobId = result.data.blob_ids[result.data.blob_ids.length - 1];
          if (result.data.tx_digests) state.txCount += result.data.tx_digests.length;
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


  return {
    state: state,
    getBaseUrl: function () { return BASE_URL; },
    getDisplayBaseUrl: function () { return BASE_URL || window.location.origin; },
    healthCheck: healthCheck,
    getConfig: getConfig,
    storeBlob: storeBlob,
    fetchBlob: fetchBlob,
    registerThread: registerThread,
    lookupThread: lookupThread,
    putCheckpoint: putCheckpoint,
    getCheckpoint: getCheckpoint,
    runBasicProof: runBasicProof,
    runBenchmarkProof: runBenchmarkProof,
    runCrossMachineProof: runCrossMachineProof,
    runIsolationProof: runIsolationProof,
    uploadFile: uploadFile,
    highlightJSON: highlightJSON,
    onLog: onLog,
    onStateChange: onStateChange,
    log: log
  };
})();
