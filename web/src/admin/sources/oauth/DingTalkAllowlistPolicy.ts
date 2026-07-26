import { msg, str } from "@lit/localize";

export const DINGTALK_ALLOWLIST_MARKER = "authentik-managed-dingtalk-allowlist";

export interface DingTalkAllowlistCompany {
    corpId: string;
    label: string;
    allowAll: boolean;
    deptIds: Array<string | number>;
}

export interface DingTalkAllowlistModel {
    companies: DingTalkAllowlistCompany[];
}

interface StoredDingTalkAllowlistCompany {
    corp_id: string;
    label?: string;
    name?: string;
    allow_all: boolean;
    dept_ids?: string[];
    dept_id_list?: string[];
}

interface StoredDingTalkAllowlistModel {
    companies: StoredDingTalkAllowlistCompany[];
}

function normalizeString(value: string | number | undefined | null): string {
    if (value === undefined || value === null) {
        return "";
    }
    return String(value).trim();
}

// Ordering must match Python's `sorted()` (plain code-point order), not any locale-
// or numeric-aware collation.
function sortStrings(values: string[]): string[] {
    return [...values].sort();
}

function fromStoredModel(model: StoredDingTalkAllowlistModel): DingTalkAllowlistModel {
    return {
        companies: model.companies.map((company) => ({
            corpId: company.corp_id,
            label: company.label ?? company.name ?? "",
            allowAll: company.allow_all,
            deptIds: company.dept_ids ?? company.dept_id_list ?? [],
        })),
    };
}

export function validateDingTalkAllowlistModel(
    model: DingTalkAllowlistModel,
): DingTalkAllowlistModel {
    if (!Array.isArray(model.companies) || model.companies.length < 1) {
        throw new Error(
            msg("Add at least one DingTalk company.", {
                id: "sources.oauth.dingtalk-allowlist.validation.company-required",
            }),
        );
    }

    const corpIds = new Set<string>();

    const companies = model.companies.map((company) => {
        const corpId = normalizeString(company.corpId);
        if (!corpId) {
            throw new Error(
                msg("Company corpId is required.", {
                    id: "sources.oauth.dingtalk-allowlist.validation.corp-id-required",
                }),
            );
        }
        if (corpIds.has(corpId)) {
            throw new Error(
                msg(str`Duplicate company corpId: ${corpId}.`, {
                    id: "sources.oauth.dingtalk-allowlist.validation.duplicate-corp-id",
                }),
            );
        }
        corpIds.add(corpId);

        const deptIds = sortStrings(
            Array.from(
                new Set(
                    company.deptIds
                        .map((deptId) => normalizeString(deptId))
                        .filter((deptId) => deptId.length > 0),
                ),
            ),
        );
        const allowAll = Boolean(company.allowAll);

        if (!allowAll && deptIds.length < 1) {
            throw new Error(
                msg(
                    str`Company ${corpId} must allow all users or include at least one department.`,
                    {
                        id: "sources.oauth.dingtalk-allowlist.validation.departments-required",
                    },
                ),
            );
        }

        return {
            corpId,
            label: normalizeString(company.label),
            allowAll,
            deptIds: allowAll ? [] : deptIds,
        };
    });

    return {
        // Plain code-point order keeps the serialized config (and therefore the
        // backend's config_version comparison) deterministic across environments.
        companies: companies.sort((left, right) =>
            left.corpId < right.corpId ? -1 : left.corpId > right.corpId ? 1 : 0,
        ),
    };
}

export function dingTalkAllowlistModelFromStoredConfig(
    value: unknown,
): DingTalkAllowlistModel | null {
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as Partial<StoredDingTalkAllowlistModel>;
    if (!Array.isArray(record.companies)) {
        return null;
    }
    try {
        return validateDingTalkAllowlistModel(
            fromStoredModel(record as StoredDingTalkAllowlistModel),
        );
    } catch {
        return null;
    }
}

export function hasDingTalkAllowlistPolicyMarker(expression: string): boolean {
    return expression.split("\n").some((line) => line.trim() === `# ${DINGTALK_ALLOWLIST_MARKER}`);
}

export function parseDingTalkAllowlistPolicy(expression: string): DingTalkAllowlistModel | null {
    if (!hasDingTalkAllowlistPolicyMarker(expression)) {
        return null;
    }

    const lines = expression.split("\n");
    const configLine = lines.find((line) => line.startsWith("# config: "));
    if (!configLine) {
        return null;
    }

    try {
        return dingTalkAllowlistModelFromStoredConfig(
            JSON.parse(configLine.slice("# config: ".length)),
        );
    } catch {
        return null;
    }
}
