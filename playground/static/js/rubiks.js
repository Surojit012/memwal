(function () {
  'use strict';

  // Wait for Three.js to load
  if (typeof THREE === 'undefined') {
    var attempts = 0;
    var timer = setInterval(function () {
      attempts++;
      if (typeof THREE !== 'undefined') {
        clearInterval(timer);
        initRubiks();
      } else if (attempts > 50) {
        clearInterval(timer);
      }
    }, 100);
    return;
  } else {
    initRubiks();
  }

  function initRubiks() {
    var container = document.getElementById('rubiks-container');
    if (!container) return;

    var scene = new THREE.Scene();
    
    // Setup camera
    var aspect = container.clientWidth / container.clientHeight;
    var camera = new THREE.PerspectiveCamera(45, aspect, 0.1, 1000);
    camera.position.set(5.5, 4.5, 6.5);
    camera.lookAt(0, 0, 0);

    // Setup renderer with transparent background
    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // Lighting
    var ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);
    
    var dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(5, 10, 7.5);
    scene.add(dirLight);

    var dirLight2 = new THREE.DirectionalLight(0xffffff, 0.4);
    dirLight2.position.set(-5, -5, -5);
    scene.add(dirLight2);

    // Palette matching the new blue theme
    var colors = [
      0xC1E8FF, // Right
      0x7DA0CA, // Left
      0x5483B3, // Top
      0x0e3d7a, // Bottom
      0x052659, // Front
      0x0a3270  // Back
    ];
    var coreColor = 0x021024; // Inner face color

    var cubeSize = 0.95; // Slightly less than 1 to give small gaps
    var cubes = [];
    var allCubesGroup = new THREE.Group();
    scene.add(allCubesGroup);

    var pivot = new THREE.Object3D();
    allCubesGroup.add(pivot);

    // Create 3x3x3 rubik's cube
    for (var x = -1; x <= 1; x++) {
      for (var y = -1; y <= 1; y++) {
        for (var z = -1; z <= 1; z++) {
          var geometry = new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize);
          
          // Make an array of materials for each of the 6 faces
          var materials = [];
          for (var i = 0; i < 6; i++) {
            // Only color the face if it's on the outer edge of the 3x3x3 grid
            var isOuter = false;
            if (i === 0 && x === 1) isOuter = true; // Right
            if (i === 1 && x === -1) isOuter = true; // Left
            if (i === 2 && y === 1) isOuter = true; // Top
            if (i === 3 && y === -1) isOuter = true; // Bottom
            if (i === 4 && z === 1) isOuter = true; // Front
            if (i === 5 && z === -1) isOuter = true; // Back

            materials.push(new THREE.MeshStandardMaterial({
              color: isOuter ? colors[i] : coreColor,
              roughness: 0.2,
              metalness: 0.1,
              transparent: true,
              opacity: isOuter ? 0.9 : 0.4
            }));
          }

          var cube = new THREE.Mesh(geometry, materials);
          cube.position.set(x, y, z);
          
          // Add a subtle border helper to each cubelet
          var edges = new THREE.EdgesGeometry(geometry);
          var line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ 
            color: 0x7DA0CA, 
            transparent: true, 
            opacity: 0.3 
          }));
          cube.add(line);

          cube.userData = { x: x, y: y, z: z }; // Store logical position
          cubes.push(cube);
          allCubesGroup.add(cube);
        }
      }
    }

    // Animation state
    var queue = [];
    var isAnimating = false;
    var moveHistory = [];
    var state = 'idle'; // idle -> shuffling -> waiting -> solving -> waiting

    // Main animation loop
    var lastTime = 0;
    function animate(time) {
      requestAnimationFrame(animate);
      var delta = time - lastTime;
      lastTime = time;

      // Slowly rotate the entire rubik's cube assembly for visual interest
      allCubesGroup.rotation.y += 0.005;
      allCubesGroup.rotation.x += 0.002;

      processQueue();
      renderer.render(scene, camera);
    }

    function processQueue() {
      if (isAnimating || queue.length === 0) {
        if (!isAnimating && queue.length === 0) {
          checkStateTransition();
        }
        return;
      }

      var move = queue.shift();
      isAnimating = true;

      // Find cubes to rotate based on axis and slice index
      var activeCubes = [];
      cubes.forEach(function(c) {
        // We round the world position to handle floating point inaccuracies after rotations
        var pos = new THREE.Vector3();
        c.getWorldPosition(pos);
        allCubesGroup.worldToLocal(pos); // Get position relative to the main group

        var val = Math.round(pos[move.axis]);
        if (val === move.slice) {
          activeCubes.push(c);
        }
      });

      // Attach active cubes to pivot
      pivot.rotation.set(0, 0, 0);
      pivot.updateMatrixWorld();
      
      activeCubes.forEach(function(c) {
        pivot.attach(c);
      });

      // Animate rotation using GSAP if available, else simple lerp
      var targetRotation = move.angle;
      var obj = { r: 0 };
      
      if (typeof window.gsap !== 'undefined') {
        window.gsap.to(obj, {
          r: targetRotation,
          duration: move.duration / 1000,
          ease: 'power2.inOut',
          onUpdate: function() {
            pivot.rotation[move.axis] = obj.r;
          },
          onComplete: finalizeMove
        });
      } else {
        // Fallback animation
        var start = performance.now();
        function fallbackAnim(now) {
          var p = Math.min((now - start) / move.duration, 1);
          // Ease in out quad
          var easeP = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
          pivot.rotation[move.axis] = easeP * targetRotation;
          if (p < 1) {
            requestAnimationFrame(fallbackAnim);
          } else {
            finalizeMove();
          }
        }
        requestAnimationFrame(fallbackAnim);
      }

      function finalizeMove() {
        pivot.updateMatrixWorld();
        // Detach and re-attach to the main group
        activeCubes.forEach(function(c) {
          allCubesGroup.attach(c);
          
          // Clean up rotation matrix to prevent floating point drift over time
          c.updateMatrixWorld();
          var euler = new THREE.Euler().setFromRotationMatrix(c.matrix);
          c.rotation.set(
            Math.round(euler.x / (Math.PI/2)) * (Math.PI/2),
            Math.round(euler.y / (Math.PI/2)) * (Math.PI/2),
            Math.round(euler.z / (Math.PI/2)) * (Math.PI/2)
          );
        });
        
        isAnimating = false;
      }
    }

    function checkStateTransition() {
      if (state === 'idle') {
        state = 'shuffling';
        generateShuffle(15);
      } else if (state === 'shuffling') {
        state = 'waiting_to_solve';
        setTimeout(function() {
          state = 'solving';
          generateSolve();
        }, 1500); // Wait 1.5s before solving
      } else if (state === 'solving') {
        state = 'waiting_to_shuffle';
        setTimeout(function() {
          state = 'idle'; // Loop back
        }, 3000); // Wait 3s before shuffling again
      }
    }

    var axes = ['x', 'y', 'z'];
    var slices = [-1, 0, 1];
    var angles = [Math.PI / 2, -Math.PI / 2];

    function generateShuffle(movesCount) {
      moveHistory = [];
      for (var i = 0; i < movesCount; i++) {
        var axis = axes[Math.floor(Math.random() * axes.length)];
        var slice = slices[Math.floor(Math.random() * slices.length)];
        var angle = angles[Math.floor(Math.random() * angles.length)];
        
        var move = { axis: axis, slice: slice, angle: angle, duration: 250 };
        queue.push(move);
        // Save inverse move for solving
        moveHistory.push({ axis: axis, slice: slice, angle: -angle, duration: 150 });
      }
    }

    function generateSolve() {
      // Reverse the history and queue it up
      var solveMoves = moveHistory.reverse();
      solveMoves.forEach(function(m) {
        queue.push(m);
      });
      moveHistory = []; // Clear
    }

    // Handle resize
    window.addEventListener('resize', function() {
      if (!container) return;
      var width = container.clientWidth;
      var height = container.clientHeight;
      renderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    });

    // Start
    requestAnimationFrame(animate);
    
    // Initial delay before starting the loop
    setTimeout(function() {
      checkStateTransition();
    }, 1000);
  }

})();
