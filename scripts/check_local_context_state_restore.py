#!/usr/bin/env python3
"""Opt-in two-schema recovery rehearsal using only new local synthetic resources.

The legacy precision recommendation is an explicit HTTP fixture. One restored
saved-context comparison uses the fixed public Python numerical tool; no model,
speech, provider or hosted service is exercised. Private archives are retained.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import secrets
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import check_local_state_restore as old

ROOT, PG, PYTHON = old.ROOT, old.PG, old.PYTHON
require, digest, private_write = old.require, old.digest, old.private_write
SCHEMAS = {'cs_plan_actions': old.TABLES, 'cs_production_contexts': ('schema_migrations', 'contexts')}


class Rehearsal(old.Rehearsal):
    def sql(self, database, query):
        return super().sql(database, query).strip()

    def setup(self):
        require(self.port == 55432, 'this increment uses only the new task port55432')
        super().setup()  # verifies free port/new private directory before initdb
        self.sql('recovery_source', (ROOT/'services/gateway/migrations/002_production_contexts.up.sql').read_text())
        for database in ('recovery_legacy_source', 'recovery_legacy_destination'):
            self.sql('postgres', 'CREATE DATABASE '+database+';')
            private_write(self.run_dir/(database+'.url'), 'postgresql://recovery_operator:'+quote(self.password,safe='')+'@127.0.0.1:'+str(self.port)+'/'+database+'\n')
        self.sql('recovery_legacy_source', (ROOT/'services/gateway/migrations/001_plans_actions.up.sql').read_text())

    def rows(self, database):
        return {schema: {table: json.loads(self.sql(database,
            "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text COLLATE \"C\"), '[]'::jsonb)::text FROM "+schema+'.'+table+' t;'))
            for table in tables} for schema,tables in SCHEMAS.items()}

    def start_gateway(self, database, upstream):
        self.stop(self.gateway)
        env = dict(self.env, GATEWAY_ADDR='127.0.0.1:'+str(self.gateway_port), GATEWAY_MODE='local',
            DATABASE_URL_FILE=str(self.run_dir/(database+'.url')), DEV_IDENTITIES_FILE=str(self.run_dir/'identities.json'),
            DEV_ALLOWED_ORIGIN='http://127.0.0.1:5173', PYTHON_SERVICE_URL=upstream,
            CONTRACT_EXAMPLES_DIR=str(ROOT/'contracts/examples'))
        self.gateway = self.start_process([ROOT/'.local-tools/state-recovery-gateway',
            '-voice-local-python', PYTHON, '-voice-local-source', ROOT/'procurement-engine/src'], env, 'gateway-'+secrets.token_hex(4))
        self.url='http://127.0.0.1:'+str(self.gateway_port)
        self.wait_http(self.url,'/ready',self.gateway,30)

    def seed(self):
        self.fixture = old.ThreadingHTTPServer(('127.0.0.1',0),old.Fixture)
        threading.Thread(target=self.fixture.serve_forever,daemon=True).start()
        self.fixture_url='http://127.0.0.1:'+str(self.fixture.server_port)
        self.start_gateway('recovery_source',self.fixture_url)
        request=json.loads((ROOT/'contracts/examples/request.json').read_text())
        create={'contract_version':'0.2.0','planning_request':request}
        saved=self.http('POST','/v2/workspaces/local-workspace/plans',create,status=201)['plan']
        require(saved['snapshot']['plan']['comparisons'][0]['expected_cost']['amount']==old.EXACT,'plan precision changed')
        self.path='/v2/plans/'+saved['saved_plan_id']
        arguments={key:request[key] for key in ('material','quantity','quote_currency','settlement_currency','reporting_currency')}
        arguments.update(buyer_id=request['buyer']['id'],destination_country=request['buyer']['destination_country'])
        confirm={'contract_version':'0.2.0','expected_revision':1,'expected_plan_version':saved['snapshot']['plan']['plan_version'],
            'action_kind':'prepare_rfq','arguments':arguments}
        approval=self.http('POST',self.path+'/approvals',confirm,status=201)['approval']
        self.execute=dict(confirm,workspace_id='local-workspace',saved_plan_id=saved['saved_plan_id'],
            approval_id=approval['approval_id'],idempotency_key='restored-executed-key')
        self.receipt=self.http('POST','/v2/actions/prepare_rfq',self.execute)
        require(self.receipt['receipt']['supplier_communication'] is False,'draft must not communicate')
        unused=self.http('POST',self.path+'/approvals',confirm,status=201)['approval']
        self.stale=dict(self.execute,approval_id=unused['approval_id'],idempotency_key='restored-stale-key')
        revised=self.http('PUT',self.path,dict(create,expected_revision=1))['plan']
        current=dict(confirm,expected_revision=2,expected_plan_version=revised['snapshot']['plan']['plan_version'])
        current_approval=self.http('POST',self.path+'/approvals',current,status=201)['approval']
        self.current_execute=dict(current,workspace_id='local-workspace',saved_plan_id=saved['saved_plan_id'],
            approval_id=current_approval['approval_id'],idempotency_key='restored-current-key')
        self.current_receipt=self.http('POST','/v2/actions/prepare_rfq',self.current_execute)
        self.contexts=[]
        for name in ('recovery_precision','recovery'):
            fixture=json.loads((ROOT/'contracts/examples'/(name+'_request.json')).read_text())
            snapshot=fixture['snapshot']; snapshot['workspace_id']='local-workspace'
            context=self.http('POST','/v2/workspaces/local-workspace/production-contexts',
                {'contract_version':'0.2.0','snapshot':snapshot},status=201)['context']
            if name=='recovery_precision':
                context=self.http('PUT','/v2/production-contexts/'+context['context_id'],
                    {'contract_version':'0.2.0','expected_revision':1,'snapshot':snapshot})['context']
                require(context['snapshot']['inventory'][0]['starting_stock']=='1000000000000000000.000000000000000001','context precision changed')
                self.precision=context; self.precision_fixture=fixture
            require(context['metadata']=={'synthetic':True,'source':'caller_supplied_synthetic','syntactic_validation':'accepted','numerical_acceptance':'not_claimed'},'context provenance differs')
            self.contexts.append(context)
        self.before_http=self.http_state()
        self.before_rows=self.rows('recovery_source')
        # Populate the legacy-only source with the same six-table workflow rows.
        for table in old.TABLES[1:]:
            rows=json.dumps(self.before_rows['cs_plan_actions'][table]).replace("'","''")
            self.sql('recovery_legacy_source', "INSERT INTO cs_plan_actions."+table+" SELECT * FROM jsonb_populate_recordset(NULL::cs_plan_actions."+table+", '"+rows+"'::jsonb);")
        require({table:len(rows) for table,rows in self.before_rows['cs_plan_actions'].items()}==
            dict(schema_migrations=1,plans=1,plan_revisions=2,approvals=3,action_receipts=2,audit_events=7),'seed plan state incomplete')
        require(len(self.before_rows['cs_production_contexts']['contexts'])==2,'seed contexts incomplete')
        self.checks.append('actual_gateway_seed_all_eight_tables_synthetic_plan_precision_fixture_and_complete_contexts')
        self.stop(self.gateway)

    def export(self, revision, bundle='bundle', source='recovery_source', version=None, success=True):
        args=['export','--source-connection-file',self.run_dir/(source+'.url'),'--output-dir',self.run_dir/bundle,'--source-revision',revision]
        if version is not None: args+=['--format-version',str(version)]
        return self.backup(args,success)

    def restore_args(self, revision, bundle='bundle', source='recovery_source', target='recovery_destination'):
        path=self.run_dir/bundle
        trusted=hashlib.sha256((path/'manifest.json').read_bytes()).hexdigest()
        return ['restore','--source-connection-file',self.run_dir/(source+'.url'),'--target-connection-file',self.run_dir/(target+'.url'),
            '--bundle-dir',path,'--expected-manifest-sha256',trusted,'--expected-source-revision',revision,'--local-test-restore']

    def empty(self, database='recovery_destination'):
        require(self.sql(database,"SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname !~ '^pg_';")=='0','negative gate wrote target')

    def archive_checks(self, revision):
        result=self.export(revision)
        require(result['format_version']==2 and result['coverage']=='plan_actions_and_production_contexts','default export is not explicitly composite')
        manifest_path=self.run_dir/'bundle/manifest.json'; manifest=json.loads(manifest_path.read_text())
        require(set(manifest['schemas'])==set(SCHEMAS),'composite omitted schema')
        restore=self.restore_args(revision)
        self.export(revision,'refused-v1',version=1,success=False)
        require(not (self.run_dir/'refused-v1').exists(),'incomplete legacy export published')
        alias=list(restore); alias[alias.index('--target-connection-file')+1]=self.run_dir/'alias.url'
        self.backup(alias,False,'same actual database')
        for database in ('recovery_negative','recovery_custom_schema','recovery_system_object'):
            changed=list(restore); changed[changed.index('--target-connection-file')+1]=self.run_dir/(database+'.url')
            self.backup(changed,False,'not an empty local database')
        require(self.sql('recovery_negative','SELECT id FROM retained_marker;')=='7','populated target altered')
        require(self.sql('recovery_custom_schema',"SELECT count(*) FROM pg_namespace WHERE nspname='pgfoo';")=='1','custom schema altered')
        require(self.sql('recovery_system_object','SELECT id FROM information_schema.recovery_marker;')=='11','system-schema object altered')
        original=manifest_path.read_bytes()
        for mutate in ('missing_schema','extra_schema','missing_table','extra_table','migration','version','count'):
            data=copy.deepcopy(manifest)
            if mutate=='missing_schema': del data['schemas']['cs_production_contexts']
            elif mutate=='extra_schema': data['schemas']['foreign']={}
            elif mutate=='missing_table': del data['schemas']['cs_production_contexts']['tables']['contexts']
            elif mutate=='extra_table': data['schemas']['cs_production_contexts']['tables']['extra']={'count':0,'sha256':'0'*64}
            elif mutate=='migration': data['schemas']['cs_production_contexts']['migration_sha256']='0'*64
            elif mutate=='version': data['schemas']['cs_production_contexts']['schema_version']=2
            elif mutate=='count': data['schemas']['cs_production_contexts']['tables']['contexts']['count']=True
            manifest_path.write_text(json.dumps(data))
            self.backup(self.restore_args(revision),False);self.empty()
        manifest_path.write_bytes(original)
        badhash=list(restore); badhash[badhash.index('--expected-manifest-sha256')+1]='0'*64
        self.backup(badhash,False);self.empty()
        archive=self.run_dir/'bundle/archive.dump'; original_archive=archive.read_bytes()
        archive.write_bytes(original_archive+b'corrupt');self.backup(restore,False,'Archive SHA256');self.empty();archive.write_bytes(original_archive)
        self.sql('recovery_source','CREATE TABLE cs_production_contexts.unexpected (id integer);')
        self.export(revision,'refused-extra-table',success=False)
        self.sql('recovery_source','DROP TABLE cs_production_contexts.unexpected;')  # only harness-owned synthetic marker
        self.sql('recovery_source','ALTER TABLE cs_production_contexts.contexts RENAME TO contexts_missing;')
        self.export(revision,'refused-missing-table',success=False)
        self.sql('recovery_source','ALTER TABLE cs_production_contexts.contexts_missing RENAME TO contexts;')
        require(self.rows('recovery_source')==self.before_rows,'source gates changed accepted state')
        restored=self.backup(restore)
        require(restored['coverage']=='plan_actions_and_production_contexts','restore coverage ambiguous')
        require(self.rows('recovery_destination')==self.before_rows,'eight complete restored tables differ')
        self.checks.extend(['v2_default_both_schemas_exact_eight_tables_restored','same_alias_populated_custom_schema_system_object_rejected_unchanged',
            'trusted_manifest_schema_table_migration_version_count_and_hash_mutations_rejected_before_write','archive_corruption_rejected_before_write',
            'actual_extra_and_missing_source_table_exports_rejected','v1_export_with_contexts_refused_without_bundle'])
        self.export(revision,'legacy-bundle',source='recovery_legacy_source',version=1)
        legacy_manifest=json.loads((self.run_dir/'legacy-bundle/manifest.json').read_text())
        require(legacy_manifest['format_version']==1 and legacy_manifest['schema']=='cs_plan_actions','v1 shape changed')
        legacy_restored=self.backup(self.restore_args(revision,'legacy-bundle','recovery_legacy_source','recovery_legacy_destination'))
        require(legacy_restored['coverage']=='legacy_plan_actions_only','v1 restore falsely complete')
        for table in old.TABLES:
            q="SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text COLLATE \"C\"),'[]'::jsonb)::text FROM cs_plan_actions."+table+" t;"
            require(self.sql('recovery_legacy_source',q)==self.sql('recovery_legacy_destination',q),'legacy v1 full rows changed')
        require(self.sql('recovery_legacy_destination',"SELECT count(*) FROM pg_namespace WHERE nspname='cs_production_contexts';")=='0','legacy restore fabricated contexts')
        self.checks.append('legacy_v1_export_restore_explicit_six_table_scope_no_contexts_created')
        return manifest,hashlib.sha256(original).hexdigest(),hashlib.sha256(original_archive).hexdigest()

    def fresh_session(self, context, role=1):
        return self.http('POST','/v2/voice/saved-context-sessions',{'contract_version':'0.2.0','context_id':context['context_id'],
            'expected_revision':context['revision']},role=role,status=201)

    def turn(self, session, role=1, status=202):
        snapshot=self.precision['snapshot']
        args={'arrival_replacements':self.precision_fixture['scenario']['arrival_replacements'],'assignments':self.precision_fixture['recovery']['assignments']}
        return self.http('POST','/v2/voice/sessions/'+session['session_id']+'/turns',{'contract_version':'0.2.0',
            'context_version':session['context_version'],'call':{'name':'compare_receipt_recovery','arguments':args}},role=role,status=status)

    def context_checks(self):
        for context in self.contexts:
            path='/v2/production-contexts/'+context['context_id']
            require(self.http('GET',path)['context']==context,'restored complete context differs')
            require(self.http('GET',path,role=1)['context']==context,'viewer restored read differs')
            require(self.http('GET',path,role=2,status=404)['error']['code']=='production_context_unavailable','foreign read semantics')
            replace={'contract_version':'0.2.0','expected_revision':context['revision'],'snapshot':context['snapshot']}
            require(self.http('PUT',path,replace,role=1,status=403)['error']['code']=='forbidden','viewer replacement semantics')
            ref={'contract_version':'0.2.0','context_id':context['context_id'],'expected_revision':context['revision']}
            require(self.http('POST','/v2/voice/saved-context-sessions',ref,role=2,status=404)['error']['code']=='production_context_unavailable','foreign saved reference semantics')
            require(self.http('POST','/v2/voice/saved-context-sessions',dict(ref,snapshot=context['snapshot']),status=422)['error']['code']=='invalid_request','snapshot override accepted')
        started=self.fresh_session(self.precision);unstarted=self.fresh_session(self.precision)
        accepted=self.turn(started);base='/v2/voice/sessions/'+started['session_id'];poll=base+'/turns/'+accepted['turn_id']+'/result'
        deadline=time.monotonic()+20
        while True:
            ready=self.http('GET',poll,role=1)
            if ready['state']=='ready':break
            require(ready['state']=='pending' and time.monotonic()<deadline,'restored comparison did not settle');time.sleep(.05)
        snapshot=self.precision['snapshot'];common={'workspace_id':snapshot['workspace_id'],'source_snapshot_id':snapshot['snapshot_id'],
            'expected_plan_version':snapshot['plan_version'],'synthetic':True,'scenario_id':started['scenario_id']}
        request={'snapshot':snapshot,'scenario':dict(common,arrival_replacements=self.precision_fixture['scenario']['arrival_replacements']),
            'recovery':dict(common,assignments=self.precision_fixture['recovery']['assignments'])}
        code='import json,sys; from procure.production.recovery import compare_receipt_recovery; f=json.load(sys.stdin); print(json.dumps(compare_receipt_recovery(f["snapshot"],f["scenario"],f["recovery"])))'
        expected=json.loads(self.command([PYTHON,'-c',code],env=dict(self.env,PYTHONPATH=str(ROOT/'procurement-engine/src'),
            PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1'),input=json.dumps(request),timeout=20,label='fixed public comparison'))
        require(ready['outcome']['result']==expected and ready['outcome']['status']==expected['status'],'restored connected result differs from public tool')
        self.comparison={'exact_public_equal':True,'expected_sha256':digest(expected),'actual_sha256':digest(ready['outcome']['result']),
            'context_revision':self.precision['revision'],'status':expected['status']}
        path='/v2/production-contexts/'+self.precision['context_id']
        replacement=self.http('PUT',path,{'contract_version':'0.2.0','expected_revision':self.precision['revision'],'snapshot':snapshot})['context']
        stale={'contract_version':'0.2.0','context_id':self.precision['context_id'],'expected_revision':self.precision['revision']}
        require(self.http('POST','/v2/voice/saved-context-sessions',stale,role=1,status=409)['error']['code']=='production_context_stale','stale restored ref accepted')
        require(self.turn(unstarted,status=409)['error']['code']=='production_context_stale','stale restored session start accepted')
        for _ in range(2):
            rejected=self.http('GET',poll,role=1,status=409)
            require(rejected['error']['code']=='production_context_stale' and 'outcome' not in rejected,'stale restored result exposed')
        self.http('POST',base+'/turns/'+accepted['turn_id']+'/cancel',role=1)
        for session in (started,unstarted):self.http('DELETE','/v2/voice/sessions/'+session['session_id'],role=1)
        self.start_gateway('recovery_destination',self.fixture_url)
        require(self.http('GET',path)['context']==replacement,'post-restore context replacement lost across gateway restart')
        require(self.http('GET',path)['context']['metadata']['numerical_acceptance']=='not_claimed','comparison fabricated saved numerical acceptance')
        self.checks.extend(['restored_context_decimal_metadata_reference_workspace_and_revision_exact','restored_viewer_foreign_override_denials',
            'restored_context_fixed_public_python_comparison_full_equality','replacement_rejects_stale_ref_start_and_repeated_poll_no_outcome',
            'stale_cancel_close_available_and_new_revision_persists_gateway_restart'])

    def recover(self,revision):
        manifest,manifest_hash,archive_hash=self.archive_checks(revision)
        self.command([PG/'pg_ctl','-D',self.run_dir/'cluster','-w','stop','-m','fast'],label='task cluster restart stop');self.cluster_started=False
        self.command([PG/'pg_ctl','-D',self.run_dir/'cluster','-l',self.run_dir/'postgres.log','-o',
            '-h 127.0.0.1 -p '+str(self.port)+' -c unix_socket_directories=','-w','start'],label='task cluster restart start');self.cluster_started=True
        self.start_gateway('recovery_destination',self.fixture_url)
        require(self.rows('recovery_destination')==self.before_rows,'restart changed eight tables')
        require(self.http_state()==self.before_http,'restored HTTP state differs after cluster/gateway restart')
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts=list(pool.map(lambda _:self.http('POST','/v2/actions/prepare_rfq',self.execute),range(8)))
        require(all(item==self.receipt for item in receipts),'restored concurrent replay receipt differs')
        changed=copy.deepcopy(self.execute);changed['arguments']['quantity']['amount']='50001'
        require(self.http('POST','/v2/actions/prepare_rfq',changed,status=409)['error']['code']=='conflict','changed-content replay accepted')
        for request in (self.stale,dict(self.current_execute,idempotency_key='new-consumed-key')):
            require(self.http('POST','/v2/actions/prepare_rfq',request,status=409)['error']['code']=='conflict','stale/consumed action accepted')
        for role in (1,2):require(self.http('POST','/v2/actions/prepare_rfq',self.execute,role=role,status=403)['error']['code']=='forbidden','restored action role accepted')
        require(self.http('GET',self.path,role=2,status=404)['error']['code']=='not_found','restored foreign plan accepted')
        require(self.rows('recovery_destination')==self.before_rows and self.http_state()==self.before_http,'replay/denials added effects')
        self.checks.extend(['actual_postgres_and_gateway_restart_exact_eight_tables_and_http_state','eight_concurrent_restored_replays_same_receipt_no_new_effect',
            'restored_action_content_consumed_stale_viewer_foreign_denials_no_effect'])
        self.context_checks()
        require(self.rows('recovery_source')==self.before_rows,'source changed after switch')
        require(self.rows('recovery_destination')['cs_plan_actions']==self.before_rows['cs_plan_actions'],'context lifecycle altered workflow state')
        return {'status':'verified_local_only','source_revision':revision,'format_version':2,'coverage':'plan_actions_and_production_contexts',
            'manifest_sha256':manifest_hash,'archive_sha256':archive_hash,'schemas':manifest['schemas'],'comparison':self.comparison,
            'source_files_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                (ROOT/'scripts/local_state_backup.py',ROOT/'scripts/check_local_context_state_restore.py',ROOT/'scripts/check_local_state_restore.py')},
            'gateway_binary_sha256':hashlib.sha256((ROOT/'.local-tools/state-recovery-gateway').read_bytes()).hexdigest(),
            'checks':self.checks,'ports':{'postgres':self.port,'gateway':self.gateway_port,'fixture':self.fixture.server_port},
            'upstream_evidence':['legacy recommendation HTTP precision fixture; no numerical/model proof',
                'one restored saved-context actual fixed public Python comparison; not provider/speech proof'],
            'limits':['local PostgreSQL17, live source required; no CloudSQL18 or catastrophic-source-loss proof',
                'quiesced task writers; no online cutover proof','trusted external checksum required; not independent authenticity',
                'no model/provider/cloud/SQL activation/supplier communication']}

    def cleanup(self):
        try:super().cleanup()
        finally:
            if self.owns_run:
                for name in ('recovery_legacy_source.url','recovery_legacy_destination.url'):
                    (self.run_dir/name).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--postgres-port',type=int,default=55432)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args(); rehearsal=Rehearsal(args.run_dir.absolute(),args.postgres_port);started=time.monotonic()
    try:
        revision=rehearsal.command(['git','rev-parse','HEAD']).strip();rehearsal.setup();rehearsal.seed();receipt=rehearsal.recover(revision)
    except (Exception,KeyboardInterrupt) as error:
        receipt={'status':'failed','error':str(error) if isinstance(error,old.RehearsalError) else 'local rehearsal failed; inspect private task artifacts',
            'error_type':type(error).__name__,'checks':rehearsal.checks}
    try:rehearsal.cleanup()
    except Exception:receipt={'status':'failed','error':'task cleanup failed; inspect private task processes'}
    receipt['elapsed_seconds']=round(time.monotonic()-started,2)
    if args.receipt:private_write(args.receipt,json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,sort_keys=True));return 0 if receipt['status']=='verified_local_only' else 1


if __name__=='__main__':sys.exit(main())
