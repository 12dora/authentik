# DingTalk Login UI Regression Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the DingTalk admin allowlist corpId discovery UI and login page polish: one visible DingTalk entry, an official DingTalk icon, and a fully localized Chinese username/email label.

**Architecture:** This is a narrow UI/static-asset regression fix. The backend is already returning exactly one DingTalk source, so the fix should not change OAuth source discovery or identification-stage source selection unless a later browser verification disproves the current evidence.

**Tech Stack:** Django/authentik flow executor, Lit frontend, `@lit/localize` XLIFF files, static source icons under `web/authentik/sources/`, Docker image build.

---

## Evidence Snapshot

- Running image: `authentik-dingtalk:local`, server/worker digest `sha256:58ee4e22d3943b143eb7c51741d8ad1db05cb0a52a93a0a0b706730326fa327e`.
- Flow API returns exactly one source:

```json
{
  "sources": [
    {
      "name": "钉钉登录",
      "icon_url": "/static/authentik/sources/dingtalk.svg",
      "promoted": false,
      "challenge": {
        "component": "xak-flow-redirect",
        "to": "/source/oauth/login/dingtalk/"
      }
    }
  ]
}
```

- Database confirms exactly one OAuth source and exactly one source bound to `default-authentication-identification`.
- Earlier deployed image: `GET /static/authentik/sources/dingtalk.svg` returned `404`.
- Current local image: `GET /static/authentik/sources/dingtalk.svg` returns `200`, but the SVG is a hand-drawn/approximate blue-circle mark and does not match DingTalk official branding.
- Current browser DOM snapshot shows one DingTalk button and the username/email label renders as `邮箱或用户名`; if the user still sees two buttons or English/mixed labels, verify browser cache, host, and static bundle version before changing backend source discovery.
- Current database check shows one enabled DingTalk OAuth source and one DingTalk source bound to `default-authentication-identification`.
- The flow renderer does not deduplicate sources; if an operator binds two enabled DingTalk sources to an identification stage, the frontend will render both.
- The current admin source page bundle registers `ak-source-oauth-dingtalk-directory` but not `ak-source-oauth-dingtalk-allowlist`.
- Current `web/src/admin/sources/oauth/OAuthSourceViewPage.ts` imports and renders only `DingTalkDirectoryPanel`; it no longer imports or renders `DingTalkAllowlistPanel`.
- `DingTalkDirectoryPanel.ts` uses `sources.oauth.dingtalk-directory.*` localization IDs, but `web/xliff/zh-Hans.xlf` has no matching DingTalk directory entries, so the directory panel renders English in Chinese UI.
- `DingTalkDirectoryPanel.ts` renders sync summary as a plain flex `<ul>` with text-only `<li>` elements (`1 corp sync records`, `1 successful`, `0 failed`, `0 running`). The layout has no stable item width, label/value split, or mobile wrapping guard, which allows misalignment and text overlap.
- Brand `default_locale` is empty. Requests without `Accept-Language` render `Content-Language: en`; requests with `Accept-Language: zh-CN` render `zh-hans`.
- `web/packages/sfe/src/index.ts` still contains hard-coded English placeholders (`Email / Username`, `Password`) for the SFE path.

## Root Cause

1. **Missing allowlist UI:** The directory-service patch added `DingTalkDirectoryPanel` to `OAuthSourceViewPage.ts` but did not preserve the existing `DingTalkAllowlistPanel` import/render path. Result: the corpId discovery UI (`Discover company`) is no longer mounted.
2. **DingTalk icon regression:** `DingTalkType.name = "dingtalk"` makes `SourceType.icon_url()` resolve to `/static/authentik/sources/dingtalk.svg`. The path is correct, but the current asset is an approximate custom drawing rather than the official DingTalk logo/app icon.
3. **Directory i18n missing:** The new DingTalk directory panel introduced strings with `msg(..., { id })`, but the zh-Hans catalog was not updated with those IDs.
4. **Directory summary layout unstable:** The summary list uses inline prose per metric and flex wrapping, so the numbers/labels cannot align consistently and can overlap in narrow card widths or translated text.
5. **Chinese label regression:** `IdentificationStage.ts` builds the label with `Intl.ListFormat` from translated `Email` and `Username`, but the active build/runtime currently renders `Email或Username`. A stable product fix should use a dedicated localized label for the common `email + username` case instead of composing two generic nouns.
6. **Two-button report:** Current runtime evidence does not show duplicate source rows or duplicate flow sources. If the user still sees two entries after a hard reload, treat it as a frontend rendering/cache issue and verify shadow DOM plus static bundle version. Separately, add a real admin-side check because the existing “single visible DingTalk login entry” status item is hard-coded to `good` and would not catch duplicate DingTalk source bindings.

