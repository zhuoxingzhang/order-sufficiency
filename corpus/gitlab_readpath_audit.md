# Read-path audit of the GitLab order-only templates (commit 9d4cbde6, Section 6.2)

GitLab has 28 order-only templates: the 17 cascading deletions C01-C17 and the 11 exemptions G01-G11 of `gitlab_classify.py`.
The first part of this file audits the cascading deletions, the second the exemptions.

## Cascading deletions

Question.  Each of the 17 cascading deletions (templates C01-C17 of `gitlab_classify.py`) deletes a parent row in one database, after which a cleanup job deletes or nullifies the child rows in the other databases through loose foreign keys.
The required order removes the children first.
A reader that loads a child and then the parent it references reads against that order, and no write order protects it.
The audit asks, per template, whether GitLab has such a reader, and how such readers handle a missing parent.

Method.
- `gitlab_readpath_probe.py` takes the 249 loose foreign keys of the 17 parents that cross databases, finds the `belongs_to` association of each child model for the key, and lists the places in `app`, `ee/app`, `lib` and `ee/lib` (tests and the cleanup machinery excluded) that call it on a variable named after the child model, inside the child model, or through a delegation (`out/gitlab_readpath_probe.json`, file and line only).
- `gitlab_readpath_sample.py` draws 6 sites per template (all sites of smaller templates; seed 9), 56 in total (`out/gitlab_readpath_sample.json`), and prints them with their context.
  `gitlab_readpath_review.json` holds the verdict by hand on each.
- For the templates without a confirmed site, the associations and the foreign-key columns were searched by hand (manual lookups by id, GraphQL types, `has_many ... through`).

### Verdict per template

