"""Sentry cross-silo write templates: hand classification + summary.

Usage: python -I sentry_classify.py <sentry_models.json> <out_dir>

Source: getsentry/sentry 13ccaecc8db5a4ecb09e4839eedebb168e1cb272 (2026-10-16).
Every location below is relative to src/sentry/ of that commit.

Unit of counting: one template is one code location at which a logical write in one silo induces a
data write in the other silo.
  R = asynchronous replication through an outbox (per source model and operation, or per category),
      including tombstone-driven HybridCloudForeignKey (HCFK) cascades of the deleted parent;
  D = synchronous RPC write into the other silo inside one request or task that also writes locally.
Remote writes that touch only one silo's data, cache invalidation, migrations, relocation/backup/CLI
tooling and closed-source producers are listed in EXCLUDED and are not templates.

Classes: order = need only order; atomic = need atomic visibility; serial = need serializability;
none = unconstrained, no constraint links the cross-silo writes (any order is fine).
Dependency kinds: ind = plain FK / HCFK chain (unary, key-referencing); kcopy = FK plus copied columns
(class K shape); copyout = copy outside class K (conditional IND, non-primary key or two-hop path);
oneway = one-way conditional IND or denial constraint; derived = value derived from rows of the other
silo with equality semantics; nodep = none.
Tier: S = per login / token refresh / periodic per identity; A = per admin or member action;
L = lifecycle (create/delete org, SSO, integrations, accounts).

The script also reports a schema-level dependency census of <sentry_models.json> and writes
sentry_templates.json and sentry_templates_table.md to <out_dir>.
"""
import json
import os
import sys
from collections import Counter, defaultdict

models_path, out_dir = sys.argv[1], sys.argv[2]

T = []


def t(tid, mech, direction, what, writes, constraint, dep, cls, order, impl, tier, locs):
    T.append(dict(id=tid, mech=mech, dir=direction, what=what, writes=writes, constraint=constraint,
                  dep=dep, cls=cls, order=order, impl=impl, tier=tier, locs=locs))


C2X, X2C = 'cell->control', 'control->cell'
ASYNC_PARENT_FIRST = 'source commits first; target written asynchronously via outbox'
ASYNC_REVERSED = 'parent deleted first; child rows deleted or set null asynchronously via tombstones (reverse of the required order)'

# ---------------------------------------------------------------- R: outbox replication / cascades
t('R01', 'R', 'control->cell->control', 'Create Organization (provisioning)',
  'control OrganizationSlugReservation; cell Organization, owner OrganizationMember and OrganizationMemberTeam; control OrganizationMapping',
  'Reservation.organization_id and Mapping both reference Organization (insert); member references User', 'ind', 'order',
  'Organization before Reservation and Mapping',
  'writes Reservation first (claims the slug through a unique index in the single control database), deletes it on failure', 'L',
  ['services/organization/provisioning.py:48', 'services/organization/provisioning.py:147',
   'hybridcloud/services/control_organization_provisioning/impl.py:131',
   'hybridcloud/services/cell_organization_provisioning/impl.py:127', 'models/organization.py:275'])
t('R02', 'R', C2X, 'Modify Organization name, status or one of 9 flags',
  'cell Organization; control OrganizationMapping',
  'Mapping[organization_id, name, status, flags] ⊆ Organization[id, …], copied columns mutable', 'kcopy', 'atomic',
  'no valid order (modify-modify 2-cycle)', ASYNC_PARENT_FIRST, 'A',
  ['models/organization.py:275', 'hybridcloud/services/organization_mapping/serial.py:12'])
t('R03', 'R', 'cell->control->cell->control', 'Modify Organization slug',
  'control temporary-alias Reservation; cell Organization.slug; control PRIMARY Reservation.slug and Mapping.slug',
  'PRIMARY Reservation.slug and Mapping.slug are both copies of Organization.slug; global uniqueness enforced by a unique index in the single control database',
  'kcopy', 'atomic', 'no valid order', 'temporary alias claims the slug first, then the cell is modified, finally the control copies', 'L',
  ['services/organization/provisioning.py:80', 'services/organization/provisioning.py:195',
   'services/organization/provisioning.py:257', 'hybridcloud/services/control_organization_provisioning/impl.py:179',
   'hybridcloud/services/cell_organization_provisioning/impl.py:224', 'core/endpoints/organization_details.py:761'])
t('R04', 'R', C2X, 'Delete Organization',
  'cell Organization; control OrganizationMapping, organization-level NotificationSetting*, 19 child tables cascaded via HCFK',
  '19 HCFKs (all CASCADE) and Mapping reference Organization', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['models/organization.py:288'])
t('R05', 'R', C2X, 'Create OrganizationMember (invite, join)',
  'cell OrganizationMember; control OrganizationMemberMapping', 'Mapping is a copy of Member (insert)', 'kcopy', 'order',
  'Member before Mapping', ASYNC_PARENT_FIRST, 'A',
  ['models/organizationmember.py:663', 'hybridcloud/services/organizationmember_mapping/impl.py:24'])
t('R06', 'R', C2X, 'Modify OrganizationMember role, user_id, email, invite_status or inviter_id (change role, accept invitation, approve request)',
  'cell OrganizationMember; control OrganizationMemberMapping',
  'Mapping[organizationmember_id, role, user_id, email, …] ⊆ Member[id, …], copied columns mutable', 'kcopy', 'atomic',
  'no valid order', ASYNC_PARENT_FIRST, 'A',
  ['models/organizationmember.py:663', 'hybridcloud/services/organizationmember_mapping/impl.py:24'])