## File Structure

- Modify: `web/authentik/sources/dingtalk.svg`
  - Replace the approximate custom SVG with an official DingTalk logo/app icon asset, keeping the same filename unless the implementation deliberately overrides `DingTalkType.icon_url()`.
- Modify: `web/src/admin/sources/oauth/OAuthSourceViewPage.ts`
  - Render both DingTalk allowlist and DingTalk directory panels for DingTalk sources.
- Modify: `web/src/admin/sources/oauth/DingTalkDirectoryPanel.ts`
  - Add stable summary layout and use explicit i18n IDs for all directory strings, including dynamic summary metrics.
- Modify: `web/xliff/zh-Hans.xlf`
  - Add zh-Hans translations for all `sources.oauth.dingtalk-directory.*` strings.
- Modify: `web/src/flow/stages/identification/IdentificationStage.ts`
  - Use a deterministic localized label for `email + username`.
- Modify: `web/xliff/zh-Hans.xlf`
  - Add/adjust Chinese translation for the dedicated label.
- Modify if needed: `web/packages/sfe/src/index.ts`
  - Replace hard-coded placeholders with localized strings or align the fallback SFE placeholder with the desired Chinese text.
- Modify: `web/src/admin/sources/oauth/DingTalkAllowlistPanelState.ts`
  - Replace the hard-coded single-entry `good` status with a real check or remove the misleading status until the backend can report it.
- Modify if needed: `authentik/stages/identification/stage.py`
  - Add defensive duplicate diagnostics only if a duplicate `challenge.sources` array is reproduced.
- Test: `web/test/unit/...`
  - Add focused tests if an existing test harness covers identification stage labels or source icon rendering.

---

### Task 1: Restore DingTalk Allowlist Tab

**Files:**
- Modify: `web/src/admin/sources/oauth/OAuthSourceViewPage.ts`
- Test: create `web/test/unit/oauth-source-view-page.test.ts` if no existing source-view test is suitable

- [ ] **Step 1: Write the failing test**

Add a focused render/structure test that imports `OAuthSourceViewPage`, creates a DingTalk source, and asserts both panel custom element names appear in the rendered template.

```ts
import { OAuthSourceViewPage } from "#admin/sources/oauth/OAuthSourceViewPage";

import { ProviderTypeEnum } from "@goauthentik/api";

import { describe, expect, it } from "vitest";

describe("OAuthSourceViewPage DingTalk panels", () => {
    it("renders both allowlist and directory panels for DingTalk sources", () => {
        const page = new OAuthSourceViewPage();
        page.source = {
            pk: "source-pk",
            slug: "dingtalk",
            name: "钉钉登录",
            providerType: ProviderTypeEnum.Dingtalk,
        } as never;

        const rendered = String(page.render());

        expect(rendered).toContain("ak-source-oauth-dingtalk-allowlist");
        expect(rendered).toContain("ak-source-oauth-dingtalk-directory");
    });
});
```

Run:

```bash
npm --prefix web test -- --run test/unit/oauth-source-view-page.test.ts
```

Expected before fix: FAIL because `ak-source-oauth-dingtalk-allowlist` is missing.

- [ ] **Step 2: Restore the allowlist import**

Add the missing import next to the directory import:

```ts
import "#admin/sources/oauth/DingTalkAllowlistPanel";
import "#admin/sources/oauth/DingTalkDirectoryPanel";
```

- [ ] **Step 3: Render both DingTalk panels**

