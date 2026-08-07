# Tool budget and recovery

Declare a bounded route before the first call. Budgets count public tool calls, not internal
plugin work:

| Route | Maximum calls |
| --- | ---: |
| simple read or write | 1 |
| targeted search plus operation | 2 |
| correction or undo | 2 |
| estimate preview | 2 |
| confirmation commit | 1 |

An estimate route normally uses one combined preview; its second call is reserved for one
schema-proved field repair, not another independent estimate. Only an explicit returned cursor
or continuation handle that proves declared progress may extend a read route beyond its base
budget. A cursor page is continuation of the same route, not permission to probe other tools.

Each next action must add named evidence: a cursor page, one structured retryable field repair,
a targeted identity result, a refreshed stale handle, or an uncertain-write status result.
Track capability, tool, action, normalized arguments, and error signature privately. Never
repeat the same error fingerprint. Two different failing fingerprints exhaust the route even
when unused budget remains.

`ok:true` with a terminal outcome ends immediately. A terminal error also ends immediately.
Do not use Exec, shell, SQL, direct file reads, report-file traversal, or broad inventory scans
as fallback for a failed Diet capability. In particular, preference or learned-rule retrieval
failure stops without Exec or file search. State that the requested fact could not be read and
that nothing was changed; do not expose internal maintenance commands.

For an uncertain write outcome, use only the declared status-verification action and never
replay the mutation first. For `DATABASE_INTEGRITY_ERROR`, follow the main Skill breaker.
Changing tools is allowed only for a visible Schema-verified equivalent capability, never to
route around a business result or gain extra budget.

## Host-run write gate

The host's current inbound prompt and run id are the write authority. Queries allow only read
actions. Missing run identity, an ended run, or ambiguous non-preview intent fails closed.
Multi-domain business writes are blocked before the first mutation until a real atomic
cross-domain transaction exists. A zero-business-write preview may be created for a vague
continuation, but its eventual commit must consume the live handle in a later pure-confirmation
turn.

Contextual “补记” can authorize one create domain; the first allowed business domain locks the
run, so a second domain cannot partially commit. Contextual corrections require an opaque target
handle. Two terminal failures in one run open the circuit for all remaining Diet calls.