| Template | Parent (database) | Reader from child to parent | Evidence (file:line at commit 9d4cbde6) |
|---|---|---|---|
| C01 | chat_names (main) | yes | `lib/gitlab/chat/responder/mattermost.rb:117`, `lib/gitlab/chat/responder/slack.rb:80` (pipeline, then its chat name, no check) |
| C02 | cluster_agents (main) | none found | no association; agents list their vulnerability reads (`ee/app/models/ee/clusters/agent.rb:11`) |
| C03 | duo_workflows_workflows (main) | none found | `Vulnerabilities::Flag#workflow` is never called; the GraphQL type of triggered workflows exposes only the workflow name (`ee/app/graphql/types/vulnerabilities/triggered_workflow_type.rb:11`) |
| C04 | issues (main) | yes | `ee/app/serializers/vulnerabilities/issue_link_entity.rb:8`, `ee/lib/gitlab/data_builder/vulnerability.rb:63` (no check); `ee/app/serializers/vulnerabilities/feedback_entity.rb:28` (checked) |
| C05 | merge_requests (main) | yes | `app/models/ci/pipeline.rb:1721`, `ee/app/models/ee/ci/pipeline.rb:261` (checked by `merge_request?`, `app/models/ci/pipeline.rb:1774`) |
| C06 | namespaces (main) | yes | `ee/app/models/govern/policy.rb:89` (a policy validates its namespace; checked) |
| C07 | notes (main) | none found | `VulnerabilityUserMention#note` is never called; mentions are read from the vulnerability (`ee/app/models/ee/vulnerability.rb:74`) |
| C08 | organizations (main) | yes | `lib/api/ci/helpers/runner.rb:260` (the runner API loads the organization of the authenticated runner by id) |
| C09 | p_ci_builds (ci) | yes | `ee/app/models/security/scan.rb:63` (delegation without `allow_nil`), `ee/app/models/security/scan.rb:207` (no check); `app/presenters/ml/candidate_details_presenter.rb:53` (checked) |
| C10 | p_ci_pipelines (ci) | yes | `app/views/shared/_merge_request_pipeline_status.html.haml:3`, `ee/app/services/merge_requests/merge_strategies/from_train_ref.rb:22` (checked) |
| C11 | p_ci_workloads (ci) | none found | `Workflow#workloads` (`ee/app/models/ai/duo_workflows/workflow.rb:136`) is never called |
| C12 | plans (main) | none | the model ignores `ci_pending_builds.plan_id` (`app/models/ci/pending_build.rb:13`) |
| C13 | projects (main) | yes | `app/models/ci/pipeline_schedule.rb:164`, `app/workers/ci/unlock_pipelines_in_queue_worker.rb:26`, `ee/app/services/sbom/create_vulnerabilities_service.rb:142` (no check) |
| C14 | security_policies (main) | none found | `Vulnerabilities::SeverityOverride#security_policy` is never called; its GraphQL type exposes no policy |
| C15 | tags (ci) | yes | `ee/app/models/dast/profile.rb:19`, `:65` (`tag_list` loads the tags of a profile's tag rows without a join; a missing tag is left out) |
| C16 | users (main) | yes | `app/models/ci/pipeline.rb:1978` (no check); `ee/app/services/ci/minutes/track_live_consumption_service.rb:117` (checked) |
| C17 | vulnerabilities (sec) | yes | notes follow their polymorphic `noteable`: `app/views/search/results/_note.html.haml:6`, `app/helpers/notes_helper.rb:115` (checked) |

Hence 11 of the 17 templates have a reader that reads against the required order.
For the other 6, the readers that we found list the children of a parent, filter by the parent's id, or do not read the column at all.

### Tolerance in the sample

Of the 56 sampled sites, 24 are no cross-database read from child to parent (another model with the same association name, such as the source of a membership or a policy configuration in the database of its namespace; a writer path; generic code that never reaches the edge; or a reader that loaded the parent first).
Of the other 32, 24 tolerate a missing parent (safe navigation, a presence or permission check, a guard clause) and 8 raise an error (a method called on the missing parent, or a delegation without `allow_nil`).

GitLab's guide for loose foreign keys (`doc/development/database/loose_foreign_keys.md`, section "Risks of loose foreign keys and possible mitigations") describes exactly these readers: a view or a background job that loads the deleted parent of a child returns a 404 or a 500 or fails, and the suggested mitigation is to skip a child whose parent is not found.

### Limits

The probe is static.
It misses dereferences through variables named differently from the child model, serializers that expose the association by name, and caches; "none found" means that neither the probe nor the searches by hand found a reader.
The sample is random per template, so the share of tolerant sites is an estimate, not a census.

## Exemptions

The 11 exemptions write in one transaction across databases and need only order (`gitlab_classify.py`).
The same rule applies: a reader that reads first the side written last is protected, and a reader that reads the other side first and relies on the constraint is not.
For a foreign key written parent first, a reader that follows the key from the child reads the later side first, and a reader that lists the children of a parent finds them only after the parent; neither can see a child without its parent.

| Template | Writes, in the required order | Reader against the order | Evidence (file:line at commit 9d4cbde6) |
|---|---|---|---|
| G01 | delete the upload records of an owner, then the owner | yes: from an upload to its owner | `ee/app/replicators/geo/concerns/upload_replicator_behavior.rb:50` (Geo refuses to download when the owner is missing, `:53`); `app/workers/object_storage/migrate_uploads_worker.rb:107` (error recorded per upload); `lib/gitlab/cleanup/project_uploads.rb:44` (checked) |
| G02 | user, then its personal namespace | none | readers follow `namespaces.owner_id` from the namespace; a user without a namespace is legal and is becoming optional (`app/models/user.rb:3138`) |
| G03 | group, then its route | none | routes are resolved from the path to their source (`app/models/concerns/routable.rb:42`); a group read first falls back to its parent chain when the route is missing (`routable.rb:145`) |
| G04 | user and its details, then the membership | none | memberships follow `members.user_id` to the user |
| G05 | organization, then its owner membership | none | `organization_users` follow their keys to the organization and the user (`app/models/organizations/organization_user.rb:7`) |
| G06 | project, then its route | none | as G03 |
| G07 | delete the old upload record, then insert the new one | none | both writes go to the uploads database; readers look the record up by path in one query (`app/models/concerns/with_uploads.rb:70`) |
| G08 | upload, then its verification state | none | the state follows its key to the upload (`ee/app/models/geo/upload_state.rb:9`) |
| G09 | upload, then the state row of its partition | none | as G08 |
| G10 | bot user, then its project membership | none | the project finds its bot through the membership (`ee/app/models/ee/project.rb:231`) |
| G11 | remove the SCIM-managed membership, then deactivate the SCIM identity | yes: from a member to its identity | the members API shows the SCIM identity of each member (`ee/lib/ee/api/entities/member.rb:15`, `ee/app/models/ee/member.rb:232`); GitLab deactivates the identity first and schedules the removal (`ee/lib/gitlab/scim/group/deprovisioning_service.rb:28`), so the API already shows inactive identities on members; the membership check reads the identity first (`ee/lib/gitlab/auth/group_saml/membership_enforcer.rb:17`) |

Hence 2 of the 11 exemptions have a reader against the required order, both deletions, and both readers tolerate what they see.
Over the 28 order-only templates of GitLab, 13 have such a reader.
