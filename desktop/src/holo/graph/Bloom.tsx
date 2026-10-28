// Restrained bloom (three's UnrealBloomPass, no extra dependency): only what is already bright – impulses, the
// core, active nodes – gets a soft halo. Half resolution; mounted only on the high quality tier and switched off
// while the microphone records (the GPU then belongs to speech recognition).
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

export function Bloom({ strength = 0.42, radius = 0.42, threshold = 0.34 }: { strength?: number; radius?: number; threshold?: number }) {
  const { gl, scene, camera, size } = useThree();
  const { composer, bloom } = useMemo(() => {
    const c = new EffectComposer(gl);
    c.addPass(new RenderPass(scene, camera));
    const b = new UnrealBloomPass(new THREE.Vector2(256, 256), strength, radius, threshold);
    c.addPass(b);
    return { composer: c, bloom: b };
  }, [gl, scene, camera, strength, radius, threshold]);
  useEffect(() => {
    composer.setPixelRatio(gl.getPixelRatio());
    composer.setSize(size.width, size.height);
    bloom.resolution.set(Math.max(1, size.width / 2), Math.max(1, size.height / 2));
  }, [composer, bloom, gl, size]);
  useEffect(() => () => composer.dispose(), [composer]);
  // priority 1: R3F stops its own render call and this composer draws the frame
  useFrame(() => composer.render(), 1);
  return null;
}