In the DingTalk-only block, render allowlist first because corpId discovery/configuration is a prerequisite for directory sync.

```ts
${this.source.providerType === ProviderTypeEnum.Dingtalk
    ? html`
          <div
              role="tabpanel"
              tabindex="0"
              slot="page-dingtalk-allowlist"
              id="page-dingtalk-allowlist"
              aria-label="${msg("DingTalk Allowlist")}"
              class="pf-c-page__main-section pf-m-no-padding-mobile"
          >
              <ak-source-oauth-dingtalk-allowlist
                  .source=${this.source}
              ></ak-source-oauth-dingtalk-allowlist>
          </div>
          <div
              role="tabpanel"
              tabindex="0"
              slot="page-dingtalk-directory"
              id="page-dingtalk-directory"
              aria-label="${msg("DingTalk Directory")}"
              class="pf-c-page__main-section pf-m-no-padding-mobile"
          >
              <div class="pf-l-grid pf-m-gutter">
                  <ak-source-oauth-dingtalk-directory
                      class="pf-l-grid__item pf-m-12-col"
                      .source=${this.source}
                  ></ak-source-oauth-dingtalk-directory>
              </div>
          </div>
      `
    : nothing}
```

- [ ] **Step 4: Verify the page bundle contains both custom elements**

After frontend build/image build, run:

```bash
docker exec easyauth-authentik-server-1 sh -lc \
  "grep -R --exclude='*.map' 'ak-source-oauth-dingtalk-allowlist\\|ak-source-oauth-dingtalk-directory' -n /web/dist/src/admin/sources /web/dist/admin | head -20"
```

Expected: both custom element names are present in non-map JS.

### Task 2: Add DingTalk Directory zh-Hans i18n

**Files:**
- Modify: `web/src/admin/sources/oauth/DingTalkDirectoryPanel.ts`
- Modify: `web/xliff/zh-Hans.xlf`
- Test: `web/test/unit/dingtalk-directory-panel.test.ts`

- [ ] **Step 1: Write a failing coverage check for directory message IDs**

Add a small test or static assertion that lists the required DingTalk directory message IDs and verifies each one exists in `web/xliff/zh-Hans.xlf`.

Required IDs:

```ts
const DINGTALK_DIRECTORY_ZH_HANS_IDS = [
    "sources.oauth.dingtalk-directory.title",
    "sources.oauth.dingtalk-directory.summary.total",
    "sources.oauth.dingtalk-directory.summary.success",
    "sources.oauth.dingtalk-directory.summary.error",
    "sources.oauth.dingtalk-directory.summary.running",
    "sources.oauth.dingtalk-directory.summary.unknown",
    "sources.oauth.dingtalk-directory.status.success",
    "sources.oauth.dingtalk-directory.status.error",
    "sources.oauth.dingtalk-directory.status.running",
    "sources.oauth.dingtalk-directory.status.unknown",
    "sources.oauth.dingtalk-directory.refresh",
    "sources.oauth.dingtalk-directory.corp-id",
    "sources.oauth.dingtalk-directory.sync-now",
    "sources.oauth.dingtalk-directory.sync.corp-id-required",
    "sources.oauth.dingtalk-directory.sync.queued",
    "sources.oauth.dingtalk-directory.empty.title",
    "sources.oauth.dingtalk-directory.empty.body",
    "sources.oauth.dingtalk-directory.table.corp-id",
    "sources.oauth.dingtalk-directory.table.status",
    "sources.oauth.dingtalk-directory.table.started",
    "sources.oauth.dingtalk-directory.table.finished",
    "sources.oauth.dingtalk-directory.table.counters",
    "sources.oauth.dingtalk-directory.table.error",
    "sources.oauth.dingtalk-directory.docs",
];
```

Run:

```bash
npm --prefix web test -- --run test/unit/dingtalk-directory-panel.test.ts
```

Expected before fix: FAIL because `zh-Hans.xlf` does not contain the directory IDs.

- [ ] **Step 2: Give every dynamic summary string an explicit ID**

Replace the summary strings:

```ts
msg(str`${summary.total} corp sync records`)
msg(str`${summary.success} successful`)
msg(str`${summary.error} failed`)
msg(str`${summary.running} running`)
msg(str`${summary.unknown} unknown`)
```

with explicit IDs:

```ts
msg(str`${summary.total} corp sync records`, {
    id: "sources.oauth.dingtalk-directory.summary.total",
})
msg(str`${summary.success} successful`, {
    id: "sources.oauth.dingtalk-directory.summary.success",
})
msg(str`${summary.error} failed`, {
    id: "sources.oauth.dingtalk-directory.summary.error",
})
msg(str`${summary.running} running`, {
    id: "sources.oauth.dingtalk-directory.summary.running",
})
msg(str`${summary.unknown} unknown`, {
    id: "sources.oauth.dingtalk-directory.summary.unknown",
})
```

Also change the card title to:

```ts
msg("DingTalk directory sync", {
    id: "sources.oauth.dingtalk-directory.title",
})
```

- [ ] **Step 3: Add zh-Hans translations**

Add the corresponding `trans-unit` entries to `web/xliff/zh-Hans.xlf`. Use clear operator-facing Chinese:

```xml
<trans-unit id="sources.oauth.dingtalk-directory.title">
  <source>DingTalk directory sync</source>
  <target>钉钉通讯录同步</target>
</trans-unit>
<trans-unit id="sources.oauth.dingtalk-directory.summary.total">
  <source><x id="0" equiv-text="${summary.total}"/> corp sync records</source>
  <target><x id="0" equiv-text="${summary.total}"/> 条企业同步记录</target>
</trans-unit>
<trans-unit id="sources.oauth.dingtalk-directory.summary.success">
  <source><x id="0" equiv-text="${summary.success}"/> successful</source>
  <target><x id="0" equiv-text="${summary.success}"/> 条成功</target>
</trans-unit>
<trans-unit id="sources.oauth.dingtalk-directory.summary.error">
  <source><x id="0" equiv-text="${summary.error}"/> failed</source>
  <target><x id="0" equiv-text="${summary.error}"/> 条失败</target>
</trans-unit>
<trans-unit id="sources.oauth.dingtalk-directory.summary.running">
  <source><x id="0" equiv-text="${summary.running}"/> running</source>
  <target><x id="0" equiv-text="${summary.running}"/> 条运行中</target>
</trans-unit>
```

Include the remaining required IDs from Step 1.

- [ ] **Step 4: Verify Chinese bundle contains directory translations**

Run:

```bash
npm --prefix web run tsc
rg "sources.oauth.dingtalk-directory.title|钉钉通讯录同步" web/src web/xliff
```

Expected: TypeScript passes and the zh-Hans catalog contains the new DingTalk directory translations.

### Task 3: Fix DingTalk Directory Summary Layout

**Files:**
- Modify: `web/src/admin/sources/oauth/DingTalkDirectoryPanel.ts`
- Test: `web/test/unit/dingtalk-directory-panel.test.ts`

- [ ] **Step 1: Add a layout regression test**

Add a rendering-oriented test or snapshot assertion that `renderSummary()` emits structured metric items with separate value and label spans/classes.

Expected structure:

```html
<ul class="ak-dingtalk-directory-summary">
  <li class="ak-dingtalk-directory-summary-item">
    <span class="ak-dingtalk-directory-summary-value">1</span>
    <span class="ak-dingtalk-directory-summary-label">企业同步记录</span>
  </li>
</ul>
```

- [ ] **Step 2: Split metric value and label**

Replace the prose-only summary items with structured metric items:

```ts
private renderSummaryItem(value: number, label: string): TemplateResult {
    return html`<li class="ak-dingtalk-directory-summary-item">
        <span class="ak-dingtalk-directory-summary-value">${value}</span>
        <span class="ak-dingtalk-directory-summary-label">${label}</span>
    </li>`;
}
```

Then render labels separately:

```ts
${this.renderSummaryItem(
    summary.total,
    msg("Corp sync records", { id: "sources.oauth.dingtalk-directory.summary.total.label" }),
)}
${this.renderSummaryItem(
    summary.success,
    msg("Successful", { id: "sources.oauth.dingtalk-directory.summary.success.label" }),
)}
${this.renderSummaryItem(
    summary.error,
    msg("Failed", { id: "sources.oauth.dingtalk-directory.summary.error.label" }),
)}
${this.renderSummaryItem(
    summary.running,
    msg("Running", { id: "sources.oauth.dingtalk-directory.summary.running.label" }),
)}
```

- [ ] **Step 3: Stabilize CSS**

Replace the current summary CSS with a grid that cannot overlap:

```css
.ak-dingtalk-directory-summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
    gap: var(--pf-global--spacer--sm);
    margin: 0;
    padding: 0;
    list-style: none;
}

.ak-dingtalk-directory-summary-item {
    min-width: 0;
    padding: var(--pf-global--spacer--sm);
    border: 1px solid var(--pf-global--BorderColor--100);
    border-radius: 4px;
}

.ak-dingtalk-directory-summary-value,
.ak-dingtalk-directory-summary-label {
    display: block;
    overflow-wrap: anywhere;
}

.ak-dingtalk-directory-summary-value {
    font-size: var(--pf-global--FontSize--xl);
    font-weight: var(--pf-global--FontWeight--bold);
    line-height: 1.2;
}

.ak-dingtalk-directory-summary-label {
    color: var(--pf-global--Color--200);
    font-size: var(--pf-global--FontSize--sm);
}
```

- [ ] **Step 4: Browser verify layout**

After deployment, inspect the DingTalk source page at desktop width and a narrow/mobile width.

Expected:
- `1 企业同步记录`, `1 成功`, `0 失败`, `0 运行中` appear as separate metric cells
- metric values and labels do not overlap
- wrapping keeps cards aligned and readable

### Task 4: Replace DingTalk Static Icon With Official Asset

**Files:**
- Modify: `web/authentik/sources/dingtalk.svg`
- Modify if needed: `authentik/sources/oauth/types/dingtalk.py`
- Test if URL changes: `authentik/sources/oauth/tests/test_type_dingtalk.py`

- [ ] **Step 1: Source the official icon**

Use DingTalk's official design/material page as the primary source:

- Official page: `https://open.dingtalk.com/document/design`
- The page advertises DingTalk VIS/logo material download and DingTalk App icon download.
- Do not use third-party icon aggregators unless official assets are unavailable and the fallback is explicitly documented.

Preferred implementation:

- If the official download provides SVG, optimize that SVG and save it as `web/authentik/sources/dingtalk.svg`.
- If the official download provides PNG only, either:
  - embed the official PNG inside a same-path SVG wrapper so the existing `/static/authentik/sources/dingtalk.svg` contract remains unchanged, or
  - add the PNG under `web/authentik/sources/`, override `DingTalkType.icon_url()` to return the PNG path, and update the path assertion test.

Do not keep the current blue-circle hand-drawn approximation.

- [ ] **Step 2: Verify static asset locally**

Run:

```bash
curl -fsSI http://localhost:19000/static/authentik/sources/dingtalk.svg
```

Expected after rebuild/redeploy: `HTTP/1.1 200 OK`.

- [ ] **Step 3: Verify asset provenance and visual match**

Record the official source URL in the commit or PR notes. After deployment, verify:

- the rendered login button uses the official DingTalk icon, not the old blue-circle approximation;
- the image loads from `/static/authentik/sources/dingtalk.svg` or the intentionally updated static URL;
- browser devtools/network shows no 404 for the DingTalk icon;
- a hard refresh does not show the old asset.

### Task 5: Make Email/Username Label Deterministically Chinese

**Files:**
- Modify: `web/src/flow/stages/identification/IdentificationStage.ts`
- Modify: `web/xliff/zh-Hans.xlf`

- [ ] **Step 1: Add a dedicated label helper**

Replace the current direct composition:

```ts
const label = OR_LIST_FORMATTERS.format(fields.map((f) => UI_FIELDS[f]));
```

with:

```ts
const label = this.renderUserFieldLabel(fields);
```

