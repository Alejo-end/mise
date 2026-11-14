"""Why do some unrelated views read 'low'? Candidate discriminators on genuine vs unrelated slot views."""
import sys; sys.path.insert(0, ".")
import numpy as np
from PIL import Image, ImageFilter
from tests.device.replay import Sequence, paint_reference, sd
s = Sequence("steady-01"); cfg = s.config
APART = [[0.05,0.30,0.28,0.45],[0.36,0.30,0.28,0.45],[0.67,0.30,0.28,0.45]]
e, f = s.refs[3]; rng = np.random.default_rng(7)
base = s.frames[3].image
def via_camera(ref, blur=0, gain=1.0, noise=0.0, shift=(0, 0)):
    """Paint a 48x48 reference into the frame at slot 3 and crop it back, like the board sees it."""
    r = np.roll(ref, shift, axis=(0, 1)) * gain + rng.normal(0, noise, ref.shape)
    img = paint_reference(base, APART[2], r)
    if blur: img = img.filter(ImageFilter.GaussianBlur(blur))
    return sd.crop(img, APART[2])
def cols(a, b, frac):  # spatial mix: left part from b (some units removed), rest from a
    m = a.copy(); k = int(round(48 * frac)); m[:, :k] = b[:, :k]; return m
cases = {
  "GENUINE full": via_camera(f), "GENUINE empty": via_camera(e),
  "GENUINE full, noise .03": via_camera(f, noise=.03), "GENUINE full, blur 1": via_camera(f, blur=1),
  "GENUINE full, light x0.8": via_camera(f, gain=.8), "GENUINE full, shifted 2 px": via_camera(f, shift=(2, 2)),
  "GENUINE full, shifted 4 px": via_camera(f, shift=(0, 4)),
  "GENUINE 1/3 removed (cols)": via_camera(cols(f, e, 1/3)), "GENUINE 1/2 removed (cols)": via_camera(cols(f, e, .5)),
  "GENUINE 2/3 removed (cols)": via_camera(cols(f, e, 2/3)), "GENUINE blend .5": via_camera((e + f) / 2),
  "UNRELATED recorded scene": sd.crop(base, APART[2]),
  "UNRELATED half covered": via_camera(np.concatenate([f[:, :24], rng.normal(.1, .2, (48, 24))], 1)),
  "UNRELATED third covered": via_camera(np.concatenate([f[:, :32], rng.normal(.1, .2, (48, 16))], 1)),
  "UNRELATED noise": via_camera(rng.normal(0, .25, (48, 48))), "UNRELATED other object": via_camera(f.T.copy()),
  "UNRELATED slot1 full photo": via_camera(s.refs[1][1]),
  "UNRELATED hand (smooth blob) half": via_camera(np.concatenate([f[:, :24], np.full((48, 24), .15)], 1)),
}
span = sd.diff(e, f)
print(f"span d(empty,full) = {span:.4f}   current rule: uncertain if min(d_e, d_f) > 0.85 x span")
print(f"{'case':34s} {'state':9s} {'fill':>5s} {'d_e/sp':>6s} {'d_f/sp':>6s} {'r=(de+df)/sp':>12s} {'outside band':>12s}")
lo, hi = np.minimum(e, f), np.maximum(e, f)
for name, p in cases.items():
    st, fill, why = sd.classify(p, e, f, cfg)
    de, df = sd.diff(p, e) / span, sd.diff(p, f) / span
    tol = 0.08
    outside = float(((p < lo - tol) | (p > hi + tol)).mean())
    print(f"{name:34s} {str(st):9s} {fill if fill is not None else float('nan'):5.2f} {de:6.2f} {df:6.2f} {de+df:12.2f} {outside:12.2f}")

def sdiff(a, b, m=4):
    """Shift-tolerant L1: the smallest mean difference over shifts up to m px, on the shared interior."""
    best = 1e9
    for dy in range(-m, m + 1):
        for dx in range(-m, m + 1):
            A = a[max(0, dy):48 + min(0, dy), max(0, dx):48 + min(0, dx)]
            B = b[max(0, -dy):48 + min(0, -dy), max(0, -dx):48 + min(0, -dx)]
            best = min(best, float(np.abs(A - B).mean()))
    return best
print("\nshift-tolerant distances (shifts up to 4 px)")
print(f"{'case':34s} {'r_shift':>7s} {'fill_shift':>10s}")
for name, p in cases.items():
    de, df = sdiff(p, e), sdiff(p, f)
    sp = sdiff(e, f)
    print(f"{name:34s} {(de + df) / sp:7.2f} {de / (de + df + 1e-6):10.2f}")
