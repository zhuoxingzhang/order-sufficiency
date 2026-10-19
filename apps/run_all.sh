#!/bin/sh
# Static analyzer of Sections 6.1 and 6.2 over the ten further applications, GitLab and Sentry, from
# scratch, followed by its validation and Figure 4b.  The outputs of the paper are in out/ (gzipped).
# SRC is a directory with checkouts of the applications at these commits, in directories named as in
# the loops below (owner_repository on GitHub):
#   zulip/zulip 68812981, makeplane/plane 4345fb3e, saleor/saleor 0c0e2697,
#   openedx/openedx-platform d7859be1, PostHog/posthog 069e3b3a, chatwoot/chatwoot 61c9ef44,
#   mastodon/mastodon 7f90e0fd, discourse/discourse 630b787e, spree/spree c6a498f7,
#   instructure/canvas-lms 1c9f0bb8, and GitLab (gitlab, commit 9d4cbde6) and Sentry (sentry, commit
#   13ccaecc) as in corpus/.
# The analyzer reads only *.rb, *.py, *.sql and config/*.yml, and skips tests, fixtures and locales,
# so sparse checkouts of these files suffice.  No application code is run.
set -e
cd "$(dirname "$0")"
P=${PY:-python3}
S=${SRC:?set SRC to the directory of the checkouts}
mkdir -p out
for a in zulip_zulip:zulip makeplane_plane:makeplane saleor_saleor:saleor openedx_openedx-platform:openedx \
         PostHog_posthog:PostHog; do
  $P -I analyzer/run_django.py "$S/${a%%:*}" out/${a##*:}.json
done
for a in chatwoot_chatwoot:chatwoot mastodon_mastodon:mastodon discourse_discourse:discourse spree_spree:spree \
         instructure_canvas-lms:instructure; do
  $P -I analyzer/run_rails.py "$S/${a%%:*}" out/${a##*:}.json
done
$P -I analyzer/run_gitlab.py "$S/gitlab" ../deciders/data/gitlab_schema.json out/gitlab.json
$P -I analyzer/run_sentry.py "$S/sentry" ../corpus/out/sentry_templates.json out/sentry.json
gzip -9 -n -f out/*.json
$P -I validate.py out
$P -I fig_apps.py out fig_apps.tex
