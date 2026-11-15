// ---------------------------------------------------------------------------
// sim.cpp — deterministic simulation core.
//
// Determinism strategy
// --------------------
// * All dynamics (forces, drag, gravity, wall restitution, sphere-sphere
//   collision impulses and positional corrections) are evaluated in Q32.32
//   fixed point.  Every per-step update is computed from a snapshot of the
//   previous state (Jacobi style), so no result depends on evaluation order.
// * Pair-collision contributions are accumulated with integer atomic adds.
//   Integer addition is commutative, hence any thread interleaving and any
//   pair tiling produce the *same bits*.
// * Rendering: particles are sorted by (view-space depth, id) — a total
//   order, so ties are broken deterministically.  Shading of each particle
//   is independent (draw batches only partition shading); the final
//   premultiplied-alpha over-fold is applied in the single global sorted
//   order, so draw-batch partitioning cannot change the image.
// ---------------------------------------------------------------------------
#include "sim.hpp"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <numeric>
#include <thread>

uint64_t fnvMix(uint64_t h, uint64_t x) {
  for (int i = 0; i < 8; i++) {
    h ^= (x & 0xff);
    h *= 1099511628211ull;
    x >>= 8;
  }
  return h;
}
static uint64_t fnvMixI64(uint64_t h, int64_t x) { return fnvMix(h, (uint64_t)x); }
static uint64_t fnvMixD(uint64_t h, double d) {
  uint64_t u;
  std::memcpy(&u, &d, 8);
  return fnvMix(h, u);
}

// Deterministic parallel_for over [0,n) cut into tiles of `tile` elements.
// The body f(begin,end) must be order-independent (guaranteed by the fixed
// point / snapshot design above); scheduling itself is work-stealing via an
// atomic tile counter.
template <class F>
static void parallelFor(int64_t n, int64_t tile, int threads, F&& f) {
  if (tile < 1) tile = 1;
  if (threads <= 1 || n <= tile) {
    for (int64_t b = 0; b < n; b += tile) f(b, std::min(n, b + tile));
    return;
  }
  const int64_t nTiles = (n + tile - 1) / tile;
  std::atomic<int64_t> next = {0};
  auto worker = [&]() {
    int64_t t;
    while ((t = next.fetch_add(1)) < nTiles) {
      int64_t b = t * tile;
      f(b, std::min(n, b + tile));
    }
  };
  std::vector<std::thread> pool;
  pool.reserve(threads - 1);
  for (int i = 0; i < threads - 1; i++) pool.emplace_back(worker);
  worker();
  for (auto& t : pool) t.join();
}

void Sim::init() {
  // scene constants
  dt      = Fix::raw(Fix::kOne / 64);   // 1/64 s, exact in binary
  kSpring = Fix::fromDouble(0.02);
  kSwirl  = Fix::fromDouble(0.015);
  kDrag   = Fix::fromDouble(0.05);
  gravity = Fix::fromDouble(0.15);
  rest    = Fix::fromDouble(0.8);
  boxB    = Fix::fromInt(8);
  prad    = Fix::fromDouble(0.4);

  // deterministic splitmix64 stream from seed
  uint64_t s = P.seed;
  auto rnd = [&]() {
    s += 0x9E3779B97F4A7C15ull;
    uint64_t z = s;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
  };
  auto frand = [&](double lo, double hi) {
    double u = (rnd() >> 11) * (1.0 / 9007199254740992.0);
    return lo + (hi - lo) * u;
  };

  parts.resize(P.numParticles);
  for (int i = 0; i < P.numParticles; i++) {
    Particle& p = parts[i];
    p.px = Fix::fromDouble(frand(-6.0, 6.0));
    p.py = Fix::fromDouble(frand(-6.0, 6.0));
    p.pz = Fix::fromDouble(frand(-6.0, 6.0));
    p.vx = Fix::fromDouble(frand(-1.0, 1.0));
    p.vy = Fix::fromDouble(frand(-1.0, 1.0));
    p.vz = Fix::fromDouble(frand(-1.0, 1.0));
    p.r  = 0.25 + 0.75 * frand(0.0, 1.0);
    p.g  = 0.25 + 0.75 * frand(0.0, 1.0);
    p.b  = 0.25 + 0.75 * frand(0.0, 1.0);
    p.a  = 0.5;
  }
  if (P.injectTies) {
    // Force a deterministic cluster of exactly-equal view-space depths.
    for (int i = 10; i < 16 && i < P.numParticles; i++) parts[i].pz = Fix::fromInt(0);
  }
  order.assign(P.numParticles, 0);
  fb.assign((size_t)P.width * P.height * 4, 0.0);
}

// Wall collision against the axis-aligned box [-B,B]^3 with restitution.
static void wallCollide(Fix& p, Fix& v, Fix lim, Fix rest) {
  const Fix hi = lim, lo = -lim;
  if (p > hi) {
    p = hi;
    if (v.v > 0) v = -(v * rest);
  } else if (p < lo) {
    p = lo;
    if (v.v < 0) v = -(v * rest);
  }
}