t('R07', 'R', C2X, 'Delete OrganizationMember',
  "cell OrganizationMember; control OrganizationMemberMapping, the user's Identity rows on this organization's integrations",
  'Mapping and Identity depend on the membership', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'A',
  ['models/organizationmember.py:649', 'hybridcloud/services/organizationmember_mapping/impl.py:103'])
t('R08', 'R', C2X, 'Create ProjectKey', 'cell ProjectKey; control ProjectKeyMapping',
  'Mapping[project_key_id, public_key] ⊆ ProjectKey[id, public_key] (insert)', 'kcopy', 'order', 'ProjectKey before Mapping',
  ASYNC_PARENT_FIRST, 'A', ['models/projectkey.py:209', 'hybridcloud/services/replica/impl.py:295'])
t('R09', 'R', C2X, 'Delete ProjectKey', 'cell ProjectKey; control ProjectKeyMapping', 'as above (delete)', 'kcopy', 'order',
  'Mapping before ProjectKey', ASYNC_REVERSED, 'A', ['models/projectkey.py:198', 'hybridcloud/services/replica/impl.py:315'])
t('R10', 'R', C2X, 'Create OrganizationAvatar', 'cell OrganizationAvatar; control OrganizationAvatarReplica',
  'Replica[organization_id, avatar_type, avatar_ident] ⊆ Avatar[organization_id, avatar_type, ident] (insert)', 'kcopy', 'order',
  'Avatar before Replica', ASYNC_PARENT_FIRST, 'L',
  ['models/avatars/organization_avatar.py:45', 'hybridcloud/services/replica/impl.py:320'])
t('R11', 'R', C2X, 'Replace OrganizationAvatar (ident regenerated)', 'cell OrganizationAvatar; control OrganizationAvatarReplica',
  'as above, copied columns mutable', 'kcopy', 'atomic', 'no valid order', ASYNC_PARENT_FIRST, 'L',
  ['models/avatars/organization_avatar.py:45', 'models/avatars/base.py:163', 'hybridcloud/services/replica/impl.py:320'])
t('R12', 'R', C2X, 'Delete OrganizationAvatar', 'cell OrganizationAvatar; control OrganizationAvatarReplica',
  'as above (delete)', 'kcopy', 'order', 'Replica before Avatar', ASYNC_REVERSED, 'L',
  ['models/avatars/organization_avatar.py:55', 'hybridcloud/services/replica/impl.py:332'])
t('R13', 'R', C2X, 'Delete Team', 'cell Team; control NotificationSettingOption, NotificationSettingProvider (cascade on team_id)',
  '2 HCFKs reference Team', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['models/team.py:112', 'notifications/models/notificationsettingoption.py:15',
   'notifications/models/notificationsettingprovider.py:15'])
t('R14', 'R', C2X, 'Delete Project',
  'cell Project; control project-level NotificationSetting*, UserOption(project_id) cascaded, OrgAuthToken.project_last_used_id set null',
  '2 HCFKs and notification settings reference Project', 'ind', 'order', 'children before parent',
  'deletes control notification settings synchronously first, then Project; HCFK child rows handled asynchronously', 'L',
  ['models/project.py:892', 'users/models/user_option.py:198', 'models/orgauthtoken.py:55'])
t('R15', 'R', C2X, "Delete a team's ExternalActor", 'cell ExternalActor; control NotificationSettingProvider(team, provider)',
  'one-way: team notification setting for the provider => team is linked to the provider', 'oneway', 'order', 'setting deleted first',
  'deletes control setting synchronously first, then ExternalActor', 'L',
  ['integrations/models/external_actor.py:72', 'integrations/models/external_actor.py:84'])
t('R16', 'R', C2X, 'Audited operation in a cell writes an audit log entry',
  'cell audited business rows; control AuditLogEntry', 'AuditLogEntry.organization_id references Organization (insert)', 'ind', 'order',
  'business rows before log entry', ASYNC_PARENT_FIRST, 'A',
  ['audit_log/services/log/impl.py:107', 'audit_log/services/log/impl.py:112', 'receivers/outbox/cell.py:184'])
t('R17', 'R', X2C, 'Modify User email or is_active', 'control User; OrganizationMember.user_email, user_is_active in every cell',
  'Member[user_id, user_email, user_is_active] ⊆ User[id, email, is_active], copied columns mutable', 'kcopy', 'atomic',
  'no valid order', ASYNC_PARENT_FIRST, 'A',
  ['users/models/user.py:669', 'organizations/services/organization/impl.py:643', 'models/organizationmember.py:237',
   'users/api/endpoints/user_emails.py:186'])
t('R18', 'R', X2C, 'Delete User', 'control User; 71 HCFK columns across all cells (30 CASCADE, 41 SET_NULL)',
  '71 HCFKs reference User', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['users/models/user.py:229', 'users/models/user.py:392'])
t('R19', 'R', X2C, 'Create AuthProvider', 'control AuthProvider; cell AuthProviderReplica', 'Replica is a copy of AuthProvider (insert)',
  'kcopy', 'order', 'AuthProvider before Replica', ASYNC_PARENT_FIRST, 'L',
  ['models/authprovider.py:48', 'hybridcloud/services/replica/impl.py:220'])
