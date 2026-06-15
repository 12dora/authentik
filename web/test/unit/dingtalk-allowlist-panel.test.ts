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

    it("allows a discovered company to be saved without first selecting departments", () => {
        expect(allowlistPanel).toContain(
            "this.upsertCompany(result.corpId, result.label || result.corpId, true, []);",
        );
    });
});