void Sim::step() {
  const int n = (int)parts.size();

  // Snapshot of the previous state: every computation below reads only the
  // snapshot, so evaluation order is irrelevant.
  std::vector<Fix> sx(n), sy(n), sz(n), svx(n), svy(n), svz(n);
  for (int i = 0; i < n; i++) {
    sx[i] = parts[i].px;  sy[i] = parts[i].py;  sz[i] = parts[i].pz;
    svx[i] = parts[i].vx; svy[i] = parts[i].vy; svz[i] = parts[i].vz;
  }

  // Impulse (velocity delta) and positional-correction accumulators.
  // Integer atomics: any accumulation order gives identical bits.
  std::vector<std::atomic<int64_t>> jx(n), jy(n), jz(n), cx(n), cy(n), cz(n);
  for (int i = 0; i < n; i++) {
    jx[i] = 0; jy[i] = 0; jz[i] = 0;
    cx[i] = 0; cy[i] = 0; cz[i] = 0;
  }

  // --- sphere-sphere collisions, tiled over a fixed pair list -------------
  std::vector<std::pair<int,int>> pairs;
  pairs.reserve((size_t)n * (n - 1) / 2);
  for (int i = 0; i < n; i++)
    for (int j = i + 1; j < n; j++) pairs.emplace_back(i, j);

  const Fix R        = prad + prad;             // sum of radii
  const Fix onePlusE = Fix::raw(Fix::kOne) + rest;
  const Fix half     = Fix::raw(Fix::kOne / 2);
  const Fix negHalf  = Fix::raw(-Fix::kOne / 2);

  parallelFor((int64_t)pairs.size(), P.pairTile, P.threads, [&](int64_t b, int64_t e) {
    for (int64_t k = b; k < e; k++) {
      const int i = pairs[k].first, j = pairs[k].second;
      const Fix dx = sx[i] - sx[j];
      const Fix dy = sy[i] - sy[j];
      const Fix dz = sz[i] - sz[j];
      const __int128 dsq = (__int128)dx.v * dx.v + (__int128)dy.v * dy.v +
                           (__int128)dz.v * dz.v;   // Q64.64
      const __int128 rsq = (__int128)R.v * R.v;
      if (dsq >= rsq) continue;

      Fix nx, ny, nz, dist;
      if (dsq == 0) {                 // exact coincidence: deterministic normal
        nx = Fix::raw(Fix::kOne); ny = Fix::raw(0); nz = Fix::raw(0);
        dist = Fix::raw(0);
      } else {
        dist = Fix::raw((int64_t)isqrt128((unsigned __int128)dsq));  // Q32.32
        nx = dx / dist; ny = dy / dist; nz = dz / dist;
      }

      const Fix rvx = svx[i] - svx[j];
      const Fix rvy = svy[i] - svy[j];
      const Fix rvz = svz[i] - svz[j];
      const Fix vrel = rvx * nx + rvy * ny + rvz * nz;
      if (vrel.v < 0) {  // approaching: equal-mass impulse j = -(1+e)*vrel/2
        const Fix jimp = (onePlusE * vrel) * negHalf;
        const int64_t ix = (nx * jimp).v, iy = (ny * jimp).v, iz = (nz * jimp).v;
        jx[i].fetch_add(ix); jy[i].fetch_add(iy); jz[i].fetch_add(iz);
        jx[j].fetch_sub(ix); jy[j].fetch_sub(iy); jz[j].fetch_sub(iz);
      }
      // positional correction: push each by overlap/2 along the normal
      const Fix push = (R - dist) * half;
      const int64_t px = (nx * push).v, py = (ny * push).v, pz = (nz * push).v;
      cx[i].fetch_add(px); cy[i].fetch_add(py); cz[i].fetch_add(pz);
      cx[j].fetch_sub(px); cy[j].fetch_sub(py); cz[j].fetch_sub(pz);
    }
  });

  // --- per-particle force field + integration, tiled ----------------------
  const Fix lim = boxB - prad;
  parallelFor(n, P.updateTile, P.threads, [&](int64_t b, int64_t e) {
    for (int64_t i = b; i < e; i++) {
      // linear force field: spring to origin + swirl + drag + gravity
      const Fix fx = -kSpring * sx[i] - kSwirl * sy[i] - kDrag * svx[i];
      const Fix fy = -kSpring * sy[i] + kSwirl * sx[i] - kDrag * svy[i] - gravity;
      const Fix fz = -kSpring * sz[i] - kDrag * svz[i];

      Fix vx = svx[i] + fx * dt + Fix::raw(jx[i].load());
      Fix vy = svy[i] + fy * dt + Fix::raw(jy[i].load());
      Fix vz = svz[i] + fz * dt + Fix::raw(jz[i].load());
      Fix px = sx[i] + vx * dt + Fix::raw(cx[i].load());
      Fix py = sy[i] + vy * dt + Fix::raw(cy[i].load());
      Fix pz = sz[i] + vz * dt + Fix::raw(cz[i].load());

      wallCollide(px, vx, lim, rest);
      wallCollide(py, vy, lim, rest);
      wallCollide(pz, vz, lim, rest);

      Particle& p = parts[i];
      p.px = px; p.py = py; p.pz = pz;
      p.vx = vx; p.vy = vy; p.vz = vz;
    }
  });

  last.count       = n;
  last.stateDigest = stateDigest();
}

