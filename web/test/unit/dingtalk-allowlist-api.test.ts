import { DingTalkAllowlistApi } from "../../src/admin/sources/oauth/DingTalkAllowlistApi";

import { Configuration } from "@goauthentik/api";

import { describe, expect, it, vi } from "vitest";

function makeResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
    });
}

describe("DingTalkAllowlistApi", () => {
    it("posts normalized config and expected revision to the apply transaction", async () => {
        const fetchApi = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
            makeResponse({
                revision: "rev-2",
                config: null,
                managed_policy: { exists: true, pk: "policy-pk", name: "managed" },
                policy_binding: { exists: true, pk: "binding-pk", enabled: true },
                source_link_guard: { enabled: true },
                policy_bindings: [{ exists: true, pk: "binding-pk", enabled: true, target: "" }],
                callback_url: "",
            }),
        );
        const api = new DingTalkAllowlistApi(
            new Configuration({ basePath: "", fetchApi: fetchApi as typeof fetch }),
        );

        const status = await api.sourcesOauthDingtalkAllowlistApplyCreate("ding/talk", {
            expectedRevision: "rev-1",
            config: {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: [10, "20"],
                    },
                ],
            },
        });

        const [url, init] = fetchApi.mock.calls[0];
        expect(String(url)).toContain("/sources/oauth/dingtalk-allowlist/ding%2Ftalk/apply/");
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
            config: {
                companies: [
                    {
                        corp_id: "corp-a",
                        label: "Alpha",
                        allow_all: false,
                        dept_ids: ["10", "20"],
                    },
                ],
            },
            expected_revision: "rev-1",
        });
        expect(status.revision).toBe("rev-2");
        expect(status.policyBindings).toEqual([
            { _exists: true, pk: "binding-pk", enabled: true, target: "" },
        ]);
    });

    it("posts only the expected revision to the remove transaction", async () => {
        const fetchApi = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
            makeResponse({
                revision: "rev-3",
                config: null,
                managed_policy: { exists: false, pk: null, name: "" },
                policy_binding: { exists: false, pk: null, enabled: false },
                source_link_guard: { enabled: false },
                policy_bindings: [],
                callback_url: "",
            }),
        );
        const api = new DingTalkAllowlistApi(
            new Configuration({ basePath: "", fetchApi: fetchApi as typeof fetch }),
        );

        await api.sourcesOauthDingtalkAllowlistRemoveCreate("dingtalk", {
            expectedRevision: "rev-2",
        });

        const [url, init] = fetchApi.mock.calls[0];
        expect(String(url)).toContain("/sources/oauth/dingtalk-allowlist/dingtalk/remove/");
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: "rev-2" });
    });
});
