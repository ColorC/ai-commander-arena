// RPG save-point effect from ics-creative/160304_threejs_save_point (MIT, see LICENSE; src/ at commit of 2026-10-21):
// light pillar, ground swirl and glow, three-layer magic circle, rising orbs / sparks and the burst wave, driven by GSAP.
// Ported from the TypeScript sources almost line for line. Changes, all for the offline programme renderer:
//   - `three` (r170, WebGL) instead of `three/webgpu`; the effect only uses MeshBasic / Sprite additive materials.
//   - textures come from `base` ($ARENA_DATA/video/assets-v9/savepoint/, copied from src/img/).
//   - `tint`: 'blue' keeps the original colours, 'red' swaps the red and blue channels of every computed colour.
//   - `play(at)`: the original start() (entrance, then a pulse repeating forever) becomes entrance + one pulse + the
//     entrance reversed, placed at absolute time `at` on GSAP's global timeline. The caller drives GSAP with
//     gsap.updateRoot(videoSeconds) and passes the frame step to update(), so frames are a function of the video time.
//   - the point light's distance / intensity are scaled with the object (the caller scales the whole effect to a hex).
//   - randomness comes from the `rng` passed in (seeded), not Math.random.
// The WebGPU bloom pass of the original Main.ts is not ported (the additive layers still glow).
import * as THREE from 'three';
// GSAP 3.15 is loaded by sandbox.html as a classic script (its UMD build cannot be imported as a module)
const gsap = window.gsap;

const swapRB = (c, tint) => { if (tint === 'red') { const r = c.r; c.r = c.b; c.b = r; } return c; };

class Pillar extends THREE.Object3D {
  constructor(K, topRadius = 3, bottomRadius = 3, height = 10) {
    super();
    this.K = K; this.brightness = 1;
    const loader = new THREE.TextureLoader();
    this._outerTexture = loader.load(K.base + 'pillar.png');
    this._innerTexture = loader.load(K.base + 'pillar.png');
    for (const t of [this._outerTexture, this._innerTexture]) { t.wrapS = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; }
    this._outerTexture.repeat.x = 6;
    this._innerTexture.repeat.x = 3;
    const geometry = new THREE.CylinderGeometry(topRadius, bottomRadius, height, 64, 1, true);
    const mk = color => new THREE.MeshBasicMaterial({ color, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, transparent: true, depthWrite: false, opacity: 0.5 });
    this._outerMaterial = mk(0x0070e0); this._outerMaterial.map = this._outerTexture;
    this._innerMaterial = mk(0x40d8ff); this._innerMaterial.map = this._innerTexture;
    const outer = new THREE.Mesh(geometry, this._outerMaterial), inner = new THREE.Mesh(geometry, this._innerMaterial);
    outer.position.y = height / 2; inner.position.y = height / 2;
    inner.scale.set(0.95, 1, 0.95);
    this.scale.y = 0.8;
    this.add(outer, inner);
  }
  update(delta, energy) {
    this.scale.y = 0.8 + energy * 0.2;
    this.rotation.y += delta * 0.2;
    this._outerTexture.offset.x += delta * 0.2;
    this._innerTexture.offset.x -= delta * 0.3;
    swapRB(this._outerMaterial.color.setRGB(energy * 0.2, 0.3 + energy * 0.4, 1), this.K.tint).multiplyScalar((1 + energy * 0.5) * this.brightness);
    swapRB(this._innerMaterial.color.setRGB(energy * 0.4, 0.7 + energy * 0.1, 1), this.K.tint).multiplyScalar((1 + energy) * this.brightness);
    this._outerMaterial.opacity = 0.25 + energy * 0.25;
    this._innerMaterial.opacity = this._outerMaterial.opacity * 0.6;
  }
}