t('R20', 'R', X2C, 'Modify AuthProvider config, default_role, default_global_access, scim_enabled or allow_unlinked',
  'control AuthProvider; cell AuthProviderReplica', 'Replica[auth_provider_id, config, …] ⊆ AuthProvider[id, …], copied columns mutable',
  'kcopy', 'atomic', 'no valid order', ASYNC_PARENT_FIRST, 'L',
  ['models/authprovider.py:48', 'hybridcloud/services/replica/impl.py:220'])
t('R21', 'R', X2C, 'Delete AuthProvider', 'control AuthProvider; cell AuthProviderReplica and AuthIdentityReplica (HCFK cascade)',
  'Replica and 2 HCFKs reference AuthProvider', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['models/authprovider.py:58', 'hybridcloud/services/replica/impl.py:284', 'models/authidentityreplica.py:22'])
t('R22', 'R', X2C, 'Create AuthIdentity (link SSO identity)', 'control AuthIdentity; cell AuthIdentityReplica', 'Replica is a copy of AuthIdentity (insert)',
  'kcopy', 'order', 'AuthIdentity before Replica', ASYNC_PARENT_FIRST, 'A',
  ['models/authidentity.py:42', 'hybridcloud/services/replica/impl.py:244'])
t('R23', 'R', X2C, 'Modify AuthIdentity data, last_verified or user (every SSO login, periodic verification, user merge)',
  'control AuthIdentity; cell AuthIdentityReplica', 'Replica[auth_identity_id, data, last_verified, …] ⊆ AuthIdentity[id, …], copied columns mutable',
  'kcopy', 'atomic', 'no valid order', ASYNC_PARENT_FIRST, 'S',
  ['models/authidentity.py:42', 'hybridcloud/services/replica/impl.py:244', 'auth/services/access/impl.py:88'])
t('R24', 'R', X2C, 'Delete AuthIdentity', 'control AuthIdentity; cell AuthIdentityReplica (HCFK cascade)', 'HCFK references AuthIdentity',
  'ind', 'order', 'children before parent', ASYNC_REVERSED, 'A', ['models/authidentityreplica.py:18'])
t('R25', 'R', X2C, 'Create ApiKey', 'control ApiKey; cell ApiKeyReplica', 'Replica is a copy of ApiKey (insert)', 'kcopy', 'order',
  'ApiKey before Replica', ASYNC_PARENT_FIRST, 'L', ['models/apikey.py:57', 'hybridcloud/services/replica/impl.py:261'])
t('R26', 'R', X2C, 'Modify ApiKey label, scope_list, allowed_origins or status', 'control ApiKey; cell ApiKeyReplica',
  'Replica[apikey_id, label, status, …] ⊆ ApiKey[id, …], copied columns mutable', 'kcopy', 'atomic', 'no valid order', ASYNC_PARENT_FIRST, 'L',
  ['models/apikey.py:57', 'api/endpoints/organization_api_key_details.py:74', 'api/authentication.py:300'])
t('R27', 'R', X2C, 'Delete ApiKey', 'control ApiKey; cell ApiKeyReplica (HCFK cascade)', 'HCFK references ApiKey', 'ind', 'order',
  'children before parent', ASYNC_REVERSED, 'L', ['hybridcloud/models/apikeyreplica.py:23', 'api/endpoints/organization_api_key_details.py:106'])
t('R28', 'R', X2C, 'Create ApiToken', 'control ApiToken; cell ApiTokenReplica', 'Replica is a copy of ApiToken (insert)', 'kcopy', 'order',
  'ApiToken before Replica', ASYNC_PARENT_FIRST, 'A', ['models/apitoken.py:331', 'hybridcloud/services/replica/impl.py:158'])
t('R29', 'R', X2C, 'Refresh ApiToken (token, hashed_token, expires_at) or modify scope', 'control ApiToken; cell ApiTokenReplica',
  'Replica[apitoken_id, token, hashed_token, expires_at, …] ⊆ ApiToken[id, …], copied columns mutable', 'kcopy', 'atomic',
  'no valid order', ASYNC_PARENT_FIRST, 'S',
  ['models/apitoken.py:481', 'web/frontend/oauth_token.py:442', 'api/authentication.py:485'])
t('R30', 'R', X2C, 'Delete ApiToken', 'control ApiToken; cell ApiTokenReplica', 'Replica references ApiToken (delete)', 'ind', 'order',
  'children before parent', ASYNC_REVERSED, 'A', ['models/apitoken.py:341', 'hybridcloud/services/replica/impl.py:188'])
t('R31', 'R', X2C, 'Create OrgAuthToken', 'control OrgAuthToken; cell OrgAuthTokenReplica', 'Replica is a copy of OrgAuthToken (insert)',
  'kcopy', 'order', 'OrgAuthToken before Replica', ASYNC_PARENT_FIRST, 'L',
  ['models/orgauthtoken.py:121', 'hybridcloud/services/replica/impl.py:198'])
t('R32', 'R', X2C, 'Rename or deactivate OrgAuthToken (name, date_deactivated)', 'control OrgAuthToken; cell OrgAuthTokenReplica',
  'Replica[orgauthtoken_id, name, date_deactivated, …] ⊆ OrgAuthToken[id, …], copied columns mutable', 'kcopy', 'atomic',
  'no valid order', ASYNC_PARENT_FIRST, 'L',
  ['api/endpoints/organization_auth_token_details.py:58', 'api/endpoints/organization_auth_token_details.py:85',
   'api/authentication.py:716'])
