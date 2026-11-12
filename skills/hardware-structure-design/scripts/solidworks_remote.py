"""SSH -> interactive Windows task -> MCP stdio. No network listener or replay."""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=True), encoding='utf-8')
    temporary.replace(path)


def failed(result):
    return result.get('isError', False) or any(
        '[ERROR]' in item.get('text', '') for item in result.get('content', []))


def claim(lock, owner):
    with lock.open('x', encoding='utf-8') as stream:
        json.dump(owner, stream)


async def worker(request):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    job = json.loads(request.read_text(encoding='utf-8-sig'))
    report_path = request.with_suffix('.progress.json')
    done = request.with_suffix('.done.json')
    # One lock across configs and clients; a crash deliberately leaves it for reconciliation.
    lock = Path.home() / '.solidworks-cad-writer.lock'
    report = {'run_id': job['run_id'], 'state': 'starting', 'ok': False, 'calls': []}
    acquired = False
    uncertain = False
    try:
        claim(lock, {'run_id': job['run_id'], 'pid': os.getpid(),
                     'request': str(request), 'started_at': time.time()})
        acquired = True
        atomic_json(report_path, report)
        server = Path(job['config']['server'])
        params = StdioServerParameters(command=sys.executable, args=[str(server)], cwd=str(server.parent))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for index, call in enumerate(job['calls']):
                    report.update(state='executing', current_index=index, current_tool=call['name'])
                    atomic_json(report_path, report)
                    uncertain = True
                    if call['name'] == 'tools/list':
                        result = (await session.list_tools()).model_dump(mode='json')
                    else:
                        result = (await session.call_tool(call['name'], call.get('arguments', {}))).model_dump(mode='json')
                    report['calls'].append({'name': call['name'], 'result': result})
                    report['state'] = 'failed' if failed(result) else 'returned'
                    atomic_json(report_path, report)
                    uncertain = False
                    if failed(result):
                        break
                else:
                    report.update(state='completed', ok=True)
    except FileExistsError:
        # Another worker may still be writing the freshly created owner record.
        try:
            owner = json.loads(lock.read_text())
        except (OSError, ValueError):
            owner = {}
        report.update(state='blocked', ok=False, owner_run_id=owner.get('run_id'),
                      error='Writer lock exists; inspect owner and progress before retrying')
    except BaseException:
        report.update(state='unknown' if uncertain else 'failed', ok=False, error=traceback.format_exc())
    finally:
        if acquired and not uncertain:
            # Never delete a lock whose ownership changed externally.
            if json.loads(lock.read_text())['run_id'] == job['run_id']:
                lock.unlink()
        report['lock_retained'] = acquired and uncertain
        atomic_json(report_path, report)
        atomic_json(done, report)


def validate(calls, config):
    if not isinstance(calls, list) or not calls or any(
        not isinstance(c, dict) or not isinstance(c.get('name'), str) or not c['name']
        or not isinstance(c.get('arguments', {}), dict) for c in calls):
        raise ValueError('Expected a nonempty array of {name, arguments}')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', config['ssh_host']):
        raise ValueError('Use a configured SSH alias, not command options')
    directory = config['remote_dir']
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', directory) or directory.startswith('/') or '..' in directory.split('/'):
        raise ValueError('remote_dir must be a safe home-relative staging directory')
    for key in ('python', 'server'):
        if not isinstance(config[key], str) or not config[key] or any(c in config[key] for c in '\r\n"'):
            raise ValueError('Invalid Windows path: ' + key)
    timeout = config.get('timeout_seconds', 600)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError('timeout_seconds must be 1..3600')


def ps_string(value):
    return "'" + value.replace("'", "''") + "'"


def ssh(config, script, timeout=30):
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                             config['ssh_host'], 'powershell -NoProfile -EncodedCommand ' + encoded],
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout.decode('utf-8-sig').strip()


def status(config, run_id):
    if not re.fullmatch('[0-9a-f]{32}', run_id):
        raise ValueError('Invalid run ID')
    directory = ps_string(config['remote_dir'])
    return json.loads(ssh(config, f"""
$d=Join-Path $env:USERPROFILE {directory}
$done=Join-Path $d '{run_id}.done.json'
$progress=Join-Path $d '{run_id}.progress.json'
if(Test-Path $done){{Get-Content $done -Raw}}
elseif(Test-Path $progress){{Get-Content $progress -Raw}}
else{{'{{"state":"not_started_or_unavailable"}}'}}
"""))


