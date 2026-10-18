import { useEffect, useRef } from 'react';
import { useDemoTheme } from './demo-theme';
import './zoorq-ecosystem.css';

/** A route bends around an interruption and resolves into the Q tail. */
export function ZoorqEcosystem() {
  const { resolvedTheme } = useDemoTheme();
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let release: (() => void) | undefined;

    const mount = async () => {
      const THREE = await import('three');
      if (disposed) return;
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
      const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = resolvedTheme === 'dark' ? 0.9 : 1.06;
      renderer.domElement.className = 'zoorq-ecosystem-canvas';
      renderer.domElement.setAttribute('aria-hidden', 'true');
      host.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 30);
      camera.position.set(0, 0, 5.5);
      camera.lookAt(0, 0, 0);
      const sculpture = new THREE.Group();
      sculpture.rotation.set(-0.35, -0.35, -0.075);
      sculpture.position.y = 0.18;
      scene.add(sculpture);
      const baseRotation = { x: sculpture.rotation.x, y: sculpture.rotation.y };

      const porcelain = new THREE.MeshStandardMaterial({ color: resolvedTheme === 'dark' ? 0xb9bec8 : 0xe5e3de, roughness: 0.64, metalness: 0.03 });
      const porcelainSide = new THREE.MeshStandardMaterial({ color: resolvedTheme === 'dark' ? 0x616a79 : 0x909aaa, roughness: 0.67, metalness: 0.04, side: THREE.DoubleSide });
      const cobalt = new THREE.MeshStandardMaterial({ color: resolvedTheme === 'dark' ? 0x5679c4 : 0x2752a5, roughness: 0.55, metalness: 0.05 });
      const amber = new THREE.MeshStandardMaterial({ color: 0xb87936, roughness: 0.62, metalness: 0.03, side: THREE.DoubleSide });

      const ringPoints = Array.from({ length: 90 }, (_, i) => {
        const angle = (-20 + 310 * i / 89) * Math.PI / 180;
        return new THREE.Vector3(Math.cos(angle) * 1.13, Math.sin(angle) * 1.13, Math.sin(angle * 2) * 0.12);
      });
      // Beveled rectangular section: broad porcelain face, visible graphite sidewall.
      // Each station follows the Q arc, so light reads on a real extruded route.
      const section: [number, number][] = [
        [-0.095, 0.085], [0.095, 0.085], [0.12, 0.055], [0.12, -0.055],
        [0.095, -0.085], [-0.095, -0.085], [-0.12, -0.055], [-0.12, 0.055],
      ];
      const positions: number[] = [];
      const faceIndices: number[] = [];
      const sideIndices: number[] = [];
      const eventIndices: number[] = [];
      const ringGeometry = new THREE.BufferGeometry();
      ringPoints.forEach((point, station) => {
        const angle = (-20 + 310 * station / 89) * Math.PI / 180;
        const radialX = Math.cos(angle), radialY = Math.sin(angle);
        section.forEach(([radial, depth]) => positions.push(point.x + radialX * radial, point.y + radialY * radial, point.z + depth));
      });
      for (let station = 0; station < ringPoints.length - 1; station++) {
        for (let edgeIndex = 0; edgeIndex < section.length; edgeIndex++) {
          const nextEdge = (edgeIndex + 1) % section.length;
          const a = station * section.length + edgeIndex;
          const b = station * section.length + nextEdge;
          const c = (station + 1) * section.length + edgeIndex;
          const d = (station + 1) * section.length + nextEdge;
          const endSegment = station < 2 || station >= ringPoints.length - 3;
          const target = endSegment ? eventIndices : edgeIndex === 0 || edgeIndex === 1 || edgeIndex === 7 ? faceIndices : sideIndices;
          target.push(a, b, c, b, d, c);
        }
      }
      for (const station of [0, ringPoints.length - 1]) {
        const center = positions.length / 3;
        const point = ringPoints[station];
        positions.push(point.x, point.y, point.z);
        for (let i = 0; i < section.length; i++) {
          eventIndices.push(center, station * section.length + i, station * section.length + (i + 1) % section.length);
        }
      }
      ringGeometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      ringGeometry.setIndex([...faceIndices, ...sideIndices, ...eventIndices]);
      ringGeometry.addGroup(0, faceIndices.length, 0);
      ringGeometry.addGroup(faceIndices.length, sideIndices.length, 1);
      ringGeometry.addGroup(faceIndices.length + sideIndices.length, eventIndices.length, 2);
      ringGeometry.computeVertexNormals();
      const ring = new THREE.Mesh(ringGeometry, [porcelain, porcelainSide, amber]);
      sculpture.add(ring);
      const bypassCurve = new THREE.CatmullRomCurve3([
        ringPoints[ringPoints.length - 1],
        new THREE.Vector3(0.69, -1.28, 0.14),
        new THREE.Vector3(1.31, -1.18, 0.27),
        new THREE.Vector3(1.57, -0.76, 0.34),
        new THREE.Vector3(1.42, -0.20, 0.19),
        ringPoints[0],
      ], false, 'centripetal');
      const bypassGeometry = new THREE.TubeGeometry(bypassCurve, 100, 0.06, 10, false);
      const bypass = new THREE.Mesh(bypassGeometry, cobalt);
      sculpture.add(bypass);

      const tailGeometry = new THREE.TubeGeometry(new THREE.CatmullRomCurve3([
        new THREE.Vector3(1.31, -1.18, 0.27),
        new THREE.Vector3(1.54, -1.30, 0.30),
        new THREE.Vector3(1.84, -1.52, 0.31),
      ]), 36, 0.058, 10, false);
      const tail = new THREE.Mesh(tailGeometry, cobalt);
      sculpture.add(tail);
      const tailCap = new THREE.Mesh(new THREE.SphereGeometry(0.058, 16, 12), cobalt);
      tailCap.position.set(1.84, -1.52, 0.31);
      sculpture.add(tailCap);

      scene.add(new THREE.AmbientLight(0xffffff, resolvedTheme === 'dark' ? 0.45 : 0.62));
      const key = new THREE.DirectionalLight(0xffffff, resolvedTheme === 'dark' ? 1.85 : 2.3);
      key.position.set(-2.3, 3.8, 5);
      scene.add(key);
      const edge = new THREE.DirectionalLight(0x8eafff, 0.75);
      edge.position.set(3, -1, 3);
      scene.add(edge);

      const targetTilt = { x: 0, y: 0 };
      let visible = true;
      let lastFrame = 0;
      let frame = 0;
      let width = 0;
      let height = 0;
      let firstPaint = false;

      const draw = () => {
        renderer.render(scene, camera);
        if (!firstPaint) { host.classList.add('zoorq-ecosystem-ready'); firstPaint = true; }
      };
      const active = () => !disposed && visible && !document.hidden;
      const tick = (now: number) => {
        frame = 0;
        if (!active()) return;
        if (now - lastFrame < 1000 / 30) { frame = requestAnimationFrame(tick); return; }
        lastFrame = now;
        sculpture.rotation.y += (baseRotation.y + targetTilt.x - sculpture.rotation.y) * 0.11;
        sculpture.rotation.x += (baseRotation.x + targetTilt.y - sculpture.rotation.x) * 0.11;
        draw();
        const moving = Math.abs(baseRotation.y + targetTilt.x - sculpture.rotation.y) + Math.abs(baseRotation.x + targetTilt.y - sculpture.rotation.x) > 0.001;
        if (moving) frame = requestAnimationFrame(tick);
      };
      const schedule = () => { if (active() && !frame) frame = requestAnimationFrame(tick); };
      const resize = () => {
        const bounds = host.getBoundingClientRect();
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.position.z = width < 460 ? 5.15 : 5.5;
        camera.updateProjectionMatrix();
        draw();
      };
      const pointerMove = (event: PointerEvent) => {
        if (reducedMotion.matches || !width || !height) return;
        const bounds = host.getBoundingClientRect();
        targetTilt.x = ((event.clientX - bounds.left) / width - 0.5) * 0.13;
        targetTilt.y = ((event.clientY - bounds.top) / height - 0.5) * -0.13;
        schedule();
      };
      const pointerLeave = () => { targetTilt.x = 0; targetTilt.y = 0; schedule(); };
      const sync = () => {
        if (!active()) { cancelAnimationFrame(frame); frame = 0; lastFrame = 0; return; }
        if (reducedMotion.matches) { targetTilt.x = 0; targetTilt.y = 0; sculpture.rotation.set(baseRotation.x, baseRotation.y, sculpture.rotation.z); draw(); }
        schedule();
      };
      const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync(); }, { threshold: 0 });
      const resizer = new ResizeObserver(resize);
      let listening = false;
      let released = false;
      release = () => {
        if (released) return;
        released = true;
        cancelAnimationFrame(frame);
        if (listening) {
          host.removeEventListener('pointermove', pointerMove);
          host.removeEventListener('pointerleave', pointerLeave);
          document.removeEventListener('visibilitychange', sync);
          reducedMotion.removeEventListener('change', sync);
          intersection.disconnect();
          resizer.disconnect();
        }
        host.classList.remove('zoorq-ecosystem-ready');
        [ringGeometry, bypassGeometry, tailGeometry].forEach(geometry => geometry.dispose());
        sculpture.traverse(object => { if (object instanceof THREE.Mesh && object.geometry !== ringGeometry && object.geometry !== bypassGeometry && object.geometry !== tailGeometry) object.geometry.dispose(); });
        [porcelain, porcelainSide, cobalt, amber].forEach(material => material.dispose());
        renderer.dispose();
        renderer.forceContextLoss();
        renderer.domElement.remove();
      };
      // Yield to the browser, then prepare shaders before the first WebGL draw.
      await new Promise<void>(resolve => window.setTimeout(resolve, 0));
      if (disposed) return;
      const bounds = host.getBoundingClientRect();
      renderer.setSize(Math.max(1, bounds.width), Math.max(1, bounds.height), false);
      camera.aspect = Math.max(1, bounds.width) / Math.max(1, bounds.height);
      camera.position.z = bounds.width < 460 ? 5.15 : 5.5;
      camera.updateProjectionMatrix();
      await renderer.compileAsync(scene, camera);
      if (disposed) return;
      listening = true;
      host.addEventListener('pointermove', pointerMove);
      host.addEventListener('pointerleave', pointerLeave);
      document.addEventListener('visibilitychange', sync);
      reducedMotion.addEventListener('change', sync);
      intersection.observe(host);
      resizer.observe(host);
      resize();
    };

    mount().catch(() => { release?.(); /* Static route remains visible when WebGL is unavailable. */ });
    return () => { disposed = true; release?.(); };
  }, [resolvedTheme]);

  return <div className="zoorq-ecosystem" ref={hostRef} aria-hidden="true">
    <svg className="zoorq-ecosystem-fallback" viewBox="0 0 560 390" fill="none" focusable="false">
      <defs>
        <linearGradient id="zoorq-ceramic" x1="152" y1="61" x2="379" y2="311"><stop stopColor="#ffffff" /><stop offset=".55" stopColor="#dfdfd8" /><stop offset="1" stopColor="#a8adb5" /></linearGradient>
        <linearGradient id="zoorq-cobalt" x1="300" y1="255" x2="480" y2="360"><stop stopColor="#174aaf" /><stop offset="1" stopColor="#6a94f3" /></linearGradient>
        <filter id="zoorq-shadow" x="0" y="0" width="560" height="390"><feGaussianBlur stdDeviation="15" /></filter>
      </defs>
      <ellipse cx="292" cy="348" rx="130" ry="13" fill="#222b39" opacity=".09" filter="url(#zoorq-shadow)" />
      <path d="M387 172C365 119 323 88 268 88 194 88 134 145 134 216s60 127 134 127c16 0 30-2 43-7" stroke="#6d7786" strokeWidth="25" strokeLinecap="butt" transform="translate(2 5)" />
      <path d="M387 172C365 119 323 88 268 88 194 88 134 145 134 216s60 127 134 127c16 0 30-2 43-7" stroke="url(#zoorq-ceramic)" strokeWidth="18" strokeLinecap="butt" />
      <path d="M311 336c47 13 112-10 133-62 17-43-4-82-57-102" stroke="url(#zoorq-cobalt)" strokeWidth="12" strokeLinecap="round" />
      <path d="m381 324 70 50" stroke="url(#zoorq-cobalt)" strokeWidth="12" strokeLinecap="round" />
      <path d="m386 177 4-10M306 336l10-3" stroke="#b87936" strokeWidth="19" strokeLinecap="butt" />
    </svg>
  </div>;
}
