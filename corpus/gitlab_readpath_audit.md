# Read-path audit of the GitLab cascading deletions (commit 9d4cbde6, Section 6.2)

Question.  Each of the 17 cascading deletions (templates C01-C17 of `gitlab_classify.py`) deletes a parent row in one database, after which a cleanup job deletes or nullifies the child rows in the other databases through loose foreign keys.
The required order removes the children first.
A reader that loads a child and then the parent it references reads against that order, and no write order protects it.
The audit asks, per template, whether GitLab has such a reader, and how such readers handle a missing parent.

Method.
- `gitlab_readpath_probe.py` takes the 249 loose foreign keys of the 17 parents that cross databases, finds the `belongs_to` association of each child model for the key, and lists the places in `app`, `ee/app`, `lib` and `ee/lib` (tests and the cleanup machinery excluded) that call it on a variable named after the child model, inside the child model, or through a delegation (`out/gitlab_readpath_probe.json`, file and line only).
- `gitlab_readpath_sample.py` draws 6 sites per template (all sites of smaller templates; seed 9), 56 in total (`out/gitlab_readpath_sample.json`), and prints them with their context.
  `gitlab_readpath_review.json` holds the verdict by hand on each.
- For the templates without a confirmed site, the associations and the foreign-key columns were searched by hand (manual lookups by id, GraphQL types, `has_many ... through`).

## Verdict per template

| Template | Parent (database) | Reader from child to parent | Evidence (file:line at commit 9d4cbde6) |
|---|---|---|---|
| C01 | chat_names (main) | yes | `lib/gitlab/chat/responder/mattermost.rb:117`, `lib/gitlab/chat/responder/slack.rb:80` (pipeline, then its chat name, no check) |
| C02 | cluster_agents (main) | none found | no association; agents list their vulnerability reads (`ee/app/models/ee/clusters/agent.rb:11`) |
| C03 | duo_workflows_workflows (main) | none found | `Vulnerabilities::Flag#workflow` is never called; the GraphQL type of triggered workflows exposes only the workflow name (`ee/app/graphql/types/vulnerabilities/triggered_workflow_type.rb:11`) |
| C04 | issues (main) | yes | `ee/app/serializers/vulnerabilities/issue_link_entity.rb:8`, `ee/lib/gitlab/data_builder/vulnerability.rb:63` (no check); `ee/app/serializers/vulnerabilities/feedback_entity.rb:28` (checked) |
| C05 | merge_requests (main) | yes | `app/models/ci/pipeline.rb:1721`, `ee/app/models/ee/ci/pipeline.rb:261` (no check) |
| C06 | namespaces (main) | yes | `ee/app/models/govern/policy.rb:89`, `ee/app/views/groups/settings/_delete_button_with_security_policies.html.haml:13` (checked) |
| C07 | notes (main) | none found | `VulnerabilityUserMention#note` is never called; mentions are read from the vulnerability (`ee/app/models/ee/vulnerability.rb:74`) |
| C08 | organizations (main) | yes | `lib/api/ci/helpers/runner.rb:260` (the runner API loads the organization of the authenticated runner by id) |
| C09 | p_ci_builds (ci) | yes | `ee/app/models/security/scan.rb:63` (delegation without `allow_nil`), `ee/app/models/security/scan.rb:207` (no check); `app/presenters/ml/candidate_details_presenter.rb:53` (checked) |
| C10 | p_ci_pipelines (ci) | yes | `app/views/shared/_merge_request_pipeline_status.html.haml:3`, `ee/app/services/merge_requests/merge_strategies/from_train_ref.rb:22` (checked) |
| C11 | p_ci_workloads (ci) | none found | `Workflow#workloads` (`ee/app/models/ai/duo_workflows/workflow.rb:136`) is never called |
| C12 | plans (main) | none | the model ignores `ci_pending_builds.plan_id` (`app/models/ci/pending_build.rb:13`) |
| C13 | projects (main) | yes | `app/models/ci/pipeline_schedule.rb:164`, `app/workers/ci/unlock_pipelines_in_queue_worker.rb:26`, `ee/app/services/sbom/create_vulnerabilities_service.rb:142` (no check) |
| C14 | security_policies (main) | none found | `Vulnerabilities::SeverityOverride#security_policy` is never called; its GraphQL type exposes no policy |
| C15 | tags (ci) | yes | `ee/app/models/dast/profile_tag.rb:12` (checked) |
| C16 | users (main) | yes | `app/models/ci/pipeline.rb:1978` (no check); `ee/app/services/ci/minutes/track_live_consumption_service.rb:117` (checked) |
| C17 | vulnerabilities (sec) | yes | notes follow their polymorphic `noteable`: `app/views/search/results/_note.html.haml:6`, `app/helpers/notes_helper.rb:115` (checked) |

Hence 11 of the 17 templates have a reader that reads against the required order.
For the other 6, the readers that we found list the children of a parent, filter by the parent's id, or do not read the column at all.

## Tolerance in the sample

Of the 56 sampled sites, 19 are no cross-database read from child to parent (another model with the same association name, such as the source of a membership; a writer path; generic code that never reaches the edge; or a reader that loaded the parent first).
Of the other 37, 26 tolerate a missing parent (safe navigation, a presence or permission check, a guard clause) and 11 raise an error (a method called on the missing parent, or a delegation without `allow_nil`).

GitLab's guide for loose foreign keys (`doc/development/database/loose_foreign_keys.md`, section "Risks of loose foreign keys and possible mitigations") describes exactly these readers: a view or a background job that loads the deleted parent of a child returns a 404 or a 500 or fails, and the suggested mitigation is to skip a child whose parent is not found.

## Limits

The probe is static.
It misses dereferences through variables named differently from the child model, serializers that expose the association by name, and caches; "none found" means that neither the probe nor the searches by hand found a reader.
The sample is random per template, so the share of tolerant sites is an estimate, not a census.
