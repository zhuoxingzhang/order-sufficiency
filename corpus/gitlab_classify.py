"""GitLab cross-database write templates: hand classification.

Usage: python -I gitlab_classify.py <gitlab_schema.json> <out_dir>

Source: gitlab-org/gitlab commit 9d4cbde6 (2026-10-15).  GitLab stores its tables in the databases
main, ci and sec (plus shared tables) and keeps no transaction across them.  A template is one code
location at which a logical write touches rows of two databases:
  G = one of the 15 transactions that GitLab exempts from its guard against cross-database writes
      (PreventCrossDatabaseModification); locations are relative to the repository root;
  C = the deletion of a parent whose loose foreign keys cross databases: the parent row is deleted in
      its database, and a cleanup job deletes or nullifies the child rows in the other database
      (config/gitlab_loose_foreign_keys.yml); one template per parent.
Classes: order = only order needed; atomic = needs atomic visibility; serial = needs
serializability; none = no constraint links the writes of the two databases.
"""
import json
import os
import sys
from collections import Counter

schema_path, out_dir = sys.argv[1], sys.argv[2]

# (id, location, what the template does, rows written, constraint between them, operation, class)
EXEMPT = [
    ('G01', 'app/models/concerns/with_uploads.rb', 'Destroy an owner (project, group, ...) and delete its upload records',
     'main: owner row; database of uploads: uploads', 'uploads(model_type, model_id) references the owner', 'delete', 'order'),
    ('G02', 'app/models/namespace/traversal_hierarchy.rb', 'Create a user and synchronize the traversal_ids of its personal namespace',
     'users; namespaces.traversal_ids',
     'traversal_ids is the sequence of ancestor ids along parent_id plus the own id; the namespace references the user', 'insert', 'order'),
    ('G03', 'app/services/groups/create_service.rb', 'Create a group and its route',
     'namespaces; routes, redirect_routes',
     'routes.source_id references the group; routes.path is the path of the parent group plus the path of the group', 'insert', 'order'),
    ('G04', 'app/services/members/creator_service.rb', 'Create a user and its membership on invitation',
     'users, user_details, user_preferences, emails, identities; members',
     'user_details, user_preferences, emails, identities and members reference users', 'insert', 'order'),
    ('G05', 'app/services/organizations/create_service.rb', 'Create an organization and its owner',
     'organizations; organization_users and others', 'organization_users references organizations and users', 'insert', 'order'),
    ('G06', 'app/services/projects/create_service.rb', 'Create a project and its route',
     'projects; routes, redirect_routes',
     'routes.source_id references the project; routes.path is the path of the namespace plus the path of the project', 'insert', 'order'),
    ('G07', 'app/uploaders/records_uploads.rb', 'Delete the old upload record and write the new one',
     'uploads', 'uploads references the owner; at most one record per path', 'insert/delete', 'order'),
    ('G08', 'ee/app/models/ee/upload.rb (first location)', 'Write the verification state of an upload',
     'uploads; upload_states', 'upload_states.upload_id references uploads', 'insert', 'order'),
    ('G09', 'ee/app/models/ee/upload.rb (second location)', 'Write the verification state of a partitioned upload table',
     'partitioned upload table; its state table', 'the state row references the upload row', 'insert', 'order'),
    ('G10', 'ee/app/services/security/orchestration/create_bot_service.rb', 'Create a security policy bot user and add it as a project member',
     'users; members, notification_settings, events, projects',
     'members, notification_settings and events reference users and the project', 'insert', 'order'),
    ('G11', 'ee/lib/gitlab/scim/group/deprovisioning_service.rb', 'Deactivate a SCIM identity and remove its group access',
     'identities; members', 'the membership depends on a valid SCIM identity', 'delete', 'order'),
    ('G12', 'app/models/member.rb (update_two_factor_requirement)', 'Recompute the two-factor requirement of a user when its memberships change',
     'members; users, user_details, user_preferences',
     'users.require_two_factor_authentication_from_group is the disjunction of the two-factor requirements of the groups of the user; '
     'the grace period is their minimum', 'modify', 'atomic'),
    ('G13', 'app/services/groups/transfer_service.rb', 'Transfer a group, rewriting the full route paths of the group and all its descendants',
     'namespaces.parent_id; routes, redirect_routes', 'the routes.path of every descendant is the concatenation of the paths of its ancestors',
     'modify', 'atomic'),
    ('G14', 'app/services/projects/transfer_service.rb', 'Transfer a project, rewriting its full route path',
     'projects.namespace_id; routes, redirect_routes', 'routes.path is the path of the namespace plus the path of the project',
     'modify', 'atomic'),
    ('G15', 'lib/api/notification_settings.rb', 'Modify the notification settings and the notification email together',
     'notification_settings; users, user_details', 'no constraint relates the two sides', 'modify', 'none'),
]

T = [dict(id=i, mech='exemption', what=w, writes=wr, constraint=c, op=op, cls=cls, locs=[loc])
     for i, loc, w, wr, c, op, cls in EXEMPT]

d = json.load(open(schema_path, encoding='utf-8'))
tabs, cross = d['tables'], {}
for f in d['lfk']:
    c, p = f['child'], f['table']
    if c in tabs and p in tabs and tabs[c]['db'] != tabs[p]['db'] and 'shared' not in (tabs[c]['db'], tabs[p]['db']):
        cross.setdefault(p, []).append(f"{c}.{f.get('column')} ({tabs[c]['db']}, {f.get('on_delete')})")
for i, p in enumerate(sorted(cross), 1):
    T.append(dict(id=f'C{i:02d}', mech='loose FK cascade',
                  what=f'Delete a row of {p} ({tabs[p]["db"]} database) and delete or nullify its child rows '
                       f'in other databases through loose foreign keys',
                  writes=f'{tabs[p]["db"]}: {p}; other databases: ' + ', '.join(cross[p]),
                  constraint=f'the foreign-key columns of the child rows reference {p}', op='delete', cls='order',
                  locs=['config/gitlab_loose_foreign_keys.yml']))
assert len(EXEMPT) == 15 and len(cross) == 17, (len(EXEMPT), len(cross))

LABEL = {'order': 'need only order', 'atomic': 'need atomic visibility', 'serial': 'need serializability', 'none': 'unconstrained'}
c = Counter(x['cls'] for x in T)
print('templates', len(T), {LABEL[k]: c[k] for k in LABEL}, f"order share {c['order'] / len(T):.1%}")
for m in ('exemption', 'loose FK cascade'):
    cm = Counter(x['cls'] for x in T if x['mech'] == m)
    print(' ', m, sum(cm.values()), {LABEL[k]: cm[k] for k in LABEL})
print('operation x class', dict(Counter((x['op'], x['cls']) for x in T)))
os.makedirs(out_dir, exist_ok=True)
json.dump({'templates': T}, open(os.path.join(out_dir, 'gitlab_templates.json'), 'w', encoding='utf-8'), indent=1)
print('wrote', out_dir)
