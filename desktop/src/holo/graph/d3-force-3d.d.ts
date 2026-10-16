// Minimal typings for d3-force-3d (the package ships none); only what the layout worker uses.
declare module "d3-force-3d" {
  export interface SimNode {
    index?: number;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    fx?: number | null;
    fy?: number | null;
    fz?: number | null;
  }
  export interface SimLink<N extends SimNode> {
    source: number | N;
    target: number | N;
  }
  export interface Force<N extends SimNode> {
    (alpha: number): void;
    initialize?: (nodes: N[]) => void;
  }
  export interface Simulation<N extends SimNode> {
    nodes(): N[];
    nodes(nodes: N[]): this;
    force(name: string, force: unknown): this;
    alpha(): number;
    alpha(a: number): this;
    alphaDecay(d: number): this;
    alphaMin(m: number): this;
    velocityDecay(v: number): this;
    stop(): this;
    tick(n?: number): this;
  }
  export function forceSimulation<N extends SimNode>(nodes?: N[], numDimensions?: number): Simulation<N>;
  export function forceLink<N extends SimNode, L extends SimLink<N>>(links?: L[]): {
    id(fn: (n: N) => string | number): ReturnType<typeof forceLink<N, L>>;
    distance(d: number | ((l: L) => number)): ReturnType<typeof forceLink<N, L>>;
    strength(s: number | ((l: L) => number)): ReturnType<typeof forceLink<N, L>>;
  };
  export function forceManyBody<N extends SimNode>(): {
    strength(s: number | ((n: N) => number)): ReturnType<typeof forceManyBody<N>>;
    theta(t: number): ReturnType<typeof forceManyBody<N>>;
    distanceMax(d: number): ReturnType<typeof forceManyBody<N>>;
  };
}
