"""Read-only host audit. Output is metadata/aggregate facts, never secrets or rows."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime, timezone


def command(args, **kwargs):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=35, **kwargs)
    if result.returncode:
        # stderr/config/command output can contain credentials; never forward it.
        raise RuntimeError('Audit subprocess failed: ' + Path(args[0]).name)
    return result.stdout.strip()


app = Path(os.environ['EC2_APP_DIR']).resolve(strict=True)
assert app.is_absolute() and (app / 'docker-compose.prod.yml').is_file()
os.chdir(app)
compose = ['docker', 'compose', '-f', 'docker-compose.prod.yml']
print(json.dumps({'phase': 'host', 'checked_out_revision': command(['git', 'rev-parse', 'HEAD']),
                  'checked_out_branch': command(['git', 'branch', '--show-current']),
                  'tracked_tree_clean': not command(['git', 'status', '--porcelain', '--untracked-files=no']),
                  'compose_services': command(compose + ['config', '--services']).splitlines(),
                  'audit_uid': os.geteuid(), 'aws_cli_available': bool(shutil.which('aws'))}), flush=True)
print(json.dumps({'phase': 'containers', 'inventory': command([
    'docker', 'ps', '--format', '{{.Names}}|{{.Image}}|{{.Status}}|{{.Label "com.docker.compose.service"}}',
]).splitlines()}), flush=True)
postgres = command(compose + ['ps', '-q', 'postgres'])
api = command(compose + ['ps', '-q', 'api'])
assert postgres and api
pg = json.loads(command(['docker', 'inspect', postgres]))[0]
print(json.dumps({'phase': 'database_storage',
                  'health': pg['State'].get('Health', {}).get('Status'),
                  'mount_types': [mount['Type'] for mount in pg.get('Mounts', [])],
                  'mount_destinations': [mount['Destination'] for mount in pg.get('Mounts', [])]}), flush=True)

api_audit = r'''
import json, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cbmtv.settings')
import django
django.setup()
from django.db import connection, transaction
from django.conf import settings
data = {'phase': 'database', 'engine': settings.DATABASES['default']['ENGINE'],
        'host_is_compose_postgres': settings.DATABASES['default'].get('HOST') == 'postgres'}
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute("SELECT app, name FROM django_migrations WHERE app IN ('content','channel','homepage') ORDER BY app, name")
        data['applied_migrations'] = [list(row) for row in cursor.fetchall()]
        for key in ('archive_mode', 'wal_level', 'default_transaction_read_only'):
            cursor.execute('SHOW ' + key)
            data[key] = cursor.fetchone()[0]
        cursor.execute('SHOW archive_command')
        archive = cursor.fetchone()[0]
        data['archive_command_configured'] = bool(archive and archive != '(disabled)')
        data['archive_tools'] = [tool for tool in ('wal-g','pgbackrest','barman','aws','rsync') if tool in archive]
        cursor.execute('SELECT archived_count, failed_count, last_archived_time, last_failed_time FROM pg_stat_archiver')
        row = cursor.fetchone()
        data['wal_archive_stats'] = {'archived_count': row[0], 'failed_count': row[1],
            'last_archive_at': str(row[2]) if row[2] else None, 'last_failure_at': str(row[3]) if row[3] else None}
        cursor.execute('SELECT rolsuper FROM pg_roles WHERE rolname = current_user')
        data['application_role_is_superuser'] = cursor.fetchone()[0]
        cursor.execute("SELECT backend_type, state, count(*) FROM pg_stat_activity WHERE datname = current_database() GROUP BY backend_type,state")
        data['connection_counts'] = [list(row) for row in cursor.fetchall()]
        cursor.execute('SELECT count(*) FROM content_content')
        data['title_count'] = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM content_content WHERE status = 'approved'")
        data['approved_title_count'] = cursor.fetchone()[0]
        cursor.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema = 'public' AND ((table_name='channel_channel' AND column_name='is_active') OR (table_name='content_content' AND column_name IN ('first_published_at','publication_history_unknown')))")
        data['branch3_columns_present'] = [list(row) for row in cursor.fetchall()]
        cursor.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('content_content'::regclass, 'channel_channel'::regclass)")
        data['custom_content_channel_trigger_count'] = cursor.fetchone()[0]
print(json.dumps(data, sort_keys=True), flush=True)
'''
print(command(compose + ['exec', '-T', '-e', 'PYTHONDONTWRITEBYTECODE=1', 'api', 'python', '-'], input=api_audit), flush=True)

tools = ('pg_dump', 'pg_basebackup', 'pgbackrest', 'wal-g', 'barman', 'restic', 'duplicity', 'rclone')
roots = [app / 'backups', Path('/var/backups'), Path('/opt/backups'), Path('/srv/backups'), Path('/mnt/backups')]
inventory = []
for root in roots:
    if not root.is_dir():
        continue
    entries = []
    try:
        for path in list(root.glob('*'))[:200] + list(root.glob('*/*'))[:200]:
            if path.is_file() and any(ext in path.name.lower() for ext in ('.dump','.sql','.backup','.gz','.gpg','.age','.pgc')):
                stat = path.stat()
                entries.append({'size': stat.st_size, 'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                                'encrypted_filename_suffix': path.suffix in ('.age','.gpg')})
        inventory.append({'directory': str(root), 'readable': True, 'candidate_file_count': len(entries),
                          'newest_candidates': sorted(entries, key=lambda item: item['modified_at'], reverse=True)[:3]})
    except PermissionError:
        inventory.append({'directory': str(root), 'readable': False})
print(json.dumps({'phase': 'backup_candidates', 'inventory': inventory,
                  'host_backup_tools': [tool for tool in tools if shutil.which(tool)]}), flush=True)

cron_paths = [Path('/etc/crontab'), Path('/etc/cron.d'), Path('/etc/cron.daily'),
              Path('/etc/cron.hourly'), Path('/etc/cron.weekly'), Path('/var/spool/cron/crontabs')]
scheduled = []
for root in cron_paths:
    try:
        paths = list(root.iterdir()) if root.is_dir() else [root]
        for path in paths[:100]:
            if path.is_file():
                content = path.read_text(errors='replace')
                matches = [tool for tool in tools if tool in content]
                scheduled.append({'source': str(path), 'backup_tools': matches,
                                  'potential_application_writer': bool(re.search(r'manage\.py|celery|\brq\b|psql|gunicorn|cbmtv|docker\s+(compose|exec)', content))})
    except PermissionError:
        scheduled.append({'source': str(root), 'readable': False})
for args, label in [(['crontab','-l'], 'current_user_crontab'),
                    (['sudo','-n','crontab','-l'], 'root_crontab')]:
    try:
        content = command(args)
        scheduled.append({'source': label, 'readable': True,
                          'backup_tools': [tool for tool in tools if tool in content],
                          'noncomment_job_count': sum(bool(line.strip()) and not line.lstrip().startswith('#') for line in content.splitlines()),
                          'potential_application_writer': bool(re.search(r'manage\.py|celery|\brq\b|psql|cbmtv|docker\s+(compose|exec)', content))})
    except (RuntimeError, FileNotFoundError):
        scheduled.append({'source': label, 'readable': False, 'reason': 'unavailable_or_no_crontab'})
print(json.dumps({'phase': 'scheduled_inventory', 'sources': scheduled}), flush=True)
try:
    timers = command(['systemctl','list-timers','--all','--no-pager','--no-legend']).splitlines()
    print(json.dumps({'phase': 'timers', 'relevant_units': [line.split()[-2:] for line in timers if re.search(r'backup|postgres|cbmtv|restic|barman|wal-g', line, re.I)]}), flush=True)
except RuntimeError:
    print(json.dumps({'phase': 'timers', 'readable': False}), flush=True)
processes = command(['ps','-eo','comm=,args=']).splitlines()
print(json.dumps({'phase': 'writer_processes', 'counts': {
    name: sum(bool(re.search(pattern, line)) for line in processes)
    for name, pattern in {'web_workers': r'^\s*gunicorn\b', 'celery': r'^\s*celery\b',
                          'rq': r'^\s*rq\b', 'python_management_commands': r'^\s*python\S*\s+\S*python\S*\s+\S*manage\.py\b'}.items()}}), flush=True)
print(json.dumps({'phase': 'complete', 'production_writes_performed': False,
                  'backup_restore_verified': False, 'publication_pause_established': False}), flush=True)
