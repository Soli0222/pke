"""Apply repository Terraform to disposable Keycloak, then exercise OAuth boundaries."""
from pathlib import Path
import os
import shutil
import tempfile

import base64
import hashlib
import html
import http.cookiejar
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

password = uuid.uuid4().hex
base = ''
admin = None

def call(path, data=None, method=None, authenticated=False, form=False):
    headers = {}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + admin
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data, headers, method=method), timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        # Only a synthetic test server is contacted; never print token responses.
        body = json.loads(error.read())
        raise RuntimeError({'status': error.code, 'error': body.get('error'), 'description': body.get('error_description')}) from None

def decode(token):
    encoded = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))

class LocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if '/callback' in newurl or not newurl.startswith(base + '/'):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def open_local(opener, request):
    try:
        return opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        if error.code in (302, 303):
            return error
        raise


repo = Path(__file__).resolve().parents[3]
helm_values = (repo / 'flux/clusters/natsume/apps/keycloak/helmrelease-keycloak.yaml').read_text()
image_match = re.search(r'^    image:\n      repository: ([^\n]+)\n      tag: "([^"]+)"', helm_values, re.M)
assert image_match, 'Cannot locate Keycloak image in Helm values'
image = image_match[1] + ':' + image_match[2]
lockfile = (repo / 'terraform/keycloak/.terraform.lock.hcl').read_text()
provider_version = re.search(r'provider "registry.terraform.io/keycloak/keycloak" \{\s+version\s*=\s*"([^"]+)"', lockfile)[1]
name = 'pke-keycloak-oauth-' + uuid.uuid4().hex[:10]
verifier = 'a' * 64
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')

def authorization(client_id, redirect, scope='openid', prompt=None, method='S256'):
    params = dict(client_id=client_id, redirect_uri=redirect, response_type='code',
                  code_challenge=challenge, code_challenge_method=method)
    if scope is not None:
        params['scope'] = scope
    if prompt:
        params['prompt'] = prompt
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), LocalRedirect())
    try:
        response = open_local(opener, base + '/realms/repro/protocol/openid-connect/auth?' + urllib.parse.urlencode(params))
    except urllib.error.HTTPError as error:
        response = error
    return opener, jar, response

def login(client_id, redirect):
    opener, jar, response = authorization(client_id, redirect)
    body = response.read().decode()
    assert response.code == 200 and 'kc-form-login' in body
    action = html.unescape(re.search(r'<form[^>]+action="([^"]+)"', body)[1])
    for cookie in jar:
        cookie.secure = False
    with open_local(opener, urllib.request.Request(action, urllib.parse.urlencode(dict(username='test-user', password=password)).encode())) as response:
        location = response.headers.get('Location', '')
        body = response.read().decode()
    if not location:
        consent = urllib.parse.urljoin(base, html.unescape(re.search(r'<form[^>]+action="([^"]+)"', body)[1]))
        hidden = dict(re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', body))
        hidden['accept'] = 'Yes'
        with open_local(opener, urllib.request.Request(consent, urllib.parse.urlencode(hidden).encode())) as response:
            location = response.headers.get('Location', '')
    assert location.startswith(redirect + '?')
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)['code'][0]
    tokens = call('/realms/repro/protocol/openid-connect/token', dict(grant_type='authorization_code',
        client_id=client_id, redirect_uri=redirect, code=code, code_verifier=verifier), form=True)
    assert decode(tokens['access_token'])['azp'] == client_id
    print('PASS login, consent, PKCE token exchange: ' + redirect, flush=True)

def rejected_authorization(client_id, uri, scope='openid'):
    _, _, response = authorization(client_id, uri, scope=scope, prompt='none')
    location = response.headers.get('Location', '')
    body = response.read().decode()
    assert response.code == 400 or 'error=invalid_request' in location, (response.code, location, body[:200])
    assert 'code=' not in location and 'kc-form-login' not in body
    print('PASS authorization rejected: ' + uri + '; scope=' + str(scope), flush=True)

