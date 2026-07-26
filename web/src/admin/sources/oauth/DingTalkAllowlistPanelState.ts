import {
    type DingTalkAllowlistModel,
    validateDingTalkAllowlistModel,
} from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import type { TemplateResult } from "lit";

const DINGTALK_DISCOVERY_ALLOWED_HOSTS = new Set(["login.dingtalk.com", "oapi.dingtalk.com"]);

export type StatusState = "good" | "warning" | "danger" | "unknown";

export interface StatusItem {
    label: string;
    state: StatusState;
    detail?: string | TemplateResult;
    // Overrides ak-status-label's default "Yes" for the good state so each row reads
    // semantically (e.g. "Enabled", "Valid") instead of a generic affirmative.
    goodLabel?: string;
}

export type DingTalkFlowBindingKind = "authentication" | "enrollment";

export interface DingTalkDepartmentNode {
    deptId: string;
    name: string;
    parentId: string | null;
}

export type DingTalkDepartmentSelection = "checked" | "indeterminate" | "unchecked";

export interface DingTalkDepartmentTreeRow<TDepartment extends DingTalkDepartmentNode> {
    department: TDepartment;
    level: number;
    selection: DingTalkDepartmentSelection;
}

export interface SaveDingTalkAllowlistConfigurationOptions<TStatus> {
    model: DingTalkAllowlistModel;
    sourceSlug?: string;
    expectedRevision?: string | null;
    applyConfiguration: (
        model: DingTalkAllowlistModel,
        expectedRevision: string | null | undefined,
    ) => Promise<TStatus>;
    applyStatus: (status: TStatus) => void | Promise<void>;
    onValidatedModel?: (model: DingTalkAllowlistModel) => void;
}

export interface SaveDingTalkAllowlistConfigurationResult<TStatus> {
    model: DingTalkAllowlistModel;
    status: TStatus;
}

export async function saveDingTalkAllowlistConfiguration<TStatus>(
    options: SaveDingTalkAllowlistConfigurationOptions<TStatus>,
): Promise<SaveDingTalkAllowlistConfigurationResult<TStatus> | undefined> {
    if (!options.sourceSlug) {
        return undefined;
    }

    const model = validateDingTalkAllowlistModel(options.model);
    options.onValidatedModel?.(model);

    const status = await options.applyConfiguration(model, options.expectedRevision);
    await options.applyStatus(status);

    return {
        model,
        status,
    };
}

export function dingtalkStatusLabelProperties(state: StatusState): {
    good: boolean;
    type: "error" | "warning" | "info";
} {
    return {
        good: state === "good",
        // "unknown" is a neutral not-yet-checked state, not a failure.
        type: state === "warning" ? "warning" : state === "unknown" ? "info" : "error",
    };
}

export function splitDingTalkDepartmentIds(value: string): string[] {
    return Array.from(
        new Set(
            value
                .split(/[\s,，]+/u)
                .map((deptId) => deptId.trim())
                .filter((deptId) => deptId.length > 0),
        ),
    );
}

export function validatedDingTalkDiscoveryUrl(value: unknown): string | null {
    const url = typeof value === "string" ? value.trim() : "";
    if (!url) {
        return null;
    }
    try {
        const parsed = new URL(url);
        if (parsed.protocol !== "https:" || !DINGTALK_DISCOVERY_ALLOWED_HOSTS.has(parsed.host)) {
            return null;
        }
        return parsed.toString();
    } catch {
        return null;
    }
}

function invalidDingTalkDepartmentId(deptIds: string[]): string | undefined {
    return deptIds.find((deptId) => !/^[A-Za-z0-9_.:-]+$/u.test(deptId));
}

function sortDingTalkDepartmentIds(deptIds: string[]): string[] {
    return [...deptIds].sort((left, right) =>
        left.localeCompare(right, undefined, {
            numeric: true,
            sensitivity: "base",
        }),
    );
}

export function renderDingTalkDepartmentInput(deptIds: string[]): string {
    return sortDingTalkDepartmentIds(deptIds).join(" ");
}

// A restricted company (allowAll=false) with no departments admits nobody, and
// validateDingTalkAllowlistModel rejects it on save. Detecting it from the live model +
// pending input lets the panel warn inline while editing instead of only at save time.
export function isDingTalkCompanyMissingDepartments(
    company: DingTalkAllowlistModel["companies"][number],
    departmentInput: string | undefined,
): boolean {
    if (company.allowAll) {
        return false;
    }
    const effective = departmentInput ?? renderDingTalkDepartmentInput(company.deptIds.map(String));
    return splitDingTalkDepartmentIds(effective).length < 1;
}

