// Web Worker: the force layout never blocks the UI thread (or the voice daemon's CPU share for long).
import { createLayout, type Layout, type LayoutRequest, type LayoutResponse } from "./layout";

let layout: Layout | null = null;
let positions = new Float32Array(0);
let timer: ReturnType<typeof setTimeout> | null = null;

function post(msg: LayoutResponse) {
  (self as unknown as Worker).postMessage(msg);
}

function loop() {
  timer = null;
  if (!layout) return;
  const alpha = layout.tick(2);
  layout.positions(positions);
  post({ type: "tick", positions: positions.slice(), alpha });
  if (alpha > 0.004) timer = setTimeout(loop, 33);
  else post({ type: "settled" });
}

self.onmessage = (e: MessageEvent<LayoutRequest>) => {
  const msg = e.data;
  if (msg.type === "init") {
    positions = new Float32Array(msg.positions);
    layout = createLayout(msg.nodes, msg.links, positions, msg.alpha);
    if (timer) clearTimeout(timer);
    loop();
  }
};