t('R33', 'R', X2C, 'Delete OrgAuthToken', 'control OrgAuthToken; cell OrgAuthTokenReplica (HCFK cascade)', 'HCFK references OrgAuthToken',
  'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L', ['hybridcloud/models/orgauthtokenreplica.py:21'])
t('R34', 'R', X2C, 'Modify SentryApp webhook_url or events', 'control SentryApp; ServiceHook.url, events in the cell of each installation',
  'ServiceHook copies SentryApp webhook_url, events via SentryAppInstallation (two-hop path copy), copied columns mutable', 'kcopy', 'atomic',
  'no valid order', 'two implementations: one outbox message per installation, or synchronous RPC from a control task', 'L',
  ['sentry_apps/logic.py:220', 'receivers/outbox/control.py:111', 'sentry_apps/tasks/sentry_apps.py:916',
   'sentry_apps/tasks/sentry_apps.py:941', 'utils/sentry_apps/service_hook_manager.py:6'])
t('R35', 'R', X2C, 'Delete SentryApp', 'control SentryApp; cell Action disabled, AlertRuleTriggerAction and NotificationAction cascaded',
  '2 HCFKs and soft references in Action.config', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['sentry_apps/models/sentry_app.py:240', 'sentry_apps/models/sentry_app.py:256', 'receivers/outbox/control.py:63'])
t('R36', 'R', X2C, 'Delete SentryAppInstallation', 'control SentryAppInstallation; cell Action disabled, ServiceHook cascaded',
  'HCFK and soft reference in Action.config', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['sentry_apps/models/sentry_app_installation.py:129', 'sentry_apps/models/sentry_app_installation.py:160',
   'receivers/outbox/control.py:84', 'sentry_apps/models/servicehook.py:64'])
t('R37', 'R', X2C, 'Delete Integration',
  'control Integration; cell Action, AlertRuleTriggerAction, ExternalActor, ExternalIssue, NotificationAction cascaded',
  '5 HCFKs reference Integration', 'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L',
  ['integrations/models/integration.py:80', 'integrations/models/integration.py:91'])
t('R38', 'R', X2C, 'Delete OrganizationIntegration',
  'control OrganizationIntegration; cell Repository unlinked, ProjectCodeOwners, RepositoryProjectPathConfig, ProjectRepository, ProjectOption deleted',
  'HCFK and soft repository references', 'ind', 'order', 'children before parent',
  'deletion task unlinks cell rows synchronously, then deletes the parent; HCFK child rows asynchronously', 'L',
  ['deletions/defaults/organizationintegration.py:23', 'integrations/models/repository_project_path_config.py:20'])
t('R39', 'R', X2C, 'Delete ApiApplication', 'control ApiApplication; cell ApiTokenReplica, ServiceHook cascaded', '2 HCFKs reference ApiApplication',
  'ind', 'order', 'children before parent', ASYNC_REVERSED, 'L', ['models/apiapplication.py:120'])
t('R40', 'R', X2C, 'Disable SCIM', 'control AuthProvider; cell member flags idp:provisioned, idp:role-restricted and Team.idp_provisioned cleared',
  'one-way: idp flag => organization has SCIM enabled', 'oneway', 'order', 'clear flags first, then disable',
  'disables in the control silo first, then clears flags via outbox (a code comment acknowledges the ordering risk)', 'L',
  ['models/authprovider.py:156', 'models/authprovider.py:168', 'receivers/outbox/control.py:152',
   'organizations/services/organization/impl.py:628'])
t('R41', 'R', X2C, 'Delete a conflicting SSO identity', 'control AuthIdentity; cell member sso:invalid=1, sso:linked=0',
  'one-way: sso:linked => AuthIdentity exists (validity comes from the external IdP, not from the database)', 'oneway', 'order',
  'modify flags first, then delete identity',
  'deletes identity first, then modifies flags via outbox', 'A',
  ['auth/helper.py:513', 'models/authprovider.py:204', 'receivers/outbox/control.py:157'])
t('R42', 'R', X2C, 'Integration added to organization (integration_added signal)', 'control OrganizationIntegration; cell FeatureAdoption, OrganizationOnboardingTask',
  'onboarding records depend on the integration having been added (insert)', 'ind', 'order', 'integration before onboarding records', ASYNC_PARENT_FIRST, 'L',
  ['integrations/models/integration.py:106', 'integrations/models/integration.py:135', 'receivers/onboarding.py:532',
   'receivers/features.py:723'])

# ---------------------------------------------------------------- D: synchronous cross-silo RPC writes
t('D01', 'D', X2C, 'Register and join the only organization (single-organization mode)', 'control User; cell OrganizationMember', 'Member.user_id references User (insert)',
  'ind', 'order', 'User before Member', 'same order', 'L', ['api/endpoints/auth_register.py:86'])
t('D02', 'D', X2C, 'First SSO login creates a new user', 'control User, AuthIdentity; cell OrganizationMember (sso:linked), unapproved requesting member deleted first',
  'Member references User; one-way sso:linked => AuthIdentity exists', 'oneway', 'order', 'control silo before cell', 'same order', 'A',
  ['auth/helper.py:913', 'auth/helper.py:283', 'api/invite_helper.py:166'])
t('D03', 'D', X2C, 'Link SSO identity', 'control AuthIdentity created or reattached, conflicting identity deleted; cell member sso:linked=1 or new member created',
  'one-way sso:linked => AuthIdentity exists', 'oneway', 'order', 'identity before flag', 'same order', 'A',
  ['auth/helper.py:419', 'auth/helper.py:208'])
