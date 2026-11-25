#!/usr/bin/env bash
# Walks the whole API contract against a running backend.
#
#   ./scripts/verify.sh                               # http://localhost:8000
#   ./scripts/verify.sh https://your-app.onrender.com
#
# Handy for the demo video: every requirement in one scripted pass.

set -uo pipefail

BASE="${1:-http://localhost:8000}"
BASE="${BASE%/}"
STAMP="$(date +%s)"
PASS=0
FAIL=0

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }

# check <description> <actual> <expected>
check() {
  if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 — got '$2', expected '$3'"; fi
}

# post <session> <message> [simulate-header]
post() {
  if [ -n "${3:-}" ]; then
    curl -s -w '\n%{http_code}' -X POST "$BASE/chat" \
      -H 'Content-Type: application/json' -H "X-Simulate-Failure: $3" \
      -d "{\"session_id\":\"$1\",\"message\":\"$2\"}"
  else
    curl -s -w '\n%{http_code}' -X POST "$BASE/chat" \
      -H 'Content-Type: application/json' \
      -d "{\"session_id\":\"$1\",\"message\":\"$2\"}"
  fi
}

body()   { sed '$d' <<<"$1"; }
status() { tail -n1 <<<"$1"; }

# Minimal JSON field reader, so jq is not required.
field() {
  python -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('<unparseable>'); raise SystemExit
for part in sys.argv[1].split('.'):
    if d is None: break
    d = d.get(part) if isinstance(d, dict) else None
print('null' if d is None else d)
" "$1"
}

printf '\033[1mVerifying %s\033[0m\n' "$BASE"

# ---------------------------------------------------------------- health --
bold '1. GET /health'
H="$(curl -s -w '\n%{http_code}' "$BASE/health")"
check 'status 200' "$(status "$H")" '200'
KEYS="$(body "$H" | field keys_configured)"
check 'status is ok' "$(body "$H" | field status)" 'ok'
check 'cache enabled' "$(body "$H" | field cache_enabled)" 'True'
if [ "${KEYS:-0}" -ge 2 ] 2>/dev/null; then ok "key pool has $KEYS keys"; else bad "key pool has $KEYS keys, need >= 2"; fi

# ------------------------------------------------------ live + cache hit --
bold '2. LIVE_INFERENCE then LLM_CACHE'
Q="What is the capital of France?"
R1="$(post "s-$STAMP-a" "$Q")"
check 'first call 200' "$(status "$R1")" '200'
check 'first call is live' "$(body "$R1" | field metadata.resolution_source)" 'LIVE_INFERENCE'
check 'first call used key_1' "$(body "$R1" | field metadata.key_id)" 'key_1'
L1="$(body "$R1" | field metadata.latency_ms)"
T1="$(body "$R1" | field metadata.input_tokens)"
O1="$(body "$R1" | field metadata.output_tokens)"
REPLY1="$(body "$R1" | field reply)"
printf '        model=%s latency=%sms in=%s out=%s\n' "$(body "$R1" | field metadata.model)" "$L1" "$T1" "$O1"

# Same opening message, brand-new session -> must come from cache.
R2="$(post "s-$STAMP-b" "$Q")"
check 'second call 200' "$(status "$R2")" '200'
check 'second call is cached' "$(body "$R2" | field metadata.resolution_source)" 'LLM_CACHE'
check 'cache hit has null key_id' "$(body "$R2" | field metadata.key_id)" 'null'
check 'cache hit reply is identical' "$(body "$R2" | field reply)" "$REPLY1"
check 'cache hit reuses stored input tokens' "$(body "$R2" | field metadata.input_tokens)" "$T1"
L2="$(body "$R2" | field metadata.latency_ms)"
if [ "${L2:-999999}" -lt "${L1:-0}" ] 2>/dev/null; then
  ok "cache hit faster than live (${L2}ms < ${L1}ms)"
else
  bad "cache hit not faster (${L2}ms vs ${L1}ms)"
fi

bold '3. A different message is never served from cache'
R3="$(post "s-$STAMP-c" "Name one river in Egypt.")"
check 'different message is live' "$(body "$R3" | field metadata.resolution_source)" 'LIVE_INFERENCE'

# ------------------------------------------------------------- failover --
bold '4. Failover via X-Simulate-Failure'
for ST in 429 500; do
  RF="$(post "s-$STAMP-f$ST" "Who answered this one?" "key_1=$ST")"
  check "key_1=$ST returns 200" "$(status "$RF")" '200'
  check "key_1=$ST answered by key_2" "$(body "$RF" | field metadata.key_id)" 'key_2'
  check "key_1=$ST counts 1 fallback" "$(body "$RF" | field metadata.fallback_attempts)" '1'
  RTXT="$(body "$RF" | field reply)"
  if [ -n "$RTXT" ] && [ "$RTXT" != 'null' ]; then ok "key_1=$ST returned a real answer"; else bad "key_1=$ST returned an empty reply"; fi
done

bold '5. Whole pool down -> clean 503, nothing stored'
RD="$(post "s-$STAMP-down" "Everything is on fire" '*=429')"
check 'status 503' "$(status "$RD")" '503'
check 'detail text' "$(body "$RD" | field detail)" 'all providers unavailable'
check 'nothing written to history' "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/sessions/s-$STAMP-down/messages")" '404'

# -------------------------------------------------------- multi-turn ----
bold '6. Multi-turn memory and history'
post "s-$STAMP-m" "My name is Ada Lovelace. Remember it." > /dev/null
RM="$(post "s-$STAMP-m" "What is my name? Reply with just the name.")"
RMTXT="$(body "$RM" | field reply)"
if grep -qi 'ada' <<<"$RMTXT"; then ok "model recalled the name ($RMTXT)"; else bad "model did not recall the name (got: $RMTXT)"; fi

HIST="$(curl -s "$BASE/sessions/s-$STAMP-m/messages")"
check 'history has 4 turns' "$(python -c 'import json,sys; print(len(json.load(sys.stdin)["messages"]))' <<<"$HIST")" '4'
check 'history starts with the user turn' "$(python -c 'import json,sys; print(json.load(sys.stdin)["messages"][0]["role"])' <<<"$HIST")" 'user'
if grep -q 'system' <<<"$HIST"; then bad 'history leaked the system prompt'; else ok 'history excludes the system prompt'; fi

bold '7. Sessions stay separate'
check 'unknown session is 404' "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/sessions/s-nope-$STAMP/messages")" '404'

# ------------------------------------------------------------ validation --
bold '8. Validation returns 422'
check 'blank message' "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/chat" -H 'Content-Type: application/json' -d '{"session_id":"s-v","message":"   "}')" '422'
check 'missing message' "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/chat" -H 'Content-Type: application/json' -d '{"session_id":"s-v"}')" '422'
check 'missing session_id' "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/chat" -H 'Content-Type: application/json' -d '{"message":"hi"}')" '422'
check 'message over 4000 chars' "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/chat" -H 'Content-Type: application/json' -d "{\"session_id\":\"s-v\",\"message\":\"$(printf 'x%.0s' $(seq 1 4001))\"}")" '422'
check 'malformed JSON' "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/chat" -H 'Content-Type: application/json' --data-binary '{not json')" '422'

# ---------------------------------------------------------------- result --
printf '\n\033[1m%s passed, %s failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
