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
        expect(policy).toContain('# source: "dingtalk"');
        expect(policy).toContain(
            '# config: {"companies":[{"allow_all":false,"corp_id":"corp-a","dept_ids":["1","2"],"label":"Alpha"},{"allow_all":true,"corp_id":"corp-b","dept_ids":[],"label":"Beta"}]}',
        );
        expect(policy).toContain('if source and getattr(source, "slug", None) != "dingtalk":');
        expect(policy).toContain('if request.obj.__class__.__name__ != "Application":');
        expect(policy).toContain("if request.user and request.user.is_superuser:");
        expect(policy).toContain(
            'marker = context.get("authentik/sources/oauth/dingtalk/allowlist")',
        );
        expect(policy).toContain('marker.get("config_version")');
        expect(policy).toContain('"corp-a": {"allow_all": False, "dept_ids": {"1", "2"}}');
        expect(policy).toContain('"corp-b": {"allow_all": True, "dept_ids": set()}');
    });

    it("embeds a config line that matches the backend's config_version serialization", () => {
        const policy = renderDingTalkAllowlistPolicy(
            {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["9", "10"],
                    },
                ],
            },
            "dingtalk",
        );

        expect(policy).toContain(
            '# config: {"companies":[{"allow_all":false,"corp_id":"corp-a","dept_ids":["10","9"],"label":"Alpha"}]}',
        );
        expect(policy).toContain(
            'marker.get("config_version") != "{\\"companies\\":[{\\"allow_all\\":false,\\"corp_id\\":\\"corp-a\\",\\"dept_ids\\":[\\"10\\",\\"9\\"]}]}"',
        );
    });

    it("does not include display labels in the session authorization version", () => {
        const alpha = renderDingTalkAllowlistPolicy(
            {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: true,
                        deptIds: [],
                    },
                ],
            },
            "dingtalk",
        );
        const renamed = renderDingTalkAllowlistPolicy(
            {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Renamed",
                        allowAll: true,
                        deptIds: [],
                    },
                ],
            },
            "dingtalk",
        );

        const markerCheck = (policy: string) =>
            policy.split("\n").find((line) => line.includes('marker.get("config_version")'));
        expect(markerCheck(alpha)).toBe(markerCheck(renamed));
        expect(alpha).not.toContain('config_version") != "{\\"companies\\":[{\\"label\\"');
    });

    it("renders translatable English denial message IDs matching the backend contract", () => {
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

        expect(policy).toContain(
            'ak_message("DingTalk login failed: unable to verify your company. Contact your administrator.")',
        );
        // B4: a DingTalk login that reached policy evaluation without a company id fails closed.
        expect(policy).toContain(
            'ak_message("DingTalk login failed: unable to determine your company. Sign in with DingTalk again.")',
        );
        expect(policy).toContain(
            'ak_message("DingTalk login failed: the allowlist changed. Sign in with DingTalk again.")',
        );
        // B6: application access without a DingTalk marker is no longer blocked outright.
        expect(policy).not.toContain(
            'ak_message("钉钉登录失败：请通过允许的钉钉组织登录后访问此应用。")',
        );
        expect(policy).toContain(
            'ak_message("DingTalk login failed: your company is not allowed. Contact your administrator.")',
        );
        expect(policy).toContain(
            'ak_message("DingTalk login failed: unable to verify your department. Contact your administrator.")',
        );
        expect(policy).toContain(
            'ak_message("DingTalk login failed: your department is not allowed. Contact your administrator.")',
        );
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
