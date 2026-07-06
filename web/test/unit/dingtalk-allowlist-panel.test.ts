import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const allowlistPanel = readFileSync(
    resolve(import.meta.dirname, "../../src/admin/sources/oauth/DingTalkAllowlistPanel.ts"),
    "utf8",
);

const zhHans = readFileSync(resolve(import.meta.dirname, "../../xliff/zh-Hans.xlf"), "utf8");

describe("DingTalkAllowlistPanel localization and controls", () => {
    it("has Simplified Chinese translations for every DingTalk allowlist message id", () => {
        const ids = Array.from(
            allowlistPanel.matchAll(/id:\s*"([^"]*sources\.oauth\.dingtalk-allowlist[^"]*)"/gu),
            (match) => match[1],
        );

        const uniqueIds = new Set(ids);
        expect(uniqueIds.size).toBeGreaterThan(20);

        for (const id of uniqueIds) {
            expect(zhHans).toContain(`<trans-unit id="${id}">`);
        }
    });

    it("renders an editable label field but keeps corpId read-only inside configured rows", () => {
        expect(allowlistPanel).toContain(
            "sources.oauth.dingtalk-allowlist.company.label.aria-label",
        );
        expect(allowlistPanel).not.toContain(
            "sources.oauth.dingtalk-allowlist.company.corp-id.aria-label",
        );
    });

    it("renders loaded department checkboxes that synchronize with the department input", () => {
        expect(allowlistPanel).toContain("toggleLoadedDepartment(");
        expect(allowlistPanel).toContain("buildDingTalkDepartmentTreeRows(");
        expect(allowlistPanel).toContain('.indeterminate=${row.selection === "indeterminate"}');
        expect(allowlistPanel).not.toContain("toggleDepartment(");
    });

    it("renders bulk loaded-department selection controls", () => {
        expect(allowlistPanel).toContain("selectAllLoadedDepartments(");
        expect(allowlistPanel).toContain("invertLoadedDepartments(");
        expect(allowlistPanel).toContain("sources.oauth.dingtalk-allowlist.departments.select-all");
        expect(allowlistPanel).toContain("sources.oauth.dingtalk-allowlist.departments.invert");
    });

    it("normalizes discovered company labels from DingTalk corp name fields", () => {
        expect(allowlistPanel).toContain("payload.companyName");
        expect(allowlistPanel).toContain("payload.corp_name");
        expect(allowlistPanel).toContain("payload.corpName");
    });

    it("defaults a discovered company to restricted so an inattentive save cannot grant org-wide access", () => {
        expect(allowlistPanel).toContain(
            "this.upsertCompany(result.corpId, result.label || result.corpId, false, []);",
        );
        // The old allow-all default is gone.
        expect(allowlistPanel).not.toContain(
            "this.upsertCompany(result.corpId, result.label || result.corpId, true, []);",
        );
    });

    it("warns on every full-company row that it grants org-wide access", () => {
        expect(allowlistPanel).toContain(
            "sources.oauth.dingtalk-allowlist.company.allow-all.warning",
        );
    });

    it("renders a first-load spinner until the initial status refresh settles", () => {
        expect(allowlistPanel).toContain("private loaded = false;");
        expect(allowlistPanel).toContain("this.loaded = true;");
        expect(allowlistPanel).toContain("sources.oauth.dingtalk-allowlist.status.loading");
        expect(allowlistPanel).toContain("<ak-empty-state loading");
    });

    it("surfaces companies detected on a shared flow read-only instead of prefilling the editable model", () => {
        // The foreign shared-flow config is stored separately and never assigned to this.model.
        expect(allowlistPanel).toContain("private detectedSharedConfig?");
        expect(allowlistPanel).toContain("sources.oauth.dingtalk-allowlist.detected.title");
        expect(allowlistPanel).toContain("adoptDetectedSharedConfig(");
        expect(allowlistPanel).toContain(
            "this.detectedSharedConfig = dingTalkAllowlistModelFromStoredConfig(config)",
        );
    });

    it("guards every editable text input against mid-composition IME rewrites", () => {
        expect(allowlistPanel).toContain("@compositionstart=${this.startComposition}");
        expect(allowlistPanel).toContain("handleCompositionEnd(");
        expect(allowlistPanel).toContain("handleComposedInput(");
        expect(allowlistPanel).toContain("if (this.composing) {");
    });

    it("associates the manual company labels with their inputs via for/id", () => {
        expect(allowlistPanel).toContain('for="dingtalk-manual-corp-id"');
        expect(allowlistPanel).toContain('id="dingtalk-manual-corp-id"');
        expect(allowlistPanel).toContain('for="dingtalk-manual-label"');
        expect(allowlistPanel).toContain('id="dingtalk-manual-label"');
    });

    it("gives every department-tree checkbox an accessible name and PatternFly styling", () => {
        expect(allowlistPanel).toContain(
            "sources.oauth.dingtalk-allowlist.department.checkbox.aria-label",
        );
        // aria-level is only meaningful on treeitem/row/heading roles, not a checkbox.
        expect(allowlistPanel).not.toContain("aria-level=");
        expect(allowlistPanel).toContain('class="pf-c-check__input"');
    });

    it("styles the allow-full-company toggle as a PatternFly switch", () => {
        expect(allowlistPanel).toContain('class="pf-c-switch"');
        expect(allowlistPanel).toContain('class="pf-c-switch__input"');
    });

    it("submits the manual company and department inputs on Enter", () => {
        expect(allowlistPanel).toContain("handleSubmitKey(");
        expect(allowlistPanel).toContain("event.isComposing");
    });

    it("drops per-corp state when a company is removed so a re-add starts clean", () => {
        expect(allowlistPanel).toContain("omitRecordKey(this.fetchedDepartments, corpId)");
        expect(allowlistPanel).toContain("omitRecordKey(this.departmentInputs, corpId)");
    });

    it("closes the discovery popup itself when finishing discovery", () => {
        expect(allowlistPanel).toContain("this.discoveryPopup?.close();");
    });

    it("pages through every policy binding before creating or deleting managed bindings", () => {
        expect(allowlistPanel).toContain("listAllPolicyBindings(");
        expect(allowlistPanel).toContain("page >= response.pagination.totalPages");
        expect(allowlistPanel).toContain("this.listAllPolicyBindings({ policy: policy.pk })");
    });

    it("paginates and filters the loaded department tree", () => {
        expect(allowlistPanel).toContain("filterDingTalkDepartmentTreeRows(");
        expect(allowlistPanel).toContain("dingtalkDepartmentPageWindow(");
        expect(allowlistPanel).toContain("renderDepartmentPager(");
        expect(allowlistPanel).toContain(
            "sources.oauth.dingtalk-allowlist.departments.filter.placeholder",
        );
    });

    it("clears the discovery popup reference when the popup is closed manually", () => {
        expect(allowlistPanel).toContain("finishDiscovery(");
        expect(allowlistPanel).toContain("this.discoveryPopup.closed");
        expect(allowlistPanel).toContain("setInterval(");
    });

    it("shows an unsaved-changes banner when the local allowlist is dirty", () => {
        expect(allowlistPanel).toContain("private dirty = false;");
        expect(allowlistPanel).toContain("renderUnsavedChanges(");
        expect(allowlistPanel).toContain("sources.oauth.dingtalk-allowlist.unsaved.title");
    });

    it("reports refresh failures through the button result and an alert instead of a silent success", () => {
        expect(allowlistPanel).toContain("refreshStatusAction(");
        expect(allowlistPanel).toContain("this.refreshStatusAction()");
        expect(allowlistPanel).toContain("sources.oauth.dingtalk-allowlist.status.refresh-failed");
        // Partial failures are surfaced as an alert, not just a heading.
        expect(allowlistPanel).toContain("</ak-alert>");
    });

    it("binds checkboxes through the checked property so programmatic state stays visible", () => {
        expect(allowlistPanel).toContain(".checked=${company.allowAll}");
        expect(allowlistPanel).toContain('.checked=${row.selection === "checked"}');
        expect(allowlistPanel).not.toContain("?checked=");
    });

    it("binds the manual company inputs through the value property so they clear after adding", () => {
        expect(allowlistPanel).toContain(".value=${this.manualCorpId}");
        expect(allowlistPanel).toContain(".value=${this.manualLabel}");
    });

    it("keeps loading departments read-only instead of merging them into the allowlist", () => {
        expect(allowlistPanel).not.toContain("mergeLoadedDingTalkDepartmentInput");
        expect(allowlistPanel).toContain("normalizeDingTalkDepartments(response.departments)");
    });

    it("only accepts discovery messages from its own popup with the backend marker fields", () => {
        expect(allowlistPanel).toContain("event.source !== this.discoveryPopup");
        expect(allowlistPanel).toContain("DINGTALK_DISCOVERY_MESSAGE_SOURCE");
        expect(allowlistPanel).toContain("DINGTALK_DISCOVERY_MESSAGE_CONTEXT");
        expect(allowlistPanel).toContain("record.ok === false");
    });

    it("opens the discovery popup synchronously before awaiting the start endpoint", () => {
        const popupIndex = allowlistPanel.indexOf("window.open(");
        const startIndex = allowlistPanel.indexOf(
            "sourcesOauthDingtalkAllowlistDiscoverStartCreate",
        );
        expect(allowlistPanel).toContain('"about:blank"');
        expect(popupIndex).toBeGreaterThan(-1);
        expect(startIndex).toBeGreaterThan(popupIndex);
    });
});
