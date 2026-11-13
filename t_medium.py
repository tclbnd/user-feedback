import math

from medium import (refract_signed, fresnel_unpolarized, beer_lambert,
                    trace_events, norm_angle_deg)

# Flat slab air->glass n=1.5 at 30 deg incidence
a, tir = refract_signed(30, 0, 1.0, 1.5)
exp = math.degrees(math.asin(math.sin(math.radians(30)) / 1.5))
print("refract", a, exp, tir)
assert abs(a - exp) < 1e-12 and not tir

# Parallel exit face restores the original angle
a2, _ = refract_signed(a, 0, 1.5, 1.0)
print("exit", a2)
assert abs(a2 - 30) < 1e-12

# TIR at 45 deg (critical angle for 1.5 is ~41.81 deg)
a3, tir3 = refract_signed(45, 0, 1.5, 1.0)
print("tir", a3, tir3)
assert tir3 and abs(a3 - (-45)) < 1e-12

# Tilted normal, negative incidence
a4, t4 = refract_signed(-20, 10, 1.0, 1.5)
exp4 = 10 - math.degrees(math.asin(math.sin(math.radians(30)) / 1.5))
print("tilted", a4, exp4)
assert abs(a4 - exp4) < 1e-12

# wrap check
assert norm_angle_deg(181) == -179
assert norm_angle_deg(-180) == 180

f = fresnel_unpolarized(1.0, 1.5, math.cos(math.radians(30)))
print("F(30deg,1.5) =", f)
assert 0.0 < f < 0.1

assert abs(beer_lambert(0.1, 2.0) - math.exp(-0.2)) < 1e-15

mats = dict(glass=dict(ior=[1.5], absorb=[0.1]))
ray = dict(angle=30, events=[
    dict(type="enter", material="glass", normal=0),
    dict(type="distance", length=2.0),
    dict(type="exit", material="glass", normal=0),
])
tr = trace_events(ray, mats, 0)
print(tr)
assert tr.ok and abs(tr.transmission - math.exp(-0.2)) < 1e-15
assert abs(tr.exit_angle - 30.0) < 1e-12
assert tr.stack == []

# Nested stack + TIR leaves stack unchanged
mats2 = dict(glass=dict(ior=[1.5], absorb=[0.0]),
             inner=dict(ior=[2.0], absorb=[0.0]))
ray2 = dict(angle=0, events=[
    dict(type="enter", material="glass", normal=0),
    dict(type="enter", material="inner", normal=0),
    dict(type="exit", material="inner", normal=0),
])
tr2 = trace_events(ray2, mats2, 0)
print("nested", tr2.exit_angle, tr2.tir, tr2.stack)
# inner->glass at normal incidence is fine
assert tr2.ok and not tr2.tir and tr2.stack == ["glass"]

# Mismatched exit is rejected
bad = dict(angle=0, events=[dict(type="enter", material="glass", normal=0),
                            dict(type="exit", material="inner", normal=0)])
tr3 = trace_events(bad, mats2, 0)
assert not tr3.ok
print("medium tests OK")
