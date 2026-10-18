# Read-path audit of the Sentry order-only templates (commit 13ccaecc, Section 6.2)

Rule under test.  For an order-only template with legal order w1 then w2, a reader that reads
the w2 side first is protected by that order; a reader that reads the w1 side first and relies
on the constraint is protected by no order (the order only narrows the window to the gap
between its two reads); a reader of one side is protected.  For a foreign key, deletion has
w1 = child, w2 = parent, so the reader that follows the foreign key (child, then parent) is the
exposed one; insertion has w1 = parent, w2 = child, so the same reader is protected.

Method.  `readpath_probe.py` lists, for every hybrid-cloud foreign key of the 22 deletion templates,
the calls of the parent's RPC service whose arguments use the child's foreign-key column within
four lines, in non-test files that name the child model (163 hits, 125 distinct sites).
`readpath_tolerance.py` labels how each site handles a missing parent; all 45 sites labelled E or U
by the first pass were read by hand, and so was every other site whose first-pass label did not hold
(`readpath_override.json` gives the verdict and the reason for each).  Most of the latter take the
parent id from a request, a token, a task argument or stored rule data rather than from the child row.  Templates whose edges gave no
hit were searched by hand (`readpath_extra.json`).

Site labels: T tolerant (guard returns, skips, answers 404 or an empty value), B batch fetch
(missing parent silently absent), E fails (assert, or an internal exception), FP not a cross-silo
child -> parent read (parent id from the request, a token, a task argument or stored rule data;
the parent read first; a write path; or the same silo).

## Deletions (22)

| id | parent (silo) -> children | child -> parent reader | evidence (file:line in src/sentry) | missing parent | verdict |
|---|---|---|---|---|---|
| R04 | Organization (cell) -> 19 control HCFK children | yes | integrations/base.py:421 (404); sentry_apps/api/bases/sentryapps.py:250, :516 (assert); integrations/github/integration.py:549, gitlab/integration.py:334 (assert); api/authentication.py:580 (org-scope check skipped); api/endpoints/secret_scanning/github.py:128 (continue) | 14 T, 4 E | exposed |
| R07 | OrganizationMember (cell) -> OrganizationMemberMapping, Identity (control) | none found | mapping-only readers (core/endpoints/organization_index.py:315, web/frontend/setup_wizard.py:92); member-first reader (auth/services/access/impl.py:53) | - | protected |
| R09 | ProjectKey (cell) -> ProjectKeyMapping (control) | yes, across the router | the request is routed by the mapping (web/frontend/js_sdk_loader.py:90), then the cell reads the key by the public key of the URL (:311) | T (default loader config) | exposed |
| R12 | OrganizationAvatar (cell) -> OrganizationAvatarReplica (control) | yes, through the client | api/serializers/models/organization.py:354 builds the avatar URL from the replica; the browser fetches the file from the cell | T (broken image) | exposed |
| R13 | Team (cell) -> NotificationSettingOption/Provider (control) | none found | notifications/notificationcontroller.py:76 reads settings for recipients already read in the cell | - | protected |
| R14 | Project (cell) -> project-level settings, UserOption (control) | none found | notificationcontroller.py:76 (project ids from the event); user_notification_settings_options.py:32 reads settings only | - | protected |
| R18 | User (control) -> 71 cell HCFK columns | yes | 66 candidate sites: 26 T, 22 B, 1 E (types/actor.py:269 InvalidActor), 17 FP; e.g. utils/committers.py:238 (comment: a set user_id does not mean the user exists) | 48 T/B, 1 E | exposed |
| D06 | User (control) -> memberships (cell) | yes | the OrganizationMember.user_id sites of R18 (e.g. models/organizationmember.py:447, api/serializers/models/organization_member/base.py:39) | T | exposed |
| R21 | AuthProvider (control) -> AuthProviderReplica, AuthIdentityReplica (cell) | none found | the cell reads only the replicas (auth/services/access/impl.py:79, :88) | - | protected |
| R24 | AuthIdentity (control) -> AuthIdentityReplica (cell) | none found | access/impl.py:88, :116 (replica only) | - | protected |
| R27 | ApiKey (control) -> ApiKeyReplica (cell) | none found | api/authentication.py:300 (replica only) | - | protected |
| R30 | ApiToken (control) -> ApiTokenReplica (cell) | none found | api/authentication.py:485 (replica only) | - | protected |
| R33 | OrgAuthToken (control) -> OrgAuthTokenReplica (cell) | none found | api/authentication.py:716 (replica only) | - | protected |
| R35 | SentryApp (control) -> Action, AlertRuleTriggerAction, NotificationAction (cell) | yes | an alert fires for the app named by the action: notifications/notification_action/action_handler_registry/webhook_handler.py:73, sentry_apps/services/legacy_webhook/service.py:98 (logged and skipped) | T | exposed |
| R36 | SentryAppInstallation (control) -> ServiceHook, Action config (cell) | yes | rules/actions/sentry_apps/notify_event.py:55 (T), :156 (E, rule label), notification_action/.../sentry_app_issue_alert_handler.py:95 (E, alert fires) | 1 T, 2 E | exposed |
| R37 | Integration (control) -> Action, AlertRuleTriggerAction, ExternalActor, ExternalIssue, NotificationAction (cell) | yes | 28 candidate sites: 12 T, 1 E (integrations/models/external_issue.py:97 assert), 15 FP; e.g. integrations/slack/utils/notifications.py:300 (comment: integration removed, rule still active) | 12 T, 1 E | exposed |
| R38 | OrganizationIntegration (control) -> code mappings (cell) | yes | integrations/utils/stacktrace_link.py:41, integrations/utils/source_context.py:66, integrations/api/endpoints/organization_code_mapping_codeowners.py:22 | 3 T | exposed |
| R39 | ApiApplication (control) -> ServiceHook, ApiTokenReplica (cell) | yes | sentry_apps/models/servicehook.py:95, issues/action_log/base.py:93 | 2 T | exposed |
| D08 | one-way: project UserOption (control) => membership (cell); options first | none found | notificationcontroller.py reads members (w2) first; settings API reads options only | - | protected |
| D12 | one-way: sso:linked flag (cell) => AuthIdentity (control); flags first | none found | flag-only readers (api/serializers/models/organization_member/base.py:120, core/endpoints/organization_member_details.py:275) | - | protected |
| R15 | one-way: team setting for a provider (control) => team ExternalActor (cell); settings first | yes | team notification reads the setting (w1), then the ExternalActor at integrations/notifications.py:86 | T (empty) | exposed |
| R41 | one-way: as D12 | none found | as D12 | - | protected |

