---
title: DingTalk multi-company allowlist
tags:
    - policy
    - expression
    - sources
    - dingtalk
---

This fork can limit DingTalk login to selected companies and departments. The
admin interface creates and binds the required [expression policy](./index.mdx);
operators do not need to write policy code.

## Configure

1. Create one DingTalk OAuth source and keep its default identifier matching
   mode.
2. Assign its authentication and enrollment flows.
3. Open the source and select **DingTalk Allowlist**.
4. Discover a company through DingTalk, or enter its `corpId` manually.
5. Allow the whole company or select one or more departments.
6. Apply the configuration and check the status shown in the panel.

One source can serve multiple companies. Company names are display labels;
access decisions use `corpId` and department IDs. Keep real IDs and secrets out
of committed documentation.

The source requests these scopes automatically:

```text
openid corpid Contact.User.Read
```

## Access behavior

- A DingTalk source without an enabled allowlist denies all DingTalk logins.
- A missing or unknown company ID is denied.
- A restricted company requires at least one matching department.
- A full-company rule does not require department data.
- Login, enrollment, and source linking use the same allowlist.
- Other OAuth sources using a shared flow are not evaluated against this
  DingTalk allowlist.

The panel manages the generated policy. Do not edit its Python expression or
configuration comments manually.

## Protect an application

Bind the managed allowlist policy to an application when that application must
be reached through an allowed DingTalk login. Non-superusers without current
DingTalk allowlist evidence are denied. Superusers remain exempt so local
administrators retain recovery access.

Changing the allowlist invalidates the previous DingTalk evidence. Users must
sign in through DingTalk again before opening a protected application.

Do not bind the policy globally unless every application, including recovery
and administration surfaces, is intended to require DingTalk.

## Identity

Account matching uses DingTalk `unionId` when available and falls back to
`openId`. The visible authentik username is DingTalk `userid`, which is unique
only within a company. Do not enable username or email linking for this source.