t('D04', 'D', X2C, 'SSO login with an existing identity', 'control AuthIdentity data, last_verified modified; cell member sso flags reset or member rejoins',
  'one-way sso:linked => AuthIdentity exists; the identity exists throughout', 'nodep', 'none', 'any', '-', 'S', ['auth/helper.py:215'])
t('D05', 'D', X2C, 'Accept invitation', 'cell member user_id written; control AuditLogEntry; member_joined signal writes cell FeatureAdoption',
  'audit log entry and onboarding record reference the existing organization and member (insert)', 'ind', 'order', 'member before log entry', 'same order', 'A',
  ['api/invite_helper.py:227', 'api/endpoints/accept_organization_invite.py:46'])
t('D06', 'D', X2C, 'Delete own account', 'cell memberships deleted, solely owned organizations scheduled for deletion; control audit entry, User deleted or deactivated',
  'memberships and organization ownership reference User', 'ind', 'order', 'children before parent', 'same order (memberships deleted before user)', 'L',
  ['users/api/endpoints/user_details.py:386'])
t('D07', 'D', C2X, 'SCIM deactivates member', 'cell member deleted; control audit entry; User.is_staff, is_superuser and UserPermission revoked in the SaaS superuser organization',
  'derived: privilege <=> membership in the corresponding privileged team (strict reading)', 'derived', 'atomic',
  'no valid order (under the one-way reading, revoking privileges before deleting the member suffices)',
  'deletes member first, then revokes privileges', 'L', ['core/endpoints/scim/members.py:199'])
t('D08', 'D', C2X, 'Remove member', "cell member and the invitations it issued deleted; control UserOption rows of the user on this organization's projects deleted, audit entry",
  'one-way: project-level UserOption => user is a member of the organization', 'oneway', 'order', 'options deleted first', 'deletes member first, deletes options after commit', 'A',
  ['core/endpoints/organization_member_details.py:474'])
t('D09', 'D', X2C, 'Periodic SSO identity verification', 'cell member sso flags; control AuthIdentity.last_verified',
  'one-way sso:linked => AuthIdentity exists is unaffected', 'nodep', 'none', 'any', '-', 'S', ['tasks/auth/check_auth.py:97'])
t('D10', 'D', X2C, 'Enable SSO', 'cell Organization.require_2fa turned off; control AuthProvider, AuthIdentity; cell member sso:linked; sso_enabled signal',
  'denial constraint: an organization cannot both require 2FA and enable SSO; member flag depends one-way on the identity', 'oneway', 'order',
  'turn off 2FA first, then create AuthProvider',
  'same order (turns off 2FA first)', 'L', ['auth/helper.py:1275', 'auth/helper.py:1387'])
t('D11', 'D', X2C, 'Modify SSO settings', 'control AuthProvider scim, allow_unlinked, config; cell Organization.default_role',
  'no constraint links the two sides', 'nodep', 'none', 'any', '-', 'L', ['web/frontend/organization_auth_settings.py:141'])
t('D12', 'D', X2C, 'Disable SSO', 'control AuthProvider and its AuthIdentity rows deleted, User.is_managed reset; cell sso:linked, sso:invalid cleared for all members',
  'one-way sso:linked => AuthIdentity exists', 'oneway', 'order', 'clear flags first, then delete identities',
  'deletes in the control silo first, then a cell task clears the flags', 'L',
  ['web/frontend/organization_auth_settings.py:120', 'tasks/auth/auth.py:71'])
t('D13', 'D', X2C, 'Partner SSO provisioning', 'control AuthProvider, AuditLogEntry; cell FeatureAdoption (sso_enabled)',
  'onboarding record depends on SSO having been provisioned (insert)', 'ind', 'order', 'control silo before cell', 'same order', 'L', ['auth/services/auth/impl.py:42'])
t('D14', 'D', C2X, 'SCIM privileged-team membership change', 'cell OrganizationMemberTeam inserted or deleted; control User.is_staff, is_superuser and UserPermission (task)',
  'derived: privilege <=> membership in the corresponding privileged team (strict reading)', 'derived', 'atomic',
  'no valid order (under the one-way reading, granting after adding the membership and revoking before removing it suffices)',
  'revocation goes first (fail-secure)', 'L',
  ['core/endpoints/scim/teams.py:641', 'core/endpoints/scim/teams.py:681', 'tasks/scim/privilege_sync.py:141'])
t('D15', 'D', X2C, 'Link GitHub identity', 'control Identity; cell ExternalActor (in every organization with the GitHub integration installed)',
  'ExternalActor mirrors the GitHub login of the identity (insert)', 'ind', 'order', 'identity before ExternalActor', 'same order', 'A',
  ['identity/github/provider.py:125', 'identity/github/provider.py:132'])
t('D16', 'D', C2X, 'Link team to a messaging channel', 'cell ExternalActor; control NotificationSettingProvider(team)',
  'one-way: team notification setting => team is linked to the provider', 'oneway', 'order', 'ExternalActor before setting', 'same order', 'L',
  ['integrations/messaging/linkage.py:384'])
t('D17', 'D', X2C, 'Install SentryApp', 'control SentryAppInstallation; cell ServiceHook (copies url, events)', 'ServiceHook references the installation and copies fields of its app (insert)',
  'ind', 'order', 'installation before ServiceHook', 'same order', 'L', ['sentry_apps/installations.py:128', 'sentry_apps/installations.py:154'])