export function dingtalkDepartmentInputsFromModel(
    model: DingTalkAllowlistModel,
): Record<string, string> {
    return Object.fromEntries(
        model.companies.map((company) => [
            company.corpId,
            company.allowAll ? "" : renderDingTalkDepartmentInput(company.deptIds.map(String)),
        ]),
    );
}

export function upsertDingTalkCompany(
    model: DingTalkAllowlistModel,
    corpId: string,
    label: string,
    allowAll: boolean,
    deptIds: string[],
): DingTalkAllowlistModel {
    const existing = model.companies.find((company) => company.corpId === corpId);
    // Re-discovering or re-adding a known company must never widen its access:
    // an existing entry keeps its configured allowAll/deptIds and only picks up
    // a fresher label.
    const companies = existing
        ? model.companies.map((company) =>
              company.corpId === corpId
                  ? {
                        ...company,
                        label: label || company.label,
                    }
                  : company,
          )
        : [
              ...model.companies,
              {
                  corpId,
                  label,
                  allowAll,
                  deptIds,
              },
          ];

    return {
        companies: companies.sort((left, right) =>
            left.corpId < right.corpId ? -1 : left.corpId > right.corpId ? 1 : 0,
        ),
    };
}

export function updateDingTalkCompany(
    model: DingTalkAllowlistModel,
    corpId: string,
    patch: Partial<DingTalkAllowlistModel["companies"][0]>,
): DingTalkAllowlistModel {
    return {
        companies: model.companies.map((company) =>
            company.corpId === corpId ? { ...company, ...patch } : company,
        ),
    };
}

export function removeDingTalkCompany(
    model: DingTalkAllowlistModel,
    corpId: string,
): DingTalkAllowlistModel {
    return {
        companies: model.companies.filter((company) => company.corpId !== corpId),
    };
}

export function addDingTalkDepartments(
    model: DingTalkAllowlistModel,
    departmentInputs: Record<string, string>,
    corpId: string,
): {
    model: DingTalkAllowlistModel;
    departmentInputs: Record<string, string>;
    error?: string;
    invalidDepartmentId?: string;
} {
    const deptIds = splitDingTalkDepartmentIds(departmentInputs[corpId] || "");
    const invalid = invalidDingTalkDepartmentId(deptIds);
    if (invalid) {
        return {
            model,
            departmentInputs,
            error: `${invalid} is not a valid DingTalk department ID.`,
            invalidDepartmentId: invalid,
        };
    }
    const company = model.companies.find((candidate) => candidate.corpId === corpId);
    if (!company) {
        return { model, departmentInputs };
    }
    if (deptIds.length < 1) {
        return {
            model: updateDingTalkCompany(model, corpId, {
                allowAll: false,
                deptIds: [],
            }),
            departmentInputs: { ...departmentInputs, [corpId]: "" },
        };
    }

    return {
        model: updateDingTalkCompany(model, corpId, {
            allowAll: false,
            deptIds: sortDingTalkDepartmentIds(Array.from(new Set(deptIds))),
        }),
        departmentInputs: {
            ...departmentInputs,
            [corpId]: renderDingTalkDepartmentInput(deptIds),
        },
    };
}

export function applyDingTalkDepartmentInputs(
    model: DingTalkAllowlistModel,
    departmentInputs: Record<string, string>,
): {
    model: DingTalkAllowlistModel;
    departmentInputs: Record<string, string>;
    error?: string;
    invalidDepartmentId?: string;
} {
    let nextModel = model;
    let nextInputs = departmentInputs;
    for (const company of model.companies) {
        if (company.allowAll) {
            continue;
        }
        const result = addDingTalkDepartments(nextModel, nextInputs, company.corpId);
        if (result.error) {
            return {
                model,
                departmentInputs,
                error: result.error,
                invalidDepartmentId: result.invalidDepartmentId,
            };
        }
        nextModel = result.model;
        nextInputs = result.departmentInputs;
    }
    return { model: nextModel, departmentInputs: nextInputs };
}

