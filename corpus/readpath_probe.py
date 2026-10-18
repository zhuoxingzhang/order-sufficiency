"""Read-path audit (Section 6.2), step 1: find read paths that dereference a cross-silo foreign key from the child side.

For every deletion template of Sentry that needs only order, the required write order deletes
the children before the parent.  A reader that reads a child row and then fetches its parent
across silos (child -> parent) reads the two sides in the commit order, so no write order
protects it.  This probe lists candidate sites: a call of the parent's RPC service whose
arguments (on the same line or within WINDOW lines) use the child's foreign-key column, in a
file that names the child model.  Every reported site is then checked by hand.

usage: python -I readpath_probe.py SENTRY_SRC MODELS_JSON OUT_JSON
  SENTRY_SRC   .../src/sentry of the Sentry checkout (commit 13ccaecc)
  MODELS_JSON  out/sentry_models.json (hybrid-cloud foreign keys per model)
"""
import json, os, re, sys

SRC, MODELS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
WINDOW = 4

# parent model -> RPC calls that fetch it by id (from the service call census of the checkout)
SERVICES = {
    'User': r'user_service\.(get_user|get_many_by_id|get_many|serialize_many|get_many_ids)\(',
    'Organization': r'organization_service\.(get_organization_by_id|get|serialize_organization|get_org_by_id)\(',
    'Project': r'project_service\.(get_by_id|get_many_by_organizations|serialize_many|get_by_slug)\(',
    'Team': r'(team_service|organization_service)\.(get_team|get_teams|get_team_by_id)\(',
    'Integration': r'integration_service\.(get_integration|get_integrations|organization_context|organization_contexts)\(',
    'OrganizationIntegration': r'integration_service\.(get_organization_integration|get_organization_integrations|organization_context|organization_contexts)\(',
    'SentryApp': r'app_service\.(get_sentry_app_by_id|get_sentry_apps|get_many|find_service_hook_sentry_app|get_sentry_app_by_slug)\(',
    'SentryAppInstallation': r'app_service\.(get_many|get_installation_by_id|get_installation|find_installation_by_proxy_user)\(',
    'ApiApplication': r'(app_service|api_application_service|apiapplication_service)\.\w+\(',
    'AuthProvider': r'auth_service\.(get_auth_provider|get_auth_provider_by_id|get_org_auth_config)\(',
    'AuthIdentity': r'(auth_service|auth_identity_service|usersocialauth_service)\.\w+\(',
    'ApiKey': r'(api_key_service|apikey_service)\.\w+\(',
    'ApiToken': r'(api_token_service|apitoken_service)\.\w+\(',
    'OrgAuthToken': r'(orgauthtoken_service|org_auth_token_service)\.\w+\(',
}
# deletion templates (out/templates.csv) -> parent model and the silo of the parent
TEMPLATES = {
    'R04': 'Organization', 'R13': 'Team', 'R14': 'Project', 'R18': 'User', 'D06': 'User',
    'R21': 'AuthProvider', 'R24': 'AuthIdentity', 'R27': 'ApiKey', 'R30': 'ApiToken', 'R33': 'OrgAuthToken',
    'R35': 'SentryApp', 'R36': 'SentryAppInstallation', 'R37': 'Integration', 'R38': 'OrganizationIntegration',
    'R39': 'ApiApplication',
}
TARGET = {'AUTH_USER_MODEL': 'User', 'sentry.User': 'User'}

models = json.load(open(MODELS, encoding='utf-8'))['models']
edges = []  # (parent, child model, column, child silo)
for m in models:
    for r in m['refs']:
        if r['kind'] != 'hcfk':
            continue
        p = TARGET.get(r['target'], r['target'].split('.')[-1])
        if p in SERVICES:
            edges.append((p, m['name'], r['col'], m['silo']))

files = []
for root, dirs, fs in os.walk(SRC):
    dirs[:] = [x for x in dirs if x not in ('tests', 'testutils', 'migrations', 'fixtures')]
    for f in fs:
        if f.endswith('.py') and not f.startswith('test_'):
            files.append(os.path.join(root, f))
text = {}
for f in files:
    try:
        text[f] = open(f, encoding='utf-8').read().split('\n')
    except UnicodeDecodeError:
        pass

res = []
for p, child, col, silo in edges:
    svc = re.compile(SERVICES[p])
    colre = re.compile(r'\.' + re.escape(col) + r'\b|\b' + re.escape(col) + r'\s*=')
    namere = re.compile(r'\b' + re.escape(child) + r'\b')
    hits = []
    for f, lines in text.items():
        body = '\n'.join(lines)
        if not namere.search(body) or not svc.search(body):
            continue
        for i, line in enumerate(lines):
            if svc.search(line):
                lo, hi = max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)
                if any(colre.search(lines[j]) for j in range(lo, hi)):
                    hits.append({'file': os.path.relpath(f, SRC), 'line': i + 1, 'text': line.strip()[:160]})
    res.append({'parent': p, 'child': child, 'col': col, 'child_silo': silo, 'n': len(hits), 'hits': hits})

json.dump({'templates': TEMPLATES, 'edges': res}, open(OUT, 'w'), indent=1)
for r in sorted(res, key=lambda r: (r['parent'], -r['n'])):
    print(f"{r['parent']:24s} {r['child']}.{r['col']} [{r['child_silo']}]: {r['n']}")