t('D18', 'D', X2C, 'Bitbucket uninstall', 'control Integration disabled; cell Repository disabled', 'one-way: repository active => integration active',
  'oneway', 'order', 'disable repositories first', 'disables integration first', 'L', ['integrations/bitbucket/uninstalled.py:34'])
t('D19', 'D', X2C, 'GitHub installation deleted', 'control Integration disabled; cell Repository disabled', 'one-way: repository active => integration active',
  'oneway', 'order', 'disable repositories first', 'disables integration first', 'L', ['integrations/github/webhook.py:609'])
t('D20', 'D', X2C, 'Cursor installation removed', 'control Integration disabled; cell Repository disabled', 'one-way: repository active => integration active',
  'oneway', 'order', 'disable repositories first', 'disables integration first', 'L', ['integrations/cursor_origin/handlers.py:107'])
t('D21', 'D', X2C, 'Cursor installation restored', 'control Integration enabled; cell repositories restored by sync', 'one-way: repository active => integration active',
  'oneway', 'order', 'enable integration first', 'same order', 'L', ['integrations/cursor_origin/handlers.py:136'])
t('D22', 'D', C2X, 'Create or link external issue', 'cell ExternalIssue, GroupLink; control OrganizationIntegration.config defaults, UserOption',
  'no constraint links the two sides', 'nodep', 'none', 'any', '-', 'A',
  ['issues/endpoints/group_integration_details.py:362', 'integrations/mixins/issues.py:219'])
t('D23', 'D', X2C, 'Merge users', 'cell memberships merged; control tokens, member mappings, identities, options reattached to the target user',
  'pointers reattached to an existing key (IND with frozen right-hand side)', 'ind', 'order', 'reattach first, then delete the source user', 'same order', 'L',
  ['users/models/user.py:409', 'users/models/user.py:432', 'auth_v2/endpoints/auth_merge_user_accounts.py:113',
   'organizations/services/organization/impl.py:531'])

EXCLUDED = [
    ('remote writes that touch one silo only', 'USER_IP_EVENT (UserIP), ORGAUTHTOKEN_UPDATE_USED (OrgAuthToken.date_last_used), ISSUE_COMMENT_UPDATE (email reply writes a cell comment), unsubscribe, Slack default organization, identity token refresh, GCP connection check, SCM repository sync and migration tasks, ServiceHook project settings, Teams uninstall, SSO unlink email',
     ['receivers/outbox/cell.py:190', 'receivers/outbox/cell.py:196', 'receivers/outbox/control.py:173',
      'api/endpoints/organization_unsubscribe.py:111', 'integrations/source_code_management/sync_repos.py:463']),
    ('cache invalidation only', 'SENTRY_APP_UPDATE, SENTRY_APP_INSTALLATION_UPDATE, cache clearing in USER_UPDATE, non-delete messages of INTEGRATION_UPDATE and API_APPLICATION_UPDATE',
     ['receivers/outbox/control.py:35', 'receivers/outbox/control.py:49', 'receivers/outbox/control.py:100']),
    ('asynchronous tasks inside a cell', 'GROUP_ACTION_LOG_EVENT, SEER_RUN_CREATE, SENTRY_APP_NORMALIZE_ACTIONS',
     ['receivers/outbox/cell.py:65', 'receivers/outbox/cell.py:230', 'receivers/outbox/cell.py:373']),
    ('no cross-silo data modification', 'ProjectKey update (ProjectKeyMapping copies only public_key); Identity delete (no cross-silo child tables); update messages of OrganizationIntegration and Team (empty handlers)',
     ['models/projectkey.py:194', 'users/models/identity.py:225', 'integrations/models/organization_integration.py:47']),
    ('migration, relocation, backup and CLI tooling', 'SCM_INTEGRATION_CONFIG_BACKFILL, relocation, backup, createuser, merge_users commands',
     ['receivers/outbox/cell.py:124', 'relocation/tasks/process.py:1479', 'runner/commands/createuser.py:186']),
    ('deprecated, or producer not in the open-source code', 'DISABLE_AUTH_PROVIDER, ORGANIZATION_MAPPING_CUSTOMER_ID_UPDATE, POST_ORGANIZATION_PROVISION, SUBSCRIPTION_UPDATE, PARTNER_ACCOUNT_UPDATE, SEND_VERCEL_INVOICE, FTC_CONSENT',
     ['receivers/outbox/cell.py:209', 'receivers/outbox/cell.py:223']),
]