void Sim::render() {
  const int n = (int)parts.size();
  const int W = P.width, H = P.height;

  // --- stable depth sort: key = (view-space depth, id) — a total order ----
  // Camera looks down -z; smaller z is farther.  Draw back-to-front.
  order.resize(n);
  std::iota(order.begin(), order.end(), 0);
  std::sort(order.begin(), order.end(), [&](int a, int b) {
    if (parts[a].pz.v != parts[b].pz.v) return parts[a].pz.v < parts[b].pz.v;
    return a < b;   // deterministic tie-break for equal depths
  });

  // --- shading: independent per particle, partitioned into draw batches ---
  struct Shade { double cx, cy, rpx, pr, pg, pb, pa; };
  std::vector<Shade> sh(n);
  const double Bd = boxB.toDouble();
  parallelFor(n, P.drawBatch, P.threads, [&](int64_t b, int64_t e) {
    for (int64_t i = b; i < e; i++) {
      const Particle& p = parts[i];
      Shade& s = sh[i];
      s.cx  = (p.px.toDouble() / Bd * 0.5 + 0.5) * W;
      s.cy  = (0.5 - p.py.toDouble() / Bd * 0.5) * H;
      s.rpx = prad.toDouble() / Bd * 0.5 * W;
      s.pa  = p.a;
      s.pr  = p.r * p.a;   // premultiplied alpha
      s.pg  = p.g * p.a;
      s.pb  = p.b * p.a;
    }
  });

  // --- compositing: single left-to-right over-fold in global sorted order -
  // (draw batches only partition shading above, so their number/size can
  //  never influence the image)
  std::fill(fb.begin(), fb.end(), 0.0);
  for (int px = 0; px < W * H; px++) {   // opaque background
    fb[4 * px + 0] = 0.1; fb[4 * px + 1] = 0.2;
    fb[4 * px + 2] = 0.3; fb[4 * px + 3] = 1.0;
  }
  uint64_t frags = 0;
  double asum = 0.0;
  for (int oi = 0; oi < n; oi++) {
    const Shade& s = sh[order[oi]];
    const int x0 = std::max(0, (int)std::floor(s.cx - s.rpx));
    const int x1 = std::min(W - 1, (int)std::ceil(s.cx + s.rpx));
    const int y0 = std::max(0, (int)std::floor(s.cy - s.rpx));
    const int y1 = std::min(H - 1, (int)std::ceil(s.cy + s.rpx));
    for (int y = y0; y <= y1; y++) {
      for (int x = x0; x <= x1; x++) {
        const double dx = (x + 0.5) - s.cx, dy = (y + 0.5) - s.cy;
        if (dx * dx + dy * dy > s.rpx * s.rpx) continue;  // point-sampled disc
        double* f = &fb[4 * (y * W + x)];
        const double ia = 1.0 - s.pa;
        f[0] = s.pr + ia * f[0];   // premultiplied over
        f[1] = s.pg + ia * f[1];
        f[2] = s.pb + ia * f[2];
        f[3] = s.pa + ia * f[3];
        asum += s.pa;
        frags++;
      }
    }
  }

  last.count      = n;
  last.sortDigest = sortDigest();
  last.fbDigest   = fbDigest();
  last.fragCount  = frags;
  last.alphaSum   = asum;
}

uint64_t Sim::stateDigest() const {
  uint64_t h = 14695981039346656037ull;
  for (const Particle& p : parts) {
    h = fnvMixI64(h, p.px.v); h = fnvMixI64(h, p.py.v); h = fnvMixI64(h, p.pz.v);
    h = fnvMixI64(h, p.vx.v); h = fnvMixI64(h, p.vy.v); h = fnvMixI64(h, p.vz.v);
  }
  return h;
}

uint64_t Sim::sortDigest() const {
  uint64_t h = 14695981039346656037ull;
  for (int id : order) {
    h = fnvMixI64(h, parts[id].pz.v);
    h = fnvMix(h, (uint64_t)id);
  }
  return h;
}

uint64_t Sim::fbDigest() const {
  uint64_t h = 14695981039346656037ull;
  for (double d : fb) h = fnvMixD(h, d);
  return h;
}
