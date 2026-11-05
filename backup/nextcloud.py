"""Nextcloud AIO capture adapter; the controller remains owner of its children."""
import json
from pathlib import Path
import subprocess
import time


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(args):
    return subprocess.check_output(args, text=True)


def inspect(declarations):
    ids = output(['docker', 'ps', '-q']).split()
    children = [c for c in json.loads(output(['docker', 'inspect', *ids]))
                if c['Name'].startswith('/nextcloud-aio-')]
    required = {'nextcloud-aio-mastercontainer', 'nextcloud-aio-nextcloud',
                'nextcloud-aio-database', 'nextcloud-aio-redis'}
    if not required.issubset({c['Name'].lstrip('/') for c in children}):
        raise RuntimeError('Nextcloud capture requires its controller, app, PostgreSQL and Redis')
    volumes = {v for d in declarations.values() for v in d.get('volumes', [])}
    roots = [p for d in declarations.values() for p in d.get('paths', [])]
    excluded = [p for d in declarations.values() for p in d.get('exclude', {})]
    for c in children:
        for m in c['Mounts']:
            if m['Type'] == 'volume' and m['Name'] not in volumes:
                raise RuntimeError('Undeclared AIO volume: ' + m['Name'])
            if m['Type'] == 'bind' and m['Source'] != '/var/run/docker.sock':
                if not any(Path(m['Source']).is_relative_to(Path(p)) for p in roots + excluded):
                    raise RuntimeError('Undeclared AIO bind: ' + m['Source'])
    return children


def plan(declarations, volumes):
    children = inspect(declarations)
    doc = next(d for d in declarations.values() if d.get('adapter') == 'nextcloud-aio')
    copies = doc['quiesce']['copy']
    copies += [m['Source'] for c in children for m in c['Mounts']
               if m['Type'] == 'bind' and m['Source'] != '/var/run/docker.sock']
    database = volumes['nextcloud_aio_database']
    doc['quiesce'] = {'method': 'stop', 'services': [c['Name'].lstrip('/') for c in children
                      if c['Name'] not in ('/nextcloud-aio-database', '/nextcloud-aio-redis')],
                      'copy': sorted(set(p for p in copies if p != database))}
    return {'nextcloud-aio': {'database_volume': 'nextcloud_aio_database',
                             'containers': children}}


def prepare(cleanup):
    status = json.loads(output(['docker', 'exec', '--user', 'www-data', 'nextcloud-aio-nextcloud',
                               'php', 'occ', 'status', '--output=json']))
    if status.get('maintenance'):
        raise RuntimeError('Preserving existing Nextcloud maintenance state; capture aborted')
    cleanup.callback(resume)
    command(['docker', 'exec', '--user', 'www-data', 'nextcloud-aio-nextcloud',
             'php', 'occ', 'maintenance:mode', '--on'], stdout=subprocess.DEVNULL)


def resume():
    for _ in range(90):
        r = subprocess.run(['docker', 'exec', '--user', 'www-data', 'nextcloud-aio-nextcloud',
                            'php', 'occ', 'maintenance:mode', '--off'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError('Nextcloud maintenance cleanup needs attention')


def dump(stage, filename, container, program):
    with (stage / filename).open("wb") as f:
        command(["docker", "exec", container, "sh", "-c", program], stdout=f)

def capture(stage):
    name = "nextcloud-aio-database"
    dump(stage, "database.dump", name, 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"')
    dump(stage, "globals.sql", name, 'exec pg_dumpall --globals-only -U "$POSTGRES_USER"')
    with (stage / "database.dump").open("rb") as src:
        command(["docker", "exec", "-i", name, "pg_restore", "--list"], stdin=src, stdout=subprocess.DEVNULL)
    sql = "SELECT format('ALTER DATABASE %I OWNER TO %I;',datname,pg_get_userbyid(datdba)) FROM pg_database WHERE datname=current_database(); SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC;',current_database()); SELECT format('GRANT %s ON DATABASE %I TO %s%s;',x.privilege_type,d.datname,CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(x.grantee)) END,CASE WHEN x.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END) FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) x WHERE d.datname=current_database();"
    with (stage / "database-attributes.sql").open("wb") as f:
        command(["docker", "exec", name, "sh", "-c", 'exec psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', "sh", sql], stdout=f)
    def query(statement):
        return output(["docker", "exec", name, "sh", "-c", 'exec psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', "sh", statement])
    tables = query("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    counts = " UNION ALL ".join("SELECT '" + t.replace("'", "''") + "',count(*) FROM \"" + t.replace('"', '""') + '\"' for t in tables) + " ORDER BY 1"
    (stage / "postgres-counts.sql").write_text(counts)
    (stage / "postgres-counts.txt").write_text(query(counts))
    command(["docker", "exec", "nextcloud-aio-redis", "sh", "-c", 'REDISCLI_AUTH="$REDIS_HOST_PASSWORD" redis-cli SAVE'], stdout=subprocess.DEVNULL)
