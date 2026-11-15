#pragma once
// ---------------------------------------------------------------------------
// fix.hpp — Q32.32 fixed-point arithmetic.
//
// All arithmetic reduces to integer operations on int64_t / __int128, which
// are exact, associative and commutative (add/sub), and platform-deterministic
// (two's-complement arithmetic right shift on gcc/clang/msvc).  Therefore:
//   * parallel reduction order never changes the result (integer + commutes),
//   * any tiling / batch partition yields bit-identical state,
//   * no FPU rounding-mode or contraction (FMA) dependence exists.
// ---------------------------------------------------------------------------
#include <cstdint>
#include <cmath>

struct Fix {
  int64_t v;  // raw Q32.32 value

  static constexpr int kFrac = 32;
  static constexpr int64_t kOne = int64_t(1) << 32;

  static constexpr Fix raw(int64_t r) { Fix f; f.v = r; return f; }
  static constexpr Fix fromInt(int64_t i) { return raw(i << 32); }
  // Initialization-only helper (deterministic llround on a literal double).
  static Fix fromDouble(double d) {
    return raw((int64_t)std::llround(d * 4294967296.0));
  }
  double toDouble() const { return (double)v * (1.0 / 4294967296.0); }
};

constexpr Fix operator+(Fix a, Fix b) { return Fix::raw(a.v + b.v); }
constexpr Fix operator-(Fix a, Fix b) { return Fix::raw(a.v - b.v); }
constexpr Fix operator-(Fix a)        { return Fix::raw(-a.v); }
inline Fix operator*(Fix a, Fix b) {
  return Fix::raw((int64_t)(((__int128)a.v * b.v) >> 32));
}
inline Fix operator/(Fix a, Fix b) {
  return Fix::raw((int64_t)(((__int128)a.v << 32) / b.v));
}
constexpr bool operator<(Fix a, Fix b)  { return a.v <  b.v; }
constexpr bool operator>(Fix a, Fix b)  { return a.v >  b.v; }
constexpr bool operator<=(Fix a, Fix b) { return a.v <= b.v; }
constexpr bool operator>=(Fix a, Fix b) { return a.v >= b.v; }
constexpr bool operator==(Fix a, Fix b) { return a.v == b.v; }
constexpr bool operator!=(Fix a, Fix b) { return a.v != b.v; }

// floor(sqrt(n)) for 0 <= n < 2^128, exact integer (bit-by-bit) algorithm.
// Used to normalize collision normals in Q32.32 without any libm dependence.
inline uint64_t isqrt128(unsigned __int128 n) {
  unsigned __int128 res = 0;
  unsigned __int128 bit = (unsigned __int128)1 << 126;  // highest power of 4
  while (bit > n) bit >>= 2;
  while (bit != 0) {
    if (n >= res + bit) {
      n -= res + bit;
      res = (res >> 1) + bit;
    } else {
      res >>= 1;
    }
    bit >>= 2;
  }
  return (uint64_t)res;
}
