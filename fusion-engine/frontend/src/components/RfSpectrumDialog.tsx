import { useEffect, useRef } from "react";
import { X, Zap } from "lucide-react";
import { Button } from "./ui/button";

// Deterministic synthetic waterfall. Every carrier stays at a fixed frequency.
const powerColors = [[0, 0, 55], [0, 15, 175], [0, 90, 255], [0, 225, 255], [95, 255, 70], [255, 240, 0], [255, 45, 0]];
function noise(x: number, y: number) {
  let value = Math.imul(x + 1, 374761393) ^ Math.imul(y + 42, 668265263);
  value = Math.imul(value ^ (value >>> 13), 1274126177);
  return ((value ^ (value >>> 16)) >>> 0) / 4294967296;
}

function drawSpectrum(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const width = canvas.width, height = canvas.height;
  const pixels = ctx.createImageData(width, height);
  for (let y = 0; y < height; y++) {
    const frequency = 200 - y / (height - 1) * 100;
    const band = (center: number, bandwidth: number) => Math.exp(-(((frequency - center) / bandwidth) ** 2));
    for (let x = 0; x < width; x++) {
      const time = x / (width - 1) * 60;
      const grain = noise(x, y);
      const texture = noise(Math.floor(x / 3), Math.floor(y / 2));
      const flicker = .8 + .2 * noise(x, 900);
      let power = .045 + .18 * grain ** 2 + .12 * texture ** 3;
      // Continuous narrow carrier, weak channels, and a broad noisy emission.
      power += .78 * flicker * band(184, .42);
      power += .13 * band(193, .3);
      power += (time % 4 < 3.4 ? .27 : .08) * flicker * band(173, .38);
      power += .7 * (.7 + .3 * grain) * band(134, 2.5);
      // Repeated short transmissions create the dashed horizontal trace.
      power += (time % 3 < 1.5 ? .76 : .015) * flicker * band(148, .48);
      power += (time >= 6 && time <= 29 ? .58 : .05) * flicker * band(162, .45);
      // A faint lower carrier with occasional bright pulses at the same frequency.
      const pulse = [5.5, 18.3, 31.8, 43.3, 55].reduce((sum, center) => sum + Math.exp(-(((time - center) / .15) ** 2)), 0);
      power += .25 * flicker * band(117, .36) + .46 * pulse * band(117, 1);
      const scaled = Math.min(1, power) * (powerColors.length - 1);
      const index = Math.min(powerColors.length - 2, Math.floor(scaled));
      const mix = scaled - index;
      const offset = (y * width + x) * 4;
      for (let c = 0; c < 3; c++) pixels.data[offset + c] = powerColors[index][c] * (1 - mix) + powerColors[index + 1][c] * mix;
      pixels.data[offset + 3] = 255;
    }
  }
  ctx.putImageData(pixels, 0, 0);
}

export function RfSpectrumDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    if (canvas.current) drawSpectrum(canvas.current);
    return () => { dialog.current?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={dialog} aria-labelledby="rf-title" onCancel={onClose}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    className="m-auto max-h-[90vh] w-[calc(100%-2rem)] max-w-4xl overflow-auto border border-line bg-panel p-0 text-ink shadow-panel backdrop:bg-black/70">
    <section className="p-5">
      <header className="flex items-start gap-3">
        <Zap size={20} className="mt-1 text-command" />
        <div><h2 id="rf-title" className="font-mono text-sm font-bold uppercase tracking-wider">RF · Spectral display</h2>
          <p className="mt-1 text-xs text-muted">Strait of Hormuz · 17 AUG 2026 091347Z · 26.550°N, 56.450°E</p></div>
        <Button autoFocus className="ml-auto" size="sm" variant="ghost" onClick={onClose} aria-label="Close RF spectrum"><X size={18} /></Button>
      </header>
      <div className="my-4 flex flex-wrap gap-3 font-mono text-[10px] uppercase text-command"><span>Simulated sample</span><span>Duration 01:00</span><span>100–200 MHz</span></div>
      <div className="overflow-x-auto">
        <div className="min-w-[480px]">
          <div className="relative ml-20 mr-4">
            <span className="absolute -left-20 top-1/2 -translate-y-1/2 [writing-mode:vertical-rl] rotate-180 font-mono text-[11px] text-muted">Frequency (MHz)</span>
            <canvas ref={canvas} width={900} height={450} className="block aspect-[2/1] w-full border border-line" role="img" aria-label="Simulated spectrogram, time 0 to 60 seconds, frequency 100 to 200 MHz. Horizontal continuous carriers and repeating bursts at fixed frequencies against a textured blue noise floor. Cyan, yellow, and red indicate increasing signal power." />
            {[200, 180, 160, 140, 120, 100].map((frequency, index) => <div key={frequency} className="pointer-events-none absolute left-0 right-0 border-t border-white/5" style={{ top: `${index * 20}%` }}><span className="absolute right-full -translate-y-1/2 pr-2 font-mono text-[10px] text-muted">{frequency}</span></div>)}
            {[0, 10, 20, 30, 40, 50, 60].map((time) => <div key={time} className="pointer-events-none absolute bottom-0 top-0 border-l border-white/5" style={{ left: `${time / 60 * 100}%` }}><span className="absolute top-full mt-2 -translate-x-1/2 font-mono text-[10px] text-muted">{time}</span></div>)}
          </div>
          <p className="mb-3 ml-20 mt-8 text-center font-mono text-[11px] text-muted">Elapsed time (seconds)</p>
        </div>
      </div>
      <footer className="border-t border-line pt-3 text-xs text-muted">
        <div className="mb-2 flex items-center gap-2"><span>Low</span><span className="h-2 w-32 bg-[linear-gradient(90deg,#000037,#000faf,#005aff,#00e1ff,#5fff46,#fff000,#ff2d00)]" /><span>High relative power</span></div>
        Synthetic carriers and bursts at fixed frequencies over a one-minute capture. Demonstration data; no live receiver connected.
      </footer>
    </section>
  </dialog>;
}