export function toggleDingTalkDepartmentInput(
    currentInput: string,
    deptId: string,
    selected: boolean,
): string {
    const deptIds = new Set(splitDingTalkDepartmentIds(currentInput));
    if (selected) {
        deptIds.add(deptId);
    } else {
        deptIds.delete(deptId);
    }
    return renderDingTalkDepartmentInput(Array.from(deptIds));
}

function loadedDingTalkDepartmentIds(departments: DingTalkDepartmentNode[]): string[] {
    return departments.map((department) => department.deptId);
}

function sortDingTalkDepartments<TDepartment extends DingTalkDepartmentNode>(
    departments: TDepartment[],
): TDepartment[] {
    return [...departments].sort((left, right) =>
        left.deptId.localeCompare(right.deptId, undefined, {
            numeric: true,
            sensitivity: "base",
        }),
    );
}

interface DingTalkDepartmentTreeIndex<TDepartment extends DingTalkDepartmentNode> {
    childrenByParent: Map<string, TDepartment[]>;
    departmentById: Map<string, TDepartment>;
}

function buildDingTalkDepartmentTreeIndex<TDepartment extends DingTalkDepartmentNode>(
    departments: TDepartment[],
): DingTalkDepartmentTreeIndex<TDepartment> {
    const departmentById = new Map<string, TDepartment>();
    for (const department of departments) {
        if (!departmentById.has(department.deptId)) {
            departmentById.set(department.deptId, department);
        }
    }

    const childrenByParent = new Map<string, TDepartment[]>();
    for (const department of departmentById.values()) {
        // A department that names itself as parent (self-reference) is treated as a root
        // rather than its own child, so it cannot seed an infinite descent below.
        const parentId =
            department.parentId &&
            department.parentId !== department.deptId &&
            departmentById.has(department.parentId)
                ? department.parentId
                : "";
        let children = childrenByParent.get(parentId);
        if (!children) {
            children = [];
            childrenByParent.set(parentId, children);
        }
        children.push(department);
    }
    for (const [parentId, children] of childrenByParent) {
        childrenByParent.set(parentId, sortDingTalkDepartments(children));
    }
    return { childrenByParent, departmentById };
}

function dingtalkDepartmentSelectionState(
    selectedCount: number,
    subtreeSize: number,
): DingTalkDepartmentSelection {
    if (selectedCount === 0) {
        return "unchecked";
    }
    if (selectedCount === subtreeSize) {
        return "checked";
    }
    return "indeterminate";
}

export function buildDingTalkDepartmentTreeRows<TDepartment extends DingTalkDepartmentNode>(
    departments: TDepartment[],
    selectedDeptIds: Set<string>,
): DingTalkDepartmentTreeRow<TDepartment>[] {
    const { childrenByParent, departmentById } = buildDingTalkDepartmentTreeIndex(departments);
    const rows: DingTalkDepartmentTreeRow<TDepartment>[] = [];
    const rowByDeptId = new Map<string, DingTalkDepartmentTreeRow<TDepartment>>();
    const subtreeSizes = new Map<string, number>();
    const selectedCounts = new Map<string, number>();
    const visited = new Set<string>();

    const visitFrom = (roots: TDepartment[]): void => {
        const stack = roots
            .map((department) => ({ department, level: 0, expanded: false }))
            .reverse();

        while (stack.length > 0) {
            const frame = stack.pop();
            if (!frame) {
                continue;
            }
            const { department, level, expanded } = frame;
            if (expanded) {
                let subtreeSize = 1;
                let selectedCount = selectedDeptIds.has(department.deptId) ? 1 : 0;
                for (const child of childrenByParent.get(department.deptId) || []) {
                    subtreeSize += subtreeSizes.get(child.deptId) ?? 0;
                    selectedCount += selectedCounts.get(child.deptId) ?? 0;
                }
                subtreeSizes.set(department.deptId, subtreeSize);
                selectedCounts.set(department.deptId, selectedCount);
                const row = rowByDeptId.get(department.deptId);
                if (row) {
                    row.selection = dingtalkDepartmentSelectionState(selectedCount, subtreeSize);
                }
                continue;
            }
            if (visited.has(department.deptId)) {
                continue;
            }
            visited.add(department.deptId);
            const row = {
                department,
                level,
                selection: "unchecked" as DingTalkDepartmentSelection,
            };
            rows.push(row);
            rowByDeptId.set(department.deptId, row);
            stack.push({ department, level, expanded: true });
            const children = childrenByParent.get(department.deptId) || [];
            for (let index = children.length - 1; index >= 0; index -= 1) {
                const child = children[index];
                if (child) {
                    stack.push({ department: child, level: level + 1, expanded: false });
                }
            }
        }
    };

    visitFrom(childrenByParent.get("") || []);
    visitFrom(
        Array.from(departmentById.values()).filter((department) => !visited.has(department.deptId)),
    );

    for (const row of rows) {
        row.selection = dingtalkDepartmentSelectionState(
            selectedCounts.get(row.department.deptId) ?? 0,
            subtreeSizes.get(row.department.deptId) ?? 1,
        );
    }

    return rows;
}

