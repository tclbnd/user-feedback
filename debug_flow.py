import sys, json, os, tempfile, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memc, make_scene

d = tempfile.mkdtemp(dir=os.environ['TMPDIR'])
f0, f1 = make_scene.scene_translate(96, 64, 10)
flat = lambda a: [v for r in a for v in r]
memc.write_pgm(os.path.join(d, 'f0.pgm'), 96, 64, flat(f0))
memc.write_pgm(os.path.join(d, 'f1.pgm'), 96, 64, flat(f1))
for levels in (1, 2, 3):
    req = {'frames': [{'file': 'f0.pgm'}, {'file': 'f1.pgm'}],
           't': [0.5], 'block': 8, 'levels': levels}
    rp = os.path.join(d, 'req%d.json' % levels)
    json.dump(req, open(rp, 'w'))
    od = os.path.join(d, 'out%d' % levels)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'memc.py'), rp, od], check=True)
    fl = json.load(open(os.path.join(od, 'flow_f01.json')))
    print('--- levels', levels, '---')
    vs = fl['vectors']
    nbx = 12
    for by in range(2, 6):
        print(' '.join('%+3d/c%d' % (vs[by * nbx + bx]['dx'],
              vs[by * nbx + bx]['conf']) for bx in range(12)))