Add this method inside `IdentificationStage`:

```ts
protected renderUserFieldLabel(fields: UserFieldsEnum[]) {
    const normalized = [...fields].sort();
    if (
        normalized.length === 2 &&
        normalized.includes(UserFieldsEnum.Email) &&
        normalized.includes(UserFieldsEnum.Username)
    ) {
        return msg("Email or username");
    }
    return OR_LIST_FORMATTERS.format(normalized.map((field) => UI_FIELDS[field]));
}
```

- [ ] **Step 2: Add the zh-Hans translation**

Add or update the XLIFF unit:

```xml
<trans-unit id="sources.login.email-or-username">
  <source>Email or username</source>
  <target>邮箱或用户名</target>
</trans-unit>
```

If the localization tooling rewrites IDs, run the repo's localization extraction/update command instead of hand-maintaining a noncanonical ID.

- [ ] **Step 3: Add a focused test**

If an identification-stage test exists, add a case that renders `userFields: ["email", "username"]` under `zh-Hans` and asserts the visible label is `邮箱或用户名`.

Run:

```bash
npm --prefix web test -- --run test/unit/<identification-stage-test>.test.ts
```

Expected: the new test fails before the code change and passes after.

### Task 6: Align SFE Fallback Placeholders

**Files:**
- Modify: `web/packages/sfe/src/index.ts`

- [ ] **Step 1: Replace hard-coded English fallback copy**

If the deployed flow can enter the SFE path, replace:

```ts
placeholder="Email / Username"
placeholder="Password"
```

with localized strings or the desired fallback copy:

```ts
placeholder=${"邮箱或用户名"}
placeholder=${"密码"}
```

Preferred long-term fix: wire SFE to the same localization helper instead of hard-coding Chinese in TypeScript.

- [ ] **Step 2: Verify SFE bundle**

Run:

```bash
npm --prefix web run tsc
```

Expected: TypeScript passes.

### Task 7: Rebuild, Deploy, and Browser-Verify

**Files:**
- No source edits beyond Tasks 1-3.

- [ ] **Step 1: Build frontend and image**

Run:

```bash
npm --prefix web run tsc
DOCKER_IMAGE=authentik-dingtalk:local make docker
```

Expected: build completes without errors.

- [ ] **Step 2: Recreate server and worker**

Run:

```bash
docker compose -f /Users/konata/.local/share/easyauth/authentik/compose.yml up -d --force-recreate server worker
```

Expected: server and worker use the new `authentik-dingtalk:local` digest and become healthy.

- [ ] **Step 3: Verify API and static asset**

Run:

```bash
curl -fsS http://localhost:19000/-/health/ready/
curl -fsSI http://localhost:19000/static/authentik/sources/dingtalk.svg
curl -fsS -H 'Accept: application/json' \
  'http://localhost:19000/api/v3/flows/executor/default-authentication-flow/?query=next%3D%252F'
```

Expected:
- readiness returns `200`
- icon returns `200`
- flow `sources` array contains exactly one `钉钉登录` item
- admin source page bundle contains both `ak-source-oauth-dingtalk-allowlist` and `ak-source-oauth-dingtalk-directory`
- zh-Hans bundle/catalog contains DingTalk directory translations

- [ ] **Step 4: Verify in browser**

Open `http://localhost:19000/` with a hard reload or cache disabled.

Expected:
- DingTalk OAuth Source admin page shows the DingTalk Allowlist panel with `Discover company`, `Save and apply`, and `Refresh status`
- DingTalk OAuth Source admin page also shows the DingTalk Directory panel
- DingTalk Directory panel is localized in Chinese under zh-Hans
- DingTalk Directory summary metrics are aligned and do not overlap at desktop and narrow widths
- exactly one DingTalk login button
- DingTalk icon is visible and not broken
- username/email label is `邮箱或用户名` in Chinese

## Rollback

If the new image still shows duplicate buttons but the flow API still returns one source, clear browser cache/service-worker state and compare the loaded `FlowInterface-*.js` URL against the latest image. If the flow API returns two sources, rollback the source/stage configuration rather than changing frontend rendering.
