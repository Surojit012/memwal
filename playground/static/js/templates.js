/* ============================================================================
   MemWal Example Templates
   ============================================================================ */

var Templates = (function () {
  'use strict';

  var TEMPLATES = [
    {
      icon: '💬',
      title: 'Personal Assistant',
      desc: 'Store a multi-turn conversation checkpoint. Simulates a personal assistant that remembers past interactions.',
      tags: ['checkpoint', 'messages', 'context'],
      endpoint: 'checkpoint-put',
      params: {
        thread_id: 'personal-assistant-001',
        data: JSON.stringify({
          messages: [
            { role: 'user', content: 'What is my meeting schedule for tomorrow?' },
            { role: 'assistant', content: 'You have a 10am standup and a 2pm design review.' },
            { role: 'user', content: 'Reschedule the design review to 3pm.' },
            { role: 'assistant', content: 'Done. Your design review is now at 3pm tomorrow.' }
          ],
          metadata: { agent_type: 'personal_assistant', model: 'gpt-4o', source: 'playground' }
        }, null, 2)
      }
    },
    {
      icon: '🔬',
      title: 'Research Agent',
      desc: 'Checkpoint a research agent that has accumulated findings across multiple queries and sources.',
      tags: ['checkpoint', 'knowledge', 'research'],
      endpoint: 'checkpoint-put',
      params: {
        thread_id: 'research-agent-001',
        data: JSON.stringify({
          messages: [
            { role: 'user', content: 'Research the current state of decentralised storage.' },
            { role: 'assistant', content: 'Key findings: Walrus (by Mysten Labs) uses erasure coding across 1000+ nodes. Filecoin and Arweave remain dominant. IPFS is widely adopted for addressing.' }
          ],
          research_context: {
            topic: 'Decentralised Storage 2024',
            sources_checked: 12,
            key_entities: ['Walrus', 'Filecoin', 'Arweave', 'IPFS', 'Mysten Labs']
          },
          metadata: { agent_type: 'research_agent', source: 'playground' }
        }, null, 2)
      }
    },
    {
      icon: '🔗',
      title: 'Multi-Agent Workflow',
      desc: 'Register multiple threads to simulate a multi-agent system where agents share context through the on-chain registry.',
      tags: ['registry', 'multi-agent', 'coordination'],
      endpoint: 'store-blob',
      params: {
        data: JSON.stringify({
          workflow_id: 'multi-agent-pipeline-001',
          agents: ['planner', 'researcher', 'writer', 'reviewer'],
          current_stage: 'research',
          shared_context: {
            objective: 'Write a technical blog post about MemWal',
            constraints: ['2000 words', 'include code examples', 'target developer audience']
          }
        }, null, 2),
        epochs: '5'
      }
    },
    {
      icon: '📖',
      title: 'RAG System',
      desc: 'Store retrieval-augmented generation context including document embeddings metadata and retrieval history.',
      tags: ['checkpoint', 'RAG', 'embeddings'],
      endpoint: 'checkpoint-put',
      params: {
        thread_id: 'rag-system-001',
        data: JSON.stringify({
          messages: [
            { role: 'user', content: 'What does the MemWal registry contract do?' },
            { role: 'assistant', content: 'The registry contract (registry.move) provides a shared on-chain mapping from thread_id to blob_id using Sui dynamic fields.' }
          ],
          retrieval_context: {
            documents_searched: 3,
            chunks_retrieved: 5,
            top_chunk: 'module memwal::registry { ... public struct Registry has key { id: UID, entries: Table<String, String> } ... }',
            similarity_threshold: 0.82
          },
          metadata: { agent_type: 'rag_system', model: 'gpt-4o-mini', source: 'playground' }
        }, null, 2)
      }
    },
    {
      icon: '🧠',
      title: 'Long-Term Memory',
      desc: 'Store a simple key-value blob to test Walrus blob storage and retrieval without the full checkpoint flow.',
      tags: ['blob', 'storage', 'walrus'],
      endpoint: 'store-blob',
      params: {
        data: JSON.stringify({
          agent_id: 'memory-agent-001',
          long_term_memories: [
            { key: 'user_preference_theme', value: 'dark', timestamp: new Date().toISOString() },
            { key: 'user_preference_language', value: 'python', timestamp: new Date().toISOString() },
            { key: 'last_project', value: 'memwal-playground', timestamp: new Date().toISOString() }
          ]
        }, null, 2),
        epochs: '10'
      }
    }
  ];

  function init() {
    renderTemplates();
  }

  function renderTemplates() {
    var container = document.getElementById('templates-grid');
    if (!container) return;

    var html = '';
    TEMPLATES.forEach(function (tpl, idx) {
      html += '<div class="template-card" data-template-idx="' + idx + '">';
      html += '  <div class="template-card__icon">' + tpl.icon + '</div>';
      html += '  <h3 class="template-card__title">' + tpl.title + '</h3>';
      html += '  <p class="template-card__desc">' + tpl.desc + '</p>';
      html += '  <div class="template-card__tags">';
      tpl.tags.forEach(function (tag) {
        html += '    <span class="template-card__tag">' + tag + '</span>';
      });
      html += '  </div>';
      html += '</div>';
    });

    container.innerHTML = html;

    // Attach click handlers
    container.querySelectorAll('.template-card').forEach(function (card) {
      card.addEventListener('click', function () {
        var idx = parseInt(this.dataset.templateIdx);
        loadTemplate(idx);
      });
    });
  }

  function loadTemplate(idx) {
    var tpl = TEMPLATES[idx];
    if (!tpl) return;

    MemwalAPI.log('info', 'Loading template: ' + tpl.title);

    // Switch to playground panel and load the template
    if (typeof Dashboard !== 'undefined' && Dashboard.switchPanel) {
      Dashboard.switchPanel('playground');
    }

    // Load into API tester
    if (typeof ApiTester !== 'undefined' && ApiTester.loadTemplate) {
      ApiTester.loadTemplate(tpl.endpoint, tpl.params);
    }
  }

  return {
    init: init
  };
})();
