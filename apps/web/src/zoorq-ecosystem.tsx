import { useEffect, useRef } from 'react';
import { useDemoTheme } from './demo-theme';
import './zoorq-ecosystem.css';

type Point3 = { x: number; y: number; z: number };
type Projected = { x: number; y: number; z: number; scale: number };

// A fixed, illustrative constellation. It represents no suppliers, routes, or live data.
const nodes: Point3[] = Array.from({ length: 58 }, (_, index) => {
  const y = 1 - (index + 0.5) * 2 / 58;
  const radius = Math.sqrt(1 - y * y);
  const angle = index * Math.PI * (3 - Math.sqrt(5));
  return { x: Math.cos(angle) * radius, y, z: Math.sin(angle) * radius };
});

const links: [number, number][] = nodes.flatMap((node, index) => {
  const nearest = nodes
    .map((other, otherIndex) => ({ otherIndex, distance: (node.x - other.x) ** 2 + (node.y - other.y) ** 2 + (node.z - other.z) ** 2 }))
    .filter(({ otherIndex }) => otherIndex > index)
    .sort((a, b) => a.distance - b.distance)
    .slice(0, 2);
  return nearest.map(({ otherIndex }) => [index, otherIndex] as [number, number]);
});

function rotate(point: Point3, xAngle: number, yAngle: number): Point3 {
  const cx = Math.cos(xAngle);
  const sx = Math.sin(xAngle);
  const cy = Math.cos(yAngle);
  const sy = Math.sin(yAngle);
  const y = point.y * cx - point.z * sx;
  const z = point.y * sx + point.z * cx;
  return { x: point.x * cy + z * sy, y, z: z * cy - point.x * sy };
}