# ---------------------------------------------------------------- redundancy INDs crossing silos
# (name, IND, copied source columns that the workload mutates, shape, note, location)
COPIES = [
    ('OrganizationMapping', 'Mapping[organization_id, slug, name, status, 9 flags, date_created] ⊆ Organization[id, …]',
     'slug, name, status, flags', 'K', 'also has unique key slug', 'hybridcloud/services/organization_mapping/serial.py:12'),
    ('OrganizationMemberMapping', 'Mapping[organizationmember_id, organization_id, role, user_id, email, inviter_id, invite_status] ⊆ Member[id, …]',
     'role, user_id, email, invite_status', 'K', 'copied columns user_id, inviter_id also carry FKs to User (implied by transitivity)',
     'hybridcloud/services/organizationmember_mapping/impl.py:31'),
    ('ProjectKeyMapping', 'Mapping[project_key_id, public_key] ⊆ ProjectKey[id, public_key]', '-', 'K', 'public_key immutable, IND stable',
     'hybridcloud/services/replica/impl.py:295'),
    ('ApiKeyReplica', 'Replica[apikey_id, organization_id, label, key, status, allowed_origins, scope_list] ⊆ ApiKey[id, …]',
     'label, status, allowed_origins, scope_list', 'K', 'replica table deliberately has no unique constraint on key ("to simplify replication")',
     'hybridcloud/services/replica/impl.py:261'),
    ('ApiTokenReplica', 'Replica[apitoken_id, user_id, application_id, organization, token, hashed_token, expires_at, scope_list, …] ⊆ ApiToken[id, …]',
     'token, hashed_token, expires_at, scope_list', 'K', 'application_is_active is a value derived via ApiApplication, not in K',
     'hybridcloud/services/replica/impl.py:158'),
    ('OrgAuthTokenReplica', 'Replica[orgauthtoken_id, organization, token_hashed, name, scope_list, created_by_id, date_deactivated] ⊆ OrgAuthToken[id, …]',
     'name, date_deactivated, created_by_id', 'K', '', 'hybridcloud/services/replica/impl.py:198'),
    ('AuthProviderReplica', 'Replica[auth_provider_id, organization_id, provider, config, default_role, default_global_access, scim_enabled, allow_unlinked] ⊆ AuthProvider[id, …]',
     'config, default_role, default_global_access, scim_enabled, allow_unlinked', 'K', '', 'hybridcloud/services/replica/impl.py:220'),
    ('AuthIdentityReplica', 'Replica[auth_identity_id, user_id, auth_provider_id, ident, data, last_verified] ⊆ AuthIdentity[id, …]',
     'data, last_verified, user_id', 'K', '', 'hybridcloud/services/replica/impl.py:244'),
    ('OrganizationMember user cache columns', 'Member[user_id, user_email, user_is_active] ⊆ User[id, email, is_active]',
     'email, is_active', 'K', '', 'models/organizationmember.py:237'),
    ('OrganizationAvatarReplica', 'Replica[organization_id, avatar_type, avatar_ident] ⊆ OrganizationAvatar[organization_id, avatar_type, ident]',
     'avatar_type, ident', 'out:nonpk', 'references the non-primary unique key organization_id', 'hybridcloud/services/replica/impl.py:320'),
    ('ServiceHook app fields', 'ServiceHook[installation_id, url, events] copies SentryApp[webhook_url, events] via SentryAppInstallation.sentry_app',
     'webhook_url, events', 'out:path', 'two-hop path copy, not a single IND', 'utils/sentry_apps/service_hook_manager.py:6'),
    ('OrganizationSlugReservation (PRIMARY)', 'Reservation[organization_id, slug] ⊆ Organization[id, slug], PRIMARY rows only',
     'slug', 'out:cond', 'conditional IND', 'services/organization/provisioning.py:195'),
    ('ExternalActor (GitHub identity mirror)', 'ExternalActor.external_name = "@" + GitHub login',
     'login', 'out:derived', 'functional copy, maintained best-effort only', 'identity/github/provider.py:132'),
]
DERIVED = [
    ('OrganizationMember.flags sso:linked, sso:invalid', 'one-way: linked => AuthIdentity exists; validity comes from the external IdP', 'auth/services/access/service.py:41'),
    ('OrganizationMember.flags idp:provisioned, Team.idp_provisioned', 'one-way: idp flag => SCIM enabled', 'organizations/services/organization/impl.py:628'),
    ('User.is_staff, is_superuser, UserPermission', 'derived: privilege <=> membership in the privileged team (SaaS superuser organization only)', 'tasks/scim/privilege_sync.py:24'),
    ('Repository.status', 'one-way: repository active => integration active', 'integrations/bitbucket/uninstalled.py:52'),
    ('ApiTokenReplica.application_is_active', 'derived from ApiApplication.status', 'hybridcloud/services/replica/impl.py:174'),
]

# ---------------------------------------------------------------- explicit operation / order agreement
# op: what the template does to the rows that carry the cross-silo constraint.
OP = {**{k: 'insert' for k in 'R01 R05 R08 R10 R16 R19 R22 R25 R28 R31 R42 D01 D02 D03 D10 D13 D15 D16 D17 D22'.split()},
      **{k: 'delete' for k in 'R04 R07 R09 R12 R13 R14 R15 R18 R21 R24 R27 R30 R33 R35 R36 R37 R38 R39 R41 D06 D07 D08 D12'.split()},
      **{k: 'modify' for k in 'R02 R03 R06 R11 R17 R20 R23 R26 R29 R32 R34 R40 D04 D05 D09 D11 D18 D19 D20 D21 D23'.split()},
      'D14': 'insert/delete'}
# agree: does Sentry's actual write order equal the order the constraint needs (order-class templates only)?
AGREE = {**{k: 'same' for k in 'R05 R08 R10 R15 R16 R19 R22 R25 R28 R31 R42 D01 D02 D03 D05 D06 D10 D13 D15 D16 D17 D21 D23'.split()},
         **{k: 'reversed' for k in 'R01 R04 R07 R09 R12 R13 R18 R21 R24 R27 R30 R33 R35 R36 R37 R39 R40 R41 D08 D12 D18 D19 D20'.split()},
         **{k: 'mixed' for k in 'R14 R38'.split()}}
# copies whose IND is outside the strict K shape: conditional reservation, non-PK key, two-hop path
for k in 'R03 R10 R11 R12 R34'.split():
    next(x for x in T if x['id'] == k)['dep'] = 'copyout'
