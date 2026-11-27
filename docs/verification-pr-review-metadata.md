# Pull-request metadata qualification

This qualification is intentionally separate from the normal issue/comment transfer proof. It
uses the live Gitea 28.4.0 `/swagger.v1.json` contract and synthetic objects in the selected
integration repository. Inventory support does not imply transfer support.

## Field and relationship result

| Class | Native identity and parent | Request fields relevant to transfer | Response-only facts | First import | Return/update | Deletion |
| --- | --- | --- | --- | --- | --- | --- |
| Pull request | global PR ID plus repository issue index; head/base ref and SHA | create: `head`, `base`, `title`, `body`, labels/reviewers; patch: title/body/state/base and `content_version` | native author, IDs, created/updated/merged times, merge state and commit | Blocked: portable head/base repository/ref mapping and native-author fidelity are not implemented | Blocked: no proven atomic version precondition; no PR baseline adapter | Not supported |
| Review | global review ID; parent PR; commit ID | body, event, commit ID and nested inline comments | native reviewer, submitted/updated time, state, stale/dismissed | Blocked: mapped reviewer, exact commit and dependent PR must all be established | Arbitrary review/body update is not exposed; submit/dismiss are state transitions, not a general update | API deletion applies to pending review state only and is not portable deletion fidelity |
| Inline review comment | global comment ID; parent review and therefore parent PR; path and diff position | only nested review creation or a new reply; path, body, old/new position | native author, review ID, commit IDs, created/updated time and current/original position | Blocked: parent review and exact diff position must be recreated first | No PATCH route for the existing inline comment | No DELETE route for the existing inline comment |
| Timeline/system event | global timeline ID where supplied; parent issue or PR; ordered API result | none | event-specific response union, author and timestamps | Not supported: timeline endpoint is read-only | Not supported | Not supported |
| Reaction | natural key of parent, native author, content and created time; API supplies no ID | content only | author and server creation time | Blocked: no native ID, historical time cannot be supplied, and local author approval would still be required | Set-like create/delete operation, not an object PATCH | API delete exists, but package absence never authorizes deletion and no transfer adapter enables it |

Two equal-body reviews or review comments remain distinct because their global native IDs and
typed parents differ. Body changes are facts, not identity. Normal issue comments, PR conversation
comments, reviews and inline review comments remain different inventory classes.

## Runtime evidence

`tests/integration/content_inventory_probe.py` creates a real PR, PR conversation comment, review
and inline review comment, then reads timelines and reactions. It verifies typed parents, distinct
namespaces, direct counts, read-only collection and an unchanged control repository.

`tests/integration/openapi_migration_probe.py` records the exact request/response properties and
available methods from both running Gitea instances. The common report fails if required fields,
route methods or the explicit support classification disappear. The existing server-side repository
migration probe remains an initial-import experiment; it is not treated as a single-object return
path.

The desktop inventory and plan review render these classes and the package limitations before any
write. GitFerry does not create operations for the blocked classes, so restart, replay or a new
package ID cannot bypass the block. No target POST/PATCH/DELETE is attempted for them.

## Remaining limits

- No native historical author or timestamp is fabricated.
- No general PR/ref mapping, review-position translation or merge reconstruction is implemented.
- No atomic server-side conditional PR/review update is claimed.
- Timeline ordering is observed from the API response only; it is not recreated.
- Reactions are inventoried with a natural key, not misrepresented as stable native-ID objects.
- Repository migration still needs live source/target access and is unsuitable for incremental
  return of individual objects.
