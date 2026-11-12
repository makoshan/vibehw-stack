"""Run a Mac-authored KiCad script unchanged on Windows: `python win_shim.py script.py [args]`.

finish_vNN.py hardcode Mac tool paths in their subprocess calls; map those to the Windows KiCad
install and refuse any other Mac path rather than run something half-translated.
"""
import runpy, subprocess, sys

KICAD = r'C:\Program Files\KiCad\10.0\bin'
MAP = {
    '/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3': sys.executable,
    '/opt/homebrew/bin/kicad-cli': KICAD + r'\kicad-cli.exe',
}


def fix(args):
    if isinstance(args, (list, tuple)):
        args = [MAP.get(a, a) if isinstance(a, str) else a for a in args]
        bad = [a for a in args if isinstance(a, str) and a.startswith(('/Applications/', '/opt/', '/Users/', '/usr/local/'))]
        if bad:
            raise SystemExit(f'win_shim: unmapped Mac path {bad}')
    return args


class Popen(subprocess.Popen):  # run/call/check_output all go through Popen
    def __init__(self, args, *a, **k):
        super().__init__(fix(args), *a, **k)


subprocess.Popen = Popen

if __name__ == '__main__':
    assert fix(['/opt/homebrew/bin/kicad-cli', 'x'])[0].endswith('kicad-cli.exe')
    try:
        fix(['/Users/x/tool']); raise AssertionError('unmapped path accepted')
    except SystemExit:
        pass
    if len(sys.argv) < 2:
        sys.exit('usage: win_shim.py script.py [args]')
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_path(script, run_name='__main__')
