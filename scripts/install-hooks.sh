#!/usr/bin/env bash
# Make `git push` run scripts/ci_local.sh first.
set -euo pipefail
root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
hook="$root/.git/hooks/pre-push"
cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/scripts/ci_local.sh"
HOOK
chmod +x "$hook"
echo "pre-push hook installed: $hook"
