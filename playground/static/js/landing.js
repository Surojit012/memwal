(function () {
  'use strict';

  var palette = {
    cafeNoir: [5 / 255, 38 / 255, 89 / 255],
    kombu: [14 / 255, 61 / 255, 122 / 255],
    moss: [84 / 255, 131 / 255, 179 / 255],
    tan: [125 / 255, 160 / 255, 202 / 255],
    bone: [193 / 255, 232 / 255, 255 / 255]
  };

  function hasGsap() {
    return typeof window.gsap !== 'undefined';
  }

  function initGsapIntro() {
    if (!hasGsap()) return;

    var gsap = window.gsap;
    var tl = gsap.timeline({ defaults: { ease: 'power3.out' } });

    gsap.set(['.hero__eyebrow', '.hero__title', '.hero__subtitle', '.hero__ctas', '.hero__tech-badges'], {
      autoAlpha: 0,
      y: 24
    });
    gsap.set('.landing-header', { autoAlpha: 0, y: -18 });

    tl.to('.landing-header', { autoAlpha: 1, y: 0, duration: 0.75 })
      .to('.hero__eyebrow', { autoAlpha: 1, y: 0, duration: 0.6 }, '-=0.25')
      .to('.hero__title', { autoAlpha: 1, y: 0, duration: 0.9 }, '-=0.2')
      .to('.hero__subtitle', { autoAlpha: 1, y: 0, duration: 0.72 }, '-=0.38')
      .to('.hero__ctas', { autoAlpha: 1, y: 0, duration: 0.62 }, '-=0.28')
      .to('.hero__tech-badges', { autoAlpha: 1, y: 0, duration: 0.62 }, '-=0.32');
  }

  function initIntroWhenReady() {
    if (hasGsap()) {
      initGsapIntro();
      return;
    }

    var attempts = 0;
    var timer = window.setInterval(function () {
      attempts += 1;
      if (hasGsap()) {
        window.clearInterval(timer);
        initGsapIntro();
      }
      if (attempts >= 20) {
        window.clearInterval(timer);
      }
    }, 100);
  }

  function revealElement(el) {
    if (hasGsap()) {
      window.gsap.to(el, {
        autoAlpha: 1,
        y: 0,
        duration: 0.8,
        ease: 'power3.out'
      });
    } else {
      el.classList.add('is-visible');
    }
  }

  function initScrollReveal() {
    var revealEls = document.querySelectorAll('.reveal');
    if (!revealEls.length) return;

    if (hasGsap()) {
      window.gsap.set(revealEls, { autoAlpha: 0, y: 28 });
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            revealElement(entry.target);
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

  function initCardStagger() {
    var grids = [
      document.getElementById('features-grid'),
      document.getElementById('usecases-grid')
    ];

    grids.forEach(function (grid) {
      if (!grid) return;
      var cards = Array.from(grid.children);
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;

            if (hasGsap()) {
              window.gsap.fromTo(cards, {
                autoAlpha: 0,
                y: 18
              }, {
                autoAlpha: 1,
                y: 0,
                duration: 0.55,
                stagger: 0.08,
                ease: 'power3.out'
              });
            } else {
              cards.forEach(function (card, i) {
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
            }

            observer.unobserve(entry.target);
          });
        },
        { threshold: 0.1 }
      );
      observer.observe(grid);
    });
  }

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
            : 'rgba(2, 16, 36, 0.72)';
        }
      },
      { passive: true }
    );
  }

  function transitionTo(url) {
    var overlay = document.getElementById('page-transition');
    if (!overlay || !hasGsap()) {
      window.location.href = url;
      return;
    }

    window.gsap.timeline({
      defaults: { ease: 'power4.inOut' },
      onComplete: function () {
        window.location.href = url;
      }
    })
      .set(overlay, { transformOrigin: 'bottom', scaleY: 0 })
      .to(overlay, { scaleY: 1, duration: 0.55 })
      .to('.landing', { y: -24, autoAlpha: 0, duration: 0.38 }, '<');
  }

  function initSmoothAnchors() {
    document.querySelectorAll('a[href]').forEach(function (anchor) {
      anchor.addEventListener('click', function (e) {
        var href = this.getAttribute('href');
        if (!href || href === '#') return;

        if (href.charAt(0) === '#') {
          var target = document.querySelector(href);
          if (target) {
            e.preventDefault();
            var targetY = target.getBoundingClientRect().top + window.scrollY - 72;
            if (hasGsap()) {
              var scrollState = { y: window.scrollY };
              window.gsap.to(scrollState, {
                y: targetY,
                duration: 0.8,
                ease: 'power3.inOut',
                onUpdate: function () {
                  window.scrollTo(0, scrollState.y);
                }
              });
            } else {
              target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          }
          return;
        }

        var isExternal = this.hostname && this.hostname !== window.location.hostname;
        var isModified = e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || this.target === '_blank';
        if (!isExternal && !isModified) {
          e.preventDefault();
          transitionTo(href);
        }
      });
    });
  }

  function initWorkflowAnimation() {
    var diagram = document.getElementById('workflow-diagram');
    if (!diagram) return;

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;

          var nodes = diagram.querySelectorAll('.workflow__node');
          var connectors = diagram.querySelectorAll('.workflow__connector');

          if (hasGsap()) {
            window.gsap.fromTo(nodes, {
              autoAlpha: 0,
              y: 16
            }, {
              autoAlpha: 1,
              y: 0,
              duration: 0.55,
              stagger: 0.11,
              ease: 'power3.out'
            });
            window.gsap.fromTo(connectors, {
              autoAlpha: 0,
              scaleX: 0
            }, {
              autoAlpha: 1,
              scaleX: 1,
              duration: 0.42,
              stagger: 0.1,
              ease: 'power2.out',
              delay: 0.18
            });
          } else {
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
          }

          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.2 }
    );

    observer.observe(diagram);
  }

  function createShader(gl, type, source) {
    var shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      return null;
    }
    return shader;
  }

  function createProgram(gl, vertexSource, fragmentSource) {
    var vertex = createShader(gl, gl.VERTEX_SHADER, vertexSource);
    var fragment = createShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
    if (!vertex || !fragment) return null;

    var program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      gl.deleteProgram(program);
      return null;
    }
    return program;
  }

  function buildTextPoints(text, width, height) {
    var canvas = document.createElement('canvas');
    var scale = 0.42;
    canvas.width = Math.max(1, Math.floor(width * scale));
    canvas.height = Math.max(1, Math.floor(height * scale));
    var ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return new Float32Array(0);

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '900 ' + Math.max(34, Math.floor(canvas.width / 12)) + 'px Geist, Arial, sans-serif';

    var lines = text.split('\n');
    var lineHeight = Math.floor(canvas.width / 9);
    var start = canvas.height / 2 - ((lines.length - 1) * lineHeight) / 2;
    lines.forEach(function (line, i) {
      ctx.fillText(line, canvas.width / 2, start + i * lineHeight);
    });

    var data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    var points = [];
    var step = Math.max(4, Math.floor(canvas.width / 180));

    for (var y = 0; y < canvas.height; y += step) {
      for (var x = 0; x < canvas.width; x += step) {
        var alpha = data[(y * canvas.width + x) * 4 + 3];
        if (alpha > 80) {
          points.push((x / canvas.width) * 2 - 1);
          points.push(1 - (y / canvas.height) * 2);
          points.push(Math.random());
        }
      }
    }

    return new Float32Array(points);
  }

  function initWebglHeroText() {
    var canvas = document.getElementById('hero-webgl-text');
    if (!canvas) return;

    var gl = canvas.getContext('webgl', { alpha: true, antialias: true });
    if (!gl) {
      canvas.style.display = 'none';
      return;
    }

    var vertexSource = [
      'attribute vec2 a_position;',
      'attribute float a_seed;',
      'uniform float u_time;',
      'uniform float u_ratio;',
      'varying float v_seed;',
      'void main() {',
      '  float wave = sin((a_position.x * 4.0) + u_time * 1.2 + a_seed * 6.2831) * 0.018;',
      '  float drift = cos((a_position.y * 5.0) + u_time * 0.8 + a_seed * 4.0) * 0.014;',
      '  vec2 pos = vec2(a_position.x * 0.82 + drift, a_position.y * 0.34 + wave);',
      '  pos.y += 0.04;',
      '  gl_Position = vec4(pos.x, pos.y, 0.0, 1.0);',
      '  gl_PointSize = 1.6 + a_seed * 2.4;',
      '  v_seed = a_seed;',
      '}'
    ].join('\n');

    var fragmentSource = [
      'precision mediump float;',
      'uniform float u_time;',
      'uniform vec3 u_tan;',
      'uniform vec3 u_moss;',
      'varying float v_seed;',
      'void main() {',
      '  vec2 p = gl_PointCoord - vec2(0.5);',
      '  float d = length(p);',
      '  if (d > 0.5) discard;',
      '  float pulse = 0.45 + 0.55 * sin(u_time * 0.9 + v_seed * 6.2831);',
      '  vec3 color = mix(u_moss, u_tan, pulse);',
      '  gl_FragColor = vec4(color, 0.42 * (1.0 - d * 1.8));',
      '}'
    ].join('\n');

    var program = createProgram(gl, vertexSource, fragmentSource);
    if (!program) {
      canvas.style.display = 'none';
      return;
    }

    var buffer = gl.createBuffer();
    var positionLoc = gl.getAttribLocation(program, 'a_position');
    var seedLoc = gl.getAttribLocation(program, 'a_seed');
    var timeLoc = gl.getUniformLocation(program, 'u_time');
    var ratioLoc = gl.getUniformLocation(program, 'u_ratio');
    var tanLoc = gl.getUniformLocation(program, 'u_tan');
    var mossLoc = gl.getUniformLocation(program, 'u_moss');
    var points = new Float32Array(0);
    var count = 0;
    var start = performance.now();

    function resize() {
      var rect = canvas.getBoundingClientRect();
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      gl.viewport(0, 0, canvas.width, canvas.height);
      points = buildTextPoints('MEMWAL\nPORTABLE MEMORY', canvas.width, canvas.height);
      count = points.length / 3;
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, points, gl.STATIC_DRAW);
    }

    function render(now) {
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.enableVertexAttribArray(positionLoc);
      gl.vertexAttribPointer(positionLoc, 2, gl.FLOAT, false, 12, 0);
      gl.enableVertexAttribArray(seedLoc);
      gl.vertexAttribPointer(seedLoc, 1, gl.FLOAT, false, 12, 8);
      gl.uniform1f(timeLoc, (now - start) / 1000);
      gl.uniform1f(ratioLoc, canvas.width / Math.max(1, canvas.height));
      gl.uniform3fv(tanLoc, palette.tan);
      gl.uniform3fv(mossLoc, palette.moss);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.drawArrays(gl.POINTS, 0, count);
      requestAnimationFrame(render);
    }

    resize();
    window.addEventListener('resize', resize);
    requestAnimationFrame(render);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initIntroWhenReady();
    initScrollReveal();
    initCardStagger();
    initHeaderScroll();
    initSmoothAnchors();
    initWorkflowAnimation();
    initWebglHeroText();
    initHeroReveal();
  });

  function initHeroReveal() {
    var hero = document.getElementById('hero');
    var revealLayer = document.getElementById('hero-reveal-layer');
    if (!hero || !revealLayer) return;

    var RADIUS = 400;
    var currentX = -9999;
    var currentY = -9999;
    var targetX = -9999;
    var targetY = -9999;
    var isHovering = false;
    var rafId = null;

    function updateMask() {
      currentX += (targetX - currentX) * 0.12;
      currentY += (targetY - currentY) * 0.12;

      var mask =
        'radial-gradient(circle ' + RADIUS + 'px at ' +
        currentX + 'px ' + currentY + 'px, ' +
        'rgba(0, 0, 0, 0.25) 0%, transparent 60%)';

      revealLayer.style.webkitMaskImage = mask;
      revealLayer.style.maskImage = mask;

      if (isHovering || Math.abs(targetX - currentX) > 0.5 || Math.abs(targetY - currentY) > 0.5) {
        rafId = requestAnimationFrame(updateMask);
      } else {
        rafId = null;
      }
    }

    hero.addEventListener('mousemove', function (e) {
      var rect = hero.getBoundingClientRect();
      targetX = e.clientX - rect.left;
      targetY = e.clientY - rect.top;

      if (!isHovering) {
        isHovering = true;
        currentX = targetX;
        currentY = targetY;
      }

      if (!rafId) {
        rafId = requestAnimationFrame(updateMask);
      }
    });

    hero.addEventListener('mouseleave', function () {
      isHovering = false;
      revealLayer.style.webkitMaskImage = 'radial-gradient(circle 0px at 50% 50%, black 0%, transparent 0%)';
      revealLayer.style.maskImage = 'radial-gradient(circle 0px at 50% 50%, black 0%, transparent 0%)';
    });
  }

})();
