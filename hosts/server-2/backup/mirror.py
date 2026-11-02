#!/usr/bin/env python3
"""Run on the existing backup gateway; cloud credentials never reach server-2."""
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import datetime

os.umask(0o077)
sys.excepthook = sys.__excepthook__

PROGRAM = r'''
import pathlib,subprocess,os,json,fcntl,datetime
os.umask(0o077)
root=pathlib.Path('/mirror')
config=json.loads((root/'config.json').read_text())
lock=open('/backup.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
raw=subprocess.check_output(['sh','-c','set -a; . /etc/infra-backup/backup.env; env -0'])
env=dict(s.decode().split('=',1) for s in raw.split(b'\0') if b'=' in s)
original=env['RESTIC_REPOSITORY']
assert '/restic/' in original,'Configure the private destination prefix explicitly'
destination=original.rsplit('/restic/',1)[0]+'/restic/server-2'
assert destination!=original,'Never reuse the gateway host repository'
source=(root/'secrets/repository').read_text().strip()
env.update(RESTIC_REPOSITORY=destination,RESTIC_PASSWORD_FILE='/mirror/secrets/b2-password',RESTIC_FROM_REPOSITORY=source,RESTIC_FROM_PASSWORD_FILE='/mirror/secrets/nas-password',RESTIC_CACERT='/mirror/secrets/receiver-ca.crt',RESTIC_CACHE_DIR='/cache',GOMEMLIMIT='384MiB')
def run(args,allowed=(0,)):
    r=subprocess.run(['restic',*args],env=env,capture_output=True,text=True)
    log=root/'last-run.log'
    with log.open('a') as f:f.write((r.stdout+r.stderr).replace(source,'[private source]').replace(destination,'[offsite repository]'))
    os.chown(log,1000,1000)
    if r.returncode not in allowed:raise RuntimeError('Restic mirror operation failed: '+str(r.returncode))
    return r
r=run(['snapshots','--json'],allowed=(0,10))
if r.returncode==10:run(['init','--copy-chunker-params'])
run(['copy','--host','server-2'])
if MODE=='maintenance':
    run(['forget','--group-by','host,tags','--keep-daily','14','--keep-weekly','8','--keep-monthly','12','--prune'])
    run(['check','--read-data-subset=10%'])
else:run(['check'])
snapshots=json.loads(run(['snapshots','--json']).stdout)
status={'result':'success','snapshots':len(snapshots),'last_success':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':MODE}
f=root/'status.json';f.write_text(json.dumps(status,indent=2));os.chown(f,1000,1000)
print(json.dumps({'event':'server_2_offsite_mirror_complete',**status}),flush=True)
'''


def main():
    directory = Path(os.environ.get("MIRROR_DIR", "/opt/server-2-backup-mirror"))
    config = json.loads((directory / "config.json").read_text())
    mode = sys.argv[1] if len(sys.argv) > 1 else "copy"
    if mode not in {"copy", "maintenance"}:
        raise SystemExit("Expected copy or maintenance")
    cache = directory / "cache"
    cache.mkdir(exist_ok=True, mode=0o700)
    args = ["docker", "run", "--rm", "-i", "--name", "server-2-backup-mirror", "--read-only", "--memory", "768m", "--cpus", "1.5", "--tmpfs", "/tmp:rw,size=128m", "--label", "infra.backup.host=server-2"]
    for source, target, readonly in [
        (directory, "/mirror", False),
        (cache, "/cache", False),
        (config["restic_binary"], "/usr/local/bin/restic", True),
        (config["rclone_binary"], "/usr/local/bin/rclone", True),
        (config["gateway_settings"], "/etc/infra-backup/backup.env", True),
        (config["rclone_settings"], config["rclone_settings"], True),
        (config["gateway_lock"], "/backup.lock", False),
    ]:
        args += ["--mount", "type=bind,src=" + str(source) + ",dst=" + str(target) + (",readonly" if readonly else "")]
    args += ["--entrypoint", "python", config["tool_image"], "-"]
    (directory / "status.json").write_text(json.dumps({"result": "running", "mode": mode, "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    try:
        subprocess.run(args, input="MODE=" + repr(mode) + "\n" + PROGRAM, text=True, check=True)
    except BaseException as error:
        subprocess.run(["docker", "rm", "-f", "server-2-backup-mirror"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (directory / "status.json").write_text(json.dumps({"result": "failed", "mode": mode, "error_type": type(error).__name__}))
        raise


if __name__ == "__main__":
    def interrupt(signum, frame):
        raise InterruptedError("Offsite mirror interrupted")

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupt)
    main()
