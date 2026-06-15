/* ============================================================================
   MemWal Landing Page — Animations & Interactions
   ============================================================================ */

(function () {
  'use strict';

  // ── Scroll Reveal ─────────────────────────────────────────────────────
  // Uses IntersectionObserver for staggered fade-in on scroll

  function initScrollReveal() {
    var revealEls = document.querySelectorAll('.reveal');
    if (!revealEls.length) return;

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
    );

    revealEls.forEach(function (el) {
      observer.observe(el);
    });
  }

  // ── Feature Card Stagger ──────────────────────────────────────────────
  // Add staggered reveal to feature cards and use case cards

  function initCardStagger() {
    var grids = [
      document.getElementById('features-grid'),
      document.getElementById('usecases-grid')
    ];

    grids.forEach(function (grid) {
      if (!grid) return;
      var cards = grid.children;
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              Array.from(cards).forEach(function (card, i) {
                card.style.opacity = '0';
                card.style.transform = 'translateY(16px)';
                card.style.transition =
                  'opacity 300ms ease ' + i * 80 + 'ms, ' +
                  'transform 300ms ease ' + i * 80 + 'ms';
                requestAnimationFrame(function () {
                  requestAnimationFrame(function () {
                    card.style.opacity = '1';
                    card.style.transform = 'translateY(0)';
                  });
                });
              });
              observer.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.1 }
      );
      observer.observe(grid);
    });
  }

  // ── Header Scroll Effect ──────────────────────────────────────────────

  function initHeaderScroll() {
    var header = document.getElementById('landing-header');
    if (!header) return;

    var scrolled = false;
    window.addEventListener(
      'scroll',
      function () {
        var isScrolled = window.scrollY > 40;
        if (isScrolled !== scrolled) {
          scrolled = isScrolled;
          header.style.borderBottomColor = scrolled
            ? 'var(--border-default)'
            : 'var(--border-subtle)';
          header.style.background = scrolled
            ? 'var(--bg-overlay)'
            : 'rgba(10, 10, 11, 0.6)';
        }
      },
      { passive: true }
    );
  }

  // ── Smooth Anchor Scrolling ───────────────────────────────────────────

  function initSmoothAnchors() {
    document.querySelectorAll('a[href^="#"]').forEach(function (anchor) {
      anchor.addEventListener('click', function (e) {
        var targetId = this.getAttribute('href');
        if (targetId === '#') return;
        var target = document.querySelector(targetId);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    });
  }

  // ── Workflow Connector Animation ──────────────────────────────────────
  // Animate the workflow connectors when visible

  function initWorkflowAnimation() {
    var diagram = document.getElementById('workflow-diagram');
    if (!diagram) return;

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var nodes = diagram.querySelectorAll('.workflow__node');
            nodes.forEach(function (node, i) {
              node.style.opacity = '0';
              node.style.transform = 'translateY(12px)';
              node.style.transition =
                'opacity 300ms ease ' + i * 120 + 'ms, ' +
                'transform 300ms ease ' + i * 120 + 'ms';
              requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                  node.style.opacity = '1';
                  node.style.transform = 'translateY(0)';
                });
              });
            });

            var connectors = diagram.querySelectorAll('.workflow__connector');
            connectors.forEach(function (conn, i) {
              conn.style.opacity = '0';
              conn.style.transition = 'opacity 200ms ease ' + (i * 120 + 200) + 'ms';
              requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                  conn.style.opacity = '1';
                });
              });
            });

            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.2 }
    );

    observer.observe(diagram);
  }

  // ── Init ──────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    initScrollReveal();
    initCardStagger();
    initHeaderScroll();
    initSmoothAnchors();
    initWorkflowAnimation();
  });
})();
