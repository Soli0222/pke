#!/usr/bin/env python3
"""Git Syncに置くJSONの構造とPKE内の参照を、Grafanaへの接続なしで検証する。"""
import argparse
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).resolve().parents[1] / 'grafana/schemas'
BUILTINS = {'grafana': 'grafana', '-- Grafana --': 'grafana',
            '__expr__': '__expr__', '-100': '__expr__', '-- Mixed --': 'mixed',
            '-- Dashboard --': 'datasource'}
VARIABLE = re.compile(r'^\$(?:([A-Za-z_][\w]*)|\{([A-Za-z_][\w]*)\})$')


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('non-finite JSON number')


def load(path):
    return json.loads(path.read_text(), object_pairs_hook=reject_duplicates,
                      parse_constant=reject_constant)


def walk(value, path='$'):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f'{path}.{key}')
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, f'{path}[{i}]')


def schema_errors(data, name):
    schema = load(SCHEMAS / name)
    Draft202012Validator.check_schema(schema)
    return [f'{"/".join(map(str, e.absolute_path)) or "$"}: schema {e.validator}'
            for e in Draft202012Validator(schema).iter_errors(data)]


def content_errors(resource, datasources):
    errors = []
    spec = resource['spec']
    v2 = resource['apiVersion'].endswith('/v2')
    variables = [v['spec'] | {'type': v['kind']} for v in spec['variables']] if v2 else spec.get('templating', {}).get('list', [])
    dsvars = {}
    names = set()
    for var in variables:
        name = var.get('name')
        if not isinstance(name, str) or not name or name in names:
            errors.append('variable name is missing or duplicated')
            continue
        names.add(name)
        if var.get('type') in ('datasource', 'DatasourceVariable'):
            plugin = var.get('pluginId') if v2 else var.get('query')
            if plugin not in {d['type'] for d in datasources.values()}:
                errors.append(f'variable {name}: unknown datasource plugin')
            dsvars[name] = plugin
            # A saved datasource selection must also point to a known UID.
            selection = var.get('current') or {}
            if not isinstance(selection, dict):
                errors.append(f'variable {name}: invalid datasource selection')
                continue
            current = selection.get('value')
            for uid in current if isinstance(current, list) else [current]:
                if uid and (not isinstance(uid, str) or uid not in datasources or datasources[uid]['type'] != plugin):
                    errors.append(f'variable {name}: unknown datasource selection')

    def check_source(ref, at, plugin=None):
        if isinstance(ref, dict):
            plugin = ref.get('type', plugin)
            uid = ref.get('uid', ref.get('name'))
        else:
            uid = ref
        if not isinstance(uid, str) or not uid:
            errors.append(f'{at}: explicit datasource UID is required')
            return
        match = VARIABLE.fullmatch(uid)
        if match:
            name = match.group(1) or match.group(2)
            expected = dsvars.get(name)
            if not expected:
                errors.append(f'{at}: undeclared datasource variable')
                return
        elif uid in BUILTINS:
            expected = BUILTINS[uid]
        elif uid in datasources:
            expected = datasources[uid]['type']
        else:
            errors.append(f'{at}: unknown datasource UID')
            return
        allowed_types = {'grafana', 'datasource'} if expected == 'grafana' else {expected}
        if plugin and plugin not in allowed_types:
            errors.append(f'{at}: datasource plugin does not match UID')

    ids = set()
    for at, value in walk(spec):
        if isinstance(value, str) and re.search(r'\$\{?DS_[A-Z0-9_]+', value):
            errors.append(f'{at}: unresolved import placeholder')
        if not isinstance(value, dict):
            continue
        if '__inputs' in value:
            errors.append(f'{at}: import __inputs must be resolved and removed')
        if 'libraryPanel' in value or value.get('kind') == 'LibraryPanel':
            errors.append(f'{at}: library panels are not managed by Git Sync')
        if 'datasource' in value:
            check_source(value['datasource'], at, value.get('group'))
        if value.get('builtIn') == 1 and value.get('type') == 'dashboard':
            if value.get('datasource', {}).get('uid') not in ('grafana', '-- Grafana --'):
                errors.append(f'{at}: built-in annotations must use Grafana datasource')
        if value.get('kind') == 'DataQuery' and 'datasource' not in value:
            errors.append(f'{at}: DataQuery requires explicit datasource')
        if value.get('kind') == 'ElementReference' and value.get('name') not in spec.get('elements', {}):
            errors.append(f'{at}: unknown layout element reference')
        # v1 nested row panels and v2 element panels both have IDs.
        panel = (not v2 and 'id' in value and 'type' in value) or (v2 and 'id' in value and 'vizConfig' in value)
        if panel:
            panel_id = value['id']
            if panel_id in ids:
                errors.append(f'{at}: duplicate panel ID')
            ids.add(panel_id)
            if not v2 and value.get('targets') and 'datasource' not in value:
                if any('datasource' not in target for target in value['targets']):
                    errors.append(f'{at}: queries require explicit datasource')
    for field in ('id', 'uid', 'version', 'folderUID'):
        if field in spec:
            errors.append(f'spec.{field}: server identity/folder belongs outside the saved spec')
    return errors


