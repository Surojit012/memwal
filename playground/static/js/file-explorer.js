/* ============================================================================
   MemWal File Explorer Panel
   ============================================================================ */

var FileExplorer = (function () {
  'use strict';

  var uploadedFiles = [];

  function init() {
    var zone = document.getElementById('file-upload-zone');
    var fileInput = document.getElementById('file-input');

    if (!zone || !fileInput) return;

    // Click to upload
    zone.addEventListener('click', function () {
      fileInput.click();
    });

    fileInput.addEventListener('change', function () {
      if (this.files.length > 0) {
        handleFiles(this.files);
      }
      this.value = '';
    });

    // Drag and drop
    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.add('is-drag-over');
    });

    zone.addEventListener('dragleave', function (e) {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove('is-drag-over');
    });

    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove('is-drag-over');

      if (e.dataTransfer.files.length > 0) {
        handleFiles(e.dataTransfer.files);
      }
    });
  }

  function handleFiles(fileList) {
    Array.from(fileList).forEach(function (file) {
      uploadFile(file);
    });
  }

  function uploadFile(file) {
    var tempEntry = {
      name: file.name,
      size: file.size,
      type: file.type || 'application/octet-stream',
      blob_id: null,
      status: 'uploading'
    };
    uploadedFiles.unshift(tempEntry);
    renderFileList();

    MemwalAPI.uploadFile(file).then(function (result) {
      if (result.ok) {
        tempEntry.blob_id = result.data.blob_id;
        tempEntry.status = 'stored';
      } else {
        tempEntry.status = 'error';
        tempEntry.error = result.data.detail || result.data.error || 'Upload failed';
      }
      renderFileList();
    });
  }

  function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
  }

  function renderFileList() {
    var container = document.getElementById('file-list-container');
    if (!container) return;

    if (uploadedFiles.length === 0) {
      container.innerHTML = '';
      return;
    }

    var html = '<div class="file-list">';
    uploadedFiles.forEach(function (file) {
      html += '<div class="file-item">';
      html += '  <div class="file-item__icon">';
      if (file.status === 'uploading') {
        html += '    <div class="spinner spinner--sm"></div>';
      } else if (file.status === 'error') {
        html += '    <svg viewBox="0 0 24 24" style="stroke: var(--status-error);"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
      } else {
        html += '    <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
      }
      html += '  </div>';
      html += '  <span class="file-item__name">' + escapeHtml(file.name) + '</span>';
      html += '  <span class="file-item__size">' + formatSize(file.size) + '</span>';

      if (file.blob_id) {
        html += '  <span class="file-item__blob-id" title="' + escapeHtml(file.blob_id) + '">' + escapeHtml(file.blob_id.substring(0, 16)) + '...</span>';
      } else if (file.status === 'uploading') {
        html += '  <span class="file-item__blob-id text-muted">uploading...</span>';
      } else if (file.status === 'error') {
        html += '  <span class="file-item__blob-id" style="color: var(--status-error);">failed</span>';
      }

      html += '</div>';
    });
    html += '</div>';

    container.innerHTML = html;
  }

  function escapeHtml(str) {
    if (typeof str !== 'string') str = String(str);
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  return {
    init: init
  };
})();