try:
    subprocess.run(['docker', 'run', '--rm', '-d', '--name', name, '-p', '127.0.0.1::8080',
        '-e', 'KC_BOOTSTRAP_ADMIN_USERNAME=repro-admin', '-e', 'KC_BOOTSTRAP_ADMIN_PASSWORD=' + password,
        image, 'start-dev', '--features=cimd,resource-indicators'], check=True, stdout=subprocess.DEVNULL)
    port = subprocess.check_output(['docker', 'port', name, '8080/tcp'], text=True).strip().rsplit(':', 1)[1]
    base = 'http://127.0.0.1:' + port
    for attempt in range(120):
        try:
            call('/realms/master/.well-known/openid-configuration')
            break
        except Exception:
            time.sleep(0.5)
    admin = call('/realms/master/protocol/openid-connect/token', dict(grant_type='password', client_id='admin-cli',
        username='repro-admin', password=password), form=True)['access_token']
    call('/admin/realms', dict(realm='repro', enabled=True, sslRequired='none'), authenticated=True)
    call('/admin/realms/repro/users', dict(username='test-user', enabled=True, email='test@example.invalid', emailVerified=True,
        firstName='Test', lastName='User', credentials=[dict(type='password', value=password, temporary=False)]), authenticated=True)
    with tempfile.TemporaryDirectory(prefix='keycloak-local-tf-') as work:
        work = Path(work)
        for filename in ['.terraform.lock.hcl']:
            shutil.copy(repo / 'terraform/keycloak' / filename, work / filename)
        (work / 'fixture.tf.json').write_text(json.dumps({
            'terraform': {'required_providers': {'keycloak': {'source': 'keycloak/keycloak', 'version': provider_version}}},
            'provider': {'keycloak': {'url': base, 'realm': 'master', 'client_id': 'admin-cli', 'username': 'repro-admin', 'password': password}},
            'resource': {'keycloak_realm': {'pke': {'realm': 'repro', 'enabled': True, 'ssl_required': 'none'}},
                'keycloak_realm_default_client_scopes': {'pke': {'realm_id': '${keycloak_realm.pke.id}', 'default_scopes': ['basic', 'profile', 'email']}}}
        }))
        env = {k:v for k,v in os.environ.items() if not k.startswith(('TF_', 'KEYCLOAK_'))}
        for args in [['init', '-backend=false', '-input=false'], ['import', '-input=false', 'keycloak_realm.pke', 'repro'],
                     ['plan', '-input=false', '-out=plan'], ['apply', '-input=false', 'plan']]:
            if args[0] == 'plan':
                for filename in ['dcr.tf', 'mcp.tf']:
                    shutil.copy(repo / 'terraform/keycloak' / filename, work / filename)
            result = subprocess.run(['terraform', *args], cwd=work, env=env, text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError('Isolated Terraform ' + args[0] + ' failed: ' + result.stderr[-3000:])
            print('PASS isolated Terraform ' + args[0], flush=True)
    components = call('/admin/realms/repro/components?type=org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy', authenticated=True)
    trusted = next(c for c in components if c['providerId'] == 'trusted-hosts' and c['subType'] == 'anonymous')
    call('/admin/realms/repro/components/' + trusted['id'], method='DELETE', authenticated=True)
    endpoint = '/realms/repro/clients-registrations/openid-connect'
    def register(uris):
        return call(endpoint, dict(client_name='Synthetic policy test', redirect_uris=uris,
            token_endpoint_auth_method='none', grant_types=['authorization_code', 'refresh_token'], response_types=['code']))

    uris = ['http://localhost:8787/callback', 'http://127.0.0.1:8787/callback', 'http://[::1]:8787/callback',
            'https://grok.com/connectors-oauth-exchange-code/']
    registered = register(uris)
    client = call('/admin/realms/repro/clients?' + urllib.parse.urlencode({'clientId': registered['client_id']}), authenticated=True)[0]
    assert client['attributes']['pkce.code.challenge.method'] == 'S256'
    assert client['consentRequired'] and not client['fullScopeAllowed']
    assert not client['implicitFlowEnabled'] and not client['directAccessGrantsEnabled']
    print('PASS mixed localhost/IP/HTTPS DCR registration and security attributes', flush=True)
    for uri in uris:
        login(registered['client_id'], uri)
    _, _, response = authorization(registered['client_id'], uris[0], method='plain', prompt='none')
    assert response.code == 400 or 'error=invalid_request' in response.headers.get('Location', '')
    print('PASS PKCE plain rejected', flush=True)

    for uri in ['https://new-mcp.example/*', 'com.example.app:/callback', 'https://new-mcp.example/callback#fragment']:
        try:
            register([uri])
        except RuntimeError as error:
            assert error.args[0]['status'] in [400, 403]
            print('PASS registration rejected: ' + uri, flush=True)
        else:
            raise AssertionError('Unexpected registration: ' + uri)

    for uri in ['http://new-mcp.example/callback', 'http://10.0.0.1/callback',
                'http://localhost.evil.example/callback', 'http://localhost@new-mcp.example/callback']:
        client = register([uri])
        for scope in ['openid', None]:
            rejected_authorization(client['client_id'], uri, scope)
    # Registration access token updates must retain the authorization boundary.
    assert registered['registration_client_uri'].startswith(base + '/')
    update_uri = 'http://updated.example/callback'
    request = urllib.request.Request(registered['registration_client_uri'], json.dumps(dict(
        client_id=registered['client_id'], redirect_uris=[update_uri], token_endpoint_auth_method='none',
        grant_types=['authorization_code', 'refresh_token'], response_types=['code'])).encode(),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + registered['registration_access_token']}, method='PUT')
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 200
    rejected_authorization(registered['client_id'], update_uri)

    # An admin-created client also cannot bypass the authorization policy.
    call('/admin/realms/repro/clients', dict(clientId='admin-created', enabled=True, protocol='openid-connect',
        publicClient=True, standardFlowEnabled=True, redirectUris=['http://admin-created.example/callback']), authenticated=True)
    rejected_authorization('admin-created', 'http://admin-created.example/callback')
    # Existing pre-registered HTTPS clients still reach the login form.
    _, _, response = authorization('https://chatgpt.com/oauth/client.json', 'https://chatgpt.com/connector_platform_oauth_redirect')
    assert response.code == 200 and 'kc-form-login' in response.read().decode()
    print('PASS pre-registered ChatGPT authorization entry', flush=True)
    # Only public metadata is fetched. No user credentials or code are sent to Claude.
    for attempt in range(2):
        _, _, response = authorization('https://claude.ai/oauth/mcp-oauth-client-metadata', 'https://claude.ai/api/mcp/auth_callback')
        body = response.read().decode()
        assert response.code == 200 and 'kc-form-login' in body, (response.code, body[:250])
    print('PASS Claude CIMD initial registration and repeated authorization entry', flush=True)
finally:
    subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Removed isolated Keycloak', flush=True)
