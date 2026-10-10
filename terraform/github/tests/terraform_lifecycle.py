#!/usr/bin/env python3
"""Exercise migration and lifecycle with actual Terraform and mocked providers.

Only temporary local state and fixture secrets are used. No credentials or
production backend configuration are loaded, and no GitHub APIs are called.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import ROOT, address, load, render, resolved
from manage import inspect_plan


def execute(root, *args):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('TF_', 'AWS_', 'GITHUB_', 'OP_'))}
    result = subprocess.run(['terraform', f'-chdir={root}', *args], env=env,
                            capture_output=True, text=True)
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def main():
    c = load()
    locked = dict(re.findall(r'provider "registry.terraform.io/([^"]+)" \{\s+version\s+= "([^"]+)"',
                             (ROOT / '.terraform.lock.hcl').read_text()))
    requirement = {'terraform': {'required_providers': {
        source.split('/')[-1]: {'source': source, 'version': locked[source]}
        for source in ('integrations/github', 'hashicorp/external')
    }}}
    with tempfile.TemporaryDirectory(prefix='pke-github-tests-') as directory:
        root = Path(directory)
        (root / 'main.tf.json').write_text(json.dumps(requirement))
        shutil.copy(ROOT / '.terraform.lock.hcl', root / '.terraform.lock.hcl')
        # Reuse installed binaries if available, but never backend metadata/state.
        if (ROOT / '.terraform/providers').exists():
            (root / '.terraform').mkdir()
            (root / '.terraform/providers').symlink_to(ROOT / '.terraform/providers', target_is_directory=True)
        tests = root / 'tests'
        tests.mkdir()
        legacy = root / 'legacy'
        legacy.mkdir()
        active = {key: value for key, value in resolved(c).items() if value['state'] == 'active'}
        archived = {key: value for key, value in resolved(c).items() if value['state'] == 'archived'}
        active['private'] = {'name': 'private', 'default_branch': 'main'}
        legacy_config = copy.deepcopy(requirement)
        legacy_config.update({
            'resource': {
                'github_repository': {
                    'repositories': {'for_each': active, 'name': '${each.value.name}', 'archive_on_destroy': False,
                                     'lifecycle': {'prevent_destroy': True}},
                    'archived': {'for_each': archived, 'name': '${each.value.name}', 'archived': True,
                                 'lifecycle': {'prevent_destroy': True}},
                },
                'github_branch_default': {'repositories': {
                    'for_each': active, 'repository': '${github_repository.repositories[each.key].name}', 'branch': '${each.value.default_branch}'}},
                'github_actions_secret': {'repository': {
                    'for_each': {'pke/KEY': 'pke'}, 'repository': '${github_repository.repositories[each.value].name}',
                    'secret_name': 'KEY', 'value': '${data.external.onepassword_actions_secrets.result.fixture}'}},
            },
            'data': {'external': {'onepassword_actions_secrets': {'program': ['never-executed']}}},
            'output': {'ids': {'value': '${merge({for k, v in github_repository.repositories: k => v.id}, {for k, v in github_repository.archived: k => v.id})}'}},
        })
        (legacy / 'main.tf.json').write_text(json.dumps(legacy_config))

        phases = {}
        phases['migrate'] = copy.deepcopy(c)
        phases['steady'] = copy.deepcopy(c)
        phases['archive'] = copy.deepcopy(c)
        phases['archive']['repositories']['pke'].update(state='archived', description='ignored-while-archived')
        phases['restore'] = copy.deepcopy(c)
        phases['rename'] = copy.deepcopy(c)
        phases['rename']['repositories']['pke']['name'] = 'pke-renamed'
        phases['delete'] = copy.deepcopy(phases['rename'])
        del phases['delete']['repositories']['pke']
        phases['delete']['retired']['pke'] = {'name': 'pke-renamed', 'action': 'delete'}
        phases['forget'] = copy.deepcopy(phases['delete'])
        del phases['forget']['repositories']['sui']
        phases['forget']['retired']['sui'] = {'name': 'sui', 'action': 'forget'}
        phases['cleanup'] = copy.deepcopy(phases['forget'])
        for key, repo in phases['cleanup']['repositories'].items():
            phases['cleanup']['retired'][key] = {'name': repo.get('name', key), 'action': 'delete'}
        phases['cleanup']['repositories'] = {}

        for name, inventory in phases.items():
            module = root / name
            module.mkdir()
            content = b'mocked-inventory'
            (module / 'repositories.yaml').write_bytes(content)
            generated = json.loads(render(inventory, content))
            generated.update(copy.deepcopy(requirement))
            generated['output']['ids'] = {'value': {key: '${' + address(key) + '.id}' for key in inventory['repositories']}}
            (module / 'main.tf.json').write_text(json.dumps(generated))
            shutil.copy(ROOT / 'migrations.tf.json', module / 'migrations.tf.json')

        runs = '''mock_provider "github" {}
mock_provider "external" {
  mock_data "external" {
    defaults = { result = { fixture = "test-fixture-only" } }
  }
}
run "legacy" {
  state_key = "lifecycle"
  command = apply
  module { source = "./legacy" }
}
'''
        for name in phases:
            runs += f'run "{name}_plan" {{\n  state_key = "lifecycle"\n  command = plan\n  module {{ source = "./{name}" }}\n}}\n'
            runs += f'run "{name}" {{\n  state_key = "lifecycle"\n  command = apply\n  module {{ source = "./{name}" }}\n'
            if name not in ('delete', 'forget', 'cleanup'):
                runs += '''  assert {
    condition = output.ids["pke"] == run.legacy.ids["pke"]
    error_message = "pke identity changed during migration/archive/restore/rename"
  }
'''
            if name == 'migrate':
                runs += '''  assert {
    condition = alltrue([for key, id in output.ids : id == run.legacy.ids[key]])
    error_message = "migration replaced an existing repository"
  }
'''
            if name == 'archive':
                runs += '''  assert {
    condition = github_repository.repo_pke.archived && github_repository.repo_pke.description == "Polestar Kubernetes Engine"
    error_message = "archive must preserve settings while changing only archived"
  }
'''
            if name == 'restore':
                runs += '''  assert {
    condition = !github_repository.repo_pke.archived
    error_message = "repository was not unarchived"
  }
'''
            if name == 'rename':
                runs += '''  assert {
    condition = github_repository.repo_pke.name == "pke-renamed"
    error_message = "repository name did not change"
  }
'''
            runs += '}\n'
        (tests / 'lifecycle.tftest.hcl').write_text(runs)
        execute(root, 'init', '-backend=false', '-input=false')
        output = execute(root, 'test', '-json', '-verbose')
        states = {}
        plans = {}
        for line in output.splitlines():
            event = json.loads(line)
            if event.get('type') in ('test_run', 'test_summary'):
                print(event.get('@message', ''))
            if event.get('type') == 'test_plan':
                plans[event.get('@testrun')] = event['test_plan']
            if event.get('type') == 'test_state':
                states[event.get('@testrun')] = event['test_state']
        # Validate Terraform's resulting state, beyond output expressions.
        for phase in phases:
            state = states.get(phase)
            if state is None:
                raise AssertionError(f'no verbose state for {phase}')
            resources = state.get('root_module', {}).get('resources', [])
            if any(r['type'] in ('external', 'github_actions_secret') for r in resources):
                raise AssertionError(f'legacy secret state retained in {phase}')
            names = {r['values']['name'] for r in resources if r['type'] == 'github_repository'}
            expected = {r.get('name', key) for key, r in phases[phase]['repositories'].items()}
            if names != expected:
                raise AssertionError(f'{phase}: missing={expected - names}, extra={names - expected}')
        for phase in phases:
            if 'resource_changes' not in plans[phase + '_plan']:
                raise AssertionError('Terraform verbose plan is missing resource_changes')
            inspect_plan(plans[phase + '_plan'], phases[phase])
        for item in plans['steady_plan']['resource_changes']:
            if item['change']['actions'] != ['no-op']:
                raise AssertionError('migration did not converge to a no-op plan')
        print('Verified: all repository IDs preserved; archive/restore/rename; explicit delete/forget; plaintext state removed.')


if __name__ == '__main__':
    main()
