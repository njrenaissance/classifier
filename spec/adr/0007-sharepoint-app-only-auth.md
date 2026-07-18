# ADR-0007 — SharePoint access via app-only (client-credentials) auth

Status: accepted

## Context

`classifier` ingests documents from SharePoint via the Microsoft Graph API, in addition to the local filesystem. It runs as an on-demand batch job ([[0003-cli-batch-interface]]) with no interactive user present at run time. Graph supports two auth models: app-only (application permissions) and delegated (acts as a signed-in user).

## Decision

Authenticate to Microsoft Graph using **app-only / client-credentials** flow — an app registration with application permissions (e.g. `Sites.Read.All`), running unattended.

## Alternatives

- **Delegated (user sign-in)** — acts with a specific user's permissions, narrower access, but requires an interactive sign-in, which does not fit an unattended batch run.

## Tradeoffs

- **Gain:** runs unattended with no interactive login; fits the batch/CLI model cleanly.
- **Give up:** requires an Azure AD app registration and **admin consent**; application permissions are broad (e.g. read access across sites), a larger blast radius than a single delegated user.

## Consequences

- Requires provisioning: app registration, a client secret or certificate, and consented Graph scopes — real setup work to scope during implementation planning.
- Credentials (client secret / cert) must be handled per the project's secrets conventions, not hardcoded.
- Graph app-registration + scopes are a meaningful chunk of v1 work and may warrant their own build issue.
