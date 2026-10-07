import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import local_state_backup as backup


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.private = Path(self.root.name) / 'private'
        self.private.mkdir(mode=0o700)

    def connection(self, text='postgresql://operator:secret@127.0.0.1:55428/source'):
        path = self.private / 'connection'
        path.write_text(text + '\n')
        path.chmod(0o600)
        return path

    def test_private_connection_rejects_symlink_and_world_readable_file(self):
        path = self.connection()
        path.chmod(0o644)
        with self.assertRaises(backup.BackupError):
            backup.load_connection(path)
        path.chmod(0o600)
        link = self.private / 'link'
        link.symlink_to(path)
        with self.assertRaises(backup.BackupError):
            backup.load_connection(link)

    def test_connection_rejects_non_loopback_system_db_and_url_options(self):
        for url in (
            'postgresql://operator:secret@example.com:55428/source',
            'postgresql://operator:secret@127.0.0.1:55428/postgres',
            'postgresql://operator:secret@127.0.0.1:55428/source?sslmode=disable',
        ):
            with self.subTest(url=url):
                with self.assertRaises(backup.BackupError):
                    backup.load_connection(self.connection(url))

    def test_same_actual_cluster_and_database_oid_is_rejected(self):
        with self.assertRaises(backup.BackupError):
            backup.assert_distinct({'system_identifier': '123', 'database_oid': '9'}, {'system_identifier': '123', 'database_oid': '9'})
        backup.assert_distinct({'system_identifier': '123', 'database_oid': '9'}, {'system_identifier': '123', 'database_oid': '10'})

    def test_populated_target_rejected_before_restore(self):
        with mock.patch.object(backup, 'query', return_value='1'):
            with self.assertRaises(backup.BackupError):
                backup.verify_empty(object())

    def test_archive_list_rejects_extra_view_even_with_six_tables(self):
        entries = ['1; 2615 100 SCHEMA - cs_plan_actions operator']
        for position, name in enumerate(backup.TABLES, start=2):
            entries += [f'{position}; 1259 100 TABLE cs_plan_actions {name} operator',
                        f'{position + 10}; 0 100 TABLE DATA cs_plan_actions {name} operator']
        with mock.patch.object(backup, '_plain_tool', return_value=('\n'.join(entries) + '\n').encode()):
            backup.archive_list(self.private / 'archive.dump', format_version=1)
        entries.append('30; 1259 100 VIEW cs_plan_actions surprise operator')
        with mock.patch.object(backup, '_plain_tool', return_value=('\n'.join(entries) + '\n').encode()):
            with self.assertRaises(backup.BackupError):
                backup.archive_list(self.private / 'archive.dump', format_version=1)

    def test_manifest_requires_trusted_hash_version_and_archive_integrity(self):
        bundle = self.private / 'bundle'
        bundle.mkdir(mode=0o700)
        archive = bundle / 'archive.dump'
        archive.write_bytes(b'archive')
        archive.chmod(0o600)
        manifest = bundle / 'manifest.json'
        data = {'format_version': 1, 'schema': 'cs_plan_actions', 'schema_version': 1,
                'source_revision': 'a' * 40, 'migration_sha256': 'b' * 64,
                'archive_sha256': hashlib.sha256(b'archive').hexdigest(),
                'source_identity': {'system_identifier': '123', 'database_oid': '9', 'database': 'source'},
                'server_version': '17.11', 'tools': {'psql': '17.11', 'pg_dump': '17.11', 'pg_restore': '17.11'},
                'tables': {name: {'count': 0, 'sha256': hashlib.sha256(b'').hexdigest()} for name in ('schema_migrations', 'plans', 'plan_revisions', 'approvals', 'action_receipts', 'audit_events')}}
        manifest.write_text(json.dumps(data))
        manifest.chmod(0o600)
        trusted = hashlib.sha256(manifest.read_bytes()).hexdigest()
        self.assertEqual(backup.validate_manifest(bundle, trusted, 'a' * 40, data['tools'], 'b' * 64), data)
        with self.assertRaises(backup.BackupError):
            backup.validate_manifest(bundle, '0' * 64, 'a' * 40, data['tools'], 'b' * 64)
        with self.assertRaises(backup.BackupError):
            backup.validate_manifest(bundle, trusted, 'c' * 40, data['tools'], 'b' * 64)
        for field, value in (
            ('format_version', 2), ('schema', 'other_schema'), ('schema_version', 2),
            ('tools', {'psql': '17.11', 'pg_dump': '17.12', 'pg_restore': '17.11'}),
            ('migration_sha256', 'c' * 64), ('source_revision', 'c' * 40),
        ):
            with self.subTest(field=field):
                changed = {**data, field: value}
                manifest.write_text(json.dumps(changed))
                changed_trust = hashlib.sha256(manifest.read_bytes()).hexdigest()
                with self.assertRaises(backup.BackupError):
                    backup.validate_manifest(bundle, changed_trust, 'a' * 40, data['tools'], 'b' * 64)
        manifest.write_text(json.dumps(data))
        trusted = hashlib.sha256(manifest.read_bytes()).hexdigest()
        archive.write_bytes(b'corrupt')
        with self.assertRaises(backup.BackupError):
            backup.validate_manifest(bundle, trusted, 'a' * 40, data['tools'], 'b' * 64)

    def test_failed_dump_never_publishes_bundle_or_leaves_stage(self):
        source_file = self.connection()
        destination = self.private / 'new-bundle'
        with (mock.patch.object(backup, '_git_revision', return_value='a' * 40),
              mock.patch.object(backup, 'tool_versions', return_value={'psql': '17.11', 'pg_dump': '17.11', 'pg_restore': '17.11'}),
              mock.patch.object(backup, '_server_version', return_value='17.11'),
              mock.patch.object(backup, 'identity', return_value={'system_identifier': '1', 'database_oid': '2', 'database': 'source'}),
              mock.patch.object(backup, '_source_schema'),
              mock.patch.object(backup, 'snapshot_holder', return_value=nullcontext('00000003-0000001B-1')),
              mock.patch.object(backup, 'run_tool', side_effect=backup.BackupError('pg_dump failed.'))):
            with self.assertRaises(backup.BackupError):
                backup.export_bundle(str(source_file), str(destination), 'a' * 40)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.private.glob('.local-state-stage-*')), [])

    def test_publish_never_overwrites_even_an_empty_target(self):
        stage = self.private / 'stage'
        stage.mkdir(mode=0o700)
        target = self.private / 'target'
        target.mkdir(mode=0o700)
        with self.assertRaises(backup.BackupError):
            backup.publish_bundle(stage, target)
        self.assertTrue(stage.exists())
        self.assertTrue(target.exists())

    def test_publish_moves_complete_private_directory_atomically(self):
        stage = self.private / 'stage'
        stage.mkdir(mode=0o700)
        (stage / 'manifest.json').write_text('complete')
        target = self.private / 'bundle'
        backup.publish_bundle(stage, target)
        self.assertFalse(stage.exists())
        self.assertEqual((target / 'manifest.json').read_text(), 'complete')

    def test_tool_failure_does_not_expose_private_stderr(self):
        connection = backup.load_connection(self.connection())
        secret = 'secret-from-tool-stderr'
        with mock.patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['pg_dump'], stderr=secret)):
            with self.assertRaises(backup.BackupError) as caught:
                backup.run_tool('pg_dump', [], connection)
        self.assertNotIn(secret, str(caught.exception))

    def test_restore_failure_redacts_tool_stderr(self):
        connection = backup.load_connection(self.connection())
        secret = 'raw-target-password-in-stderr'
        with mock.patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['pg_restore'], stderr=secret)):
            with self.assertRaises(backup.BackupError) as caught:
                backup.restore_archive(self.private / 'archive.dump', connection)
        self.assertNotIn(secret, str(caught.exception))

    def test_snapshot_is_set_before_rows_and_tool_cannot_prompt(self):
        connection = backup.load_connection(self.connection())
        fake = mock.Mock(stdout=b'')
        with mock.patch('subprocess.run', return_value=fake) as process:
            value = backup._table_digest(connection, 'plans', '00000003-0000001B-1')
        command = process.call_args.args[0]
        sql = command[command.index('-c') + 1]
        self.assertLess(sql.index('SET TRANSACTION SNAPSHOT'), sql.index('COPY (SELECT'))
        self.assertIn('--no-password', command)
        self.assertEqual(process.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(value['count'], 0)

    def test_homebrew_server_and_tool_versions_preserve_exact_identity(self):
        connection = backup.load_connection(self.connection())
        with mock.patch.object(backup, 'query', return_value='17.11 (Homebrew)'):
            self.assertEqual(backup._server_version(connection), '17.11 (Homebrew)')
        outputs = [f'{name} (PostgreSQL) 17.11 (Homebrew)\n'.encode() for name in ('psql', 'pg_dump', 'pg_restore')]
        with mock.patch.object(backup, '_plain_tool', side_effect=outputs):
            self.assertEqual(backup.tool_versions(), {'psql': '17.11 (Homebrew)', 'pg_dump': '17.11 (Homebrew)', 'pg_restore': '17.11 (Homebrew)'})

    def test_restore_arguments_never_clean_drop_or_create(self):
        connection = backup.load_connection(self.connection())
        with mock.patch.object(backup, 'run_tool', return_value=b'') as tool:
            backup.restore_archive(self.private / 'archive.dump', connection)
        args = tool.call_args.args[1]
        self.assertIn('--single-transaction', args)
        self.assertIn('--exit-on-error', args)
        self.assertFalse(any(word in args for word in ('--clean', '--create', '--if-exists', '--drop')))

    def test_default_export_uses_one_snapshot_for_both_schemas(self):
        source_file = self.connection()
        destination = self.private / 'composite'
        snapshot = '00000003-0000001B-1'
        with (mock.patch.object(backup, '_git_revision', return_value='a' * 40),
              mock.patch.object(backup, 'tool_versions', return_value={'psql': '17.11', 'pg_dump': '17.11', 'pg_restore': '17.11'}),
              mock.patch.object(backup, '_server_version', return_value='17.11'),
              mock.patch.object(backup, 'identity', return_value={'system_identifier': '1', 'database_oid': '2', 'database': 'source'}),
              mock.patch.object(backup, '_source_schema'),
              mock.patch.object(backup, 'snapshot_holder', return_value=nullcontext(snapshot)),
              mock.patch.object(backup, 'run_tool', return_value=b'') as tool,
              mock.patch.object(backup, 'archive_list'),
              mock.patch.object(backup, 'publish_bundle') as publish):
            result = backup.export_bundle(str(source_file), str(destination), 'a' * 40)
            manifest = json.loads((publish.call_args.args[0] / 'manifest.json').read_text())
        dump_args = [call.args[1] for call in tool.call_args_list if call.args[0] == 'pg_dump']
        digest_args = [call.args[1] for call in tool.call_args_list if call.args[0] == 'psql']
        self.assertEqual(len(dump_args), 1)
        self.assertIn('--schema=cs_plan_actions', dump_args[0])
        self.assertIn('--schema=cs_production_contexts', dump_args[0])
        self.assertIn('--snapshot=' + snapshot, dump_args[0])
        self.assertEqual(len(digest_args), 8)
        for args in digest_args:
            sql = args[args.index('-c') + 1]
            self.assertIn("SET TRANSACTION SNAPSHOT '" + snapshot + "'", sql)
            self.assertLess(sql.index('SET TRANSACTION SNAPSHOT'), sql.index('COPY (SELECT'))
        self.assertEqual(sum('FROM cs_plan_actions.' in args[args.index('-c') + 1] for args in digest_args), 6)
        self.assertEqual(sum('FROM cs_production_contexts.' in args[args.index('-c') + 1] for args in digest_args), 2)
        self.assertEqual(set(manifest), {'format_version', 'source_revision', 'archive_sha256', 'source_identity', 'server_version', 'tools', 'schemas'})
        self.assertEqual(manifest['format_version'], 2)
        self.assertEqual(set(manifest['schemas']), {'cs_plan_actions', 'cs_production_contexts'})
        self.assertEqual(set(manifest['schemas']['cs_plan_actions']['tables']), set(backup.TABLES))
        self.assertEqual(set(manifest['schemas']['cs_production_contexts']['tables']), {'schema_migrations', 'contexts'})
        self.assertEqual(result['coverage'], 'plan_actions_and_production_contexts')

    def test_v2_manifest_rejects_missing_or_extra_schemas_and_tables(self):
        bundle = self.private / 'v2-bundle'
        bundle.mkdir(mode=0o700)
        archive = bundle / 'archive.dump'
        archive.write_bytes(b'archive')
        archive.chmod(0o600)
        manifest_file = bundle / 'manifest.json'
        tables = lambda names: {name: {'count': 0, 'sha256': '0' * 64} for name in names}
        plan = {'schema_version': 1, 'migration_sha256': 'b' * 64, 'tables': tables(backup.TABLES)}
        context = {'schema_version': 1, 'migration_sha256': 'c' * 64, 'tables': tables(('schema_migrations', 'contexts'))}
        data = {'format_version': 2, 'source_revision': 'a' * 40, 'archive_sha256': hashlib.sha256(b'archive').hexdigest(),
                'source_identity': {'system_identifier': '1', 'database_oid': '2', 'database': 'source'},
                'server_version': '17.11', 'tools': {'psql': '17.11', 'pg_dump': '17.11', 'pg_restore': '17.11'},
                'schemas': {'cs_plan_actions': plan, 'cs_production_contexts': context}}
        def validate(value):
            manifest_file.write_text(json.dumps(value))
            manifest_file.chmod(0o600)
            return backup.validate_manifest(bundle, hashlib.sha256(manifest_file.read_bytes()).hexdigest(), 'a' * 40,
                                            data['tools'], {'cs_plan_actions': 'b' * 64, 'cs_production_contexts': 'c' * 64})
        self.assertEqual(validate(data), data)
        for changed in (
            {**data, 'schemas': {'cs_plan_actions': plan}},
            {**data, 'schemas': {**data['schemas'], 'surprise': context}},
            {**data, 'schemas': {**data['schemas'], 'cs_production_contexts': {**context, 'tables': tables(('schema_migrations',))}}},
            {**data, 'schemas': {**data['schemas'], 'cs_production_contexts': {**context, 'tables': tables(('schema_migrations', 'contexts', 'extra'))}}},
            {**data, 'schemas': {**data['schemas'], 'cs_production_contexts': {**context, 'migration_sha256': 'd' * 64}}},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(backup.BackupError):
                    validate(changed)

    def test_v1_export_refuses_even_empty_context_schema(self):
        connection = backup.load_connection(self.connection())
        with mock.patch.object(backup, 'query', return_value='cs_plan_actions,cs_production_contexts'):
            with self.assertRaises(backup.BackupError):
                backup._source_schema(connection, format_version=1)

    def test_archive_toc_requires_all_eight_tables_and_no_foreign_entries(self):
        entries = ['1; 2615 100 SCHEMA - cs_plan_actions operator',
                   '2; 2615 100 SCHEMA - cs_production_contexts operator']
        position = 3
        for schema, names in (('cs_plan_actions', backup.TABLES),
                              ('cs_production_contexts', ('schema_migrations', 'contexts'))):
            for name in names:
                entries.extend((f'{position}; 1259 100 TABLE {schema} {name} operator',
                                f'{position + 20}; 0 100 TABLE DATA {schema} {name} operator'))
                position += 1
        def check(lines):
            with mock.patch.object(backup, '_plain_tool', return_value=('\n'.join(lines) + '\n').encode()):
                backup.archive_list(self.private / 'archive.dump', format_version=2)
        check(entries)
        for changed in (entries[:-1], entries + ['99; 1259 100 TABLE cs_production_contexts extra operator'],
                        entries + ['99; 1259 100 VIEW cs_production_contexts extra operator'],
                        entries + ['99; 1259 100 TABLE public extra operator']):
            with self.subTest(changed=changed[-1]):
                with self.assertRaises(backup.BackupError):
                    check(changed)

    def test_failed_restore_marks_target_quarantined(self):
        connection_file = self.connection()
        source_id = {'system_identifier': '1', 'database_oid': '2', 'database': 'source'}
        target_id = {'system_identifier': '1', 'database_oid': '3', 'database': 'target'}
        manifest = {'format_version': 2, 'source_identity': source_id, 'server_version': '17.11'}
        with (mock.patch.object(backup, 'tool_versions', return_value={'psql': '17.11', 'pg_dump': '17.11', 'pg_restore': '17.11'}),
              mock.patch.object(backup, 'validate_manifest', return_value=manifest),
              mock.patch.object(backup, 'archive_list'),
              mock.patch.object(backup, 'identity', side_effect=(source_id, target_id)),
              mock.patch.object(backup, '_server_version', return_value='17.11'),
              mock.patch.object(backup, 'verify_empty'),
              mock.patch.object(backup, 'restore_archive', side_effect=backup.BackupError('pg_restore failed'))):
            with self.assertRaisesRegex(backup.BackupError, 'quarantined'):
                backup.restore_bundle(str(connection_file), str(connection_file), str(self.private), 'a' * 64, 'b' * 40)


if __name__ == '__main__':
    unittest.main()
