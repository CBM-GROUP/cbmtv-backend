"""Controlled Branch 3 operations. Never print credentials, dumps or viewer rows."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

TARGET = '66c8f1412bc8b29d23bcd564f85532d8ca35b367'
APP = Path(os.environ['EC2_APP_DIR']).resolve(strict=True)
STATE_DIR = Path.home() / '.cbmtv-homepage-rollout'
STATE = STATE_DIR / 'state.json'
COMPOSE = ['docker', 'compose', '-f', 'docker-compose.prod.yml']
TABLES = ('content_content', 'channel_channel', 'content_season', 'content_episode', 'content_miniseries')
REQUIRED = [('channel','0004_channel_is_active'),('content','0017_content_first_published_at_and_more'),
            ('content','0018_preserve_unknown_publication_history'),('homepage','0001_initial')]


def run(args, data=None, text=True, timeout=120, env=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=text, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError('Operation subprocess failed: ' + Path(args[0]).name + ' exit=' + str(result.returncode))
    return result.stdout.strip() if text else result.stdout


def emit(**data):
    print(json.dumps(data, sort_keys=True), flush=True)


def save(state):
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    temporary = STATE_DIR / 'state.tmp'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(state, output, sort_keys=True)
    temporary.replace(STATE)


def load():
    return json.loads(STATE.read_text())


def sql(statement, container=None):
    if container:
        command = ['docker','exec','-i',container]
    else:
        command = COMPOSE + ['exec','-T','postgres']
    return run(command + ['sh','-c','exec psql -X -v ON_ERROR_STOP=1 -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], data=statement)


SNAPSHOT = r'''
import json,os,sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','cbmtv.settings')
import django
django.setup()
from django.db import connection,transaction
from psycopg2 import sql
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cursor.execute('SELECT pg_database_size(current_database())')
        assert cursor.fetchone()[0] < 128*1024*1024, 'Database exceeds bounded in-memory backup budget'
        cursor.execute('SELECT pg_export_snapshot()')
        snapshot=cursor.fetchone()[0]
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables=[row[0] for row in cursor.fetchall()]
        counts={}
        for table in tables:
            cursor.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier('public',table)))
            counts[table]=cursor.fetchone()[0]
        cursor.execute('SELECT app,name FROM django_migrations ORDER BY app,name')
        migrations=[list(row) for row in cursor.fetchall()]
        print(json.dumps({'snapshot':snapshot,'counts':counts,'migrations':migrations}),flush=True)
        sys.stdin.readline()
'''

UPLOAD = r'''
import hashlib,json,os,sys
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
region=os.environ['HOMEPAGE_BACKUP_REGION']
config=Config(connect_timeout=5,read_timeout=60,retries={'max_attempts':2})
account=boto3.client('sts',region_name=region,config=config).get_caller_identity()['Account']
bucket=f'cbmtv-prod-db-backups-{account}-{region}'
s3=boto3.client('s3',region_name=region,config=config)
try:
    s3.head_bucket(Bucket=bucket,ExpectedBucketOwner=account)
    tags=s3.get_bucket_tagging(Bucket=bucket,ExpectedBucketOwner=account)['TagSet']
    assert {'Key':'Purpose','Value':'postgres-backup'} in tags, 'Existing bucket purpose differs'
except ClientError as error:
    if error.response['Error']['Code'] not in ('404','NoSuchBucket','NotFound'):raise
    args={'Bucket':bucket,'ObjectOwnership':'BucketOwnerEnforced'}
    if region!='us-east-1':args['CreateBucketConfiguration']={'LocationConstraint':region}
    s3.create_bucket(**args)
    s3.put_bucket_tagging(Bucket=bucket,ExpectedBucketOwner=account,Tagging={'TagSet':[
        {'Key':'Application','Value':'cbmtv'},{'Key':'Purpose','Value':'postgres-backup'},
        {'Key':'ManagedBy','Value':'homepage-rollout'},{'Key':'RetentionDays','Value':'30'}]})
s3.put_public_access_block(Bucket=bucket,ExpectedBucketOwner=account,PublicAccessBlockConfiguration={
    'BlockPublicAcls':True,'IgnorePublicAcls':True,'BlockPublicPolicy':True,'RestrictPublicBuckets':True})
s3.put_bucket_ownership_controls(Bucket=bucket,ExpectedBucketOwner=account,OwnershipControls={'Rules':[{'ObjectOwnership':'BucketOwnerEnforced'}]})
s3.put_bucket_encryption(Bucket=bucket,ExpectedBucketOwner=account,ServerSideEncryptionConfiguration={
    'Rules':[{'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'}}]})
s3.put_bucket_policy(Bucket=bucket,ExpectedBucketOwner=account,Policy=json.dumps({'Version':'2012-10-17','Statement':[
    {'Sid':'RequireTLS','Effect':'Deny','Principal':'*','Action':'s3:*',
     'Resource':[f'arn:aws:s3:::{bucket}',f'arn:aws:s3:::{bucket}/*'],
     'Condition':{'Bool':{'aws:SecureTransport':'false'}}}]}))
s3.put_bucket_lifecycle_configuration(Bucket=bucket,ExpectedBucketOwner=account,LifecycleConfiguration={'Rules':[
    {'ID':'postgres-backups-30-days','Status':'Enabled','Filter':{'Prefix':'postgres/'},
     'Expiration':{'Days':30},'AbortIncompleteMultipartUpload':{'DaysAfterInitiation':1}}]})
assert all(s3.get_public_access_block(Bucket=bucket,ExpectedBucketOwner=account)['PublicAccessBlockConfiguration'].values())
assert s3.get_bucket_ownership_controls(Bucket=bucket,ExpectedBucketOwner=account)['OwnershipControls']['Rules'][0]['ObjectOwnership']=='BucketOwnerEnforced'
assert s3.get_bucket_encryption(Bucket=bucket,ExpectedBucketOwner=account)['ServerSideEncryptionConfiguration']['Rules'][0]['ApplyServerSideEncryptionByDefault']['SSEAlgorithm']=='AES256'
assert s3.get_bucket_lifecycle_configuration(Bucket=bucket,ExpectedBucketOwner=account)['Rules'][0]['Expiration']['Days']==30
policy=s3.get_bucket_policy(Bucket=bucket,ExpectedBucketOwner=account)['Policy']
assert json.loads(policy)['Statement'][0]['Condition']['Bool']['aws:SecureTransport']=='false'
assert s3.get_bucket_policy_status(Bucket=bucket,ExpectedBucketOwner=account)['PolicyStatus']['IsPublic'] is False
body=sys.stdin.buffer.read(128*1024*1024+1)
assert len(body)<=128*1024*1024 and body.startswith(b'PGDMP'), 'Invalid or oversized custom-format backup'
digest=hashlib.sha256(body).hexdigest()
key=os.environ['HOMEPAGE_BACKUP_KEY']
s3.put_object(Bucket=bucket,Key=key,Body=body,ServerSideEncryption='AES256',
    ExpectedBucketOwner=account,Metadata={'sha256':digest,'release':os.environ['HOMEPAGE_RELEASE']},
    ContentType='application/octet-stream')
head=s3.head_object(Bucket=bucket,Key=key,ExpectedBucketOwner=account)
assert head['ServerSideEncryption']=='AES256' and head['ContentLength']==len(body)
download=s3.get_object(Bucket=bucket,Key=key,ExpectedBucketOwner=account)['Body']
stored=hashlib.sha256()
while True:
    part=download.read(1024*1024)
    if not part:break
    stored.update(part)
assert stored.hexdigest()==digest, 'Backup round-trip checksum differs'
print(json.dumps({'bucket':bucket,'key':key,'region':region,'sha256':digest,'bytes':len(body),
    'public_access_blocked':True,'tls_required':True,'encryption':'AES256','retention_days':30,
    'roundtrip_checksum_verified':True}),flush=True)
'''


def backup():
    emit(phase='backup_preflight')
    if STATE.exists():
        previous = load()
        assert not previous.get('paused'), 'Publication is already paused; inspect state before another backup'
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    assert available >= 256 * 1024 * 1024, 'Insufficient host RAM for a bounded isolated restore'
    token = urlopen(Request('http://169.254.169.254/latest/api/token',method='PUT',
        headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'}),timeout=5).read().decode()
    identity = json.load(urlopen(Request('http://169.254.169.254/latest/dynamic/instance-identity/document',
        headers={'X-aws-ec2-metadata-token':token}),timeout=5))
    api = run(COMPOSE + ['ps','-q','api'])
    assert api
    snapshot_process = subprocess.Popen(['docker','exec','-i',api,'python','-c',SNAPSHOT],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        snapshot = json.loads(snapshot_process.stdout.readline())
        assert set(snapshot['snapshot']) <= set('0123456789ABCDEF-'), 'Unexpected snapshot identifier'
        dump = run(COMPOSE + ['exec','-T','postgres','sh','-c',
            'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges --snapshot="$1"',
            'sh',snapshot['snapshot']],text=False,timeout=180)
    finally:
        if snapshot_process.stdin:
            snapshot_process.stdin.close()
        snapshot_process.wait(timeout=20)
    assert snapshot_process.returncode == 0 and dump.startswith(b'PGDMP') and len(dump) <= 128*1024*1024
    emit(phase='consistent_dump_captured_in_memory',bytes=len(dump))
    key = 'postgres/' + datetime.now(timezone.utc).strftime('%Y/%m/%d/%H%M%S-') + secrets.token_hex(6) + '.dump'
    result = run(['docker','exec','-i','-e','HOMEPAGE_BACKUP_REGION='+identity['region'],
        '-e','HOMEPAGE_BACKUP_KEY='+key,'-e','HOMEPAGE_RELEASE='+TARGET,api,'python','-c',UPLOAD],
        data=dump,text=False,timeout=240)
    metadata = json.loads(result)
    assert metadata['sha256'] == hashlib.sha256(dump).hexdigest()
    emit(phase='encrypted_backup_roundtrip_verified',**metadata)
    save({'backup':metadata,'backup_verified':False,'source_counts':snapshot['counts'],
          'source_migrations':snapshot['migrations'],'target':TARGET,'paused':False})
    restore_name = 'cbmtv-homepage-restore-' + os.environ['HOMEPAGE_RUN_ID']
    restore_id = None
    restore_env = dict(os.environ, POSTGRES_PASSWORD=secrets.token_urlsafe(32))
    image = run(['docker','inspect','--format','{{.Image}}',run(COMPOSE+['ps','-q','postgres'])])
    try:
        restore_id = run(['docker','run','--detach','--name',restore_name,
            '--label','cbmtv.homepage.restore='+os.environ['HOMEPAGE_RUN_ID'],
            '--network','none','--memory','256m','--memory-swap','256m',
            '--tmpfs','/var/lib/postgresql/data:rw,nosuid,size=192m',
            '-e','POSTGRES_PASSWORD','-e','POSTGRES_DB=restore_test','-e','POSTGRES_USER=restore_test',
            image,'postgres','-c','shared_buffers=16MB','-c','work_mem=1MB','-c','maintenance_work_mem=16MB',
            '-c','max_connections=10'],env=restore_env)
        for _ in range(40):
            ready = subprocess.run(['docker','exec',restore_id,'pg_isready','-U','restore_test','-d','restore_test'],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated restore did not become ready')
        run(['docker','exec','-i',restore_id,'pg_restore','-U','restore_test','-d','restore_test',
             '--no-owner','--no-privileges','--exit-on-error'],data=dump,text=False,timeout=180)
        for table,count in snapshot['counts'].items():
            quoted='"'+table.replace('"','""')+'"'
            assert int(sql('SELECT count(*) FROM public.'+quoted,restore_id)) == count, 'Restored table count differs'
        restored = json.loads(sql("SELECT coalesce(json_agg(json_build_array(app,name) ORDER BY app,name),'[]') FROM django_migrations",restore_id))
        assert restored == snapshot['migrations'], 'Restored migration records differ'
        restore_info = json.loads(run(['docker','inspect',restore_id]))[0]
        assert restore_info['HostConfig']['NetworkMode']=='none'
        assert '/var/lib/postgresql/data' in restore_info['HostConfig']['Tmpfs']
        state=load()
        state.update(backup_verified=True,backup_verified_at=datetime.now(timezone.utc).isoformat())
        save(state)
        emit(phase='backup_verified',**metadata,restore_verified=True,
             restored_table_count=len(snapshot['counts']),foreign_keys_restored=True,
             migration_records_match=True,restore_network='none',restore_storage='tmpfs')
    finally:
        if restore_id:
            label=run(['docker','inspect','--format','{{index .Config.Labels "cbmtv.homepage.restore"}}',restore_id])
            assert label==os.environ['HOMEPAGE_RUN_ID']
            run(['docker','rm','--force',restore_id])


def pause():
    state=load()
    assert state['backup_verified'] and state['target']==TARGET and not state['paused']
    age=datetime.now(timezone.utc)-datetime.fromisoformat(state['backup_verified_at'])
    assert age.total_seconds()<3600, 'Restore verification must be recent'
    # The migration's post_migrate default-channel hook must not need an insert.
    default_count=int(sql("SELECT count(*) FROM channel_channel WHERE name IN ('CBM Movies','CBM Music','CBM Documentaries','CBM Originals','CBM Series')"))
    assert default_count==5, 'Default channels require review before the write pause'
    sql(PAUSE_SQL)
    state.update(paused=True,paused_at=datetime.now(timezone.utc).isoformat())
    save(state)
    guards=int(sql("SELECT count(*) FROM pg_trigger WHERE tgname='cbmtv_homepage_rollout_pause' AND tgenabled='A' AND NOT tgisinternal"))
    assert guards==5
    probes=["UPDATE content_content SET title=title WHERE id=(SELECT min(id) FROM content_content)",
            "UPDATE channel_channel SET name=name WHERE id=(SELECT min(id) FROM channel_channel)"]
    for probe in probes:
        result=subprocess.run(COMPOSE+['exec','-T','postgres','sh','-c',
            'exec psql -X -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
            input='BEGIN; '+probe+'; ROLLBACK;',stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert result.returncode and '55000' in result.stderr, 'Independent writer pause probe failed'
    state['paused_counts']={table:int(sql('SELECT count(*) FROM public.'+table)) for table in TABLES}
    save(state)
    emit(phase='publication_paused',always_enabled_guards=guards,independent_writer_probes='blocked',
         viewer_reads_and_progress='not_globally_paused')


def verify():
    state=load()
    assert state['paused'] and state['backup_verified'] and run(['git','rev-parse','HEAD'])==TARGET
    applied=json.loads(sql("SELECT coalesce(json_agg(json_build_array(app,name)),'[]') FROM django_migrations"))
    assert all(list(migration) in applied for migration in REQUIRED)
    for table,count in state['paused_counts'].items():
        assert int(sql('SELECT count(*) FROM public.'+table))==count, 'Content ownership/count changed during pause'
    assert int(sql('SELECT count(*) FROM content_content WHERE first_published_at IS NOT NULL'))==0
    assert int(sql('SELECT count(*) FROM content_content WHERE NOT publication_history_unknown'))==0
    # Installed Django contract checks run read-only apart from an outer rollback.
    api=run(COMPOSE+['ps','-q','api'])
    result=run(['docker','exec','-i',api,'python','-c',VERIFY_API],timeout=180)
    proof=json.loads(result)
    assert proof['passed']
    state.update(verified_revision=TARGET,verified_at=datetime.now(timezone.utc).isoformat())
    save(state)
    emit(phase='backend_verified',revision=TARGET,migrations_verified=REQUIRED,
         legacy_history_preserved=True,publication_still_paused=True,**proof)


def resume():
    state=load()
    assert state['paused'] and state.get('verified_revision')==TARGET
    assert run(['git','rev-parse','HEAD'])==TARGET
    sql(RESUME_SQL)
    assert int(sql("SELECT count(*) FROM pg_trigger WHERE tgname='cbmtv_homepage_rollout_pause' AND NOT tgisinternal"))==0
    state.update(paused=False,resumed_at=datetime.now(timezone.utc).isoformat())
    save(state)
    emit(phase='publication_resumed',revision=TARGET,temporary_guards_remaining=0)


def main():
    assert APP.is_absolute() and (APP/'docker-compose.prod.yml').is_file()
    os.chdir(APP)
    operation=os.environ['HOMEPAGE_OPERATION']
    assert operation in ('backup','pause','verify','resume')
    {'backup':backup,'pause':pause,'verify':verify,'resume':resume}[operation]()


# GENERATED ACCEPTANCE PAYLOADS
PAUSE_SQL = "-- Temporary publication pause. Test on an isolated restore before production use.\n-- This transaction fails rather than replacing any pre-existing guard.\nBEGIN;\nSET LOCAL lock_timeout = '5s';\nDO $$ BEGIN\n    IF to_regprocedure('public.cbmtv_homepage_publication_guard()') IS NOT NULL THEN\n        RAISE EXCEPTION 'A homepage publication guard already exists; inspect it before proceeding';\n    END IF;\nEND $$;\nCREATE FUNCTION public.cbmtv_homepage_publication_guard() RETURNS trigger\nLANGUAGE plpgsql AS $$\nBEGIN\n    -- The only data-migration write needed by Branch 3 marks unknown history.\n    -- It changes no publication status, ownership, media or visible metadata.\n    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'content_content'\n       AND (to_jsonb(NEW) - 'publication_history_unknown') = (to_jsonb(OLD) - 'publication_history_unknown')\n       AND to_jsonb(OLD)->>'publication_history_unknown' = 'false'\n       AND to_jsonb(NEW)->>'publication_history_unknown' = 'true'\n       AND to_jsonb(NEW)->>'first_published_at' IS NULL THEN\n        RETURN NEW;\n    END IF;\n    RAISE EXCEPTION USING ERRCODE = '55000',\n        MESSAGE = 'Content publication and channel changes are temporarily paused for verified maintenance';\nEND $$;\nCREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_content\n    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();\nCREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.channel_channel\n    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();\nCREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_season\n    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();\nCREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_episode\n    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();\nCREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_miniseries\n    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();\nALTER TABLE public.content_content ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;\nALTER TABLE public.channel_channel ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;\nALTER TABLE public.content_season ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;\nALTER TABLE public.content_episode ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;\nALTER TABLE public.content_miniseries ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;\nCOMMIT;\n"

RESUME_SQL = "-- Remove only this rollout's temporary guards after backend verification.\nBEGIN;\nSET LOCAL lock_timeout = '5s';\nDROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_content;\nDROP TRIGGER cbmtv_homepage_rollout_pause ON public.channel_channel;\nDROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_season;\nDROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_episode;\nDROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_miniseries;\nDROP FUNCTION public.cbmtv_homepage_publication_guard();\nCOMMIT;\n"

VERIFY_API = '"""Production acceptance probes; synthetic writes are always rolled back."""\nimport copy\nimport json\nimport os\nimport secrets\nfrom datetime import timedelta\nos.environ.setdefault(\'DJANGO_SETTINGS_MODULE\', \'cbmtv.settings\')\nimport django\ndjango.setup()\nfrom django.db import transaction\nfrom django.utils import timezone\nfrom rest_framework.test import APIClient\nfrom rest_framework_simplejwt.tokens import RefreshToken\nfrom accounts.models import User, DashboardRole\nfrom content.models import Content\nfrom content.publication import public_eligibility\nfrom library.models import WatchProgress\n\nhost = \'apicbmtv.cbmgroupco.com\'\nchecks = []\n\ndef client(user=None):\n    result = APIClient(HTTP_HOST=host)\n    if user:\n        result.credentials(HTTP_AUTHORIZATION=\'Bearer \' + str(RefreshToken.for_user(user).access_token))\n    return result\n\ndef section(response, name):\n    assert response.status_code == 200, \'Homepage response status\'\n    return next(row for row in response.data[\'sections\'] if row[\'id\'] == name)[\'items\']\n\ndef private(response):\n    assert response[\'Cache-Control\'] == \'private, no-store\'\n    assert {\'authorization\', \'cookie\'} <= {key.strip().lower() for key in response[\'Vary\'].split(\',\')}\n\nwith transaction.atomic():\n    try:\n        suffix = secrets.token_hex(8)\n        def user(label, **kwargs):\n            instance = User(email=f\'rollout-{suffix}-{label}@example.invalid\', name=\'Temporary rollout verification\', **kwargs)\n            instance.set_unusable_password()\n            instance.save()\n            return instance\n        viewer_a, viewer_b = user(\'a\'), user(\'b\')\n        manager = user(\'manager\', is_staff=True, role=\'internal_admin\')\n        role = DashboardRole.objects.create(name=\'rollout-\' + suffix, permissions=[\'channels\'])\n        restricted = user(\'restricted\', dashboard_role=role)\n        anon, a, b, admin, denied = client(), client(viewer_a), client(viewer_b), client(manager), client(restricted)\n        health = anon.get(\'/health/\')\n        assert health.status_code == 200 and health.json()[\'status\'] == \'ok\'\n        home = anon.get(\'/api/homepage/\')\n        assert not section(home, \'continue_watching\')\n        private(home)\n        checks += [\'health\', \'anonymous_homepage\', \'private_cache_headers\']\n        for actor in (anon, a, denied):\n            for path, method in ((\'/api/homepage/manage/\', \'get\'), (\'/api/homepage/manage/\', \'put\'), (\'/api/homepage/preview/\', \'post\')):\n                response = getattr(actor, method)(path, {}, format=\'json\') if method != \'get\' else actor.get(path)\n                assert response.status_code in (401, 403), \'Management permission boundary\'\n                private(response)\n        checks.append(\'anonymous_viewer_and_wrong_section_management_denied\')\n        response = admin.get(\'/api/homepage/manage/\')\n        assert response.status_code == 200\n        config = copy.deepcopy(response.data)\n        titles = list(Content.objects.filter(**public_eligibility()).order_by(\'pk\')[:2])\n        assert len(titles) == 2, \'Two public titles needed for isolated progress probes\'\n        for viewer, title in ((viewer_a, titles[0]), (viewer_b, titles[1])):\n            WatchProgress.objects.create(user=viewer, content=title, position_seconds=41, duration_seconds=100)\n        for actor, title in ((a, titles[0]), (b, titles[1])):\n            response = actor.get(\'/api/homepage/\')\n            items = section(response, \'continue_watching\')\n            assert len(items) == 1 and items[0][\'content\'][\'id\'] == title.pk\n            private(response)\n        assert not section(anon.get(\'/api/homepage/\'), \'continue_watching\')\n        checks.append(\'jwt_authenticated_viewers_receive_only_own_progress\')\n        now = timezone.now()\n        config[\'featured_titles\'] = [{\'content\': titles[0].pk, \'start_at\': (now - timedelta(hours=1)).isoformat(), \'end_at\': (now + timedelta(hours=1)).isoformat()}]\n        config[\'featured_channels\'] = []\n        response = admin.put(\'/api/homepage/manage/\', config, format=\'json\')\n        assert response.status_code == 200, \'Authorized configuration save\'\n        assert admin.get(\'/api/homepage/manage/\').data[\'featured_titles\'][0][\'content\'] == titles[0].pk\n        assert [item[\'id\'] for item in section(anon.get(\'/api/homepage/\'), \'featured_hero\')] == [titles[0].pk]\n        preview = admin.post(\'/api/homepage/preview/\', config, format=\'json\')\n        assert [item[\'id\'] for item in section(preview, \'featured_hero\')] == [titles[0].pk]\n        assert not section(preview, \'continue_watching\')\n        private(preview)\n        checks += [\'authorized_configuration_save_and_readback\', \'preview_uses_same_eligibility_without_personal_history\', \'active_schedule_visible\']\n        for start, end in ((now + timedelta(hours=1), now + timedelta(hours=2)), (now - timedelta(hours=2), now - timedelta(hours=1))):\n            config[\'featured_titles\'][0].update(start_at=start.isoformat(), end_at=end.isoformat())\n            assert admin.put(\'/api/homepage/manage/\', config, format=\'json\').status_code == 200\n            assert not section(anon.get(\'/api/homepage/\'), \'featured_hero\'), \'Schedule bypassed by fallback\'\n        checks.append(\'future_and_expired_schedules_hidden_without_fallback\')\n        invalid = copy.deepcopy(config)\n        invalid[\'featured_titles\'][0][\'start_at\'] = \'2026-11-03T00:00:00\'\n        assert admin.put(\'/api/homepage/manage/\', invalid, format=\'json\').status_code == 400\n        invalid = copy.deepcopy(config)\n        invalid[\'featured_titles\'][0][\'end_at\'] = invalid[\'featured_titles\'][0][\'start_at\']\n        assert admin.put(\'/api/homepage/manage/\', invalid, format=\'json\').status_code == 400\n        assert not section(anon.get(\'/api/homepage/\'), \'new_releases\'), \'Unknown legacy dates treated as new\'\n        checks += [\'naive_and_invalid_schedule_rejected\', \'legacy_unknown_history_excluded_from_new_releases\']\n    finally:\n        transaction.set_rollback(True)\n\nassert not User.objects.filter(email__startswith=\'rollout-\' + suffix + \'-\').exists()\nassert not DashboardRole.objects.filter(name=\'rollout-\' + suffix).exists()\nchecks.append(\'synthetic_accounts_progress_and_configuration_writes_rolled_back\')\nprint(json.dumps({\'passed\': True, \'checks\': checks}), flush=True)\n'

if __name__=='__main__':
    try:
        main()
    except Exception as error:
        # Do not forward subprocess stderr, credential errors, SQL rows or dumps.
        emit(phase='operation_failed',operation=os.environ.get('HOMEPAGE_OPERATION'),error_type=type(error).__name__,
             publication_pause_state='inspect state; never assume automatic resume')
        raise SystemExit(1)
