# Capsule policy model

## Principle

Deny by default. Permission is granted by an explicit rule, never inferred from
absence of prohibition. Every gate returns a `Decision` and appends it to the
audit log — including allows, so a clean run is still evidence.

## Gates

### License gate — `can_reconstruct(record)`

| License class | Reconstruct | Reason |
|---|---|---|
| `apache-2.0` | allow | permits derivative works; attribution carried forward |
| `proprietary-restricted` | **deny** | forbids extraction and derivative works |
| `unknown` | **deny** | undetermined; deny by default |

Classification reads `LICENSE.txt` and looks for markers, not full legalese:
an "ADDITIONAL RESTRICTIONS" / "Create derivative works" block means restricted;
an Apache header means Apache; no file, or neither marker, means unknown.

Indexing is *not* gated by license. Recording that a skill exists, what it is
for, and what triggers it is metadata, not a derivative work. Only rebuilding is
gated.

**Override.** `Policy(allow_restricted_reconstruction=True)` flips the restricted
row to allow, marks the decision `requires_approval`, and logs it. It exists for
operators who hold rights Capsule cannot verify from the filesystem. It is off by
default and should stay off unless someone has actually checked.

### Path gate — `can_write(path)`

Read-only roots (`/mnt/skills`, `/mnt/user-data/uploads`, `/mnt/transcripts`)
deny writes. Writable roots (`/home/claude`, `/mnt/user-data/outputs`) allow
them. Everything else denies: being outside the read-only set is not permission.

Paths are resolved before comparison, so `../` traversal cannot escape.

### Action gate — `check_action(action)`

- read-class (`index`, `read`, `route`, `validate`) — allow
- write-class (`reconstruct`, `package`, `write`) — allow, then gated per path
- risky (`delete`, `overwrite`, `network`, `exec`) — deny, approval required
- anything unrecognised — deny

## Overwrite

`reconstruct()` refuses to replace an existing pack unless `overwrite=True`.
A refused reconstruction leaves no partial artifacts behind.

## Audit format

```
ALLOW reconstruct:paint -- Apache-2.0 permits derivative works; attribution carried forward
DENY  reconstruct:docx -- license forbids extraction and derivative works
DENY  write:/mnt/skills/public/docx -- path is under read-only root /mnt/skills
DENY  reconstruct:morning -- license could not be determined; deny by default
```

`policy.audit_text()` for humans, `policy.audit_json()` for machines. Each entry
carries action, subject, verdict, reason, approval flag and timestamp.

## Conflict resolution order

1. nearest scoped instruction
2. active policy
3. selected skill pack
4. repository docs
5. condensed global index
6. general fallback behavior
