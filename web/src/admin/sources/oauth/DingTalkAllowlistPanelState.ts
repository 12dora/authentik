import {
    type DingTalkAllowlistModel,
    renderDingTalkAllowlistPolicy,
    validateDingTalkAllowlistModel,
} from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import type { TemplateResult } from "lit";

export type StatusState = "good" | "warning" | "danger" | "unknown";

export interface StatusItem {
    label: string;
    state: StatusState;
    detail?: string | TemplateResult;
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

export interface SaveDingTalkAllowlistConfigurationOptions<TPolicy, TSource, TFlow> {
    model: DingTalkAllowlistModel;
    sourceSlug?: string;
    createOrUpdatePolicy: (expression: string) => Promise<TPolicy>;
    retrieveSource: (sourceSlug: string) => Promise<TSource>;
    getAuthenticationFlowPk: (source: TSource) => string | null | undefined;
    getEnrollmentFlowPk: (source: TSource) => string | null | undefined;
    resolveFlow: (flowPk: string | null | undefined) => Promise<TFlow | undefined>;
    ensureBinding: (flow: TFlow | undefined, policy: TPolicy) => Promise<void>;
    refreshStatus: () => Promise<void>;
    bindingFailureLabel: (kind: DingTalkFlowBindingKind) => string;
    errorMessage: (error: unknown) => string;
    onValidatedModel?: (model: DingTalkAllowlistModel) => void;
    onPolicySaved?: (policy: TPolicy) => void;
    onSourceRefreshed?: (source: TSource) => void;
    onFlowsResolved?: (authFlow: TFlow | undefined, enrollmentFlow: TFlow | undefined) => void;
}

export interface SaveDingTalkAllowlistConfigurationResult<TPolicy, TSource, TFlow> {
    model: DingTalkAllowlistModel;
    policy: TPolicy;
    source: TSource;
    authFlow: TFlow | undefined;
    enrollmentFlow: TFlow | undefined;
    failures: string[];
}

export async function saveDingTalkAllowlistConfiguration<TPolicy, TSource, TFlow>(
    options: SaveDingTalkAllowlistConfigurationOptions<TPolicy, TSource, TFlow>,
): Promise<SaveDingTalkAllowlistConfigurationResult<TPolicy, TSource, TFlow> | undefined> {
    if (!options.sourceSlug) {
        return undefined;
    }

    const failures: string[] = [];
    const model = validateDingTalkAllowlistModel(options.model);
    const expression = renderDingTalkAllowlistPolicy(model, options.sourceSlug);
    options.onValidatedModel?.(model);

    const policy = await options.createOrUpdatePolicy(expression);
    options.onPolicySaved?.(policy);

    const source = await options.retrieveSource(options.sourceSlug);
    options.onSourceRefreshed?.(source);

    const [authFlow, enrollmentFlow] = await Promise.all([
        options.resolveFlow(options.getAuthenticationFlowPk(source)),
        options.resolveFlow(options.getEnrollmentFlowPk(source)),
    ]);
    options.onFlowsResolved?.(authFlow, enrollmentFlow);

    await Promise.all([
        options.ensureBinding(authFlow, policy).catch((error: unknown) => {
            failures.push(
                `${options.bindingFailureLabel("authentication")}: ${options.errorMessage(error)}`,
            );
        }),
        options.ensureBinding(enrollmentFlow, policy).catch((error: unknown) => {
            failures.push(
                `${options.bindingFailureLabel("enrollment")}: ${options.errorMessage(error)}`,
            );
        }),
    ]);

    await options.refreshStatus();

    return {
        model,
        policy,
        source,
        authFlow,
        enrollmentFlow,
        failures,
    };
}

export function singleDingTalkLoginEntryStatusItem(label: string): StatusItem {
    return {
        label,
        state: "good",
    };
}

export function dingtalkStatusLabelProperties(state: StatusState): {
    good: boolean;
    type: "error" | "warning";
} {
    return {
        good: state === "good",
        type: state === "warning" || state === "unknown" ? "warning" : "error",
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
    const companies = existing
        ? model.companies.map((company) =>
              company.corpId === corpId
                  ? {
                        ...company,
                        label: label || company.label,
                        allowAll,
                        deptIds: allowAll
                            ? []
                            : Array.from(
                                  new Set([
                                      ...company.deptIds.map((deptId) => String(deptId)),
                                      ...deptIds,
                                  ]),
                              ),
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
            left.corpId.localeCompare(right.corpId, undefined, {
                numeric: true,
                sensitivity: "base",
            }),
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

export function mergeLoadedDingTalkDepartmentInput(
    currentInput: string,
    previousLoadedDeptIds: string[],
    nextLoadedDeptIds: string[],
): string {
    const previousLoaded = new Set(previousLoadedDeptIds.map(String));
    const manualDeptIds = splitDingTalkDepartmentIds(currentInput).filter(
        (deptId) => !previousLoaded.has(deptId),
    );
    const manual = new Set(manualDeptIds);
    return [
        ...manualDeptIds,
        ...sortDingTalkDepartmentIds(nextLoadedDeptIds.map(String)).filter(
            (deptId) => !manual.has(deptId),
        ),
    ].join(" ");
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

function dingtalkDepartmentChildrenByParent<TDepartment extends DingTalkDepartmentNode>(
    departments: TDepartment[],
): Map<string, TDepartment[]> {
    const departmentIds = new Set(departments.map((department) => department.deptId));
    const childrenByParent = new Map<string, TDepartment[]>();
    for (const department of departments) {
        const parentId =
            department.parentId && departmentIds.has(department.parentId)
                ? department.parentId
                : "";
        childrenByParent.set(parentId, [...(childrenByParent.get(parentId) || []), department]);
    }
    for (const [parentId, children] of childrenByParent) {
        childrenByParent.set(parentId, sortDingTalkDepartments(children));
    }
    return childrenByParent;
}

function collectDingTalkDepartmentSubtreeIds(
    childrenByParent: Map<string, DingTalkDepartmentNode[]>,
    deptId: string,
): string[] {
    return [
        deptId,
        ...(childrenByParent.get(deptId) || []).flatMap((child) =>
            collectDingTalkDepartmentSubtreeIds(childrenByParent, child.deptId),
        ),
    ];
}

function dingtalkDepartmentSelectionState(
    subtreeIds: string[],
    selectedDeptIds: Set<string>,
): DingTalkDepartmentSelection {
    const selectedCount = subtreeIds.filter((deptId) => selectedDeptIds.has(deptId)).length;
    if (selectedCount === 0) {
        return "unchecked";
    }
    if (selectedCount === subtreeIds.length) {
        return "checked";
    }
    return "indeterminate";
}

export function buildDingTalkDepartmentTreeRows<TDepartment extends DingTalkDepartmentNode>(
    departments: TDepartment[],
    selectedDeptIds: Set<string>,
): DingTalkDepartmentTreeRow<TDepartment>[] {
    const childrenByParent = dingtalkDepartmentChildrenByParent(departments);
    const rows: DingTalkDepartmentTreeRow<TDepartment>[] = [];

    const visit = (department: TDepartment, level: number): void => {
        rows.push({
            department,
            level,
            selection: dingtalkDepartmentSelectionState(
                collectDingTalkDepartmentSubtreeIds(childrenByParent, department.deptId),
                selectedDeptIds,
            ),
        });
        for (const child of childrenByParent.get(department.deptId) || []) {
            visit(child as TDepartment, level + 1);
        }
    };

    for (const root of childrenByParent.get("") || []) {
        visit(root, 0);
    }

    return rows;
}

export function toggleDingTalkDepartmentTreeInput(
    currentInput: string,
    departments: DingTalkDepartmentNode[],
    deptId: string,
    selected: boolean,
): string {
    const childrenByParent = dingtalkDepartmentChildrenByParent(departments);
    const deptIds = new Set(splitDingTalkDepartmentIds(currentInput));
    for (const subtreeDeptId of collectDingTalkDepartmentSubtreeIds(childrenByParent, deptId)) {
        if (selected) {
            deptIds.add(subtreeDeptId);
        } else {
            deptIds.delete(subtreeDeptId);
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
            new Set([...splitDingTalkDepartmentIds(currentInput), ...loadedDingTalkDepartmentIds(departments)]),
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
