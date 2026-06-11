import {
    DINGTALK_ALLOWLIST_MARKER,
    parseDingTalkAllowlistPolicy,
    renderDingTalkAllowlistPolicy,
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

describe("renderDingTalkAllowlistPolicy", () => {
    it("renders a deterministic marked expression policy", () => {
        const policy = renderDingTalkAllowlistPolicy(
            {
                companies: [
                    {
                        corpId: "corp-b",
                        label: "Beta",
                        allowAll: true,
                        deptIds: [],
                    },
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["2", "1"],
                    },
                ],
            },
            "dingtalk",
        );

        expect(policy).toContain(`# ${DINGTALK_ALLOWLIST_MARKER}`);
        expect(policy).toContain(
            '# config: {"companies":[{"corp_id":"corp-a","label":"Alpha","allow_all":false,"dept_ids":["1","2"]},{"corp_id":"corp-b","label":"Beta","allow_all":true,"dept_ids":[]}]}',
        );
        expect(policy).toContain('if source and getattr(source, "slug", None) != "dingtalk":');
        expect(policy).toContain('"corp-a": {"allow_all": False, "dept_ids": {"1", "2"}}');
        expect(policy).toContain('"corp-b": {"allow_all": True, "dept_ids": set()}');
    });

    it("renders Chinese denial messages matching the backend allowlist contract", () => {
        const policy = renderDingTalkAllowlistPolicy(
            {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["10"],
                    },
                ],
            },
            "dingtalk",
        );

        expect(policy).toContain('ak_message("钉钉登录失败：无法确认企业信息，请联系管理员。")');
        expect(policy).toContain('ak_message("钉钉登录失败：当前企业未被允许，请联系管理员。")');
        expect(policy).toContain('ak_message("钉钉登录失败：无法确认部门信息，请联系管理员。")');
        expect(policy).toContain('ak_message("钉钉登录失败：当前部门未被允许，请联系管理员。")');
        expect(policy).not.toContain("DingTalk did not return a company ID.");
        expect(policy).not.toContain("This DingTalk company is not allowed.");
        expect(policy).not.toContain("This DingTalk account is not in an allowed department.");
    });
});

describe("parseDingTalkAllowlistPolicy", () => {
    it("returns the normalized model from a marked expression policy", () => {
        const model = {
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["2", "1"],
                },
            ],
        };

        const policy = renderDingTalkAllowlistPolicy(model, "dingtalk");

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
});
