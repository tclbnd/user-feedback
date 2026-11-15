#pragma once
// ---------------------------------------------------------------------------
// sim.hpp — deterministic particle simulation + premultiplied-alpha renderer.
// ---------------------------------------------------------------------------
#include "fix.hpp"
#include <vector>
#include <cstdint>

struct Params {
  int numParticles = 256;
  int steps        = 300;
  int threads      = 1;
  int updateTile   = 32;    // particles per update batch
  int pairTile     = 512;   // collision pairs per batch
  int drawBatch    = 32;    // particles per shading batch
  uint64_t seed    = 12345;
  int width        = 64;
  int height       = 64;
  bool injectTies  = false; // force several particles to identical depth
};

struct Particle {
  Fix px, py, pz;      // position (Q32.32)
  Fix vx, vy, vz;      // velocity (Q32.32)
  double r, g, b, a;   // straight color + alpha in [0,1]
};

struct FrameStats {
  int      count       = 0;
  uint64_t stateDigest = 0;  // hash of all positions+velocities
  uint64_t sortDigest  = 0;  // hash of sorted (depth,id) key sequence
  uint64_t fbDigest    = 0;  // hash of final framebuffer
  uint64_t fragCount   = 0;  // blended fragments this frame
  double   alphaSum    = 0.0;// sum of fragment alphas (fixed fold order)
};

uint64_t fnvMix(uint64_t h, uint64_t x);

class Sim {
public:
  Params P;
  std::vector<Particle> parts;
  std::vector<int>    order;   // sorted draw order, back-to-front
  std::vector<double> fb;      // W*H*4 premultiplied-alpha framebuffer
  // scene constants (fixed point)
  Fix dt, kSpring, kSwirl, kDrag, gravity, rest, boxB, prad;
  FrameStats last;

  void init();
  void step();     // parallel, tile/partition invariant
  void render();   // stable depth sort + premultiplied over compositing
  uint64_t stateDigest() const;
  uint64_t sortDigest() const;
  uint64_t fbDigest() const;
};