def run(calls, config):
    validate(calls, config)
    run_id = uuid.uuid4().hex
    print('run_id=' + run_id, file=sys.stderr, flush=True)
    directory = config['remote_dir']
    ssh(config, f"New-Item -ItemType Directory -Force (Join-Path $env:USERPROFILE {ps_string(directory)}) | Out-Null")
    script = f"""
$ErrorActionPreference='Stop'
$d=Join-Path $env:USERPROFILE {ps_string(directory)}
$client=Join-Path $d '{run_id}.py'
$request=Join-Path $d '{run_id}.json'
$done=Join-Path $d '{run_id}.done.json'
$task='Codex-SW-{run_id}'
$a=New-ScheduledTaskAction -Execute {ps_string(config['python'])} -Argument ('"'+$client+'" --worker "'+$request+'"')
$p=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName $task -Action $a -Principal $p | Out-Null
Start-ScheduledTask $task
for($i=0;$i -lt {config.get('timeout_seconds',600)};$i++){{
 if(Test-Path $done){{Get-Content $done -Raw; Unregister-ScheduledTask $task -Confirm:$false; exit 0}}
 Start-Sleep 1
}}
'{{"ok":false,"state":"waiting_timeout","run_id":"{run_id}","message":"Inspect status; do not replay mutations"}}'
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        files = [root/(run_id+ext) for ext in ('.py', '.json', '.ps1')]
        files[0].write_bytes(Path(__file__).read_bytes())
        atomic_json(files[1], {'run_id': run_id, 'config': config, 'calls': calls})
        files[2].write_text(script, encoding='utf-8')
        subprocess.run(['scp', '-C', '-q', '-o', 'BatchMode=yes', *map(str, files),
                        config['ssh_host'] + ':' + directory + '/'], check=True, timeout=120)
    # No mutation retry, including when SSH drops after dispatch.
    command = '& (Join-Path $env:USERPROFILE ' + ps_string(directory+'/'+run_id+'.ps1') + ')'
    try:
        return json.loads(ssh(config, command, config.get('timeout_seconds',600)+30))
    except (subprocess.TimeoutExpired, RuntimeError, json.JSONDecodeError) as error:
        return {'ok': False, 'state': 'unknown', 'run_id': run_id,
                'error': str(error), 'message': 'Inspect --status; do not replay mutations'}


def check():
    assert failed({'isError': False, 'content': [{'text': '[ERROR] bad selection'}]})
    assert failed({'isError': True})
    assert not failed({'content': [{'text': '[SUCCESS] saved'}]})
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp)/'lock'
        claim(lock, {'run_id': 'first'})
        try:
            claim(lock, {'run_id': 'second'})
        except FileExistsError:
            assert json.loads(lock.read_text())['run_id'] == 'first'
        else:
            raise AssertionError('Concurrent writer accepted')
        record = Path(tmp)/'progress.json'
        atomic_json(record, {'state': 'executing', 'current_index': 0})
        atomic_json(record, {'state': 'returned', 'current_index': 0})
        assert json.loads(record.read_text())['state'] == 'returned'
    config = {'ssh_host':'test-alias','remote_dir':'cad/runs','python':'C:/Python/python.exe',
              'server':'C:/cad/server.py','timeout_seconds':10}
    validate([{'name':'tools/list'}], config)
    for changed in ({'ssh_host':'-oProxyCommand=bad'}, {'remote_dir':'../escape'}, {'timeout_seconds':0}):
        try:
            validate([{'name':'tools/list'}], {**config, **changed})
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid config accepted')
    print('PASS: exclusive writer, atomic progress, MCP error handling, config validation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('calls', nargs='?', type=Path)
    parser.add_argument('--config', type=Path, default=Path.home()/'.config/mako-cad/solidworks.json')
    parser.add_argument('--status')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker(args.worker))
    elif args.check:
        check()
    else:
        config = json.loads(args.config.read_text())
        validate([{'name':'tools/list'}], config)
        if args.status:
            report = status(config, args.status)
        elif args.calls:
            report = run(json.loads(args.calls.read_text()), config)
        else:
            parser.error('Provide calls.json or --status RUN_ID')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0 if report.get('ok') else 1)
