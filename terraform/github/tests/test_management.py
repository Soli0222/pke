import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import manage


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.c = config.load()

    def change(self, address, actions, before=None, after=None, kind='github_repository'):
        return {'resource_changes': [{'address': address, 'type': kind,
                                     'change': {'actions': actions, 'before': before, 'after': after}}]}

    def test_unknown_and_duplicate_yaml_keys_fail(self):
        self.c['repositories']['pke']['archvied'] = True
        with self.assertRaises(config.ConfigError):
            config.validate(self.c)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.yaml'
            path.write_text('owner: one\nowner: two\n')
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_deleted_inventory_id_requires_explicit_disposition(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'base.yaml'
            base.write_bytes(config.MANIFEST.read_bytes())
            self.c['repositories'].pop('pke')
            with self.assertRaises(config.ConfigError):
                config.validate_transition(base, self.c)
            self.c['retired']['pke'] = {'name': 'pke', 'action': 'delete'}
            config.validate_transition(base, self.c)
            self.c['retired'].pop('private')
            with self.assertRaises(config.ConfigError):
                config.validate_transition(base, self.c)

    def test_terraform_environment_cannot_disable_locks_or_log_plaintext(self):
        with patch.dict(manage.os.environ, {'TF_LOG': 'TRACE', 'TF_LOG_PATH': '/tmp/leak', 'TF_CLI_ARGS_plan': '-lock=false'}):
            env = manage.tool_environment()
        self.assertNotIn('TF_LOG', env)
        self.assertNotIn('TF_LOG_PATH', env)
        self.assertNotIn('TF_CLI_ARGS_plan', env)

    def test_standard_saved_plan_check_needs_no_receipt_and_only_reads(self):
        plan = self.change('github_repository.repo_pke', ['update'],
                           {'name': 'pke', 'description': 'old'}, {'name': 'pke', 'description': 'new'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'any-plan-filename'
            path.write_bytes(b'fixture-plan')
            with patch.object(manage, 'generate', return_value=self.c), \
                 patch.object(manage, 'run', return_value=json.dumps(plan)) as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                manage.check_plan_command(argparse.Namespace(plan=str(path)))
            run.assert_called_once_with(['terraform', 'show', '-json', str(path.resolve())])
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(path.read_bytes(), b'fixture-plan')

    def test_import_preparation_only_writes_native_blocks_and_does_not_overwrite(self):
        remote = {name: {'name': name} for name in ('pke', 'helm-charts')}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(manage, 'ROOT', Path(directory)), \
             patch.object(manage, 'generate', return_value=self.c), \
             patch.object(manage, 'remote_repositories', return_value=remote), \
             patch.object(manage, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            manage.imports_command()
            path = Path(directory) / 'imports.generated.tf.json'
            self.assertEqual(json.loads(path.read_text()), {'import': [
                {'to': 'github_repository.repo_helm-charts', 'id': 'helm-charts'},
                {'to': 'github_repository.repo_pke', 'id': 'pke'},
                {'to': 'github_branch_default.repo_pke', 'id': 'pke'},
            ]})
            with self.assertRaises(config.ConfigError):
                manage.imports_command()
            run.assert_not_called()

    def test_ambiguous_secret_sources_fail(self):
        source = self.c['secret_sets']['renovate']['RENOVATE_CLIENT_ID']['onepassword']
        source['reference'] = 'op://some/reference'
        with self.assertRaises(config.ConfigError):
            config.validate(self.c)

    def test_explicit_secret_selection_and_deletion(self):
        repo = {'state': 'active', 'secret_sets': []}
        self.assertEqual(config.secrets_for(self.c, repo), {})
        repo['secret_sets'] = ['renovate']
        repo['actions_secrets'] = {'RENOVATE_CLIENT_ID': None}
        self.assertIsNone(config.secrets_for(self.c, repo)['RENOVATE_CLIENT_ID'])
        self.assertIn('RENOVATE_PRIVATE_KEY', config.secrets_for(self.c, repo))

    def test_duplicate_names_and_conflicting_categories_fail(self):
        self.c['repositories']['pke']['name'] = 'SUI'
        with self.assertRaises(config.ConfigError):
            config.validate(self.c)

    def test_forgotten_repository_can_be_readopted_with_new_id(self):
        self.c['repositories']['pke-v2'] = self.c['repositories'].pop('pke') | {'name': 'pke'}
        self.c['retired']['pke'] = {'name': 'pke', 'action': 'forget'}
        config.validate(self.c)
        self.c['retired']['pke']['action'] = 'delete'
        with self.assertRaises(config.ConfigError):
            config.validate(self.c)
        self.c = config.load()
        self.c['retired']['pke'] = {'name': 'pke', 'action': 'delete'}
        with self.assertRaises(config.ConfigError):
            config.validate(self.c)

    def test_generated_values_are_literals(self):
        self.c['repositories']['pke']['description'] = '${file("private")} %{if true}text'
        generated = json.loads(config.render(self.c, b'fixture'))
        self.assertEqual(generated['resource']['github_repository']['repo_pke']['description'],
                         '$${file("private")} %%{if true}text')

    def test_archive_and_rename_keep_address(self):
        self.c['repositories']['pke'].update(state='archived', name='new-name')
        result = json.loads(config.render(self.c, b'fixture'))
        resource = result['resource']['github_repository']['repo_pke']
        self.assertEqual(resource['name'], 'new-name')
        self.assertTrue(resource['archived'])
        self.assertNotIn('archived', resource['lifecycle']['ignore_changes'])
        self.assertIn('description', resource['lifecycle']['ignore_changes'])
        self.assertNotIn('repo_pke', result['resource']['github_branch_default'])
        self.assertIn({'from': 'github_branch_default.repo_pke', 'lifecycle': {'destroy': False}}, result['removed'])

    def test_implicit_deletion_and_replacement_fail(self):
        for actions in (['delete'], ['delete', 'create'], ['create', 'delete'], ['forget']):
            with self.subTest(actions=actions), self.assertRaises(config.ConfigError):
                manage.inspect_plan(self.change('github_repository.repo_pke', actions, {'name': 'pke'}), self.c)

    def test_explicit_delete_checks_remote_name(self):
        self.c['repositories'].pop('pke')
        self.c['retired']['pke'] = {'name': 'pke', 'action': 'delete'}
        plan = self.change('github_repository.repo_pke', ['delete'], {'name': 'pke'})
        manage.inspect_plan(plan, self.c)
        plan['resource_changes'][0]['change']['before']['name'] = 'other-repo'
        with self.assertRaises(config.ConfigError):
            manage.inspect_plan(plan, self.c)

    def test_secret_handoff_is_forget_only_and_redacted(self):
        plan = self.change('github_actions_secret.repository["pke/KEY"]', ['forget'],
                           {'value': 'NEVER-PRINT-THIS'}, kind='github_actions_secret')
        self.assertNotIn('NEVER-PRINT-THIS', json.dumps(manage.inspect_plan(plan, self.c)))
        plan['resource_changes'][0]['change']['actions'] = ['delete']
        with self.assertRaises(config.ConfigError):
            manage.inspect_plan(plan, self.c)

    def test_archived_secret_sync_does_not_read_or_write(self):
        args = argparse.Namespace(all=False, repo=['helm-charts'], apply=True)
        with patch.object(manage, 'gh_api') as api, patch.object(manage, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(manage.secret_command(args), 0)
            api.assert_not_called()
            run.assert_not_called()

    def test_secret_check_never_reads_onepassword(self):
        args = argparse.Namespace(all=False, repo=['pke'], apply=False)
        with patch.object(manage, 'gh_api', return_value={'name': 'pke', 'archived': False}), \
             patch.object(manage, 'run', return_value='[{"secrets": []}]'), \
             patch.object(manage, 'read_source') as read, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(manage.secret_command(args), 1)
            read.assert_not_called()

    def test_secret_sync_deduplicates_sources_and_uses_stdin(self):
        args = argparse.Namespace(all=False, repo=['pke', 'sui'], apply=True)
        calls = []
        def fake_run(command, *, stdin=None):
            calls.append((command, stdin))
            return '[{"secrets": []}]' if command[1] == 'api' else ''
        def fake_api(path):
            return {'name': path.split('/')[-1], 'archived': False}
        with patch.object(manage, 'gh_api', side_effect=fake_api), \
             patch.object(manage, 'run', side_effect=fake_run), \
             patch.object(manage, 'read_source', return_value='SECRET\n') as read, \
             patch.object(manage.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as output:
            manage.secret_command(args)
        self.assertEqual(read.call_count, 2)
        writes = [(command, stdin) for command, stdin in calls if command[1] == 'secret']
        self.assertEqual(len(writes), 4)
        self.assertTrue(all(stdin == 'SECRET\n' for _, stdin in writes))
        self.assertNotIn('SECRET', output.getvalue())
        self.assertNotIn('SECRET', json.dumps([command for command, _ in calls]))

    def test_failed_source_prevents_all_writes(self):
        args = argparse.Namespace(all=False, repo=['pke'], apply=True)
        with patch.object(manage, 'gh_api', return_value={'name': 'pke', 'archived': False}), \
             patch.object(manage, 'run', return_value='[{"secrets": []}]') as run, \
             patch.object(manage, 'read_source', side_effect=config.ConfigError('locked')):
            with self.assertRaises(config.ConfigError):
                manage.secret_command(args)
        self.assertTrue(all(call.args[0][1] == 'api' for call in run.call_args_list))

    def test_only_explicit_null_secret_is_deleted(self):
        self.c['repositories']['pke']['secret_sets'] = []
        self.c['repositories']['pke']['actions_secrets'] = {'OLD_KEY': None}
        args = argparse.Namespace(all=False, repo=['pke'], apply=True)
        def fake_run(command, **kwargs):
            return '[{"secrets":[{"name":"OLD_KEY"},{"name":"UNMANAGED_KEY"}]}]' if command[1] == 'api' else ''
        with patch.object(manage, 'load', return_value=self.c), \
             patch.object(manage, 'gh_api', return_value={'name': 'pke', 'archived': False}), \
             patch.object(manage, 'run', side_effect=fake_run) as run, \
             patch.object(manage, 'read_source') as read, contextlib.redirect_stdout(io.StringIO()):
            manage.secret_command(args)
        read.assert_not_called()
        writes = [call.args[0] for call in run.call_args_list if call.args[0][1] == 'secret']
        self.assertEqual(writes, [['gh', 'secret', 'delete', 'OLD_KEY', '--repo', 'Soli0222/pke', '--app', 'actions']])

    def test_error_output_never_exposes_tool_stderr(self):
        result = subprocess.CompletedProcess(['op', 'read'], 1, 'SECRET', 'SECRET')
        with patch.object(manage.subprocess, 'run', return_value=result):
            with self.assertRaises(config.ConfigError) as caught:
                manage.run(['op', 'read', 'reference'])
        self.assertNotIn('SECRET', str(caught.exception))

    def test_setup_failure_is_atomic_in_bash_and_zsh(self):
        script = str(config.ROOT / 'setup.sh')
        for shell in ('bash', 'zsh'):
            with self.subTest(shell=shell):
                command = 'unset GITHUB_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; gh() { printf fixture-token; }; op() { return 1; }; source "$1" >/dev/null 2>/dev/null; result=$?; [[ $result -ne 0 && -z ${GITHUB_TOKEN:-} && -z ${AWS_ACCESS_KEY_ID:-} && -z ${AWS_SECRET_ACCESS_KEY:-} ]]'
                result = subprocess.run([shell, '-c', command, 'test', script], capture_output=True)
                self.assertEqual(result.returncode, 0)

    def test_repository_and_secret_membership_is_preserved(self):
        # Inventory baseline protects this migration from silent removal of scope.
        repos = config.resolved(self.c)
        self.assertEqual(len(repos), 40)
        self.assertEqual(sum(r['state'] == 'active' for r in repos.values()), 29)
        self.assertEqual(sum(len(config.secrets_for(self.c, r)) for r in repos.values() if r['state'] == 'active'), 66)


if __name__ == '__main__':
    unittest.main()