Deletions: 11 exposed (R04, R09, R12, R18, D06, R35, R36, R37, R38, R39, R15), 11 protected.

## Modifications (7)

| id | constraint, required order | reader | verdict |
|---|---|---|---|
| D18, D19, D20 | repository active => integration active; deactivate repositories first | repository first, then integration: integrations/source_code_management/tasks.py:73, preprod/integration_utils.py:56 (both T) | exposed |
| D21 | as above; enable the integration first | repository-first readers read the w2 side first | protected |
| R40 | idp flags => SCIM on; clear the flags first | flag-only readers: core/endpoints/team_details.py:74, organization_member_team_details.py:353 | protected |
| D05 | audit log and onboarding rows reference the member; member first | insertion-like; child -> parent readers protected | protected |
| D23 | merge users: re-point to the target, then delete the source user | deletion-like; the R18 sites dereference the source user | exposed |

Modifications: 4 exposed, 3 protected.

## Insertions (19)

For a foreign key or copy written parent first, the reader that follows the foreign key reads
the w2 side first and is protected; the reader that lists the children of a parent reads them
only after it has found the parent, so it cannot see a child without its parent at all.  Every
child -> parent site above is therefore protected for the insertion templates over the same
edges (D01, R05, R08, R10, R16, R19, R22, R25, R28, R31, D13, D15, D17, R01, R42).  The one-way
insertions D02, D03, D16 have flag-only or w2-first readers (as D12 and R15 in reverse).

D10 enables SSO: it turns off the two-factor requirement of the organization (cell, w1), then
creates the AuthProvider (control, w2), and the two must not hold together.  The API access check
reads the w1 side first: `determine_access` (api/permissions.py:229) takes the organization context
with its flags, then the user's auth state, which loads the provider (auth/services/access/impl.py:27).
A request between the two writes sees both, so it demands SSO and two-factor authentication
(api/bases/organization.py:88) and is refused for that moment: exposed, but it fails closed.

Insertions: 1 exposed (D10), 18 protected.

## Site totals (125 distinct candidate sites)

T 58, B 22, E 8, FP 37.  Excluding false positives: 80 of 88 genuine sites tolerate a missing
parent and 8 fail.  The E sites:

- integrations/github/integration.py:549, integrations/gitlab/integration.py:334: assert organization context
- integrations/models/external_issue.py:97: assert integration in get_installation
- sentry_apps/api/bases/sentryapps.py:250, :516: assert the owner organization
- notification_action/issue_alert_registry/handlers/sentry_app_issue_alert_handler.py:95: SentryAppError when an alert fires
- rules/actions/sentry_apps/notify_event.py:156: ValidationError while rendering a rule label
- types/actor.py:269: Actor.InvalidActor
