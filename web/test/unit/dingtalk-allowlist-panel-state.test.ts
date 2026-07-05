import {
    addDingTalkDepartments,
    buildDingTalkDepartmentTreeRows,
    dingtalkDepartmentFetchFailureStatus,
    dingtalkDepartmentPageWindow,
    dingtalkStatusLabelProperties,
    filterDingTalkDepartmentTreeRows,
    invertLoadedDingTalkDepartmentInput,
    removeDingTalkCompany,
    saveDingTalkAllowlistConfiguration,
    selectLoadedDingTalkDepartmentInput,
    splitDingTalkDepartmentIds,
    toggleDingTalkDepartmentInput,
    toggleDingTalkDepartmentTreeInput,
    updateDingTalkCompany,
    upsertDingTalkCompany,
} from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import type { DingTalkAllowlistModel } from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import { describe, expect, it } from "vitest";

describe("DingTalkAllowlistPanelState", () => {
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
            type: "info",
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

    it("keeps the configured mode and departments when an existing company is re-discovered", () => {
        let model: DingTalkAllowlistModel = {
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["10", "20"],
                },
            ],
        };

        model = upsertDingTalkCompany(model, "corp-a", "Alpha renamed", true, []);

        expect(model.companies).toEqual([
            {
                corpId: "corp-a",
                label: "Alpha renamed",
                allowAll: false,
                deptIds: ["10", "20"],
            },
        ]);
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
            departmentInputs: { "corp-a": "10 20 30" },
        });
    });

    it("splits department IDs only on supported separators and removes duplicates", () => {
        expect(splitDingTalkDepartmentIds("10, 20，30 20\n40")).toEqual(["10", "20", "30", "40"]);
    });

    it("rejects unsupported department input without mutating the model", () => {
        const model: DingTalkAllowlistModel = {
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: ["10"],
                },
            ],
        };

        const result = addDingTalkDepartments(model, { "corp-a": "20; drop" }, "corp-a");

        expect(result).toEqual({
            model,
            departmentInputs: { "corp-a": "20; drop" },
            error: "20; is not a valid DingTalk department ID.",
            invalidDepartmentId: "20;",
        });
    });

    it("keeps the department input as the editable source after adding departments", () => {
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
            { "corp-a": "10 30" },
            "corp-a",
        );

        expect(result).toEqual({
            model: {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["10", "30"],
                    },
                ],
            },
            departmentInputs: { "corp-a": "10 30" },
        });
    });

    it("lets an empty department input remove previously added department IDs", () => {
        const result = addDingTalkDepartments(
            {
                companies: [
                    {
                        corpId: "corp-a",
                        label: "Alpha",
                        allowAll: false,
                        deptIds: ["10", "30"],
                    },
                ],
            },
            { "corp-a": "" },
            "corp-a",
        );

        expect(result.model.companies[0].deptIds).toEqual([]);
        expect(result.departmentInputs).toEqual({ "corp-a": "" });
    });

    it("toggles loaded department IDs in the editable department input", () => {
        expect(toggleDingTalkDepartmentInput("manual-1 20", "10", true)).toEqual("10 20 manual-1");
        expect(toggleDingTalkDepartmentInput("manual-1 10 20", "10", false)).toEqual("20 manual-1");
    });

    it("builds hierarchical department rows with partial parent selection", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [
                { deptId: "30", name: "Child B", parentId: "10" },
                { deptId: "10", name: "Root", parentId: null },
                { deptId: "20", name: "Child A", parentId: "10" },
                { deptId: "40", name: "Grandchild", parentId: "20" },
            ],
            new Set(["20"]),
        );

        expect(
            rows.map((row) => ({
                deptId: row.department.deptId,
                level: row.level,
                selection: row.selection,
            })),
        ).toEqual([
            { deptId: "10", level: 0, selection: "indeterminate" },
            { deptId: "20", level: 1, selection: "indeterminate" },
            { deptId: "40", level: 2, selection: "unchecked" },
            { deptId: "30", level: 1, selection: "unchecked" },
        ]);
    });

    it("toggles a parent department together with all loaded descendants", () => {
        const departments = [
            { deptId: "10", name: "Root", parentId: null },
            { deptId: "20", name: "Child", parentId: "10" },
            { deptId: "30", name: "Other", parentId: null },
        ];

        expect(toggleDingTalkDepartmentTreeInput("manual-1 30", departments, "10", true)).toEqual(
            "10 20 30 manual-1",
        );
        expect(
            toggleDingTalkDepartmentTreeInput("manual-1 10 20 30", departments, "10", false),
        ).toEqual("30 manual-1");
    });

    it("filters department tree rows by id, name, or parent id case-insensitively", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [
                { deptId: "10", name: "Engineering", parentId: null },
                { deptId: "20", name: "Sales", parentId: "10" },
                { deptId: "30", name: "工程组", parentId: "10" },
            ],
            new Set(),
        );

        expect(
            filterDingTalkDepartmentTreeRows(rows, "eng").map((row) => row.department.deptId),
        ).toEqual(["10"]);
        expect(
            filterDingTalkDepartmentTreeRows(rows, "工程").map((row) => row.department.deptId),
        ).toEqual(["30"]);
        // Filtering by a parent id keeps the matching children.
        expect(
            filterDingTalkDepartmentTreeRows(rows, "10").map((row) => row.department.deptId),
        ).toEqual(["10", "20", "30"]);
    });

    it("returns the untouched rows when the filter query is blank", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [{ deptId: "10", name: "Root", parentId: null }],
            new Set(),
        );

        expect(filterDingTalkDepartmentTreeRows(rows, "   ")).toBe(rows);
    });

    it("clamps the department page window to the available pages", () => {
        expect(dingtalkDepartmentPageWindow(120, 1, 50)).toEqual({
            page: 1,
            totalPages: 3,
            start: 0,
            end: 50,
            total: 120,
        });
        expect(dingtalkDepartmentPageWindow(120, 3, 50)).toEqual({
            page: 3,
            totalPages: 3,
            start: 100,
            end: 120,
            total: 120,
        });
        // An out-of-range page snaps back to the last page.
        expect(dingtalkDepartmentPageWindow(120, 9, 50)).toEqual({
            page: 3,
            totalPages: 3,
            start: 100,
            end: 120,
            total: 120,
        });
    });

    it("keeps a single page window when there are no departments", () => {
        expect(dingtalkDepartmentPageWindow(0, 1, 50)).toEqual({
            page: 1,
            totalPages: 1,
            start: 0,
            end: 0,
            total: 0,
        });
    });

    it("selects and inverts only loaded department IDs while preserving manual IDs", () => {
        const departments = [
            { deptId: "10", name: "Root", parentId: null },
            { deptId: "20", name: "Child", parentId: "10" },
            { deptId: "30", name: "Other", parentId: null },
        ];

        expect(selectLoadedDingTalkDepartmentInput("manual-1 20", departments)).toEqual(
            "10 20 30 manual-1",
        );
        expect(invertLoadedDingTalkDepartmentInput("manual-1 20 30", departments)).toEqual(
            "10 manual-1",
        );
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
                expect(expression).toContain("authentik-managed-dingtalk-allowlist");
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

    it("ensures the shared flow binding only once when both flows are the same flow", async () => {
        const bindingCalls: string[] = [];

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
                authenticationFlow: "shared-flow-pk",
                enrollmentFlow: "shared-flow-pk",
            }),
            getAuthenticationFlowPk: (source) => source.authenticationFlow,
            getEnrollmentFlowPk: (source) => source.enrollmentFlow,
            resolveFlow: async (flowPk) => ({ flowPk }),
            ensureBinding: async (flow) => {
                bindingCalls.push(`binding:${flow?.flowPk}`);
            },
            refreshStatus: async () => {},
            bindingFailureLabel: (kind) =>
                kind === "authentication"
                    ? "Authentication flow binding"
                    : "Enrollment flow binding",
            errorMessage: (error) => (error instanceof Error ? error.message : String(error)),
        });

        expect(bindingCalls).toEqual(["binding:shared-flow-pk"]);
        expect(result?.failures).toEqual([]);
        expect(result?.authFlow).toBe(result?.enrollmentFlow);
    });
});
