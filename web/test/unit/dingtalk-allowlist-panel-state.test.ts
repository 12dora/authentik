import {
    addDingTalkDepartments,
    buildDingTalkDepartmentTreeRows,
    dingtalkDepartmentFetchFailureStatus,
    dingtalkDepartmentPageWindow,
    dingtalkStatusLabelProperties,
    filterDingTalkDepartmentTreeRows,
    invertLoadedDingTalkDepartmentInput,
    isDingTalkCompanyMissingDepartments,
    removeDingTalkCompany,
    saveDingTalkAllowlistConfiguration,
    selectLoadedDingTalkDepartmentInput,
    splitDingTalkDepartmentIds,
    toggleDingTalkDepartmentInput,
    toggleDingTalkDepartmentTreeInput,
    updateDingTalkCompany,
    upsertDingTalkCompany,
    validatedDingTalkDiscoveryUrl,
} from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import type { DingTalkAllowlistModel } from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import { describe, expect, it, vi } from "vitest";

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

    it("accepts only HTTPS DingTalk discovery URLs", () => {
        expect(validatedDingTalkDiscoveryUrl("https://login.dingtalk.com/oauth2/auth")).toBe(
            "https://login.dingtalk.com/oauth2/auth",
        );
        expect(validatedDingTalkDiscoveryUrl("https://oapi.dingtalk.com/connect/oauth2")).toBe(
            "https://oapi.dingtalk.com/connect/oauth2",
        );
        expect(validatedDingTalkDiscoveryUrl("http://login.dingtalk.com/oauth2/auth")).toBeNull();
        expect(validatedDingTalkDiscoveryUrl("mailto:admin@example.com")).toBeNull();
        expect(validatedDingTalkDiscoveryUrl("https://example.com/oauth2/auth")).toBeNull();
        expect(validatedDingTalkDiscoveryUrl(undefined)).toBeNull();
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

    it("does not infinitely recurse when malformed data forms a department cycle", () => {
        // A duplicate department id whose second entry points back up the tree forms a
        // cycle reachable from the root; without cycle protection this recurses forever.
        const rows = buildDingTalkDepartmentTreeRows(
            [
                { deptId: "10", name: "Root", parentId: null },
                { deptId: "20", name: "Child", parentId: "10" },
                { deptId: "10", name: "Root (loop)", parentId: "20" },
            ],
            new Set(),
        );

        expect(rows.map((row) => row.department.deptId)).toEqual(["10", "20"]);
    });

    it("renders a closed department cycle as malformed roots instead of dropping every row", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [
                { deptId: "10", name: "Loop A", parentId: "20" },
                { deptId: "20", name: "Loop B", parentId: "10" },
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
            { deptId: "20", level: 1, selection: "checked" },
        ]);
    });

    it("renders orphan departments as roots", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [{ deptId: "20", name: "Orphan", parentId: "missing-parent" }],
            new Set(),
        );

        expect(rows.map((row) => ({ deptId: row.department.deptId, level: row.level }))).toEqual([
            { deptId: "20", level: 0 },
        ]);
    });

    it("uses the first department when duplicate IDs are present", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [
                { deptId: "10", name: "Original", parentId: null },
                { deptId: "10", name: "Duplicate", parentId: null },
            ],
            new Set(["10"]),
        );

        expect(rows).toHaveLength(1);
        expect(rows[0]?.department.name).toBe("Original");
        expect(rows[0]?.selection).toBe("checked");
    });

    it("treats a self-referencing department as a root instead of its own child", () => {
        const rows = buildDingTalkDepartmentTreeRows(
            [{ deptId: "10", name: "Self", parentId: "10" }],
            new Set(["10"]),
        );

        expect(rows.map((row) => ({ deptId: row.department.deptId, level: row.level }))).toEqual([
            { deptId: "10", level: 0 },
        ]);
    });

    it("toggles a self-referencing department without infinite recursion", () => {
        const departments = [{ deptId: "10", name: "Self", parentId: "10" }];

        expect(toggleDingTalkDepartmentTreeInput("", departments, "10", true)).toEqual("10");
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

    it("toggles a closed department cycle once per department", () => {
        const departments = [
            { deptId: "10", name: "Loop A", parentId: "20" },
            { deptId: "20", name: "Loop B", parentId: "10" },
        ];

        expect(toggleDingTalkDepartmentTreeInput("", departments, "10", true)).toEqual("10 20");
        expect(toggleDingTalkDepartmentTreeInput("10 20 30", departments, "10", false)).toEqual(
            "30",
        );
    });

    it("builds a 10k-wide department tree within a linear access budget", () => {
        let reads = 0;
        const departments = Array.from({ length: 10_000 }, (_, index) => {
            const deptId = String(index + 1).padStart(5, "0");
            return {
                get deptId() {
                    reads += 1;
                    return deptId;
                },
                name: `Department ${deptId}`,
                parentId: null,
            };
        });

        const rows = buildDingTalkDepartmentTreeRows(departments, new Set(["00001", "10000"]));

        expect(rows).toHaveLength(10_000);
        expect(rows[0]?.selection).toBe("checked");
        expect(rows.at(-1)?.selection).toBe("checked");
        expect(reads).toBeLessThan(700_000);
    });

    it("builds and toggles a 10k-deep chain without recursive stack growth", () => {
        const departments = Array.from({ length: 10_000 }, (_, index) => ({
            deptId: String(index + 1),
            name: `Department ${index + 1}`,
            parentId: index === 0 ? null : String(index),
        }));

        const rows = buildDingTalkDepartmentTreeRows(departments, new Set(["10000"]));

        expect(rows).toHaveLength(10_000);
        expect(rows[0]).toMatchObject({ level: 0, selection: "indeterminate" });
        expect(rows.at(-1)).toMatchObject({ level: 9_999, selection: "checked" });
        expect(
            toggleDingTalkDepartmentTreeInput("", departments, "1", true).split(" "),
        ).toHaveLength(10_000);
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

    it("validates and applies the allowlist through a single revisioned server call", async () => {
        const calls: string[] = [];
        const status = { revision: "rev-2" };

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
            expectedRevision: "rev-1",
            applyConfiguration: async (model, expectedRevision) => {
                calls.push("apply");
                expect(expectedRevision).toBe("rev-1");
                expect(model).toEqual({
                    companies: [
                        {
                            corpId: "corp-a",
                            label: "Alpha",
                            allowAll: false,
                            deptIds: ["10"],
                        },
                    ],
                });
                return status;
            },
            applyStatus: async (nextStatus) => {
                calls.push("status");
                expect(nextStatus).toBe(status);
            },
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
            status,
        });
        expect(calls).toEqual(["apply", "status"]);
    });

    it("does not apply without a source slug", async () => {
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
            applyConfiguration: async () => {
                throw new Error("must not apply");
            },
            applyStatus: async () => {
                throw new Error("must not apply status");
            },
        });

        expect(result).toBeUndefined();
    });

    it("does not apply returned status when the revisioned apply call fails", async () => {
        const applyStatus = vi.fn();

        await expect(
            saveDingTalkAllowlistConfiguration({
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
                expectedRevision: "rev-1",
                applyConfiguration: async () => {
                    throw new Error("revision_conflict");
                },
                applyStatus,
            }),
        ).rejects.toThrow("revision_conflict");

        expect(applyStatus).not.toHaveBeenCalled();
    });

    it("validates before calling the revisioned apply endpoint", async () => {
        const applyConfiguration = vi.fn();

        await expect(
            saveDingTalkAllowlistConfiguration({
                model: {
                    companies: [
                        {
                            corpId: "corp-a",
                            label: "Alpha",
                            allowAll: false,
                            deptIds: [],
                        },
                    ],
                },
                sourceSlug: "dingtalk",
                applyConfiguration,
                applyStatus: async () => undefined,
            }),
        ).rejects.toThrow(/at least one department/);

        expect(applyConfiguration).not.toHaveBeenCalled();
    });

    it("flags a restricted company with no departments as missing", () => {
        expect(
            isDingTalkCompanyMissingDepartments(
                { corpId: "corp-a", label: "Alpha", allowAll: false, deptIds: [] },
                undefined,
            ),
        ).toBe(true);
    });

    it("clears the missing-departments flag once the pending input has a department", () => {
        expect(
            isDingTalkCompanyMissingDepartments(
                { corpId: "corp-a", label: "Alpha", allowAll: false, deptIds: [] },
                "10",
            ),
        ).toBe(false);
    });

    it("falls back to the model departments when there is no pending input", () => {
        expect(
            isDingTalkCompanyMissingDepartments(
                { corpId: "corp-a", label: "Alpha", allowAll: false, deptIds: ["10"] },
                undefined,
            ),
        ).toBe(false);
    });

    it("never flags an allow-all company as missing departments", () => {
        expect(
            isDingTalkCompanyMissingDepartments(
                { corpId: "corp-a", label: "Alpha", allowAll: true, deptIds: [] },
                "",
            ),
        ).toBe(false);
    });
});