for x in T:
    x['op'] = OP[x['id']]
    x['agree'] = AGREE.get(x['id'], 'n/a')
assert all(x['cls'] != 'order' or x['agree'] != 'n/a' for x in T), [x['id'] for x in T if x['cls'] == 'order' and x['agree'] == 'n/a']

# ---------------------------------------------------------------- summary
CLS = ['order', 'atomic', 'serial', 'none']
LABEL = {'order': 'need only order', 'atomic': 'need atomic visibility', 'serial': 'need serializability', 'none': 'unconstrained'}
DEP = {'ind': 'FK/HCFK chain', 'kcopy': 'FK with copied columns', 'copyout': 'copy outside class K',
       'oneway': 'one-way conditional IND or denial constraint', 'derived': 'derived aggregate', 'nodep': 'none'}
GITLAB = {'order': 28, 'atomic': 3, 'serial': 0, 'none': 1}
OP_NAME = {'insert': 'insert', 'delete': 'delete', 'modify': 'modify', 'insert/delete': 'insert/delete'}

n = len(T)
c = Counter(x['cls'] for x in T)
print('templates', n, {LABEL[k]: c[k] for k in CLS}, f"order share {c['order'] / n:.1%}")
for m in 'RD':
    cm = Counter(x['cls'] for x in T if x['mech'] == m)
    print(' mech', m, sum(cm.values()), {LABEL[k]: cm[k] for k in CLS})
oneway_view = Counter('order' if x['dep'] == 'derived' else x['cls'] for x in T)
print('one-way reading of derived values', {LABEL[k]: oneway_view[k] for k in CLS}, f"{oneway_view['order'] / n:.1%}")
print('atomic by dependency', Counter(DEP[x['dep']] for x in T if x['cls'] == 'atomic'))
print('by dependency kind', {DEP[k]: v for k, v in Counter(x['dep'] for x in T).items()})
print('operation x class', dict(Counter((x['op'], x['cls']) for x in T)))
tier = defaultdict(Counter)
for x in T:
    tier[x['tier']][x['cls']] += 1
print('tier x class', {k: dict(v) for k, v in sorted(tier.items())})
print('order templates: Sentry order vs required order', dict(Counter(x['agree'] for x in T if x['cls'] == 'order')))
in_exact = sum(1 for x in T if x['dep'] in ('ind', 'kcopy', 'nodep'))
print('templates whose constraints lie in the exact classes (ind/kcopy/none)', in_exact, f'{in_exact / n:.1%}',
      {DEP[k]: v for k, v in Counter(x['dep'] for x in T if x['dep'] not in ('ind', 'kcopy', 'nodep')).items()})
print('dependency x class', dict(Counter((DEP[x['dep']], LABEL[x['cls']]) for x in T)))

# schema-level dependency census
d = json.load(open(models_path))
edges = d['edges']
fk = [e for e in edges if e['kind'] == 'fk']
hc = [e for e in edges if e['kind'] == 'hcfk']
cyc_nodes = {v for comp in d['cycles'] for v in comp}
in_cycle = [e for e in fk + hc if (e['child'] in cyc_nodes and e['target'] in cyc_nodes) or e['child'] == e['target']]
ints = [(m['name'], r['col']) for m in d['models'] for r in m['refs'] if r['kind'] == 'int']
print('declared FK', len(fk), 'HCFK', len(hc), 'cross-silo HCFK', sum(1 for e in hc if e['child_silo'] != e['target_silo']))
print('all FK-type INDs unary and PK-referencing:', len(fk) + len(hc), '; inside a cycle:', len(in_cycle),
      sorted({(e['child'], e['col'], e['target']) for e in in_cycle}))
print('undeclared integer *_id references (not classified)', len(ints))
# denormalised locator: cell table carrying both a project reference and an organization reference
loc = []
for m in d['models']:
    if m['silo'] != 'cell':
        continue
    cols = {r['col'] for r in m['refs']}
    if cols & {'project', 'project_id'} and cols & {'organization', 'organization_id'} and m['name'] not in ('Project',):
        loc.append(m['name'])
print('cell tables with project and organization refs (organization_id copies project.organization_id)', len(loc))
kc = Counter(s for _, _, _, s, _, _ in COPIES)
print('cross-silo copy INDs', len(COPIES), dict(kc),
      'unstable (mutable copied column)', sum(1 for x in COPIES if x[2] != '-'))

os.makedirs(out_dir, exist_ok=True)
json.dump({'templates': T, 'excluded': EXCLUDED, 'copies': COPIES, 'derived': DERIVED, 'locator_tables': loc},
          open(os.path.join(out_dir, 'sentry_templates.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def loc_str(ls):
    return '<br>'.join(f'`{p}`' for p in ls)


with open(os.path.join(out_dir, 'sentry_templates_table.md'), 'w', encoding='utf-8') as f:
    f.write('| id | direction | template | operation | rows written | constraint | dependency kind | class | required order | what Sentry does | tier | code locations |\n')
    f.write('|---|---|---|---|---|---|---|---|---|---|---|---|\n')
    for x in T:
        f.write(f"| {x['id']} | {x['dir']} | {x['what']} | {OP_NAME[x['op']]} | {x['writes']} | {x['constraint']} | {DEP[x['dep']]} | "
                f"{'**' + LABEL[x['cls']] + '**' if x['cls'] != 'order' else LABEL[x['cls']]} | {x['order']} | {x['impl']} | {x['tier']} | {loc_str(x['locs'])} |\n")
print('wrote', out_dir)