def validate(root):
    errors = []
    try:
        catalog = load(root / 'catalog.json')
        problems = schema_errors(catalog, 'catalog.schema.json')
        if problems:
            return [f'catalog.json: {e}' for e in problems], 0
    except (OSError, ValueError):
        return ['catalog.json: invalid or unreadable JSON'], 0
    seen = {}
    seen_folders = {}
    for path in sorted((root / 'dashboards').rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(f'{relative}: symbolic links are not allowed')
            continue
        if path.is_dir():
            continue
        if path.name == '.gitkeep':
            continue
        if path.suffix != '.json':
            errors.append(f'{relative}: only regular dashboard JSON files are allowed')
            continue
        try:
            resource = load(path)
        except (OSError, ValueError):
            errors.append(f'{relative}: invalid JSON (including duplicate keys/NaN)')
            continue
        is_folder = path.name == '_folder.json'
        problems = schema_errors(resource, 'folder.schema.json' if is_folder else 'dashboard.schema.json')
        if problems:
            errors.extend(f'{relative}: {e}' for e in problems)
            continue
        uid = resource['metadata']['name']
        if is_folder:
            if uid in seen_folders:
                errors.append(f'{relative}: duplicate folder UID')
            seen_folders[uid] = relative
            entry = catalog.get('folders', {}).get(uid)
            if entry is None or entry['path'] != relative or entry['title'] != resource['spec']['title']:
                errors.append(f'{relative}: folder catalog UID/path/title does not match')
            continue
        for parent in path.parents:
            if parent == root / 'dashboards':
                break
            if not (parent / '_folder.json').is_file():
                errors.append(f'{relative}: parent folder is missing _folder.json')
        if uid in seen:
            errors.append(f'{relative}: duplicate dashboard UID ({seen[uid]})')
        seen[uid] = relative
        entry = catalog['dashboards'].get(uid)
        if entry is None or entry['path'] != relative or entry['title'] != resource['spec']['title']:
            errors.append(f'{relative}: catalog UID/path/title does not match')
        else:
            origin = entry['origin']
            if origin['kind'] == 'upstream':
                if not origin['url'].startswith('https://') or origin['revision'].lower() in ('main', 'master', 'latest', 'unknown'):
                    errors.append(f'{relative}: upstream source must have HTTPS URL and pinned revision')
        errors.extend(f'{relative}: {e}' for e in content_errors(resource, catalog['datasources']))
    for uid in catalog['dashboards'].keys() - seen.keys():
        errors.append(f'catalog.json: dashboard {uid} is missing')
    for uid in catalog.get('folders', {}).keys() - seen_folders.keys():
        errors.append(f'catalog.json: folder {uid} is missing')
    if not (root / 'dashboards').is_dir():
        errors.append('dashboards directory is missing')
    return errors, len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'grafana')
    args = parser.parse_args()
    errors, count = validate(args.root)
    for error in errors:
        print(f'ERROR: {error}', file=sys.stderr)
    if errors:
        return 1
    print(f'Validated {count} dashboards (structure, identity, datasource references, provenance).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