// Filters already-built tree rows by a case-insensitive substring match against the
// department id, name, or parent id. Rows keep their computed level/selection so the
// tri-state parent selection stays consistent even when the visible set is narrowed.
export function filterDingTalkDepartmentTreeRows<TDepartment extends DingTalkDepartmentNode>(
    rows: DingTalkDepartmentTreeRow<TDepartment>[],
    query: string,
): DingTalkDepartmentTreeRow<TDepartment>[] {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) {
        return rows;
    }
    return rows.filter(({ department }) =>
        [department.deptId, department.name, department.parentId ?? ""].some((field) =>
            field.toLocaleLowerCase().includes(needle),
        ),
    );
}

export interface DingTalkDepartmentPageWindow {
    page: number;
    totalPages: number;
    start: number;
    end: number;
    total: number;
}

// Clamps a requested page against the current total so client-side pagination never
// lands on an out-of-range window (e.g. after a filter shrinks the result set).
export function dingtalkDepartmentPageWindow(
    total: number,
    page: number,
    pageSize: number,
): DingTalkDepartmentPageWindow {
    const size = Math.max(1, pageSize);
    const totalPages = Math.max(1, Math.ceil(total / size));
    const clampedPage = Math.min(Math.max(1, Math.floor(page) || 1), totalPages);
    const start = (clampedPage - 1) * size;
    const end = Math.min(start + size, total);
    return { page: clampedPage, totalPages, start, end, total };
}

export function toggleDingTalkDepartmentTreeInput(
    currentInput: string,
    departments: DingTalkDepartmentNode[],
    deptId: string,
    selected: boolean,
): string {
    const { childrenByParent, departmentById } = buildDingTalkDepartmentTreeIndex(departments);
    const deptIds = new Set(splitDingTalkDepartmentIds(currentInput));
    if (!departmentById.has(deptId)) {
        return renderDingTalkDepartmentInput(Array.from(deptIds));
    }
    const visited = new Set<string>();
    const stack = [deptId];
    while (stack.length > 0) {
        const subtreeDeptId = stack.pop();
        if (!subtreeDeptId || visited.has(subtreeDeptId)) {
            continue;
        }
        visited.add(subtreeDeptId);
        if (selected) {
            deptIds.add(subtreeDeptId);
        } else {
            deptIds.delete(subtreeDeptId);
        }
        for (const child of childrenByParent.get(subtreeDeptId) || []) {
            stack.push(child.deptId);
        }
    }
    return renderDingTalkDepartmentInput(Array.from(deptIds));
}

export function selectLoadedDingTalkDepartmentInput(
    currentInput: string,
    departments: DingTalkDepartmentNode[],
): string {
    return renderDingTalkDepartmentInput(
        Array.from(
            new Set([
                ...splitDingTalkDepartmentIds(currentInput),
                ...loadedDingTalkDepartmentIds(departments),
            ]),
        ),
    );
}

export function invertLoadedDingTalkDepartmentInput(
    currentInput: string,
    departments: DingTalkDepartmentNode[],
): string {
    const deptIds = new Set(splitDingTalkDepartmentIds(currentInput));
    for (const deptId of loadedDingTalkDepartmentIds(departments)) {
        if (deptIds.has(deptId)) {
            deptIds.delete(deptId);
        } else {
            deptIds.add(deptId);
        }
    }
    return renderDingTalkDepartmentInput(Array.from(deptIds));
}

export function dingtalkDepartmentFetchFailureStatus(
    previous: StatusItem,
    detail: string,
): StatusItem {
    return {
        label: previous.label,
        state: "danger",
        detail,
    };
}
