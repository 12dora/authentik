import {
    DINGTALK_ALLOWLIST_MARKER,
    parseDingTalkAllowlistPolicy,
    validateDingTalkAllowlistModel,
} from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import { describe, expect, it } from "vitest";

describe("validateDingTalkAllowlistModel", () => {
    it("stores department IDs as sorted strings and ignores them for allow-all companies", () => {
        const result = validateDingTalkAllowlistModel({
            companies: [
                {
                    corpId: "corp-b",
                    label: "Beta",
                    allowAll: true,
                    deptIds: ["20", 10, "10", ""],
                },
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: [2, "1", "2"],
                },
            ],
        });

        expect(result).toEqual({
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["1", "2"],
                },
                {
                    corpId: "corp-b",
                    label: "Beta",
                    allowAll: true,
                    deptIds: [],
                },
            ],
        });
    });

    it("sorts department IDs in plain code-point order matching Python's sorted()", () => {
        // Numeric-aware sorting would produce ["9", "10"] while the backend's
        // sorted() produces ["10", "9"]; the mismatch would break the
        // config_version comparison in the generated policy forever.
        const result = validateDingTalkAllowlistModel({
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["9", "10", "500000123", "60000012"],
                },
            ],
        });

        expect(result.companies[0].deptIds).toEqual(["10", "500000123", "60000012", "9"]);
    });

    it("throws when a restricted company has no departments", () => {
        expect(() =>
            validateDingTalkAllowlistModel({
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: [],
                    },
                ],
            }),
        ).toThrow(/at least one department/);
    });

    it("throws when company IDs are duplicated", () => {
        expect(() =>
            validateDingTalkAllowlistModel({
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: true,
                        deptIds: [],
                    },
                    {
                        corpId: "corp-a",
                        label: "Duplicate",
                        allowAll: true,
                        deptIds: [],
                    },
                ],
            }),
        ).toThrow(/Duplicate company/);
    });
});

describe("parseDingTalkAllowlistPolicy", () => {
    it("returns the normalized model from a marked expression policy", () => {
        const policy = `# ${DINGTALK_ALLOWLIST_MARKER}
# config: {"companies":[{"corp_id":"corp-a","label":"Alpha","allow_all":false,"dept_ids":["2","1"]}]}`;

        expect(parseDingTalkAllowlistPolicy(policy)).toEqual({
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["1", "2"],
                },
            ],
        });
    });

    it("returns null when the marker is missing", () => {
        expect(parseDingTalkAllowlistPolicy("return True")).toBeNull();
    });

    it("parses legacy managed config keys", () => {
        const policy = `# ${DINGTALK_ALLOWLIST_MARKER}
# config: {"companies":[{"corp_id":"corp-a","name":"Alpha","allow_all":false,"dept_id_list":["2","1"]}]}`;

        expect(parseDingTalkAllowlistPolicy(policy)).toEqual({
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["1", "2"],
                },
            ],
        });
    });

    it("returns null for a marked policy with an empty stored company list", () => {
        const policy = `# ${DINGTALK_ALLOWLIST_MARKER}
# config: {"companies":[]}`;

        expect(parseDingTalkAllowlistPolicy(policy)).toBeNull();
    });

    it("returns null for a marked policy with a malformed stored config", () => {
        const policy = `# ${DINGTALK_ALLOWLIST_MARKER}
# config: {`;

        expect(parseDingTalkAllowlistPolicy(policy)).toBeNull();
    });
});
