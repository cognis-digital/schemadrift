# Demo 01 - Detecting breaking schema drift in an events feed

A data producer publishes a `users` export. Yesterday's baseline and today's
current snapshot are shipped as JSON. SCHEMADRIFT infers both schemas, diffs
them, and flags **breaking** changes that would silently break downstream
consumers and dashboards.

## Files

- `users_baseline.json` - yesterday's snapshot (the agreed-upon shape)
- `users_current.json`  - today's snapshot (drifted)
- `users_contract.json` - the data contract consumers rely on

## What drifted

Between baseline and current:

1. `signup_ts` changed type `int` (epoch) -> `string` (ISO date)  => **BREAKING**
2. `country` field was **removed**                                => **BREAKING**
3. `is_premium` was added                                         => additive (safe)
4. `age` became nullable (a null slipped in)                      => nullability drift

## Run it

```sh
# 1. Inspect the inferred schema of either file
python -m schemadrift infer demos/01-basic/users_current.json

# 2. Detect drift (exit code 3 = breaking drift, 2 = non-breaking, 0 = none)
python -m schemadrift drift \
    demos/01-basic/users_baseline.json \
    demos/01-basic/users_current.json

# 3. Same, as machine-readable JSON for CI
python -m schemadrift --format json drift \
    demos/01-basic/users_baseline.json \
    demos/01-basic/users_current.json

# 4. Enforce the data contract against the current file (exit 2 on violations)
python -m schemadrift contract \
    demos/01-basic/users_current.json \
    --contract demos/01-basic/users_contract.json
```

## Expected

- `drift` reports 1 added, 1 removed, 1 type change (BREAKING), 1 nullability
  change, and exits **3**.
- `contract` fails: `signup_ts` is the wrong type, `country` is missing
  (required), and `age` has a null where the contract allows it but the type
  check still passes. Exits **2**.

Wire the non-zero exit codes into CI to block a deploy when a producer ships a
breaking change.
