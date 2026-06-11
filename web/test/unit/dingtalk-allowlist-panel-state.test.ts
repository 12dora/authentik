import {
    addDingTalkDepartments,
    dingtalkDepartmentFetchFailureStatus,
    dingtalkStatusLabelProperties,
    removeDingTalkCompany,
    saveDingTalkAllowlistConfiguration,
    singleDingTalkLoginEntryStatusItem,
    splitDingTalkDepartmentIds,
    updateDingTalkCompany,
    upsertDingTalkCompany,
} from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import type { DingTalkAllowlistModel } from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import { describe, expect, it } from "vitest";

describe("DingTalkAllowlistPanelState", () => {
    it("includes the required single visible DingTalk login entry status item", () => {
        expect(
            singleDingTalkLoginEntryStatusItem("One visible DingTalk login entry is expected"),
        ).toEqual({
            label: "One visible DingTalk login entry is expected",
            state: "good",
        });
    });

    it("uses ak-status-label type for warning states instead of an unknown warning attribute", () => {
        expect(dingtalkStatusLabelProperties("good")).toEqual({
            good: true,
            type: "error",
        });
        expect(dingtalkStatusLabelProperties("danger")).toEqual({
            good: false,
            type: "error",
        });
        expect(dingtalkStatusLabelProperties("warning")).toEqual({
            good: false,
            type: "warning",
        });
        expect(dingtalkStatusLabelProperties("unknown")).toEqual({
            good: false,
            type: "warning",
        });
    });

    it("adds, edits, and removes companies without disturbing sorted row order", () => {
        let model: DingTalkAllowlistModel = { companies: [] };

        model = upsertDingTalkCompany(model, "corp-b", "Beta", true, []);
        model = upsertDingTalkCompany(model, "corp-a", "Alpha", false, ["20"]);
        model = updateDingTalkCompany(model, "corp-a", { label: "Alpha renamed" });

        expect(model.companies).toEqual([
            {
                corpId: "corp-a",
                label: "Alpha renamed",
                allowAll: false,
                deptIds: ["20"],
            },
            {
                corpId: "corp-b",
                label: "Beta",
                allowAll: true,
                deptIds: [],
            },
        ]);

        expect(removeDingTalkCompany(model, "corp-a")).toEqual({
            companies: [
                {
                    corpId: "corp-b",
                    label: "Beta",
                    allowAll: true,
                    deptIds: [],
                },
            ],
        });
    });

    it("keeps manual department IDs usable after department discovery failure", () => {
        const failureStatus = dingtalkDepartmentFetchFailureStatus(
            { label: "Last department fetch", state: "unknown", detail: "Not run" },
            "DingTalk department permission missing",
        );
        expect(failureStatus).toEqual({
            label: "Last department fetch",
            state: "danger",
            detail: "DingTalk department permission missing",
        });

        const result = addDingTalkDepartments(
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
            { "corp-a": "20, 10 30" },
            "corp-a",
        );

        expect(result).toEqual({
            model: {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["10", "20", "30"],
                    },
                ],
            },
            departmentInputs: { "corp-a": "" },
        });
    });

    it("splits department IDs only on supported separators and removes duplicates", () => {
        expect(splitDingTalkDepartmentIds("10, 20，30 20\n40")).toEqual(["10", "20", "30", "40"]);
    });

    it("saves and applies through policy, source, flows, bindings, and refresh in order", async () => {
        const calls: string[] = [];
        const policy = { pk: "policy-pk" };
        const source = {
            authenticationFlow: "auth-flow-pk",
            enrollmentFlow: "enrollment-flow-pk",
        };
        const flows = {
            "auth-flow-pk": { policybindingmodelPtrId: "auth-binding-target" },
            "enrollment-flow-pk": { policybindingmodelPtrId: "enrollment-binding-target" },
        };

        const result = await saveDingTalkAllowlistConfiguration({
            model: {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["10"],
                    },
                ],
            },
            sourceSlug: "dingtalk",
            createOrUpdatePolicy: async (expression) => {
                calls.push("policy");
                expect(expression).toContain("authentik-jiefa-dingtalk-allowlist:v1");
                return policy;
            },
            retrieveSource: async () => {
                calls.push("source");
                return source;
            },
            getAuthenticationFlowPk: (refreshedSource) => refreshedSource.authenticationFlow,
            getEnrollmentFlowPk: (refreshedSource) => refreshedSource.enrollmentFlow,
            resolveFlow: async (flowPk) => {
                calls.push(`flow:${flowPk}`);
                return flows[flowPk as keyof typeof flows];
            },
            ensureBinding: async (flow) => {
                calls.push(`binding:${flow?.policybindingmodelPtrId}`);
            },
            refreshStatus: async () => {
                calls.push("refresh");
            },
            bindingFailureLabel: (kind) =>
                kind === "authentication"
                    ? "Authentication flow binding"
                    : "Enrollment flow binding",
            errorMessage: (error) => (error instanceof Error ? error.message : String(error)),
        });

        expect(result).toEqual({
            model: {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["10"],
                    },
                ],
            },
            policy,
            source,
            authFlow: flows["auth-flow-pk"],
            enrollmentFlow: flows["enrollment-flow-pk"],
            failures: [],
        });
        expect(calls.indexOf("policy")).toBeLessThan(calls.indexOf("source"));
        expect(calls.indexOf("source")).toBeLessThan(calls.indexOf("flow:auth-flow-pk"));
        expect(calls.indexOf("source")).toBeLessThan(calls.indexOf("flow:enrollment-flow-pk"));
        expect(calls.indexOf("flow:auth-flow-pk")).toBeLessThan(
            calls.indexOf("binding:auth-binding-target"),
        );
        expect(calls.indexOf("flow:enrollment-flow-pk")).toBeLessThan(
            calls.indexOf("binding:enrollment-binding-target"),
        );
        expect(calls.indexOf("binding:auth-binding-target")).toBeLessThan(calls.indexOf("refresh"));
        expect(calls.indexOf("binding:enrollment-binding-target")).toBeLessThan(
            calls.indexOf("refresh"),
        );
    });

    it("keeps refreshing status when one flow binding fails", async () => {
        const calls: string[] = [];

        const result = await saveDingTalkAllowlistConfiguration({
            model: {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: true,
                        deptIds: [],
                    },
                ],
            },
            sourceSlug: "dingtalk",
            createOrUpdatePolicy: async () => ({ pk: "policy-pk" }),
            retrieveSource: async () => ({
                authenticationFlow: "auth-flow-pk",
                enrollmentFlow: "enrollment-flow-pk",
            }),
            getAuthenticationFlowPk: (source) => source.authenticationFlow,
            getEnrollmentFlowPk: (source) => source.enrollmentFlow,
            resolveFlow: async (flowPk) => ({ flowPk }),
            ensureBinding: async (flow) => {
                calls.push(`binding:${flow?.flowPk}`);
                if (flow?.flowPk === "enrollment-flow-pk") {
                    throw new Error("missing stage");
                }
            },
            refreshStatus: async () => {
                calls.push("refresh");
            },
            bindingFailureLabel: (kind) =>
                kind === "authentication"
                    ? "Authentication flow binding"
                    : "Enrollment flow binding",
            errorMessage: (error) => (error instanceof Error ? error.message : String(error)),
        });

        expect(result?.failures).toEqual(["Enrollment flow binding: missing stage"]);
        expect(calls).toContain("binding:auth-flow-pk");
        expect(calls).toContain("binding:enrollment-flow-pk");
        expect(calls.at(-1)).toBe("refresh");
    });
});