function drawNetwork(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  theme: 'light' | 'dark',
  phase: number,
  tilt: { x: number; y: number },
) {
  ctx.clearRect(0, 0, width, height);
  const dark = theme === 'dark';
  const centerX = width * 0.53;
  const centerY = height * 0.5;
  const radius = Math.min(height * 0.425, width * 0.295);
  const xAngle = -0.23 + tilt.y;
  const yAngle = -0.35 + phase * 0.23 + tilt.x;
  const perspective = (point: Point3): Projected => {
    const rotated = rotate(point, xAngle, yAngle);
    const scale = 2.9 / (2.9 - rotated.z * 0.33);
    return { x: centerX + rotated.x * radius * scale, y: centerY + rotated.y * radius * scale, z: rotated.z, scale };
  };
  const projected = nodes.map(perspective);

  // The faint spherical guides add volume without becoming a solid globe.
  ctx.save();
  ctx.strokeStyle = dark ? 'rgba(130, 194, 255, 0.12)' : 'rgba(31, 91, 188, 0.12)';
  ctx.lineWidth = 1;
  for (const latitude of [-0.58, 0, 0.58]) {
    ctx.beginPath();
    for (let step = 0; step <= 88; step++) {
      const angle = step / 88 * Math.PI * 2;
      const ringRadius = Math.sqrt(1 - latitude * latitude);
      const point = perspective({ x: Math.cos(angle) * ringRadius, y: latitude, z: Math.sin(angle) * ringRadius });
      if (step === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    }
    ctx.stroke();
  }
  ctx.restore();

  // A few broad orbit paths make the network read as dimensional at small sizes.
  for (let orbit = 0; orbit < 3; orbit++) {
    ctx.beginPath();
    for (let step = 0; step <= 104; step++) {
      const angle = step / 104 * Math.PI * 2;
      const point = perspective(rotate(
        { x: Math.cos(angle) * 1.13, y: Math.sin(angle) * 1.13, z: 0 },
        0.47 + orbit * 0.62,
        orbit * 1.05,
      ));
      if (step === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    }
    ctx.strokeStyle = dark ? `rgba(105, 192, 255, ${0.17 + orbit * 0.045})` : `rgba(25, 89, 204, ${0.15 + orbit * 0.04})`;
    ctx.lineWidth = orbit === 1 ? 1.25 : 0.9;
    ctx.stroke();
  }

  for (const [from, to] of links) {
    const a = projected[from];
    const b = projected[to];
    const depth = (a.z + b.z) * 0.5;
    const opacity = depth > 0 ? 0.25 + depth * 0.28 : 0.09;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.strokeStyle = dark ? `rgba(117, 197, 255, ${opacity})` : `rgba(28, 88, 190, ${opacity})`;
    ctx.lineWidth = depth > 0 ? 1.1 : 0.8;
    ctx.stroke();
  }

  // The primary hubs are brighter; the rest remain a fine network texture.
  projected
    .map((point, index) => ({ point, index }))
    .sort((a, b) => a.point.z - b.point.z)
    .forEach(({ point, index }) => {
      const hub = index % 11 === 0 || index === 27;
      const near = Math.max(0, (point.z + 1) * 0.5);
      const size = (hub ? 4.1 : 1.7) * point.scale * (0.78 + near * 0.4);
      if (hub && point.z > -0.1) {
        const glow = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, size * 6);
        glow.addColorStop(0, dark ? 'rgba(104, 203, 255, 0.26)' : 'rgba(23, 99, 224, 0.22)');
        glow.addColorStop(1, 'rgba(104, 203, 255, 0)');
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(point.x, point.y, size * 6, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = dark
        ? `rgba(${hub ? '168, 231, 255' : '105, 193, 255'}, ${0.42 + near * 0.58})`
        : `rgba(${hub ? '19, 84, 201' : '49, 124, 224'}, ${0.37 + near * 0.63})`;
      ctx.beginPath();
      ctx.arc(point.x, point.y, size, 0, Math.PI * 2);
      ctx.fill();
      if (hub && point.z > 0) {
        ctx.strokeStyle = dark ? 'rgba(185, 233, 255, 0.53)' : 'rgba(25, 90, 199, 0.42)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(point.x, point.y, size * 2.2, 0, Math.PI * 2);
        ctx.stroke();
      }
    });
}

export function ZoorqEcosystem() {
  const { resolvedTheme } = useDemoTheme();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const tilt = { x: 0, y: 0 };
    const target = { x: 0, y: 0 };
    let width = 0;
    let height = 0;
    let phase = 0;
    let lastFrame = 0;
    let frame = 0;
    let visible = true;
    let disposed = false;

    const paint = () => {
      if (width > 0 && height > 0) drawNetwork(ctx, width, height, resolvedTheme, phase, tilt);
    };
    const active = () => !disposed && visible && !document.hidden && !motionQuery.matches;
    const tick = (now: number) => {
      frame = 0;
      if (!active()) return;
      if (now - lastFrame >= 1000 / 30) {
        const delta = lastFrame ? Math.min((now - lastFrame) / 1000, 0.06) : 0;
        lastFrame = now;
        phase += delta;
        tilt.x += (target.x - tilt.x) * 0.075;
        tilt.y += (target.y - tilt.y) * 0.075;
        paint();
      }
      frame = requestAnimationFrame(tick);
    };
    const syncMotion = () => {
      if (!active()) {
        cancelAnimationFrame(frame);
        frame = 0;
        lastFrame = 0;
        if (motionQuery.matches) {
          phase = 0;
          tilt.x = 0;
          tilt.y = 0;
          paint();
        }
      } else if (!frame) {
        lastFrame = 0;
        frame = requestAnimationFrame(tick);
      }
    };
    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      width = bounds.width;
      height = bounds.height;
      if (!width || !height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paint();
    };
    const pointerMove = (event: PointerEvent) => {
      if (motionQuery.matches) return;
      const bounds = canvas.getBoundingClientRect();
      target.x = ((event.clientX - bounds.left) / bounds.width - 0.5) * 0.42;
      target.y = ((event.clientY - bounds.top) / bounds.height - 0.5) * 0.24;
    };
    const pointerLeave = () => { target.x = 0; target.y = 0; };
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      syncMotion();
    }, { threshold: 0 });
    const resizeObserver = new ResizeObserver(resize);

    canvas.addEventListener('pointermove', pointerMove);
    canvas.addEventListener('pointerleave', pointerLeave);
    document.addEventListener('visibilitychange', syncMotion);
    motionQuery.addEventListener('change', syncMotion);
    observer.observe(canvas);
    resizeObserver.observe(canvas);
    resize();
    syncMotion();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      canvas.removeEventListener('pointermove', pointerMove);
      canvas.removeEventListener('pointerleave', pointerLeave);
      document.removeEventListener('visibilitychange', syncMotion);
      motionQuery.removeEventListener('change', syncMotion);
      observer.disconnect();
      resizeObserver.disconnect();
    };
  }, [resolvedTheme]);

  return <div className="zoorq-ecosystem" aria-hidden="true"><canvas ref={canvasRef} className="zoorq-ecosystem-canvas" /></div>;
}
