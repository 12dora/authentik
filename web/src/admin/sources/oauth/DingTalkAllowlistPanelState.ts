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
} {
    const deptIds = splitDingTalkDepartmentIds(departmentInputs[corpId] || "");
    const company = model.companies.find((candidate) => candidate.corpId === corpId);
    if (deptIds.length < 1 || !company) {
        return { model, departmentInputs };
    }

    return {
        model: updateDingTalkCompany(model, corpId, {
            allowAll: false,
            deptIds: Array.from(
                new Set([...company.deptIds.map((deptId) => String(deptId)), ...deptIds]),
            ),
        }),
        departmentInputs: { ...departmentInputs, [corpId]: "" },
    };
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