class Swirl extends THREE.Object3D {
  constructor(K) {
    super();
    this.K = K;
    this._texture = new THREE.TextureLoader().load(K.base + 'swirl.png');
    this._texture.offset.y = -0.25; this._texture.wrapS = THREE.RepeatWrapping; this._texture.colorSpace = THREE.SRGBColorSpace;
    this._material = new THREE.MeshBasicMaterial({ color: 0x0080ff, map: this._texture, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.1 });
    const mesh = new THREE.Mesh(new THREE.TorusGeometry(4, 1, 2, 100), this._material);
    mesh.position.y = 0.02; mesh.rotation.x = Math.PI / 2;
    this.add(mesh);
  }
  update(delta, energy) {
    const speed = 0.1 + energy * 0.4;
    this.rotation.y -= delta * speed;
    this._texture.offset.x -= delta * speed;
    swapRB(this._material.color.setRGB(energy * 0.8, 0.5 + energy * 0.4, 1), this.K.tint).multiplyScalar(1 + energy * 0.8);
    this._material.opacity = 0.35 + energy * 0.2;
  }
}

class MagicCircle extends THREE.Object3D {
  constructor(K) {
    super();
    this.K = K; this._layers = []; this._materials = []; this._rotations = [];
    const geometry = new THREE.PlaneGeometry(6, 6), loader = new THREE.TextureLoader();
    const layers = [
      { image: 'magic-circle-outer.svg', color: 0x0060e0, duration: 20, scale: 1, turn: 1 },
      { image: 'magic-circle-middle.svg', color: 0x00a0ff, duration: 12, scale: 0.7, turn: -1 },
      { image: 'magic-circle-center.svg', color: 0x60d0ff, duration: 8, scale: 1, turn: 1 },
    ];
    for (const { image, color, duration, scale, turn } of layers) {
      const texture = loader.load(K.base + image); texture.colorSpace = THREE.SRGBColorSpace;
      const material = new THREE.MeshBasicMaterial({ color: swapRB(new THREE.Color(color), K.tint), map: texture, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
      const plane = new THREE.Mesh(geometry, material);
      plane.position.y = 0.1; plane.rotation.x = -Math.PI / 2; plane.scale.setScalar(scale); plane.renderOrder = 1;
      const layer = new THREE.Object3D(); layer.scale.setScalar(0); layer.add(plane); this.add(layer);
      this._layers.push(layer); this._materials.push(material);
      this._rotations.push(gsap.to(layer.rotation, { y: turn * Math.PI * 2, duration, ease: 'none', repeat: -1 }));
    }
  }
  entrance(tl, at) {                                       // was startEntrance(): centre to rim, staggered
    tl.to([...this._layers].reverse().map(l => l.scale), { x: 1, y: 1, z: 1, duration: 0.65, stagger: 0.35, ease: 'back.out(1.4)' }, at);
  }
  exit(tl, at) { tl.to(this._layers.map(l => l.scale), { x: 0, y: 0, z: 0, duration: 0.6, stagger: 0.12, ease: 'power2.in' }, at); }
  update(energy) {
    for (const r of this._rotations) r.timeScale(1 + energy * 2);
    for (const m of this._materials) m.opacity = 0.3 + energy * 0.6;
  }
}

class Particle extends THREE.Sprite {
  constructor(K, index, mats) {
    const spark = index % 3 !== 0;
    super((spark ? mats.spark : mats.orb).clone());
    const R = K.rng, rand = (a, b) => a + (b - a) * R();
    this._spark = spark;
    let color = index % 4 === 0 ? 0x20c0ff : 0x0090ff;
    if (spark) color = 0x80d8ff;
    swapRB(this.material.color.set(color), K.tint).multiplyScalar(spark ? 2 : 1);
    this._color = this.material.color.clone();
    this._motion = { twinkle: 1 };
    const angle = R() * Math.PI * 2, radius = rand(0.5, 3), x = Math.cos(angle) * radius, z = Math.sin(angle) * radius;
    const duration = rand(6, 10), size = rand(spark ? 0.4 : 0.1, spark ? 0.6 : 0.2), displaySize = spark ? size * mats.scale : size;
    this.position.set(x, 0.5, z); this.material.rotation = angle; this.scale.setScalar(displaySize);
    this.material.opacity = 0;
    this.tweens = [
      gsap.timeline({ repeat: -1, repeatRefresh: true })
        .fromTo(this.position, { y: 0.5 }, { y: () => rand(5, 8), duration, ease: 'expo.in' }, 0)
        .set(this.material, { opacity: 0 }, 0)
        .to(this.material, { opacity: 1, duration: duration - 1, ease: 'expo.in' }, 0)
        .to(this.material, { opacity: 0, duration: 1, ease: 'power4.in' }, duration - 1)
        .progress(R()),
      gsap.to(this.position, { x: () => x + rand(-0.3, 0.3), z: () => z + rand(-0.3, 0.3), duration: rand(3, 5), ease: 'power4.inOut', repeat: -1, repeatRefresh: true }).progress(R()),
      gsap.to(this._motion, { twinkle: 0.7, duration: rand(0.05, 0.1), ease: 'power4.inOut', repeat: -1, yoyo: true }).progress(R()),
    ];
  }
  update(energy, sparkle) {
    const b = this._spark ? 0.5 + sparkle * 0.5 : 0.5 + energy * 0.2 + sparkle * 0.3;
    this.material.color.copy(this._color).multiplyScalar(this._motion.twinkle * b);
  }
}

const WAVE_COLORS = [0xffffff, 0xa0e0ff, 0x20c0ff, 0x4080ff];
class WaveParticle extends THREE.Sprite {
  constructor(K, index, mats) {
    super(mats.spark.clone());
    this.K = K; this.mats = mats;
    swapRB(this.material.color.set(WAVE_COLORS[index % WAVE_COLORS.length]), K.tint).multiplyScalar(1.5);
    this.material.opacity = 0; this.visible = false;
  }
  trigger(tl, at) {                                        // was trigger(delay): placed on the effect's timeline
    const R = this.K.rng, rand = (a, b) => a + (b - a) * R();
    const angle = R() * Math.PI * 2, radius = rand(0, 3), duration = rand(1, 2), height = rand(6, 10), size = rand(0.2, 0.4);
    const ds = size * this.mats.scale;
    tl.set(this, { visible: true }, at)
      .set(this.position, { x: Math.cos(angle) * radius, y: 0.5, z: Math.sin(angle) * radius }, at)
      .set(this.scale, { x: ds * 0.5, y: ds, z: 1 }, at)
      .to(this.position, { y: 0.5 + height, duration, ease: 'power1.out' }, at)
      .to(this.scale, { keyframes: { y: [ds, ds * 4, ds], easeEach: 'power4.inOut' }, duration }, at)
      .to(this.material, { keyframes: { opacity: [1, 0.7, 1, 0.7, 1, 0.7, 1, 0.7, 1, 0.7, 1, 0.7, 1, 0], easeEach: 'power4.inOut' }, duration }, at)
      .set(this, { visible: false }, at + duration);
  }
}

class ParticleEmitter extends THREE.Object3D {
  constructor(K) {
    super();
    const loader = new THREE.TextureLoader(), tex = n => { const t = loader.load(K.base + n); t.colorSpace = THREE.SRGBColorSpace; return t; };
    const mk = t => new THREE.SpriteMaterial({ map: t, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
    const mats = { orb: mk(tex('ball.png')), spark: mk(tex('particle.png')), scale: 0.6 };
    this._particles = Array.from({ length: 80 }, (_, i) => new Particle(K, i, mats));
    this._waveParticles = Array.from({ length: 240 }, (_, i) => new WaveParticle(K, i, mats));
    this.add(...this._particles, ...this._waveParticles);
    this.K = K;
  }
  emitWave(tl, at) { this._waveParticles.forEach((p, i) => p.trigger(tl, at + (i % 3) * 0.05 + this.K.rng() * 0.05)); }
  update(energy, sparkle) { for (const p of this._particles) p.update(energy, sparkle); }
}

export class SavePoint extends THREE.Object3D {
  constructor({ base, tint = 'blue', rng = Math.random, light = true } = {}) {
    super();
    const K = this.K = { base, tint, rng };
    this._motion = { energy: 0, sparkle: 0.2 };
    this._floorEffects = new THREE.Object3D();
    this._magicCircle = new MagicCircle(K);
    this._light = light ? new THREE.PointLight(swapRB(new THREE.Color(0x20b0ff), tint), 50, 4, 2) : null;
    this._pillar = new Pillar(K);
    this._spreadLight = new Pillar(K, 4, 3, 2);
    this._swirl = new Swirl(K);
    this._particleEmitter = new ParticleEmitter(K);
    const gt = new THREE.TextureLoader().load(base + 'ground.png');
    gt.colorSpace = THREE.SRGBColorSpace; gt.repeat.setScalar(0.86); gt.offset.setScalar(0.07);
    this._groundMaterial = new THREE.MeshBasicMaterial({ color: swapRB(new THREE.Color(0x0070d0), tint), map: gt, side: THREE.DoubleSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.65 });
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(8.5, 8.5), this._groundMaterial);
    ground.rotation.x = -Math.PI / 2; ground.position.y = 0.05;
    if (this._light) this._light.position.y = 2;
    this._spreadLight.brightness = 0.8;
    this._floorEffects.add(ground, this._spreadLight, this._swirl);
    this.add(this._floorEffects, this._magicCircle, this._pillar, this._particleEmitter, ...(this._light ? [this._light] : []));
    this._pillar.position.y = -8;
    this._magicCircle.rotation.y = -Math.PI / 2;
    this._floorEffects.scale.set(0, 1, 0);
    this.visible = false;
  }
  update(delta) {
    const { energy, sparkle } = this._motion;
    this._magicCircle.update(energy);
    this._pillar.update(delta, energy);
    this._spreadLight.update(delta, energy);
    this._swirl.update(delta, energy);
    this._particleEmitter.update(energy, sparkle);
    swapRB(this._groundMaterial.color.setRGB(energy * 0.4, 0.4 + energy * 0.4, 1), this.K.tint);
    this._groundMaterial.opacity = 0.65 + energy * 0.15;
    if (this._light) {
      swapRB(this._light.color.setRGB(energy, 0.6 + energy * 0.4, 1), this.K.tint);
      const s = this.getWorldScale(new THREE.Vector3()).x;   // scaled with the object: same look at any size
      this._light.distance = 4 * s; this._light.intensity = (150 + energy * 200) * s * s;
    }
    return energy;
  }
  // entrance + one pulse (the original's pulse timeline, once) + the entrance reversed; returns the end time
  play(at, { hold = 0.2 } = {}) {
    const tl = gsap.timeline({ paused: false });
    tl.set(this, { visible: true }, 0);
    this._magicCircle.entrance(tl, 0);
    tl.to(this._pillar.position, { y: 0, duration: 1.2, ease: 'power4.out' }, 0)
      .to(this._magicCircle.rotation, { y: 0, duration: 1.2, ease: 'expo.out' }, 0)
      .to(this._floorEffects.scale, { x: 1, z: 1, duration: 1.2, ease: 'expo.out' }, 0);
    const p = 0.2;
    tl.to(this._motion, { energy: 0.8, sparkle: 1, duration: 1.2, ease: 'power3.inOut' }, p);
    this._particleEmitter.emitWave(tl, p + 0.7);
    tl.to(this._motion, { energy: 0, sparkle: 0.2, duration: 3, ease: 'power1.out' }, p + 1.2);
    const out = p + 1.2 + 3 + hold;
    tl.to(this._pillar.position, { y: -8, duration: 0.9, ease: 'power3.in' }, out)
      .to(this._floorEffects.scale, { x: 0, z: 0, duration: 0.9, ease: 'expo.in' }, out);
    this._magicCircle.exit(tl, out);
    for (const pt of this._particleEmitter._particles) tl.to(pt.material, { opacity: 0, duration: 0.6 }, out);
    tl.set(this, { visible: false }, out + 1.0);
    gsap.globalTimeline.add(tl, at);
    this.endAt = at + out + 1.0;
    return this.endAt;
  }
}
