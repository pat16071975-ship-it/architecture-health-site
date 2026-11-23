#!/usr/bin/env bash
# AZ-252-04: fail closed on absent refs, diff errors, and unrelated file changes.
set -Eeuo pipefail
if [[ $# -ne 2 ]]; then echo "usage: verify_administrators_scope.sh BASE_SHA HEAD_SHA" >&2; exit 2; fi
base="$1"
head="$2"
if ! git cat-file -e "${base}^{commit}" 2>/dev/null; then
  echo "SCOPE_GUARD=FAIL; BASE_COMMIT_UNAVAILABLE=${base}" >&2
  exit 2
fi
if ! git cat-file -e "${head}^{commit}" 2>/dev/null; then
  echo "SCOPE_GUARD=FAIL; HEAD_COMMIT_UNAVAILABLE=${head}" >&2
  exit 2
fi
if ! git merge-base --is-ancestor "$base" "$head"; then
  echo "SCOPE_GUARD=FAIL; BASE_NOT_ANCESTOR_OF_HEAD" >&2
  exit 2
fi
# Deliberately do not negate a diff|grep pipeline: git diff failures must fail the job.
changed="$(git diff --name-only "$base" "$head" --)"
if [[ -z "$changed" ]]; then
  echo "SCOPE_GUARD=FAIL; EMPTY_DIFF" >&2
  exit 2
fi
while IFS= read -r file; do
  case "$file" in
    ".github/workflows/az-administrators.yml" | \
    "az-baze-auth/administrators.py" | \
    "az-baze-auth/app.py" | \
    "az-baze-auth/browser_administrators.py" | \
    "az-baze-auth/menu_order.py" | \
    "az-baze-auth/section_navigation.py" | \
    "az-baze-auth/server.py" | \
    "az-baze-auth/templates/admin.html" | \
    "az-baze-auth/templates/administrators.html" | \
    "az-baze-auth/templates/user_form.html" | \
    "az-baze-auth/test_administrators.py" | \
    "az-baze-auth/verify_administrators_scope.sh" | \
    "docs/administrators.md")
      ;;
    *)
      echo "SCOPE_GUARD=FAIL; UNRELATED_FILE=$file" >&2
      exit 1
      ;;
  esac
done <<< "$changed"
echo "SCOPE_GUARD=PASS; CHANGED_FILES=$(printf '%s\n' "$changed" | wc -l | tr -d ' ')"
