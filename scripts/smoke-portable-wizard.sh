#!/usr/bin/env bash
set -euo pipefail

executable="${1:-./dist/openai-tunnel-kit}"
temporary_dir="$(mktemp -d)"
wizard_log="$temporary_dir/wizard.log"
wizard_pid=""

cleanup() {
  if [[ -n "$wizard_pid" ]] && kill -0 "$wizard_pid" 2>/dev/null; then
    kill "$wizard_pid"
    wait "$wizard_pid" 2>/dev/null || true
  fi
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

OPENAI_TUNNEL_KIT_CONFIG_DIR="$temporary_dir/config" \
PYTHONUNBUFFERED=1 \
  "$executable" wizard --no-browser >"$wizard_log" 2>&1 &
wizard_pid=$!

wizard_url=""
for _attempt in {1..100}; do
  if ! kill -0 "$wizard_pid" 2>/dev/null; then
    cat "$wizard_log" >&2
    echo "portable wizard exited before becoming ready" >&2
    exit 1
  fi
  wizard_url="$(sed -n 's/^Wizard: //p' "$wizard_log" | tail -n 1)"
  [[ -n "$wizard_url" ]] && break
  sleep 0.1
done

if [[ -z "$wizard_url" ]]; then
  cat "$wizard_log" >&2
  echo "portable wizard did not report its loopback URL" >&2
  exit 1
fi

wizard_html="$temporary_dir/wizard.html"
curl --fail --silent --show-error "$wizard_url" >"$wizard_html"
grep -Fq '<h1>openai-tunnel-kit wizard</h1>' "$wizard_html"
grep -Fq 'id="auto-populate">Auto-populate</button>' "$wizard_html"

wizard_token="$(sed -n 's/.*<script>const token="\([^"]*\)";.*/\1/p' "$wizard_html")"
if [[ -z "$wizard_token" ]]; then
  echo "portable wizard page did not contain its request token" >&2
  exit 1
fi

wallet_response="$temporary_dir/wallet-response.json"
wallet_status="$(curl --silent --show-error \
  --output "$wallet_response" \
  --write-out '%{http_code}' \
  --request POST \
  --header "x-wizard-token: $wizard_token" \
  "${wizard_url}api/admin-credential/forget")"
if [[ "$wallet_status" != "200" && "$wallet_status" != "400" ]]; then
  cat "$wallet_response" >&2
  echo "portable wizard wallet check returned HTTP $wallet_status" >&2
  exit 1
fi
if grep -Fq 'system-wallet support is missing' "$wallet_response"; then
  cat "$wallet_response" >&2
  echo "portable wizard did not bundle keyring support" >&2
  exit 1
fi

echo "portable wizard smoke test passed"
