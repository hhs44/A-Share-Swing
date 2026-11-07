"""Install this workspace's forward-paper workflow as a macOS user launch agent."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = 'local.ashare-swing.free-daily'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    agent = Path.home()/'Library'/'LaunchAgents'/(LABEL+'.plist')
    domain = f'gui/{os.getuid()}'
    exists = subprocess.run(['launchctl', 'print', domain+'/'+LABEL], capture_output=True).returncode == 0
    if args.uninstall:
        if exists:
            subprocess.run(['launchctl', 'bootout', domain+'/'+LABEL], check=True)
        agent.unlink(missing_ok=True)
        print('Removed '+LABEL)
        return
    executable = root/'.venv'/'bin'/'python'
    if not executable.exists():
        raise RuntimeError('先创建.venv并安装requirements-free.txt')
    logs = root/'data'/'free-workflow'/'logs'; logs.mkdir(parents=True, exist_ok=True)
    payload = dict(Label=LABEL, ProgramArguments=[str(executable), '-m', 'ashare_swing.free_daily',
        '--root', str(root/'data/free-workflow'), '--scheduled'], WorkingDirectory=str(root),
        StartCalendarInterval=[dict(Hour=16, Minute=40), dict(Hour=17, Minute=10), dict(Hour=19, Minute=5)],
        StandardOutPath=str(logs/'schedule.stdout.log'), StandardErrorPath=str(logs/'schedule.stderr.log'),
        EnvironmentVariables={'PYTHONUNBUFFERED': '1'}, ProcessType='Background')
    agent.parent.mkdir(parents=True, exist_ok=True)
    temporary = agent.with_suffix('.tmp')
    temporary.write_bytes(plistlib.dumps(payload)); temporary.chmod(0o644); temporary.replace(agent)
    if exists:
        subprocess.run(['launchctl', 'bootout', domain+'/'+LABEL], check=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(agent)], check=True)
    inspection = subprocess.run(['launchctl', 'print', domain+'/'+LABEL], capture_output=True, text=True, check=True)
    (logs/'installed-schedule.txt').write_text(inspection.stdout)
    print(json.dumps(dict(label=LABEL, plist=str(agent), root=str(root), timezone='computer local time; expected Asia/Shanghai',
        times=['16:40', '17:10', '19:05'], registered=True), ensure_ascii=False))


if __name__ == '__main__':
    main()
