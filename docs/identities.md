# Local identity administration

Use **Identity administration** in the authenticated desktop application to inspect and decide
bindings. Register a realm with its own stable external marker; a realm is not a Gitea instance.
Choose a locally approved target connection and check an existing, non-administrator account.
Review the source realm, login, external identifier/SID and OU alongside the native target ID.
Email is optional and informational. Equal names or email addresses never select an account.

Submit the proposal, then explicitly approve or reject it. Candidate history survives restart
and records the local actor and revision. A conflict does not grant a binding. Reopen a rejected
or conflicted proposal only after reviewing the cause; GitFerry checks the target account again.
An existing binding cannot be redirected. A changed account identity requires investigation,
not a same-name replacement.

Issue-author, comment-author and team-member indicators describe why a binding is needed.
Approval alone does not create accounts, add memberships or write metadata. Return to the
package's local prerequisites and plan review to select the intended work. Metadata execution
still requires the appropriate local author credential reference. Technical administrators
are not valid functional authors or team members.

The host CLI uses the same application core:

```text
gitferry identities --database STATE list
gitferry identities --database STATE register-realm --input realm.json
gitferry identities --database STATE check-account --input account.json
gitferry identities --database STATE propose --input candidate.json
gitferry identities --database STATE decide --candidate-id ID --input decision.json
gitferry identities --database STATE events --candidate-id ID
```

Request fields match `/api/v1/identities` in the generated API schema. Decision documents carry
`expected_revision`, `decision` (`approved`, `rejected`, or `pending` for explicit reopening),
`profile_revision`, and an optional `credential_ref`. Secret values must never be placed in
request documents. CLI authority is local host access; browser authority is an authenticated
administrator session with CSRF protection.

Native account verification does not establish LDAP or Active Directory behavior. Directory
bootstrap, directory claim refresh and real login/access qualification are tracked separately
in #13–#16. Full 0.3 acceptance remains open until these product flows pass.
