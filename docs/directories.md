# Directory configuration and local bootstrap

Open **Manage directories** in the administrator desktop. Select a locally approved connection
and a registered realm, then save the source's host, TLS mode, DN/filter and attribute settings.
Only local secret/CA references belong in configuration. The versioned `InstanceBlueprint`
can be downloaded and imported again; it does not approve a different connection or realm.
Updates require the displayed configuration revision. Saving settings does not configure Gitea.

Enable **Directory claim lookup** to configure the read-only search base, login/external-ID
attributes, optional email attribute and direct group search. Choose anonymous read explicitly
or provide both a bind DN and a local credential reference. The password is resolved in memory;
it is never a command argument or exported value. This read-only BindDN lookup is separate from
the unsupported secret-bearing Gitea bootstrap command. An explicit CA is required, referrals
are not followed, and missing/ambiguous results block identity decisions.

LDAP simple authentication is the bounded automated bootstrap path for Gitea **1.27.2**.
Gitea binds with the user's own password at login; no directory bind password is required by
the bootstrap. LDAPS or StartTLS and an explicit trusted CA are required. Install that CA for
the running Gitea service as well as the local bootstrap process. A same-name local Gitea account
must not be adopted as a directory account. The functional user must first authenticate to
Gitea before GitFerry can verify and explicitly bind the resulting native account.

The operator needs supported administrative access to the **target Gitea host**, its actual
configuration, binary, and GitFerry's persistent local state. Run as the Gitea service account
with access to that state. The web API does not expose an arbitrary remote shell or accept
server paths from a transport package. A deployment without this local access must arrange the
bootstrap through its target administrator; it cannot report the source as configured.

```text
gitferry directories --database STATE list
gitferry directories --database STATE bootstrap-plan --instance TARGET --source-name SOURCE \
  --gitea-binary /usr/local/bin/gitea --gitea-config /etc/gitea/app.ini --ca-file /run/trust/directory.pem \
  --work-path /var/lib/gitea --custom-path /var/lib/gitea/custom
gitferry directories --database STATE bootstrap-apply --instance TARGET --source-name SOURCE \
  --gitea-binary /usr/local/bin/gitea --gitea-config /etc/gitea/app.ini --ca-file /run/trust/directory.pem \
  --work-path /var/lib/gitea --custom-path /var/lib/gitea/custom
gitferry directories --database STATE inspect-bootstrap --instance TARGET --source-name SOURCE
```

These paths are illustrative deployment paths, not values supplied by an exported blueprint.
Planning checks the binary version, local files, explicit work/custom directories, stored intent
and source-name collisions. The actual server `ROOT_URL` must match the registered target URL.
Ambient `GITEA_*` and wrapper `CONF_ARG` overrides are removed. The journal binds canonical
execution paths, directory identities, binary/configuration/CA hashes and the installation marker.
Application journals intent before the supported `gitea admin auth add-ldap-simple` call.
Confirmed replay checks the same native source ID/name/type/activation and sends no second
creation. This readback does **not** expose or prove the complete remote filter/DN/CA configuration.
Qualify actual login, negative login and effective repository access separately.

An interrupted or unusable creation result becomes `outcome_unknown`; retrying cannot create
another source. Inspect the durable journal even if the Gitea server is unavailable. Do not
delete the state database or change the source name to bypass this protection. Supported Gitea
administration must establish what happened before recovery. Automatic adoption, replacement,
and resolution based only on a matching name are not implemented.

Existing BindDN blueprints remain readable. Their automatic bootstrap is unsupported because
the pinned Gitea CLI accepts the bind password only as a command argument. Configure this mode
through Gitea's authenticated HTTPS administration UI. This remains an explicit product gap,
not an inference that the full directory requirement has been completed.

The isolated OpenLDAP evidence covers synthetic directory authentication and selected attributes.
It does not qualify Microsoft AD SIDs, nested groups, domain login forms, or every account-disable
policy. A filter-excluded synthetic account is a different claim from native AD account control.
Native Gitea authentication-source and observed directory subject changes invalidate approved
GitFerry account evidence. Each environment and recovery path is qualified separately below.

## Equal directory logins and native Gitea usernames

A Gitea username belongs to one instance-wide namespace. Two authentication sources can contain
an identical directory login, but this does not create two native accounts with the same Gitea
username. The target administrator must explicitly choose distinct native usernames and associate
each with its authentication source and external `login_name`, using supported Gitea administration.
GitFerry does not create those functional accounts or choose an alias automatically.

In Identity administration, select the native **Target login** and its own credential reference.
Then select the reviewed **Configured directory claim source** and **Directory login** separately.
The native `source_id` and `login_name` must match that selection. Review realm, external ID,
DN/OU, groups and native account before approval. Equal login or email text grants no rights.

Gitea's regular account-create API requires a native email field even when the directory record
has no email. Authentication-created accounts can receive Gitea-generated email values. These are
Gitea account prerequisites, not GitFerry identity keys or requirements on directory mail attributes.

## Availability, drift and explicit recovery

An unavailable directory blocks mapped writes while retaining the existing binding and approval.
Restored availability can make the unchanged binding usable again. A changed external identity or
reviewed group set instead creates a durable conflict. Restoring the earlier directory facts does
not erase that conflict. Inspect the retained evidence and events, enter the account's local
credential reference, select **Reopen**, review the pending candidate and explicitly **Approve**.
These steps do not recreate accounts, repeat a confirmed import or redirect an immutable binding.
An actually changed identity cannot be accepted by using Reopen as a replacement operation.

The separate Samba 4.22.11 qualification covers LDAPS with verified CA, real Gitea 1.27.2 login,
missing directory mail, rejected disabled accounts and wrong passwords, plus explicit native aliases.
This is AD-compatible server evidence, not Microsoft Windows AD qualification. Product directory
claims contain direct groups; a successful server-side nested-group query is not transitive product
permission support. The common report records these separately from the OpenLDAP desktop rights flow.


For the qualified Samba target, an already approved binding is rechecked against the directory.
Outage blocks use without replacing the binding. Changed direct groups or a disabled subject create
a persistent conflict; restoring the original facts still requires explicit local review. A user
recreated at the same DN and login has a different SID and cannot reopen the old binding. Native
Gitea login alone does not authorize replacement or grant repository rights. This recovery proof
uses the application services; the complete desktop provisioning/rights flow is OpenLDAP-qualified.
